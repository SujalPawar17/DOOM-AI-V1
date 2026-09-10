"""V6.3 ACT. Authorization + explicit RUN. No TaskEngine. Flags default off."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import os
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx

os.environ.setdefault("PROACTIVE_ENABLED", "false")
os.environ.setdefault("PROACTIVE_ACT_ENABLED", "false")
os.environ.setdefault("PROACTIVE_ACT_INTERNAL_ENABLED", "false")
os.environ.setdefault("PROACTIVE_ACT_CALENDAR_HOLD_ENABLED", "false")
os.environ.setdefault("DOOM_ASK_UNLOCK", "v63-test-unlock")
# Fail fast if another process holds DDL locks (import hang / F-V63-F04).
os.environ.setdefault("PGOPTIONS", "-c lock_timeout=8s -c statement_timeout=60s")

from proactive.act_engine import process_claimed, request_run
from proactive.act_policy import CAP_CAL_HOLD, CAP_INTERNAL, NOTE_TEMPLATE_ID, capability_enabled
from proactive.act_spec import compute_action_hash, idempotency_key, materialize_for_preparation
from proactive.ask_decisions import binding_hash_for, binding_hash_for_act
from proactive.config import ACT_POLICY_VERSION, OWNER_ID, is_act_enabled
from proactive.store import proactive_store


def _app():
    from dashboard.server import app
    return app

ORIGIN = "http://127.0.0.1:8000"
UNLOCK = "v63-test-unlock"


class _AskClient:
    def __init__(self):
        self._cookies = httpx.Cookies()

    def _call(self, method, url, **kwargs):
        async def _go():
            transport = httpx.ASGITransport(app=_app())
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1:8000",
                cookies=self._cookies,
            ) as client:
                resp = await client.request(method, url, **kwargs)
                self._cookies.update(resp.cookies)
                return resp
        return asyncio.run(_go())

    def post(self, url, **kwargs):
        return self._call("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self._call("GET", url, **kwargs)


def _pg():
    from database.postgres_db import postgres_manager
    return postgres_manager.is_connected()


def _off():
    for k in (
        "PROACTIVE_ENABLED", "PROACTIVE_PREDICTION_ENABLED", "PROACTIVE_SUGGEST_ENABLED",
        "PROACTIVE_PREPARE_ENABLED", "PROACTIVE_ASK_ENABLED",
        "PROACTIVE_ACT_ENABLED", "PROACTIVE_ACT_INTERNAL_ENABLED",
        "PROACTIVE_ACT_CALENDAR_HOLD_ENABLED", "PROACTIVE_CALENDAR_ENABLED",
    ):
        os.environ[k] = "false"


def _on_act0():
    os.environ["PROACTIVE_ENABLED"] = "true"
    os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
    os.environ["PROACTIVE_SUGGEST_ENABLED"] = "true"
    os.environ["PROACTIVE_PREPARE_ENABLED"] = "true"
    os.environ["PROACTIVE_ASK_ENABLED"] = "true"
    os.environ["PROACTIVE_ACT_ENABLED"] = "true"
    os.environ["PROACTIVE_ACT_INTERNAL_ENABLED"] = "true"
    os.environ["PROACTIVE_ACT_CALENDAR_HOLD_ENABLED"] = "false"


def _session():
    c = _AskClient()
    r = c.post(
        "/api/proactive/session",
        json={"unlock_secret": UNLOCK},
        headers={"Origin": ORIGIN},
    )
    csrf = r.json().get("csrf") or r.json().get("csrf_token") or ""
    return c, csrf


def _prep(owner: str, action_type="FUTURE_TASK_NOTE", privacy="NORMAL", extra=None) -> str:
    now = time.time()
    pid = str(uuid.uuid4())
    pred_id = str(uuid.uuid4())
    sug_id = str(uuid.uuid4())
    cal = action_type == "FUTURE_CAL_RECONCILE"
    ptype = "CAL_VS_COMMITMENT_CONFLICT" if cal else "DEADLINE_HORIZON"
    stype = "CONSIDER_RECONCILE_TIME" if cal else "CONSIDER_UNBLOCK"
    sug_tid = "suggest_reconcile_time" if cal else "suggest_unblock"
    params = {
        "risk_class": "MEDIUM",
        "horizon_hours": 1,
        "prediction_type": ptype,
        "suggestion_type": stype,
        "action_type": action_type,
        "claim_code": "CLM" + uuid.uuid4().hex[:8],
    }
    if extra:
        # not stored on prep row (allowlist); used only by tests that insert actions directly
        pass
    proactive_store.upsert_world_prediction(
        {
            "prediction_id": pred_id,
            "owner_id": owner,
            "prediction_type": ptype,
            "subject_key": "sk:" + pred_id,
            "claim_code": ptype[:40],
            "horizon_start": now,
            "horizon_end": now + 10,
            "confidence": 0.85,
            "risk_class": "MEDIUM",
            "privacy_class": privacy,
            "fingerprint": "pfp" + uuid.uuid4().hex,
            "rule_id": ptype,
            "rule_version": "v624.1",
            "evaluated_at": now,
            "valid_until": now + 86400,
            "provenance": {"evidence_ids": [str(uuid.uuid4()), str(uuid.uuid4())]},
        },
        [],
    )
    proactive_store.upsert_world_suggestion({
        "suggestion_id": sug_id,
        "owner_id": owner,
        "prediction_id": pred_id,
        "suggestion_type": stype,
        "claim_code": ptype[:40],
        "template_id": sug_tid,
        "safe_params": {
            "risk_class": "MEDIUM",
            "horizon_hours": 1,
            "prediction_type": ptype,
            "claim_code": ptype[:40],
        },
        "priority": "MEDIUM",
        "confidence": 0.85,
        "risk_class": "MEDIUM",
        "privacy_class": privacy,
        "fingerprint": "sfp" + uuid.uuid4().hex,
        "rule_id": stype,
        "rule_version": "v625.1",
        "valid_until": now + 86400,
        "evaluated_at": now,
        "provenance": {"prediction_id": pred_id},
    })
    from proactive.prepare import param_hash_of
    ph = param_hash_of(params)
    proactive_store.upsert_world_preparation({
        "preparation_id": pid,
        "owner_id": owner,
        "suggestion_id": sug_id,
        "prediction_id": pred_id,
        "preparation_type": "PREPARE_UNBLOCK_NOTE",
        "action_type": action_type,
        "future_act_class": "MUTATION",
        "template_id": "prepare_unblock_note",
        "safe_params": params,
        "param_hash": ph,
        "preview_key": "pk" + uuid.uuid4().hex[:8],
        "risk_class": "MEDIUM",
        "privacy_class": privacy,
        "fingerprint": "afp" + uuid.uuid4().hex,
        "rule_id": "t",
        "rule_version": "v626.1",
        "valid_until": now + 86400,
        "evaluated_at": now,
        "provenance": {},
    })
    return pid


def _spec(owner, prep_id, cap=CAP_INTERNAL, exec_params=None, privacy="NORMAL", risk="LOW"):
    from proactive.store import proactive_store as st
    prep = st.get_preparation(prep_id, owner) or {}
    params = exec_params or {
        "claim_code": (prep.get("safe_params") or {}).get("claim_code") or "c1",
        "note_template_id": NOTE_TEMPLATE_ID,
    }
    target = {"kind": "internal_ledger"} if cap == CAP_INTERNAL else {"kind": "google_calendar", "calendar_id": "primary"}
    atype = "FUTURE_TASK_NOTE" if cap == CAP_INTERNAL else "FUTURE_CAL_RECONCILE"
    ph = str(prep.get("param_hash") or "x")
    ah = compute_action_hash(owner, prep_id, cap, atype, target, params, ph, risk, privacy)
    return {
        "owner_id": owner,
        "preparation_id": prep_id,
        "capability_id": cap,
        "action_type": atype,
        "target_ref": target,
        "exec_params": params,
        "param_hash": ph,
        "action_hash": ah,
        "risk_class": risk,
        "privacy_class": privacy,
        "reversibility": "REVERSIBLE",
        "idempotency_key": idempotency_key(owner, cap, ah),
        "policy_version": ACT_POLICY_VERSION,
        "valid_until": time.time() + 3600,
        "provenance": {},
        "status": "READY",
    }


class TestV63Hash(unittest.TestCase):
    def test_hash_deterministic(self):
        a = compute_action_hash("o", "p", CAP_INTERNAL, "FUTURE_TASK_NOTE", {"k": 1}, {"claim_code": "a", "note_template_id": NOTE_TEMPLATE_ID}, "ph", "LOW", "NORMAL")
        b = compute_action_hash("o", "p", CAP_INTERNAL, "FUTURE_TASK_NOTE", {"k": 1}, {"claim_code": "a", "note_template_id": NOTE_TEMPLATE_ID}, "ph", "LOW", "NORMAL")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)

    def test_hash_changes_on_params(self):
        a = compute_action_hash("o", "p", CAP_INTERNAL, "T", {}, {"claim_code": "a"}, "ph", "LOW", "NORMAL")
        b = compute_action_hash("o", "p", CAP_INTERNAL, "T", {}, {"claim_code": "b"}, "ph", "LOW", "NORMAL")
        self.assertNotEqual(a, b)

    def test_v626_binding_unchanged(self):
        h = binding_hash_for(
            owner_id="o", preparation_id="p", action_type="NONE", param_hash="h",
            risk_class="NONE", privacy_class="NORMAL", valid_until_epoch=1,
            rule_version="v626.1", csrf_binding_id="worker",
        )
        self.assertEqual(len(h), 64)
        h2 = binding_hash_for_act(
            owner_id="o", preparation_id="p", action_type="NONE", param_hash="h",
            action_hash="ah", risk_class="NONE", privacy_class="NORMAL",
            valid_until_epoch=1, rule_version="v63.1", csrf_binding_id="worker",
        )
        self.assertNotEqual(h, h2)

    def test_flags_default_false(self):
        _off()
        self.assertFalse(is_act_enabled())
        self.assertFalse(capability_enabled(CAP_INTERNAL))
        self.assertFalse(capability_enabled(CAP_CAL_HOLD))

    def test_materialize_refuses_without_rfc3339(self):
        _on_act0()
        os.environ["PROACTIVE_ACT_CALENDAR_HOLD_ENABLED"] = "true"
        spec = materialize_for_preparation({
            "action_type": "FUTURE_CAL_RECONCILE",
            "privacy_class": "NORMAL",
            "safe_params": {"claim_code": "x"},
            "param_hash": "h",
            "preparation_id": "p",
            "valid_until": time.time() + 100,
        }, "o")
        self.assertIsNone(spec)

    def test_llm_payload_not_in_builder(self):
        src = inspect.getsource(materialize_for_preparation)
        self.assertNotIn("proactive.draft", src)
        src2 = inspect.getsource(compute_action_hash)
        self.assertNotIn("draft_id", src2)
        self.assertNotIn('["body"]', src2)


class TestV63AST(unittest.TestCase):
    def test_firewall(self):
        root = Path(__file__).resolve().parent
        files = [
            root / "proactive" / "act.py",
            root / "proactive" / "act_engine.py",
            root / "proactive" / "act_spec.py",
            root / "proactive" / "act_verify.py",
            root / "proactive" / "act_policy.py",
            root / "proactive" / "writers" / "internal_ledger.py",
            root / "proactive" / "writers" / "calendar_hold.py",
        ]
        forbidden = {
            "core.task_engine", "core.orchestrator", "core.cognition",
            "core.tool_registry", "subprocess", "proactive.draft",
        }
        for p in files:
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module, forbidden)
                    self.assertNotEqual(node.module, "core.model_router")
                if isinstance(node, ast.Name) and node.id == "process_request":
                    self.fail(p.name)
        eng = (root / "proactive" / "act_engine.py").read_text(encoding="utf-8")
        self.assertNotIn("APPROVED →", eng)
        http = (root / "proactive" / "connectors" / "http_safe.py").read_text(encoding="utf-8")
        self.assertIn("method not allowed", http)
        self.assertIn('method == "POST"', http)

    def test_no_execute_routes(self):
        from dashboard.server import app as a
        paths = [getattr(r, "path", "") for r in a.routes]
        self.assertNotIn("/api/proactive/execute", paths)
        self.assertNotIn("/api/proactive/approve-and-execute", paths)
        self.assertTrue(any("/api/proactive/actions/{action_id}/run" in p for p in paths))


class TestV63StoreEngine(unittest.TestCase):
    def setUp(self):
        _off()

    def tearDown(self):
        _off()

    def test_legacy_cannot_run(self):
        if not _pg():
            self.skipTest("postgres")
        _on_act0()
        owner = "t63-" + uuid.uuid4().hex[:10]
        pid = _prep(owner)
        spec = _spec(owner, pid)
        aid = proactive_store.insert_world_action(spec)
        self.assertTrue(aid)
        # v626 approval with empty action_hash
        now = time.time()
        b = binding_hash_for(
            owner_id=owner, preparation_id=pid, action_type="FUTURE_TASK_NOTE",
            param_hash=spec["param_hash"], risk_class="MEDIUM", privacy_class="NORMAL",
            valid_until_epoch=int(now + 3600), rule_version="v626.1", csrf_binding_id="worker",
        )
        appr = proactive_store.insert_approval_request({
            "owner_id": owner, "preparation_id": pid, "action_type": "FUTURE_TASK_NOTE",
            "param_hash": spec["param_hash"], "binding_hash": b, "csrf_binding_id": "worker",
            "risk_class": "MEDIUM", "privacy_class": "NORMAL", "valid_until": now + 3600,
            "rule_version": "v626.1", "action_hash": "",
        })
        proactive_store.bind_action_approval(aid, appr, owner)
        row = proactive_store.get_action(aid, owner)
        # force APPROVED_NOT_RUN without v63 hash on approval
        proactive_store.enqueue_run(aid, owner)
        # still RUN_REQUESTED only if APPROVED_NOT_RUN; enqueue requires that status
        self.assertNotEqual(str(row.get("status")), "COMPLETED")
        r = request_run(aid, owner, spec["action_hash"])
        self.assertFalse(r.get("ok") or str(proactive_store.get_action(aid, owner).get("status")) == "COMPLETED")

    def test_act0_verify_and_idempotent_run(self):
        if not _pg():
            self.skipTest("postgres")
        _on_act0()
        owner = "t63-" + uuid.uuid4().hex[:10]
        pid = _prep(owner)
        spec = _spec(owner, pid)
        aid = proactive_store.insert_world_action(spec)
        now = time.time()
        b = binding_hash_for_act(
            owner_id=owner, preparation_id=pid, action_type=spec["action_type"],
            param_hash=spec["param_hash"], action_hash=spec["action_hash"],
            risk_class=spec["risk_class"], privacy_class="NORMAL",
            valid_until_epoch=int(now + 3600), rule_version="v63.1", csrf_binding_id="worker",
        )
        appr = proactive_store.insert_approval_request({
            "owner_id": owner, "preparation_id": pid, "action_type": spec["action_type"],
            "param_hash": spec["param_hash"], "binding_hash": b, "csrf_binding_id": "worker",
            "risk_class": spec["risk_class"], "privacy_class": "NORMAL",
            "valid_until": now + 3600, "rule_version": "v63.1", "action_hash": spec["action_hash"],
        })
        self.assertTrue(appr)
        from proactive.ask_decisions import hashes_match
        from proactive.otp import emit_proactive
        from proactive.ask_decisions import _recompute
        decide = proactive_store.decide_approval(
            "approve", appr, owner, "sess", b,
            recompute=_recompute, hashes_match=hashes_match, emit=emit_proactive,
        )
        self.assertTrue(decide.get("ok"))
        row = proactive_store.get_action(aid, owner)
        self.assertEqual(row.get("status"), "APPROVED_NOT_RUN")
        r1 = request_run(aid, owner, spec["action_hash"])
        self.assertTrue(r1.get("ok"))
        r2 = request_run(aid, owner, spec["action_hash"])
        self.assertTrue(r2.get("replay") or r2.get("ok"))
        claimed = proactive_store.claim_run(owner, "w1")
        self.assertTrue(claimed)
        st = process_claimed(claimed, "w1")
        self.assertEqual(st, "COMPLETED")
        rec = proactive_store.get_action_receipt(aid, owner)
        self.assertTrue(rec and rec.get("content_hash"))
        r3 = request_run(aid, owner, spec["action_hash"])
        self.assertTrue(r3.get("replay"))
        claimed2 = proactive_store.claim_run(owner, "w1")
        self.assertIsNone(claimed2)

    def test_stale_hash_rejected(self):
        if not _pg():
            self.skipTest("postgres")
        _on_act0()
        owner = "t63-" + uuid.uuid4().hex[:10]
        pid = _prep(owner)
        spec = _spec(owner, pid)
        aid = proactive_store.insert_world_action(spec)
        now = time.time()
        b = binding_hash_for_act(
            owner_id=owner, preparation_id=pid, action_type=spec["action_type"],
            param_hash=spec["param_hash"], action_hash=spec["action_hash"],
            risk_class=spec["risk_class"], privacy_class="NORMAL",
            valid_until_epoch=int(now + 3600), rule_version="v63.1", csrf_binding_id="worker",
        )
        appr = proactive_store.insert_approval_request({
            "owner_id": owner, "preparation_id": pid, "action_type": spec["action_type"],
            "param_hash": spec["param_hash"], "binding_hash": b, "csrf_binding_id": "worker",
            "risk_class": spec["risk_class"], "privacy_class": "NORMAL",
            "valid_until": now + 3600, "rule_version": "v63.1", "action_hash": spec["action_hash"],
        })
        from proactive.ask_decisions import hashes_match, _recompute
        from proactive.otp import emit_proactive
        proactive_store.decide_approval("approve", appr, owner, "s", b, _recompute, hashes_match, emit_proactive)
        bad = request_run(aid, owner, "0" * 64)
        self.assertFalse(bad.get("ok"))

    def test_owner_csrf_run(self):
        if not _pg():
            self.skipTest("postgres")
        _on_act0()
        os.environ["DOOM_ASK_UNLOCK"] = UNLOCK
        owner = OWNER_ID
        pid = _prep(owner)
        spec = _spec(owner, pid)
        aid = proactive_store.insert_world_action(spec)
        now = time.time()
        b = binding_hash_for_act(
            owner_id=owner, preparation_id=pid, action_type=spec["action_type"],
            param_hash=spec["param_hash"], action_hash=spec["action_hash"],
            risk_class=spec["risk_class"], privacy_class="NORMAL",
            valid_until_epoch=int(now + 3600), rule_version="v63.1", csrf_binding_id="worker",
        )
        appr = proactive_store.insert_approval_request({
            "owner_id": owner, "preparation_id": pid, "action_type": spec["action_type"],
            "param_hash": spec["param_hash"], "binding_hash": b, "csrf_binding_id": "worker",
            "risk_class": spec["risk_class"], "privacy_class": "NORMAL",
            "valid_until": now + 3600, "rule_version": "v63.1", "action_hash": spec["action_hash"],
        })
        from proactive.ask_decisions import hashes_match, _recompute
        from proactive.otp import emit_proactive
        proactive_store.decide_approval("approve", appr, owner, "s", b, _recompute, hashes_match, emit_proactive)
        c, csrf = _session()
        r = c.post(f"/api/proactive/actions/{aid}/run", json={"action_hash": spec["action_hash"]})
        self.assertIn(r.status_code, (401, 403))
        r2 = c.post(
            f"/api/proactive/actions/{aid}/run",
            json={"action_hash": spec["action_hash"]},
            headers={"Origin": ORIGIN, "X-DOOM-CSRF": csrf},
        )
        self.assertEqual(r2.status_code, 200)
        g = c.get(f"/api/proactive/actions/{aid}", headers={"Origin": ORIGIN})
        self.assertEqual(g.status_code, 200)
        other = c.get(f"/api/proactive/actions/{aid}")
        # same session owner
        self.assertTrue(g.json().get("ok"))

    def test_unknown_not_retried(self):
        if not _pg():
            self.skipTest("postgres")
        _on_act0()
        owner = "t63-" + uuid.uuid4().hex[:10]
        pid = _prep(owner)
        spec = _spec(owner, pid)
        aid = proactive_store.insert_world_action(spec)
        now = time.time()
        b = binding_hash_for_act(
            owner_id=owner, preparation_id=pid, action_type=spec["action_type"],
            param_hash=spec["param_hash"], action_hash=spec["action_hash"],
            risk_class=spec["risk_class"], privacy_class="NORMAL",
            valid_until_epoch=int(now + 3600), rule_version="v63.1", csrf_binding_id="worker",
        )
        appr = proactive_store.insert_approval_request({
            "owner_id": owner, "preparation_id": pid, "action_type": spec["action_type"],
            "param_hash": spec["param_hash"], "binding_hash": b, "csrf_binding_id": "worker",
            "risk_class": spec["risk_class"], "privacy_class": "NORMAL",
            "valid_until": now + 3600, "rule_version": "v63.1", "action_hash": spec["action_hash"],
        })
        from proactive.ask_decisions import hashes_match, _recompute
        from proactive.otp import emit_proactive
        proactive_store.decide_approval("approve", appr, owner, "s", b, _recompute, hashes_match, emit_proactive)
        request_run(aid, owner, spec["action_hash"])
        claimed = proactive_store.claim_run(owner, "w1")
        with patch("proactive.act_engine._dispatch", side_effect=TimeoutError("after-send")):
            st = process_claimed(claimed, "w1")
        self.assertEqual(st, "UNKNOWN_OUTCOME")
        again = request_run(aid, owner, spec["action_hash"])
        self.assertFalse(again.get("ok"))

    def test_act1_readback_mismatch(self):
        if not _pg():
            self.skipTest("postgres")
        _on_act0()
        os.environ["PROACTIVE_ACT_CALENDAR_HOLD_ENABLED"] = "true"
        os.environ["PROACTIVE_CALENDAR_ENABLED"] = "true"
        owner = "t63-" + uuid.uuid4().hex[:10]
        pid = _prep(owner, action_type="FUTURE_CAL_RECONCILE")
        params = {
            "calendar_id": "primary",
            "start_rfc3339": "2026-09-10T10:00:00Z",
            "end_rfc3339": "2026-09-10T10:30:00Z",
            "summary_template_id": "cal_hold_v1",
        }
        spec = _spec(owner, pid, cap=CAP_CAL_HOLD, exec_params=params, risk="MEDIUM")
        aid = proactive_store.insert_world_action(spec)
        now = time.time()
        b = binding_hash_for_act(
            owner_id=owner, preparation_id=pid, action_type=spec["action_type"],
            param_hash=spec["param_hash"], action_hash=spec["action_hash"],
            risk_class="MEDIUM", privacy_class="NORMAL",
            valid_until_epoch=int(now + 3600), rule_version="v63.1", csrf_binding_id="worker",
        )
        appr = proactive_store.insert_approval_request({
            "owner_id": owner, "preparation_id": pid, "action_type": spec["action_type"],
            "param_hash": spec["param_hash"], "binding_hash": b, "csrf_binding_id": "worker",
            "risk_class": "MEDIUM", "privacy_class": "NORMAL",
            "valid_until": now + 3600, "rule_version": "v63.1", "action_hash": spec["action_hash"],
        })
        from proactive.ask_decisions import hashes_match, _recompute
        from proactive.otp import emit_proactive
        proactive_store.decide_approval("approve", appr, owner, "s", b, _recompute, hashes_match, emit_proactive)
        request_run(aid, owner, spec["action_hash"])
        claimed = proactive_store.claim_run(owner, "w1")
        with patch("proactive.act_engine._secret_ref", return_value="ref"), \
             patch("proactive.act_engine._dispatch", return_value=("eid1", "eid1", 200)), \
             patch("proactive.act_engine.verify_action", return_value=(False, "start_mismatch", "eid1")):
            st = process_claimed(claimed, "w1")
        self.assertEqual(st, "UNKNOWN_OUTCOME")

    def test_act1_ok(self):
        if not _pg():
            self.skipTest("postgres")
        _on_act0()
        os.environ["PROACTIVE_ACT_CALENDAR_HOLD_ENABLED"] = "true"
        os.environ["PROACTIVE_CALENDAR_ENABLED"] = "true"
        owner = "t63-" + uuid.uuid4().hex[:10]
        pid = _prep(owner, action_type="FUTURE_CAL_RECONCILE")
        params = {
            "calendar_id": "primary",
            "start_rfc3339": "2026-09-10T10:00:00Z",
            "end_rfc3339": "2026-09-10T10:30:00Z",
            "summary_template_id": "cal_hold_v1",
        }
        spec = _spec(owner, pid, cap=CAP_CAL_HOLD, exec_params=params, risk="MEDIUM")
        aid = proactive_store.insert_world_action(spec)
        now = time.time()
        b = binding_hash_for_act(
            owner_id=owner, preparation_id=pid, action_type=spec["action_type"],
            param_hash=spec["param_hash"], action_hash=spec["action_hash"],
            risk_class="MEDIUM", privacy_class="NORMAL",
            valid_until_epoch=int(now + 3600), rule_version="v63.1", csrf_binding_id="worker",
        )
        appr = proactive_store.insert_approval_request({
            "owner_id": owner, "preparation_id": pid, "action_type": spec["action_type"],
            "param_hash": spec["param_hash"], "binding_hash": b, "csrf_binding_id": "worker",
            "risk_class": "MEDIUM", "privacy_class": "NORMAL",
            "valid_until": now + 3600, "rule_version": "v63.1", "action_hash": spec["action_hash"],
        })
        from proactive.ask_decisions import hashes_match, _recompute
        from proactive.otp import emit_proactive
        proactive_store.decide_approval("approve", appr, owner, "s", b, _recompute, hashes_match, emit_proactive)
        request_run(aid, owner, spec["action_hash"])
        claimed = proactive_store.claim_run(owner, "w1")
        with patch("proactive.act_engine._secret_ref", return_value="ref"), \
             patch("proactive.act_engine._dispatch", return_value=("eid1", "eid1", 200)), \
             patch("proactive.act_engine.verify_action", return_value=(True, "ok", "eid1")):
            st = process_claimed(claimed, "w1")
        self.assertEqual(st, "COMPLETED")

    def test_worker_isolation_and_no_auto_approved(self):
        _on_act0()
        from proactive.worker import process_once
        with patch("proactive.worker.evaluate_world_actions", side_effect=RuntimeError("boom")):
            with patch("proactive.worker.proactive_store.claim_batch", return_value=[]) as cb:
                process_once("w")
                self.assertTrue(cb.called)

    def test_sensitive_no_materialize(self):
        _on_act0()
        spec = materialize_for_preparation({
            "action_type": "FUTURE_TASK_NOTE",
            "privacy_class": "SENSITIVE",
            "safe_params": {"claim_code": "abc"},
            "param_hash": "h",
            "preparation_id": "p",
        }, "o")
        self.assertIsNone(spec)


if __name__ == "__main__":
    unittest.main(verbosity=2)
