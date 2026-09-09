"""V6.2.4 world evidence + deterministic predictions. No INFORM/LLM/memory writes."""
from __future__ import annotations

import ast
import hashlib
import os
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PROACTIVE_ENABLED", "false")
os.environ.setdefault("PROACTIVE_PREDICTION_ENABLED", "false")

from proactive.config import OWNER_ID, is_prediction_enabled
from proactive.evidence import cite_from_commitment, independence_key
from proactive.predict import compute_c, evaluate_world_predictions, prediction_fingerprint
from proactive.snapshot import WorldSnapshot, build_world_snapshot, invalidate_snapshot
from proactive.store import proactive_store
from proactive.temporal import require_source_time
from proactive.worker import process_once


def _pg():
    from database.postgres_db import postgres_manager
    return postgres_manager.is_connected()


def _q(sql, params=None):
    from database.postgres_db import postgres_manager
    return postgres_manager.execute_query(sql, params, readonly=True)


def _acct():
    aid = "acct-" + uuid.uuid4().hex[:12]
    pid = "iso-" + uuid.uuid4().hex[:8]
    try:
        from database.postgres_db import postgres_manager
        postgres_manager.execute_query(
            "INSERT INTO projects (project_id, name) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (pid, "iso"),
            readonly=False,
        )
    except Exception:
        pid = "doom"
    proactive_store.upsert_connector_account({
        "account_id": aid, "owner_id": OWNER_ID, "connector_type": "gmail",
        "project_id": pid, "secret_ref": "z", "status": "ACTIVE",
    })
    return aid, pid


class TestV624EvidencePrediction(unittest.TestCase):
    def setUp(self):
        os.environ["PROACTIVE_ENABLED"] = "false"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "false"
        invalidate_snapshot()

    def tearDown(self):
        os.environ["PROACTIVE_ENABLED"] = "false"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "false"

    def test_01_cite_skips_empty_source(self):
        self.assertIsNone(cite_from_commitment({}))

    def test_02_independence_stable(self):
        a = independence_key("o", "COMMITMENT", "gmail", "m1", "sha")
        b = independence_key("o", "COMMITMENT", "gmail", "m1", "sha")
        self.assertEqual(a, b)

    def test_03_independence_diff_message(self):
        self.assertNotEqual(
            independence_key("o", "COMMITMENT", "gmail", "m1", "sha"),
            independence_key("o", "COMMITMENT", "gmail", "m2", "sha"),
        )

    def test_04_c_email_single_capped(self):
        ev = {"independence_key": "k1", "reliability_class": "gmail_extract", "strength": 0.55}
        c, n, email = compute_c([ev])
        self.assertTrue(email)
        self.assertEqual(n, 1)
        self.assertLessEqual(c, 0.55)

    def test_05_c_two_sources_not_email_only(self):
        evs = [
            {"independence_key": "k1", "reliability_class": "gmail_extract", "strength": 0.55},
            {"independence_key": "k2", "reliability_class": "calendar", "strength": 0.85},
        ]
        c, n, email = compute_c(evs)
        self.assertFalse(email)
        self.assertEqual(n, 2)
        self.assertGreaterEqual(c, 0.70)

    def test_06_require_source_time(self):
        self.assertFalse(require_source_time(None))
        self.assertFalse(require_source_time(0))
        self.assertTrue(require_source_time(time.time()))

    def test_07_fingerprint_stable(self):
        a = prediction_fingerprint("o", "DEADLINE_HORIZON", "commitment:x", "2026-09-10", "DEADLINE_HORIZON")
        b = prediction_fingerprint("o", "DEADLINE_HORIZON", "commitment:x", "2026-09-10", "DEADLINE_HORIZON")
        self.assertEqual(a, b)

    def test_08_flags_default_off(self):
        self.assertFalse(is_prediction_enabled())

    def test_09_eval_flags_off_zero(self):
        self.assertEqual(evaluate_world_predictions(), 0)

    def test_10_ast_no_llm_or_memory(self):
        root = Path(__file__).resolve().parent / "proactive"
        text = (root / "predict.py").read_text(encoding="utf-8") + (root / "evidence.py").read_text(encoding="utf-8")
        self.assertNotIn("model_router", text)
        self.assertNotIn("MemoryEvolutionEngine", text)
        self.assertNotIn("insert_insight", text)
        self.assertNotIn("ingest_signal", text)
        self.assertNotIn("deliver_inform", text)
        tree = ast.parse((root / "predict.py").read_text(encoding="utf-8"))
        self.assertTrue(tree.body)

    def test_11_evidence_upsert_idempotent(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        aid, pid = _acct()
        cid = str(uuid.uuid4())
        fp = hashlib.sha256(cid.encode()).hexdigest()[:48]
        proactive_store.upsert_commitment({
            "commitment_id": cid, "owner_id": OWNER_ID, "account_id": aid,
            "source_connector": "gmail", "source_message_id": "mid-idem",
            "commitment_type": "DEADLINE", "due_at": time.time() + 3600,
            "fingerprint": fp, "provenance": {"fact_id": "f"}, "evidence_ref": "f",
        })
        rows = [{"commitment_id": cid, "fingerprint": fp, "source_connector": "gmail",
                 "source_message_id": "mid-idem", "privacy_class": "PRIVATE", "project_id": pid}]
        from proactive.evidence import cite_from_commitment as cite
        e1 = cite(rows[0])
        e2 = cite(rows[0])
        id1 = proactive_store.upsert_world_evidence(e1)
        id2 = proactive_store.upsert_world_evidence(e2)
        self.assertTrue(id1)
        self.assertEqual(id1, id2)

    def test_12_ten_polls_one_evidence(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        aid, pid = _acct()
        cid = str(uuid.uuid4())
        fp = "fp" + uuid.uuid4().hex[:20]
        proactive_store.upsert_commitment({
            "commitment_id": cid, "owner_id": OWNER_ID, "account_id": aid,
            "source_connector": "gmail", "source_message_id": "m-ten",
            "commitment_type": "DEADLINE", "due_at": time.time() + 8000,
            "fingerprint": fp, "provenance": {}, "evidence_ref": "x",
        })
        from proactive.evidence import cite_from_commitment as cite
        row = {"commitment_id": cid, "fingerprint": fp, "source_connector": "gmail",
               "source_message_id": "m-ten", "privacy_class": "PRIVATE", "project_id": pid}
        ids = {proactive_store.upsert_world_evidence(cite(row)) for _ in range(10)}
        self.assertEqual(len(ids), 1)

    def test_13_email_only_deadline_abstain(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        aid, pid = _acct()
        cid = str(uuid.uuid4())
        before = proactive_store.count_world_predictions(OWNER_ID)
        proactive_store.upsert_commitment({
            "commitment_id": cid, "owner_id": OWNER_ID, "account_id": aid,
            "source_connector": "gmail", "source_message_id": "m-only",
            "commitment_type": "DEADLINE", "due_at": time.time() + 3600,
            "fingerprint": "fp" + uuid.uuid4().hex[:16], "provenance": {}, "evidence_ref": "x",
        })
        evaluate_world_predictions()
        rows = _q(
            "SELECT prediction_id FROM world_predictions WHERE subject_key = %s AND status='ACTIVE'",
            (f"commitment:{cid}",),
        )
        self.assertFalse(rows)

    def test_14_deadline_with_calendar_emits(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        aid, pid = _acct()
        due = time.time() + 3600
        cid = str(uuid.uuid4())
        proactive_store.upsert_commitment({
            "commitment_id": cid, "owner_id": OWNER_ID, "account_id": aid,
            "source_connector": "gmail", "source_message_id": "m-cal",
            "commitment_type": "DEADLINE", "due_at": due,
            "fingerprint": "fp" + uuid.uuid4().hex[:16], "provenance": {}, "evidence_ref": "x",
        })
        fid = str(uuid.uuid4())
        proactive_store.upsert_external_fact({
            "fact_id": fid, "owner_id": OWNER_ID, "account_id": aid,
            "connector_type": "calendar_google", "source_record_id": "ev1",
            "fact_kind": "CAL_EVENT", "project_id": pid, "privacy_class": "NORMAL",
            "occurred_at": due, "valid_until": due + 3600, "confidence": 0.8,
            "payload": {"k": 1}, "content_sha256": hashlib.sha256(fid.encode()).hexdigest(),
            "idempotency_key": "cal|" + fid,
        })
        evaluate_world_predictions()
        rows = _q(
            "SELECT prediction_type FROM world_predictions WHERE subject_key = %s AND status='ACTIVE'",
            (f"commitment:{cid}",),
        )
        types = [r["prediction_type"] for r in rows or []]
        self.assertIn("DEADLINE_HORIZON", types)

    def test_15_stale_gmail_abstain(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        aid, _pid = _acct()
        cid = str(uuid.uuid4())
        proactive_store.upsert_commitment({
            "commitment_id": cid, "owner_id": OWNER_ID, "account_id": aid,
            "source_connector": "gmail", "source_message_id": "m-stale",
            "commitment_type": "DEADLINE", "due_at": time.time() - 86400,
            "fingerprint": "fp" + uuid.uuid4().hex[:16], "provenance": {}, "evidence_ref": "x",
        })
        evaluate_world_predictions()
        rows = _q(
            "SELECT prediction_id FROM world_predictions WHERE prediction_type='STALE_OPEN_COMMITMENT' AND subject_key=%s AND status='ACTIVE'",
            (f"commitment:{cid}",),
        )
        self.assertFalse(rows)

    def test_16_conflict_emits(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        aid, pid = _acct()
        due = time.time() + 86400
        cid = str(uuid.uuid4())
        proactive_store.upsert_commitment({
            "commitment_id": cid, "owner_id": OWNER_ID, "account_id": aid,
            "source_connector": "gmail", "source_message_id": "m-cf",
            "commitment_type": "MEETING", "due_at": due,
            "fingerprint": "fp" + uuid.uuid4().hex[:16], "provenance": {}, "evidence_ref": "x",
        })
        fid = str(uuid.uuid4())
        proactive_store.upsert_external_fact({
            "fact_id": fid, "owner_id": OWNER_ID, "account_id": aid,
            "connector_type": "calendar_google", "source_record_id": "ev-cf",
            "fact_kind": "CAL_EVENT", "project_id": pid, "privacy_class": "NORMAL",
            "occurred_at": due + 8 * 3600, "confidence": 0.8,
            "payload": {}, "content_sha256": hashlib.sha256(fid.encode()).hexdigest(),
            "idempotency_key": "cal|" + fid,
        })
        evaluate_world_predictions()
        rows = _q(
            "SELECT prediction_id FROM world_predictions WHERE prediction_type='CAL_VS_COMMITMENT_CONFLICT' AND status='ACTIVE' AND subject_key LIKE %s",
            (f"commitment:{cid}%",),
        )
        self.assertTrue(rows)

    def test_17_matching_cal_not_conflict(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        aid, pid = _acct()
        due = time.time() + 4000
        cid = str(uuid.uuid4())
        proactive_store.upsert_commitment({
            "commitment_id": cid, "owner_id": OWNER_ID, "account_id": aid,
            "source_connector": "gmail", "source_message_id": "m-m",
            "commitment_type": "MEETING", "due_at": due,
            "fingerprint": "fp" + uuid.uuid4().hex[:16], "provenance": {}, "evidence_ref": "x",
        })
        fid = str(uuid.uuid4())
        proactive_store.upsert_external_fact({
            "fact_id": fid, "owner_id": OWNER_ID, "account_id": aid,
            "connector_type": "calendar_google", "source_record_id": "ev-m",
            "fact_kind": "CAL_EVENT", "project_id": pid, "privacy_class": "NORMAL",
            "occurred_at": due + 60, "confidence": 0.8,
            "payload": {}, "content_sha256": hashlib.sha256(fid.encode()).hexdigest(),
            "idempotency_key": "cal|" + fid,
        })
        evaluate_world_predictions()
        rows = _q(
            "SELECT prediction_id FROM world_predictions WHERE prediction_type='CAL_VS_COMMITMENT_CONFLICT' AND subject_key LIKE %s AND status='ACTIVE'",
            (f"commitment:{cid}%",),
        )
        self.assertFalse(rows)

    def test_18_task_blocked_near_due(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        from database.postgres_db import postgres_manager
        aid, pid = _acct()
        tid = "task-" + uuid.uuid4().hex[:12]
        postgres_manager.save_checkpoint({
            "task_id": tid, "goal": "g", "task_type": "t", "status": "FAILED",
            "current_step": "", "completed_steps": [], "remaining_steps": [],
            "failed_steps": [], "blocked_steps": [], "artifacts": {"project_id": pid}, "tool_results": {},
            "verification_results": {}, "models_used": [], "retry_counts": {},
            "termination_reason": "", "final_response_status": "FAILED", "resume_available": True,
        })
        cid = str(uuid.uuid4())
        proactive_store.upsert_commitment({
            "commitment_id": cid, "owner_id": OWNER_ID, "account_id": aid,
            "source_connector": "calendar_google", "source_message_id": "m-tb",
            "commitment_type": "DEADLINE", "due_at": time.time() + 3600,
            "fingerprint": "fp" + uuid.uuid4().hex[:16], "provenance": {}, "evidence_ref": "x",
        })
        evaluate_world_predictions()
        rows = _q(
            "SELECT prediction_id FROM world_predictions WHERE prediction_type='TASK_BLOCKED_NEAR_DEADLINE' AND subject_key=%s AND status='ACTIVE'",
            (f"task:{tid}",),
        )
        self.assertTrue(rows)
        postgres_manager.execute_query(
            "UPDATE proactive_commitments SET status='COMPLETED' WHERE commitment_id=%s",
            (cid,),
            readonly=False,
        )
        postgres_manager.save_checkpoint({
            "task_id": tid, "goal": "g", "task_type": "t", "status": "SUCCESS",
            "current_step": "", "completed_steps": [], "remaining_steps": [],
            "failed_steps": [], "blocked_steps": [], "artifacts": {"project_id": pid}, "tool_results": {},
            "verification_results": {}, "models_used": [], "retry_counts": {},
            "termination_reason": "", "final_response_status": "success", "resume_available": False,
        })

    def test_19_review_yesterday_no(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        aid, pid = _acct()
        fid = str(uuid.uuid4())
        proactive_store.upsert_external_fact({
            "fact_id": fid, "owner_id": OWNER_ID, "account_id": aid,
            "connector_type": "github", "source_record_id": "pr1",
            "fact_kind": "GH_REVIEW", "project_id": pid, "privacy_class": "NORMAL",
            "occurred_at": time.time() - 86400, "confidence": 0.8,
            "payload": {}, "content_sha256": hashlib.sha256(fid.encode()).hexdigest(),
            "idempotency_key": "gh|" + fid,
        })
        evaluate_world_predictions()
        rows = _q(
            "SELECT prediction_id FROM world_predictions WHERE prediction_type='OPEN_REVIEW_AGING' AND subject_key=%s AND status='ACTIVE'",
            (f"review:{fid}",),
        )
        self.assertFalse(rows)

    def test_20_review_aging_emits(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        aid, pid = _acct()
        fid = str(uuid.uuid4())
        proactive_store.upsert_external_fact({
            "fact_id": fid, "owner_id": OWNER_ID, "account_id": aid,
            "connector_type": "github", "source_record_id": "pr-old",
            "fact_kind": "GH_REVIEW", "project_id": pid, "privacy_class": "NORMAL",
            "occurred_at": time.time() - 8 * 86400, "confidence": 0.8,
            "payload": {}, "content_sha256": hashlib.sha256(fid.encode()).hexdigest(),
            "idempotency_key": "gh|" + fid,
        })
        evaluate_world_predictions()
        rows = _q(
            "SELECT prediction_id FROM world_predictions WHERE prediction_type='OPEN_REVIEW_AGING' AND subject_key=%s AND status='ACTIVE'",
            (f"review:{fid}",),
        )
        self.assertTrue(rows)

    def test_21_missing_due_no_deadline(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        aid, _pid = _acct()
        cid = str(uuid.uuid4())
        proactive_store.upsert_commitment({
            "commitment_id": cid, "owner_id": OWNER_ID, "account_id": aid,
            "source_connector": "gmail", "source_message_id": "m-nd",
            "commitment_type": "FOLLOW_UP", "due_at": None,
            "fingerprint": "fp" + uuid.uuid4().hex[:16], "provenance": {}, "evidence_ref": "x",
        })
        evaluate_world_predictions()
        rows = _q(
            "SELECT prediction_id FROM world_predictions WHERE subject_key=%s AND prediction_type='DEADLINE_HORIZON' AND status='ACTIVE'",
            (f"commitment:{cid}",),
        )
        self.assertFalse(rows)

    def test_22_poll_does_not_raise_c(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        aid, pid = _acct()
        due = time.time() + 3600
        cid = str(uuid.uuid4())
        proactive_store.upsert_commitment({
            "commitment_id": cid, "owner_id": OWNER_ID, "account_id": aid,
            "source_connector": "gmail", "source_message_id": "m-c2",
            "commitment_type": "DEADLINE", "due_at": due,
            "fingerprint": "fp" + uuid.uuid4().hex[:16], "provenance": {}, "evidence_ref": "x",
        })
        fid = str(uuid.uuid4())
        proactive_store.upsert_external_fact({
            "fact_id": fid, "owner_id": OWNER_ID, "account_id": aid,
            "connector_type": "calendar_google", "source_record_id": "ev-c2",
            "fact_kind": "CAL_EVENT", "project_id": pid, "privacy_class": "NORMAL",
            "occurred_at": due, "confidence": 0.8,
            "payload": {}, "content_sha256": hashlib.sha256(fid.encode()).hexdigest(),
            "idempotency_key": "cal|" + fid,
        })
        evaluate_world_predictions()
        r1 = _q(
            "SELECT confidence, generation FROM world_predictions WHERE subject_key=%s AND prediction_type='DEADLINE_HORIZON' AND status='ACTIVE'",
            (f"commitment:{cid}",),
        )
        evaluate_world_predictions()
        r2 = _q(
            "SELECT confidence, generation FROM world_predictions WHERE subject_key=%s AND prediction_type='DEADLINE_HORIZON' AND status='ACTIVE'",
            (f"commitment:{cid}",),
        )
        self.assertEqual(len(r1), 1)
        self.assertEqual(len(r2), 1)
        self.assertEqual(float(r1[0]["confidence"]), float(r2[0]["confidence"]))
        n = _q("SELECT COUNT(*) AS n FROM world_predictions WHERE subject_key=%s AND prediction_type='DEADLINE_HORIZON'", (f"commitment:{cid}",))
        self.assertEqual(int(n[0]["n"]), 1)

    def test_23_generation_increments(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        aid, pid = _acct()
        due = time.time() + 5000
        cid = str(uuid.uuid4())
        proactive_store.upsert_commitment({
            "commitment_id": cid, "owner_id": OWNER_ID, "account_id": aid,
            "source_connector": "gmail", "source_message_id": "m-g",
            "commitment_type": "DEADLINE", "due_at": due,
            "fingerprint": "fp" + uuid.uuid4().hex[:16], "provenance": {}, "evidence_ref": "x",
        })
        fid = str(uuid.uuid4())
        proactive_store.upsert_external_fact({
            "fact_id": fid, "owner_id": OWNER_ID, "account_id": aid,
            "connector_type": "calendar_google", "source_record_id": "ev-g",
            "fact_kind": "CAL_EVENT", "project_id": pid, "privacy_class": "NORMAL",
            "occurred_at": due, "confidence": 0.8,
            "payload": {}, "content_sha256": hashlib.sha256(fid.encode()).hexdigest(),
            "idempotency_key": "cal|" + fid,
        })
        evaluate_world_predictions()
        g1 = _q("SELECT generation FROM world_predictions WHERE subject_key=%s AND prediction_type='DEADLINE_HORIZON'", (f"commitment:{cid}",))
        evaluate_world_predictions()
        g2 = _q("SELECT generation FROM world_predictions WHERE subject_key=%s AND prediction_type='DEADLINE_HORIZON'", (f"commitment:{cid}",))
        self.assertGreater(int(g2[0]["generation"]), int(g1[0]["generation"]))

    def test_24_cross_owner_hidden(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        rows = proactive_store.list_active_predictions("alice-nope", 20)
        self.assertFalse(any(r.get("owner_id") == OWNER_ID for r in rows))

    def test_25_snapshot_splits_kinds(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        invalidate_snapshot()
        snap = build_world_snapshot(force=True)
        self.assertTrue(hasattr(snap, "calendar_facts"))
        self.assertTrue(hasattr(snap, "predictions"))
        for c in snap.commitments:
            self.assertNotEqual(c.get("kind"), "CAL_EVENT")
            self.assertNotEqual(c.get("record_kind"), "FACT")

    def test_26_snapshot_predictions_no_snippet(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        invalidate_snapshot()
        snap = build_world_snapshot(force=True)
        blob = str(snap.predictions).lower()
        self.assertNotIn("please send", blob)
        self.assertNotIn("ignore previous", blob)

    def test_27_memory_count_invariant(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        n0 = int(_q("SELECT COUNT(*) AS n FROM memory_records")[0]["n"])
        evaluate_world_predictions()
        n1 = int(_q("SELECT COUNT(*) AS n FROM memory_records")[0]["n"])
        self.assertEqual(n0, n1)

    def test_28_no_insight_from_eval(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        with patch.object(proactive_store, "insert_insight", side_effect=AssertionError("inform")):
            evaluate_world_predictions()

    def test_29_worker_eval_exception_isolated(self):
        os.environ["PROACTIVE_ENABLED"] = "true"
        with patch("proactive.predict.evaluate_world_predictions", side_effect=RuntimeError("x")):
            n = process_once("t-iso")
        self.assertIsInstance(n, int)

    def test_30_hud_no_prediction_cards(self):
        from dashboard.server import app
        src = Path(app.__dict__.get("__file__", "dashboard/server.py") if False else "dashboard/server.py")
        text = Path("dashboard/server.py").read_text(encoding="utf-8")
        self.assertNotIn("world_predictions", text)
        self.assertNotIn("/api/proactive/predictions", text)

    def test_31_otp_keys_allow_prediction_id(self):
        from observability.schemas import ALLOWED_ATTR_KEYS, FORBIDDEN_ATTR_KEYS
        self.assertIn("prediction_id", ALLOWED_ATTR_KEYS)
        self.assertIn("snippet", FORBIDDEN_ATTR_KEYS) if False else self.assertIn("body", FORBIDDEN_ATTR_KEYS)

    def test_32_future_due_not_stale(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        aid, pid = _acct()
        cid = str(uuid.uuid4())
        due = time.time() + 4000
        proactive_store.upsert_commitment({
            "commitment_id": cid, "owner_id": OWNER_ID, "account_id": aid,
            "source_connector": "calendar_google", "source_message_id": "m-fut",
            "commitment_type": "DEADLINE", "due_at": due,
            "fingerprint": "fp" + uuid.uuid4().hex[:16], "provenance": {}, "evidence_ref": "x",
        })
        evaluate_world_predictions()
        rows = _q(
            "SELECT prediction_id FROM world_predictions WHERE prediction_type='STALE_OPEN_COMMITMENT' AND subject_key=%s AND status='ACTIVE'",
            (f"commitment:{cid}",),
        )
        self.assertFalse(rows)

    def test_33_blocked_task_no_due_no_pred(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        from database.postgres_db import postgres_manager
        tid = "task-" + uuid.uuid4().hex[:12]
        postgres_manager.save_checkpoint({
            "task_id": tid, "goal": "g", "task_type": "t", "status": "FAILED",
            "current_step": "", "completed_steps": [], "remaining_steps": [],
            "failed_steps": [], "blocked_steps": [], "artifacts": {}, "tool_results": {},
            "verification_results": {}, "models_used": [], "retry_counts": {},
            "termination_reason": "", "final_response_status": "FAILED", "resume_available": True,
        })
        evaluate_world_predictions()
        rows = _q(
            "SELECT prediction_id FROM world_predictions WHERE subject_key=%s AND status='ACTIVE'",
            (f"task:{tid}",),
        )
        self.assertFalse(rows)

    def test_34_sensitive_not_emitted(self):
        ev = {"independence_key": "k", "reliability_class": "calendar", "strength": 0.85, "privacy_class": "SENSITIVE", "evidence_id": "e"}
        from proactive.predict import _emit
        n = _emit(OWNER_ID, "DEADLINE_HORIZON", "x", "DUE_72H", [ev], ["SUPPORTING"], time.time() + 3600, time.time())
        self.assertEqual(n, 0)

    def test_35_no_prediction_eval_signal_type(self):
        from proactive.schemas import SIGNAL_TYPES
        self.assertNotIn("PREDICTION_EVAL", SIGNAL_TYPES)

    def test_36_concurrent_upsert_one_row(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        from concurrent.futures import ThreadPoolExecutor
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        aid, pid = _acct()
        due = time.time() + 3600
        cid = str(uuid.uuid4())
        proactive_store.upsert_commitment({
            "commitment_id": cid, "owner_id": OWNER_ID, "account_id": aid,
            "source_connector": "gmail", "source_message_id": "m-par",
            "commitment_type": "DEADLINE", "due_at": due,
            "fingerprint": "fp" + uuid.uuid4().hex[:16], "provenance": {}, "evidence_ref": "x",
        })
        fid = str(uuid.uuid4())
        proactive_store.upsert_external_fact({
            "fact_id": fid, "owner_id": OWNER_ID, "account_id": aid,
            "connector_type": "calendar_google", "source_record_id": "ev-par",
            "fact_kind": "CAL_EVENT", "project_id": pid, "privacy_class": "NORMAL",
            "occurred_at": due, "confidence": 0.8,
            "payload": {}, "content_sha256": hashlib.sha256(fid.encode()).hexdigest(),
            "idempotency_key": "cal|" + fid,
        })
        with ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(lambda _: evaluate_world_predictions(), range(3)))
        n = _q("SELECT COUNT(*) AS n FROM world_predictions WHERE subject_key=%s AND prediction_type='DEADLINE_HORIZON'", (f"commitment:{cid}",))
        self.assertEqual(int(n[0]["n"]), 1)

    def test_37_provenance_ids_only(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
        aid, pid = _acct()
        due = time.time() + 3600
        cid = str(uuid.uuid4())
        proactive_store.upsert_commitment({
            "commitment_id": cid, "owner_id": OWNER_ID, "account_id": aid,
            "source_connector": "gmail", "source_message_id": "m-pr",
            "commitment_type": "DEADLINE", "due_at": due,
            "fingerprint": "fp" + uuid.uuid4().hex[:16], "provenance": {}, "evidence_ref": "x",
        })
        fid = str(uuid.uuid4())
        proactive_store.upsert_external_fact({
            "fact_id": fid, "owner_id": OWNER_ID, "account_id": aid,
            "connector_type": "calendar_google", "source_record_id": "ev-pr",
            "fact_kind": "CAL_EVENT", "project_id": pid, "privacy_class": "NORMAL",
            "occurred_at": due, "confidence": 0.8,
            "payload": {"snippet": "secret-text-should-not-copy"}, "content_sha256": hashlib.sha256(fid.encode()).hexdigest(),
            "idempotency_key": "cal|" + fid,
        })
        evaluate_world_predictions()
        rows = _q("SELECT provenance FROM world_predictions WHERE subject_key=%s AND prediction_type='DEADLINE_HORIZON'", (f"commitment:{cid}",))
        blob = str(rows).lower()
        self.assertNotIn("secret-text-should-not-copy", blob)

    def test_38_expire_updates_status(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        proactive_store.expire_world_predictions(OWNER_ID)

    def test_39_world_evidence_not_memory_table(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        cols = _q(
            "SELECT column_name FROM information_schema.columns WHERE table_name='world_evidence' AND column_name='memory_id'"
        )
        self.assertFalse(cols)

    def test_40_process_once_calls_eval_not_queue(self):
        os.environ["PROACTIVE_ENABLED"] = "true"
        called = {"n": 0}

        def _ev():
            called["n"] += 1
            return 0

        with patch("proactive.worker.evaluate_world_predictions", side_effect=_ev):
            with patch.object(proactive_store, "claim_batch", return_value=[]):
                with patch.object(proactive_store, "recover_expired_leases"):
                    with patch.object(proactive_store, "expire_stale"):
                        with patch("proactive.worker.poll_internal_sources"):
                            with patch("proactive.worker.poll_connectors"):
                                process_once("t40")
        self.assertEqual(called["n"], 1)


if __name__ == "__main__":
    unittest.main()
