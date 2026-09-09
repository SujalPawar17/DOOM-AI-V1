"""V6.2.5 world suggestions. No INFORM/LLM/tools/memory writes."""
from __future__ import annotations

import ast
import inspect
import os
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PROACTIVE_ENABLED", "false")
os.environ.setdefault("PROACTIVE_PREDICTION_ENABLED", "false")
os.environ.setdefault("PROACTIVE_SUGGEST_ENABLED", "false")

from proactive.config import (
    OWNER_ID,
    SUGGEST_RULE_VERSION,
    is_suggest_enabled,
)
from proactive.schemas import SIGNAL_TYPES
from proactive.snapshot import WorldSnapshot
from proactive.store import proactive_store
from proactive.suggest import (
    evaluate_suggest_worthiness,
    evaluate_world_suggestions,
    suggestion_fingerprint,
)
from proactive.suggest_templates import FORBIDDEN_TEMPLATE_SUBSTRINGS, SUGGEST_TEMPLATES
from proactive.worker import process_once


def _pg():
    from database.postgres_db import postgres_manager
    return postgres_manager.is_connected()


def _q(sql, params=None):
    from database.postgres_db import postgres_manager
    return postgres_manager.execute_query(sql, params, readonly=True)


def _w(sql, params=None):
    from database.postgres_db import postgres_manager
    return postgres_manager.execute_query(sql, params, readonly=False)


def _iso_owner() -> str:
    return "t625-" + uuid.uuid4().hex[:12]


def _on():
    os.environ["PROACTIVE_ENABLED"] = "true"
    os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
    os.environ["PROACTIVE_SUGGEST_ENABLED"] = "true"


def _off():
    os.environ["PROACTIVE_ENABLED"] = "false"
    os.environ["PROACTIVE_PREDICTION_ENABLED"] = "false"
    os.environ["PROACTIVE_SUGGEST_ENABLED"] = "false"


def _pred(
    ptype: str,
    *,
    owner: str = OWNER_ID,
    privacy: str = "NORMAL",
    risk: str = "MEDIUM",
    conf: float = 0.85,
    n_ev: int = 2,
    extra_prov: dict | None = None,
) -> str:
    pid = str(uuid.uuid4())
    fp = "pfp" + uuid.uuid4().hex
    now = time.time()
    eids = [str(uuid.uuid4()) for _ in range(n_ev)]
    prov = {"evidence_ids": eids}
    if extra_prov:
        prov.update(extra_prov)
    proactive_store.upsert_world_prediction(
        {
            "prediction_id": pid,
            "owner_id": owner,
            "prediction_type": ptype,
            "subject_key": "sk:" + pid,
            "claim_code": ptype[:40],
            "horizon_start": now,
            "horizon_end": now + 3600,
            "confidence": conf,
            "risk_class": risk,
            "privacy_class": privacy,
            "fingerprint": fp,
            "rule_id": ptype,
            "rule_version": "v624.1",
            "evaluated_at": now,
            "valid_until": now + 86400,
            "provenance": prov,
        },
        [],
    )
    return pid


class TestV625Suggest(unittest.TestCase):
    def setUp(self):
        _off()
        self._busy = patch("proactive.attention.cognition_busy", return_value=False)
        self._busy.start()
        if _pg():
            try:
                _w(
                    "UPDATE proactive_attention SET suggest_count = 0, cooldowns = '{}'::jsonb WHERE owner_id = %s",
                    (OWNER_ID,),
                )
            except Exception:
                pass

    def tearDown(self):
        self._busy.stop()
        _off()

    def test_01_suggest_flag_off_zero(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        os.environ["PROACTIVE_SUGGEST_ENABLED"] = "false"
        _pred("DEADLINE_HORIZON")
        n0 = int(_q("SELECT COUNT(*) AS n FROM world_suggestions")[0]["n"])
        evaluate_world_suggestions()
        n1 = int(_q("SELECT COUNT(*) AS n FROM world_suggestions")[0]["n"])
        self.assertEqual(n0, n1)
        self.assertFalse(is_suggest_enabled())

    def test_02_prediction_flag_off_zero(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "false"
        os.environ["PROACTIVE_SUGGEST_ENABLED"] = "true"
        _pred("DEADLINE_HORIZON")
        n0 = int(_q("SELECT COUNT(*) AS n FROM world_suggestions")[0]["n"])
        evaluate_world_suggestions()
        n1 = int(_q("SELECT COUNT(*) AS n FROM world_suggestions")[0]["n"])
        self.assertEqual(n0, n1)

    def test_03_proactive_flag_off_zero(self):
        os.environ["PROACTIVE_ENABLED"] = "false"
        n = process_once("t625-off")
        self.assertEqual(n, 0)

    def _assert_type(self, ptype, stype):
        _on()
        owner = _iso_owner()
        pid = _pred(ptype, owner=owner)
        evaluate_world_suggestions(owner)
        rows = _q(
            "SELECT suggestion_type, status FROM world_suggestions WHERE prediction_id = %s",
            (pid,),
        )
        self.assertTrue(rows)
        self.assertEqual(rows[0]["suggestion_type"], stype)
        self.assertIn(rows[0]["status"], ("OPEN", "DELIVERED"))

    def test_04_deadline_medium_suggests(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        self._assert_type("DEADLINE_HORIZON", "CONSIDER_REVIEW_WORK")

    def test_05_stale_normal_suggests(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        self._assert_type("STALE_OPEN_COMMITMENT", "CONSIDER_CONFIRM_OPEN")

    def test_06_conflict_suggests(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        self._assert_type("CAL_VS_COMMITMENT_CONFLICT", "CONSIDER_RECONCILE_TIME")

    def test_07_task_blocked_suggests(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        self._assert_type("TASK_BLOCKED_NEAR_DEADLINE", "CONSIDER_UNBLOCK")

    def test_08_review_aging_suggests(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        self._assert_type("OPEN_REVIEW_AGING", "CONSIDER_COMPLETE_REVIEW")

    def test_09_deadline_low_silent(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        pid = _pred("DEADLINE_HORIZON", owner=owner, risk="LOW")
        evaluate_world_suggestions(owner)
        rows = _q("SELECT suggestion_id FROM world_suggestions WHERE prediction_id = %s", (pid,))
        self.assertFalse(rows)

    def test_10_sensitive_none(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        pid = _pred("DEADLINE_HORIZON", owner=owner, privacy="SENSITIVE")
        evaluate_world_suggestions(owner)
        rows = _q("SELECT suggestion_id FROM world_suggestions WHERE prediction_id = %s", (pid,))
        self.assertFalse(rows)

    def test_11_private_persist_no_hud(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        pid = _pred("CAL_VS_COMMITMENT_CONFLICT", owner=owner, privacy="PRIVATE")
        evaluate_world_suggestions(owner)
        rows = _q(
            "SELECT suggestion_id, status FROM world_suggestions WHERE prediction_id = %s",
            (pid,),
        )
        self.assertTrue(rows)
        hud = proactive_store.list_hud_suggestions(owner, 50)
        ids = {h.get("suggestion_id") for h in hud}
        self.assertNotIn(rows[0]["suggestion_id"], ids)

    def test_12_duplicate_tick_one_row(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        pid = _pred("DEADLINE_HORIZON", owner=owner)
        evaluate_world_suggestions(owner)
        evaluate_world_suggestions(owner)
        rows = _q("SELECT suggestion_id FROM world_suggestions WHERE prediction_id = %s", (pid,))
        self.assertEqual(len(rows or []), 1)

    def test_13_concurrent_upsert_one_row(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        pid = _pred("DEADLINE_HORIZON", owner=owner)
        pred = [p for p in proactive_store.list_suggest_candidates(owner, 50) if p["prediction_id"] == pid][0]
        from proactive.suggest import TYPE_MAP, suggestion_fingerprint as sfp
        stype, tid = TYPE_MAP[pred["prediction_type"]]
        fp = sfp(owner, pred["fingerprint"], stype, tid, SUGGEST_RULE_VERSION)
        now = time.time()

        def _one(i):
            proactive_store.upsert_world_suggestion({
                "suggestion_id": str(uuid.uuid4()),
                "owner_id": owner,
                "prediction_id": pid,
                "suggestion_type": stype,
                "claim_code": pred["claim_code"],
                "template_id": tid,
                "safe_params": {"risk_class": "MEDIUM"},
                "priority": "MEDIUM",
                "confidence": 0.85,
                "risk_class": "MEDIUM",
                "privacy_class": "NORMAL",
                "fingerprint": fp,
                "rule_id": stype,
                "rule_version": SUGGEST_RULE_VERSION,
                "valid_until": now + 3600,
                "evaluated_at": now,
                "provenance": {},
            })

        t1 = threading.Thread(target=_one, args=(1,))
        t2 = threading.Thread(target=_one, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        rows = _q(
            "SELECT suggestion_id FROM world_suggestions WHERE owner_id = %s AND fingerprint = %s",
            (owner, fp),
        )
        self.assertEqual(len(rows or []), 1)

    def test_14_cooldown_no_second_delivery(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        pid = _pred("DEADLINE_HORIZON", owner=owner)
        evaluate_world_suggestions(owner)
        att0 = proactive_store.get_attention(owner, __import__("datetime").date.today().isoformat())
        c0 = int(att0.get("suggest_count") or 0)
        evaluate_world_suggestions(owner)
        att1 = proactive_store.get_attention(owner, __import__("datetime").date.today().isoformat())
        self.assertEqual(int(att1.get("suggest_count") or 0), c0)
        self.assertLessEqual(c0, 1)
        n = int(_q(
            "SELECT COUNT(*) AS n FROM world_suggestion_deliveries d "
            "JOIN world_suggestions s ON s.suggestion_id = d.suggestion_id "
            "WHERE s.prediction_id = %s",
            (pid,),
        )[0]["n"])
        self.assertEqual(n, 1)

    def test_15_daily_budget(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = "bud-" + uuid.uuid4().hex[:10]
        pids = [_pred("DEADLINE_HORIZON", owner=owner) for _ in range(5)]
        evaluate_world_suggestions(owner)
        delivered = 0
        open_n = 0
        for pid in pids:
            st = _q("SELECT status FROM world_suggestions WHERE prediction_id = %s", (pid,))
            self.assertTrue(st)
            if st[0]["status"] == "DELIVERED":
                delivered += 1
            if st[0]["status"] == "OPEN":
                open_n += 1
        self.assertEqual(delivered, 4)
        self.assertEqual(open_n, 1)

    def test_16_fingerprint_stable(self):
        a = suggestion_fingerprint("o", "pfp", "CONSIDER_REVIEW_WORK", "suggest_review_work", "v625.1")
        b = suggestion_fingerprint("o", "pfp", "CONSIDER_REVIEW_WORK", "suggest_review_work", "v625.1")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 48)

    def test_17_dismissed_not_reopened(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        pid = _pred("DEADLINE_HORIZON", owner=owner)
        evaluate_world_suggestions(owner)
        sid = _q("SELECT suggestion_id FROM world_suggestions WHERE prediction_id = %s", (pid,))[0]["suggestion_id"]
        self.assertTrue(proactive_store.dismiss_suggestion(sid, owner))
        evaluate_world_suggestions(owner)
        st = _q("SELECT status FROM world_suggestions WHERE suggestion_id = %s", (sid,))[0]["status"]
        self.assertEqual(st, "DISMISSED")

    def test_18_prediction_expired_suggestion_expired(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        pid = _pred("DEADLINE_HORIZON", owner=owner)
        evaluate_world_suggestions(owner)
        _w("UPDATE world_predictions SET status = 'EXPIRED' WHERE prediction_id = %s", (pid,))
        evaluate_world_suggestions(owner)
        st = _q("SELECT status FROM world_suggestions WHERE prediction_id = %s", (pid,))[0]["status"]
        self.assertEqual(st, "EXPIRED")

    def test_19_prediction_superseded_suggestion_superseded(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        pid = _pred("DEADLINE_HORIZON", owner=owner)
        evaluate_world_suggestions(owner)
        _w("UPDATE world_predictions SET status = 'SUPERSEDED' WHERE prediction_id = %s", (pid,))
        evaluate_world_suggestions(owner)
        st = _q("SELECT status FROM world_suggestions WHERE prediction_id = %s", (pid,))[0]["status"]
        self.assertEqual(st, "SUPERSEDED")

    def test_20_delivery_retry(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        pid = _pred("DEADLINE_HORIZON", owner=owner)
        with patch("proactive.suggest.may_suggest", return_value=(False, "budget")):
            evaluate_world_suggestions(owner)
        row = _q(
            "SELECT suggestion_id, status FROM world_suggestions WHERE prediction_id = %s",
            (pid,),
        )
        self.assertTrue(row)
        self.assertEqual(row[0]["status"], "OPEN")
        self.assertFalse(proactive_store.suggestion_delivery_exists(row[0]["suggestion_id"], "hud"))
        evaluate_world_suggestions(owner)
        st = _q("SELECT status FROM world_suggestions WHERE prediction_id = %s", (pid,))[0]["status"]
        self.assertEqual(st, "DELIVERED")
        self.assertTrue(proactive_store.suggestion_delivery_exists(row[0]["suggestion_id"], "hud"))

    def test_21_suggest_exception_inform_runs(self):
        os.environ["PROACTIVE_ENABLED"] = "true"
        with patch("proactive.worker.evaluate_world_suggestions", side_effect=RuntimeError("x")):
            n = process_once("t625-iso")
        self.assertIsInstance(n, int)

    def test_22_cross_owner_hidden(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        pid = _pred("DEADLINE_HORIZON", owner="alice-" + uuid.uuid4().hex[:8])
        owner = _q("SELECT owner_id FROM world_predictions WHERE prediction_id = %s", (pid,))[0]["owner_id"]
        evaluate_world_suggestions(owner)
        hud = proactive_store.list_hud_suggestions(OWNER_ID, 50)
        pids = []
        for h in hud:
            r = _q("SELECT prediction_id FROM world_suggestions WHERE suggestion_id = %s", (h["suggestion_id"],))
            if r:
                pids.append(r[0]["prediction_id"])
        self.assertNotIn(pid, pids)

    def test_23_ast_no_authority(self):
        root = Path(__file__).resolve().parent / "proactive"
        files = ["suggest.py", "suggest_templates.py", "worker.py", "delivery.py"]
        blob = ""
        for name in files:
            blob += (root / name).read_text(encoding="utf-8")
        sug = (root / "suggest.py").read_text(encoding="utf-8")
        self.assertNotIn("TaskEngine", sug)
        self.assertNotIn("task_engine", sug)
        self.assertNotIn("tool_registry", sug)
        self.assertNotIn("insert_insight", sug)
        self.assertNotIn("ingest_signal", sug)
        self.assertNotIn("model_router", sug)
        self.assertNotIn("evaluate_significance", sug)
        self.assertNotIn("SafeHttp", sug)
        sig = inspect.signature(evaluate_suggest_worthiness)
        self.assertEqual(list(sig.parameters), ["pred", "now"])
        ast.parse(sug)

    def test_24_memory_count_invariant(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        _pred("DEADLINE_HORIZON", owner=owner)
        n0 = int(_q("SELECT COUNT(*) AS n FROM memory_records")[0]["n"])
        evaluate_world_suggestions(owner)
        n1 = int(_q("SELECT COUNT(*) AS n FROM memory_records")[0]["n"])
        self.assertEqual(n0, n1)

    def test_25_insights_api_inform_only(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        _pred("DEADLINE_HORIZON", owner=owner)
        evaluate_world_suggestions(owner)
        import asyncio
        from dashboard.server import proactive_insights
        data = asyncio.run(proactive_insights())
        self.assertIsInstance(data, dict)
        for item in data.get("insights") or []:
            self.assertEqual(item.get("intervention"), "INFORM")
            self.assertNotEqual(item.get("type"), "proactive_suggestion")

    def test_26_templates_no_action_verbs(self):
        corpus = " ".join(SUGGEST_TEMPLATES.values()).lower()
        for frag in FORBIDDEN_TEMPLATE_SUBSTRINGS:
            self.assertNotIn(frag, corpus)

    def test_27_injection_not_in_safe_params(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        pid = _pred(
            "DEADLINE_HORIZON",
            owner=owner,
            extra_prov={"subject": "secret-subj", "url": "https://evil", "body": "x", "snippet": "y"},
        )
        evaluate_world_suggestions(owner)
        row = _q("SELECT safe_params FROM world_suggestions WHERE prediction_id = %s", (pid,))
        self.assertTrue(row)
        params = row[0]["safe_params"]
        if isinstance(params, str):
            import json
            params = json.loads(params)
        allowed = {"risk_class", "horizon_hours", "prediction_type", "claim_code"}
        self.assertTrue(set(params.keys()) <= allowed)
        blob = str(params).lower()
        self.assertNotIn("secret-subj", blob)
        self.assertNotIn("https://evil", blob)

    def test_28_stale_private_single_evidence_ignore(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        pid = _pred("STALE_OPEN_COMMITMENT", owner=owner, privacy="PRIVATE", n_ev=1)
        evaluate_world_suggestions(owner)
        rows = _q("SELECT suggestion_id FROM world_suggestions WHERE prediction_id = %s", (pid,))
        self.assertFalse(rows)

    def test_29_no_prediction_eval_signal(self):
        self.assertNotIn("PREDICTION_EVAL", SIGNAL_TYPES)
        self.assertNotIn("SUGGEST_EVAL", SIGNAL_TYPES)

    def test_30_busy_or_quiet_no_budget_bump(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        att0 = proactive_store.get_attention(owner, __import__("datetime").date.today().isoformat())
        c0 = int(att0.get("suggest_count") or 0)
        pid = _pred("DEADLINE_HORIZON", owner=owner)
        with patch("proactive.suggest.may_suggest", return_value=(False, "busy")):
            evaluate_world_suggestions(owner)
        att1 = proactive_store.get_attention(owner, __import__("datetime").date.today().isoformat())
        self.assertEqual(int(att1.get("suggest_count") or 0), c0)
        st = _q("SELECT status FROM world_suggestions WHERE prediction_id = %s", (pid,))[0]["status"]
        self.assertEqual(st, "OPEN")

    def test_31_snapshot_has_no_suggestions_field(self):
        self.assertFalse(hasattr(WorldSnapshot, "suggestions") and "suggestions" in WorldSnapshot.__dataclass_fields__)
        self.assertNotIn("suggestions", WorldSnapshot.__dataclass_fields__)

    def test_32_otp_suggestion_id_allowed(self):
        from observability.schemas import ALLOWED_ATTR_KEYS, FORBIDDEN_ATTR_KEYS
        self.assertIn("suggestion_id", ALLOWED_ATTR_KEYS)
        self.assertIn("suggestion_type", ALLOWED_ATTR_KEYS)
        self.assertIn("body", FORBIDDEN_ATTR_KEYS)

    def test_33_dismiss_404_other_owner(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        pid = _pred("DEADLINE_HORIZON", owner="alice-" + uuid.uuid4().hex[:8])
        owner = _q("SELECT owner_id FROM world_predictions WHERE prediction_id = %s", (pid,))[0]["owner_id"]
        evaluate_world_suggestions(owner)
        sid = _q("SELECT suggestion_id FROM world_suggestions WHERE prediction_id = %s", (pid,))[0]["suggestion_id"]
        self.assertFalse(proactive_store.dismiss_suggestion(sid, OWNER_ID))
        import asyncio
        from dashboard.server import dismiss_proactive_suggestion
        resp = asyncio.run(dismiss_proactive_suggestion(sid))
        code = getattr(resp, "status_code", None)
        self.assertEqual(code, 404)

    def test_34_no_significance_import(self):
        src = (Path(__file__).resolve().parent / "proactive" / "suggest.py").read_text(encoding="utf-8")
        self.assertNotIn("evaluate_significance", src)
        self.assertNotIn("from proactive.significance", src)
        tree = ast.parse(src)
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("significance"):
                names.append(node.module)
        self.assertFalse(names)

    def test_35_candidate_cap_50(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = "cap-" + uuid.uuid4().hex[:10]
        for _ in range(51):
            _pred("DEADLINE_HORIZON", owner=owner)
        t0 = time.perf_counter()
        evaluate_world_suggestions(owner)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        n = int(_q(
            "SELECT COUNT(*) AS n FROM world_suggestions WHERE owner_id = %s",
            (owner,),
        )[0]["n"])
        self.assertLessEqual(n, 50)
        self.assertGreaterEqual(n, 50)
        print(f"V625_PERF_MS={elapsed_ms:.2f}")

    def test_36_v624_suite_still_imports(self):
        import test_v624_evidence_prediction as m
        self.assertTrue(hasattr(m, "TestV624EvidencePrediction"))


if __name__ == "__main__":
    unittest.main()
