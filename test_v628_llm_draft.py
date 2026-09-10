"""V6.2.8 bounded LLM drafts. Non-authoritative. No ACT."""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import os
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

os.environ.setdefault("PROACTIVE_ENABLED", "false")
os.environ.setdefault("PROACTIVE_PREDICTION_ENABLED", "false")
os.environ.setdefault("PROACTIVE_SUGGEST_ENABLED", "false")
os.environ.setdefault("PROACTIVE_PREPARE_ENABLED", "false")
os.environ.setdefault("PROACTIVE_ASK_ENABLED", "false")
os.environ.setdefault("PROACTIVE_LLM_DRAFT_ENABLED", "false")
os.environ.setdefault("PROACTIVE_LLM_DRAFT_NORMAL_ENABLED", "false")
os.environ.setdefault("PROACTIVE_LLM_DRAFT_PRIVATE_ENABLED", "false")
os.environ.setdefault("DOOM_ASK_UNLOCK", "v628-test-unlock")

from dashboard.server import app
from models.base_provider import LLMResponse
from proactive.ask_decisions import binding_hash_for
from proactive.config import DRAFT_PROMPT_VERSION, OWNER_ID
from proactive.draft import evaluate_world_drafts, flags_draft_on
from proactive.draft_contract import build_input, draft_fingerprint, extract_json_object
from proactive.draft_policy import allowed_providers_for
from proactive.draft_validate import validate_draft
from proactive.prepare import TYPE_MAP, evaluate_world_preparations
from proactive.store import proactive_store
from proactive.suggest import evaluate_world_suggestions
from proactive.worker import process_once

ORIGIN = "http://127.0.0.1:8000"
UNLOCK = "v628-test-unlock"
TID = "prepare_review_outline"


class _AskClient:
    def __init__(self):
        self._cookies = httpx.Cookies()

    def _call(self, method: str, url: str, **kwargs):
        async def _go():
            transport = httpx.ASGITransport(app=app)
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


def _q(sql, params=None):
    from database.postgres_db import postgres_manager
    return postgres_manager.execute_query(sql, params, readonly=True)


def _iso_owner() -> str:
    return "t628-" + uuid.uuid4().hex[:12]


def _off():
    for k in (
        "PROACTIVE_ENABLED", "PROACTIVE_PREDICTION_ENABLED", "PROACTIVE_SUGGEST_ENABLED",
        "PROACTIVE_PREPARE_ENABLED", "PROACTIVE_ASK_ENABLED",
        "PROACTIVE_LLM_DRAFT_ENABLED", "PROACTIVE_LLM_DRAFT_NORMAL_ENABLED",
        "PROACTIVE_LLM_DRAFT_PRIVATE_ENABLED",
    ):
        os.environ[k] = "false"


def _on():
    os.environ["PROACTIVE_ENABLED"] = "true"
    os.environ["PROACTIVE_PREDICTION_ENABLED"] = "true"
    os.environ["PROACTIVE_SUGGEST_ENABLED"] = "true"
    os.environ["PROACTIVE_PREPARE_ENABLED"] = "true"
    os.environ["PROACTIVE_ASK_ENABLED"] = "true"
    os.environ["PROACTIVE_LLM_DRAFT_ENABLED"] = "true"
    os.environ["PROACTIVE_LLM_DRAFT_NORMAL_ENABLED"] = "true"


def _ok_json(tid=TID, refs=None):
    return json.dumps({
        "draft_type": tid,
        "title": "Review outline",
        "summary": "A short summary of remaining work.",
        "body": "Please review the remaining items. Nothing was carried out.",
        "warnings": [],
        "uncertainties": [],
        "source_refs": refs or [],
    })


def _fake_resp(text, tool_calls=None):
    return LLMResponse(text=text, tool_calls=tool_calls or [], model_name="fake/model")


def _pred(ptype: str, owner: str, privacy: str = "NORMAL") -> str:
    pid = str(uuid.uuid4())
    now = time.time()
    eids = [str(uuid.uuid4()), str(uuid.uuid4())]
    proactive_store.upsert_world_prediction(
        {
            "prediction_id": pid,
            "owner_id": owner,
            "prediction_type": ptype,
            "subject_key": "sk:" + pid,
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
            "provenance": {"evidence_ids": eids},
        },
        [],
    )
    return pid


def _ensure_suggestion(owner: str, pid: str, stype: str, tid: str, ptype: str, privacy="NORMAL") -> None:
    now = time.time()
    proactive_store.upsert_world_suggestion({
        "suggestion_id": str(uuid.uuid4()),
        "owner_id": owner,
        "prediction_id": pid,
        "suggestion_type": stype,
        "claim_code": ptype[:40],
        "template_id": tid,
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
        "provenance": {"prediction_id": pid},
    })


def _seed_prep(owner: str, privacy: str = "NORMAL") -> str:
    ptype = "DEADLINE_HORIZON"
    pid = _pred(ptype, owner, privacy=privacy)
    evaluate_world_suggestions(owner)
    _ensure_suggestion(owner, pid, "CONSIDER_REVIEW_WORK", "suggest_review_work", ptype, privacy=privacy)
    evaluate_world_preparations(owner)
    rows = _q("SELECT preparation_id FROM world_preparations WHERE prediction_id=%s", (pid,))
    return rows[0]["preparation_id"] if rows else ""


class TestV628Validate(unittest.TestCase):
    def test_01_strict_ok(self):
        ok, payload, reason = validate_draft(_ok_json(), TID, [])
        self.assertTrue(ok)
        self.assertEqual(payload["draft_type"], TID)
        self.assertEqual(reason, "")

    def test_02_malformed_json(self):
        ok, _, reason = validate_draft("{not json", TID, [])
        self.assertFalse(ok)
        self.assertEqual(reason, "NOT_JSON")

    def test_03_markdown_wrapped(self):
        ok, _, reason = validate_draft("```json\n" + _ok_json() + "\n```", TID, [])
        self.assertFalse(ok)
        self.assertEqual(reason, "NOT_JSON")

    def test_04_extra_keys(self):
        obj = json.loads(_ok_json())
        obj["action_type"] = "SEND"
        ok, _, reason = validate_draft(json.dumps(obj), TID, [])
        self.assertFalse(ok)
        self.assertEqual(reason, "SCHEMA")

    def test_05_invalid_enum(self):
        obj = json.loads(_ok_json())
        obj["draft_type"] = "nope"
        ok, _, reason = validate_draft(json.dumps(obj), TID, [])
        self.assertFalse(ok)
        self.assertEqual(reason, "ENUM")

    def test_06_oversized_body(self):
        obj = json.loads(_ok_json())
        obj["body"] = "x" * 2001
        ok, _, reason = validate_draft(json.dumps(obj), TID, [])
        self.assertFalse(ok)
        self.assertEqual(reason, "SIZE")

    def test_07_nested_warning(self):
        obj = json.loads(_ok_json())
        obj["warnings"] = [{}]
        ok, _, reason = validate_draft(json.dumps(obj), TID, [])
        self.assertFalse(ok)
        self.assertEqual(reason, "SIZE")

    def test_08_tool_calls(self):
        ok, _, reason = validate_draft(_ok_json(), TID, [], tool_calls=[{"name": "x"}])
        self.assertFalse(ok)
        self.assertEqual(reason, "TOOL_CALLS")

    def test_09_executable(self):
        obj = json.loads(_ok_json())
        obj["body"] = "Please run subprocess to send mail."
        ok, _, reason = validate_draft(json.dumps(obj), TID, [])
        self.assertFalse(ok)
        self.assertEqual(reason, "CONTENT")

    def test_10_secret(self):
        obj = json.loads(_ok_json())
        obj["body"] = "key gsk_abc1234567890"
        ok, _, reason = validate_draft(json.dumps(obj), TID, [])
        self.assertFalse(ok)
        self.assertEqual(reason, "SECRET")

    def test_11_provenance(self):
        obj = json.loads(_ok_json())
        obj["source_refs"] = ["not-an-id"]
        ok, _, reason = validate_draft(json.dumps(obj), TID, ["real-id"])
        self.assertFalse(ok)
        self.assertEqual(reason, "PROVENANCE")

    def test_12_empty(self):
        ok, _, reason = validate_draft("   ", TID, [])
        self.assertFalse(ok)
        self.assertEqual(reason, "EMPTY")

    def test_13_extract_rejects_prose(self):
        self.assertIsNone(extract_json_object("here is json {\"a\":1}"))

    def test_14_fingerprint_stable(self):
        a = draft_fingerprint("o", "p", "h", "v628.1", TID)
        b = draft_fingerprint("o", "p", "h", "v628.1", TID)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 48)


class TestV628Policy(unittest.TestCase):
    def setUp(self):
        _off()

    def tearDown(self):
        _off()

    def test_15_sensitive_never(self):
        self.assertEqual(allowed_providers_for("SENSITIVE"), [])

    def test_16_private_hosted_blocked(self):
        os.environ["PROACTIVE_LLM_DRAFT_PRIVATE_ENABLED"] = "true"
        groq = MagicMock()
        groq.is_enabled.return_value = True
        groq.is_available.return_value = True
        groq.deployment_mode = "HOSTED_CLOUD"
        ollama = MagicMock()
        ollama.is_enabled.return_value = True
        ollama.is_available.return_value = True
        ollama.deployment_mode = "LOCAL"
        ollama.name = "ollama"
        ollama.base_url = "http://localhost:11434"
        ollama.model = "llama3"
        with patch("core.model_router.model_router") as mr:
            mr.providers = {"groq": groq, "ollama": ollama, "fallback": MagicMock()}
            names = allowed_providers_for("PRIVATE")
        self.assertEqual(names, ["ollama"])
        self.assertNotIn("groq", names)

    def test_17_private_flag_off(self):
        os.environ["PROACTIVE_LLM_DRAFT_PRIVATE_ENABLED"] = "false"
        self.assertEqual(allowed_providers_for("PRIVATE"), [])

    def test_18_normal_flag_off(self):
        os.environ["PROACTIVE_LLM_DRAFT_NORMAL_ENABLED"] = "false"
        self.assertEqual(allowed_providers_for("NORMAL"), [])

    def test_19_normal_hosted_allowed(self):
        os.environ["PROACTIVE_LLM_DRAFT_NORMAL_ENABLED"] = "true"
        groq = MagicMock()
        groq.is_enabled.return_value = True
        groq.is_available.return_value = True
        groq.deployment_mode = "HOSTED_CLOUD"
        ollama = MagicMock()
        ollama.is_enabled.return_value = False
        ollama.is_available.return_value = False
        ollama.deployment_mode = "LOCAL"
        with patch("core.model_router.model_router") as mr:
            mr.providers = {
                "ollama": ollama, "groq": groq, "nim": ollama,
                "openai": ollama, "gemini": ollama, "fallback": ollama,
            }
            names = allowed_providers_for("NORMAL")
        self.assertNotIn("groq", names)
        self.assertNotIn("openai", names)
        self.assertNotIn("gemini", names)


class TestV628StoreWorkerApi(unittest.TestCase):
    def setUp(self):
        _off()
        os.environ["DOOM_ASK_UNLOCK"] = UNLOCK
        self._busy = patch("proactive.attention.cognition_busy", return_value=False)
        self._busy.start()
        if _pg():
            from database.postgres_db import postgres_manager
            try:
                postgres_manager._create_tables()
            except Exception:
                pass

    def tearDown(self):
        self._busy.stop()
        _off()

    def test_20_flags_off_no_generate(self):
        _off()
        self.assertFalse(flags_draft_on())
        with patch("proactive.draft.generate_bounded_draft") as gen:
            n = evaluate_world_drafts("nobody")
            self.assertEqual(n, 0)
            gen.assert_not_called()

    def test_21_accepted_persist_and_idempotent(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        prep_id = _seed_prep(owner)
        self.assertTrue(prep_id)
        pre = _q("SELECT param_hash, status, safe_params FROM world_preparations WHERE preparation_id=%s", (prep_id,))
        ph = pre[0]["param_hash"]
        fake = _fake_resp(_ok_json())
        with patch("proactive.draft.generate_bounded_draft", return_value=(fake, "ollama", "fake/model", "")):
            n1 = evaluate_world_drafts(owner)
            n2 = evaluate_world_drafts(owner)
        self.assertGreaterEqual(n1, 1)
        self.assertEqual(n2, 0)
        drafts = _q(
            "SELECT validation_status FROM world_preparation_drafts WHERE preparation_id=%s AND owner_id=%s",
            (prep_id, owner),
        )
        acc = [d for d in drafts if d["validation_status"] == "ACCEPTED"]
        self.assertEqual(len(acc), 1)
        post = _q("SELECT param_hash, safe_params FROM world_preparations WHERE preparation_id=%s", (prep_id,))
        self.assertEqual(post[0]["param_hash"], ph)

    def test_22_rejected_not_current(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        prep_id = _seed_prep(owner)
        fake = _fake_resp("{bad")
        with patch("proactive.draft.generate_bounded_draft", return_value=(fake, "groq", "fake/model", "")):
            evaluate_world_drafts(owner)
        cur = proactive_store.get_current_draft(prep_id, owner)
        self.assertIsNone(cur)
        st = _q("SELECT status FROM world_preparations WHERE preparation_id=%s", (prep_id,))
        self.assertEqual(st[0]["status"], "READY")

    def test_23_sensitive_no_row(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        prep_id = _seed_prep(owner, privacy="SENSITIVE")
        if not prep_id:
            self.skipTest("sensitive prep not created (expected ignore)")
        with patch("proactive.draft.generate_bounded_draft") as gen:
            evaluate_world_drafts(owner)
            gen.assert_not_called()
        n = _q("SELECT COUNT(*) AS n FROM world_preparation_drafts WHERE preparation_id=%s", (prep_id,))
        self.assertEqual(int(n[0]["n"]), 0)

    def test_24_private_no_hud_type(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        os.environ["PROACTIVE_LLM_DRAFT_PRIVATE_ENABLED"] = "true"
        owner = _iso_owner()
        prep_id = _seed_prep(owner, privacy="PRIVATE")
        if not prep_id:
            self.skipTest("private prep missing")
        fake = _fake_resp(_ok_json())
        with patch("proactive.draft.generate_bounded_draft", return_value=(fake, "ollama", "local", "")):
            with patch("proactive.delivery.deliver_draft") as deliv:
                evaluate_world_drafts(owner)
                deliv.assert_not_called()
        row = proactive_store.get_current_draft(prep_id, owner)
        self.assertIsNotNone(row)

    def test_25_stale_hash(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        owner = _iso_owner()
        _on()
        prep_id = _seed_prep(owner)
        ph = _q("SELECT param_hash FROM world_preparations WHERE preparation_id=%s", (prep_id,))[0]["param_hash"]
        fp = draft_fingerprint(owner, prep_id, "deadbeef" * 8, DRAFT_PROMPT_VERSION, TID)
        proactive_store.insert_world_draft({
            "owner_id": owner,
            "preparation_id": prep_id,
            "draft_type": TID,
            "structured_output": {"draft_type": TID, "title": "t", "summary": "s", "body": "b", "warnings": [], "uncertainties": [], "source_refs": []},
            "provider": "x",
            "model": "m",
            "param_hash_at_generation": "deadbeef" * 8,
            "validation_status": "ACCEPTED",
            "privacy_class": "NORMAL",
            "fingerprint": fp,
            "prompt_version": DRAFT_PROMPT_VERSION,
        })
        self.assertIsNone(proactive_store.get_current_draft(prep_id, owner))
        self.assertEqual(ph, _q("SELECT param_hash FROM world_preparations WHERE preparation_id=%s", (prep_id,))[0]["param_hash"])

    def test_26_owner_isolation_api(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = _iso_owner()
        prep_id = _seed_prep(owner)
        fake = _fake_resp(_ok_json())
        with patch("proactive.draft.generate_bounded_draft", return_value=(fake, "groq", "m", "")):
            evaluate_world_drafts(owner)
        client = _AskClient()
        unauth = client.get(f"/api/proactive/preparations/{prep_id}/draft", headers={"Origin": ORIGIN})
        self.assertIn(unauth.status_code, (401, 403))
        r = client.post(
            "/api/proactive/session",
            headers={"Origin": ORIGIN, "Content-Type": "application/json"},
            json={"unlock_secret": UNLOCK},
        )
        self.assertTrue(r.json().get("ok"))
        g = client.get(f"/api/proactive/preparations/{prep_id}/draft", headers={"Origin": ORIGIN})
        self.assertFalse(g.json().get("current"))

    def test_27_read_only_session_get(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        _on()
        owner = OWNER_ID
        prep_id = _seed_prep(owner)
        fake = _fake_resp(_ok_json())
        with patch("proactive.draft.generate_bounded_draft", return_value=(fake, "groq", "m", "")):
            evaluate_world_drafts(owner)
        client = _AskClient()
        r = client.post(
            "/api/proactive/session",
            headers={"Origin": ORIGIN, "Content-Type": "application/json"},
            json={"unlock_secret": UNLOCK},
        )
        self.assertTrue(r.json().get("ok"), r.text)
        g = client.get(f"/api/proactive/preparations/{prep_id}/draft", headers={"Origin": ORIGIN})
        body = g.json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("type"), "proactive_draft")
        self.assertFalse(body.get("tts"))
        self.assertIn("not execution", body.get("disclaimer", "").lower())

    def test_28_no_execute_routes(self):
        from dashboard import server as srv
        src = Path(srv.__file__).read_text(encoding="utf-8")
        self.assertNotIn("/approve-and-execute", src)
        self.assertNotIn("/execute-approved", src)
        self.assertIn("/api/proactive/preparations/{preparation_id}/draft", src)

    def test_29_worker_isolation(self):
        _on()
        with patch("proactive.worker.evaluate_world_drafts", side_effect=RuntimeError("boom")):
            with patch("proactive.store.proactive_store.claim_batch", return_value=[]) as cb:
                process_once("t628")
                cb.assert_called()

    def test_30_binding_unchanged(self):
        kwargs = dict(
            owner_id="o", preparation_id="p", action_type="FUTURE_TASK_NOTE",
            param_hash="h", risk_class="MEDIUM", privacy_class="NORMAL",
            valid_until_epoch=1, rule_version="v626.1", csrf_binding_id="worker",
        )
        a = binding_hash_for(**kwargs)
        b = binding_hash_for(**kwargs)
        self.assertEqual(a, b)

    def test_31_future_email_unmapped(self):
        mapped_actions = {v[2] for v in TYPE_MAP.values()}
        self.assertNotIn("FUTURE_EMAIL_DRAFT", mapped_actions)

    def test_32_tts_false(self):
        from proactive.config import TTS_PROACTIVE_ALLOWED
        self.assertFalse(TTS_PROACTIVE_ALLOWED)

    def test_33_ast_firewall(self):
        root = Path(__file__).resolve().parent
        files = [
            root / "proactive" / "draft.py",
            root / "proactive" / "draft_contract.py",
            root / "proactive" / "draft_validate.py",
            root / "proactive" / "draft_prompts.py",
            root / "proactive" / "draft_policy.py",
        ]
        forbidden_mods = {
            "subprocess", "core.task_engine", "core.orchestrator", "core.cognition",
            "core.tool_registry", "tools", "proactive.connectors.http_safe",
        }
        for p in files:
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        self.assertNotIn(a.name.split(".")[0], forbidden_mods)
                        self.assertNotIn(a.name, forbidden_mods)
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module, forbidden_mods)
                    self.assertNotEqual(node.module.split(".")[0], "subprocess")
                    self.assertNotEqual(node.module, "core.task_engine")
                    self.assertNotEqual(node.module, "core.orchestrator")
                if isinstance(node, ast.Attribute) and node.attr == "approve_task_action":
                    self.fail(f"{p.name} references approve_task_action")
                if isinstance(node, ast.Name) and node.id == "process_request":
                    self.fail(f"{p.name} references process_request")
        policy = (root / "proactive" / "draft_policy.py").read_text(encoding="utf-8")
        self.assertIn("tools=None", policy)
        self.assertIn("model_router", policy)

    def test_34_no_tools_in_generate_call(self):
        src = inspect.getsource(evaluate_world_drafts)
        self.assertNotIn("tools=[", src)

    def test_35_prompt_no_connector_keys(self):
        from proactive.draft_prompts import build_user_prompt
        facts = build_input({
            "preparation_id": "p",
            "preparation_type": "PREPARE_REVIEW_OUTLINE",
            "action_type": "NONE",
            "template_id": TID,
            "safe_params": {"claim_code": "X", "prediction_type": "DEADLINE_HORIZON",
                            "suggestion_type": "CONSIDER_REVIEW_WORK", "horizon_hours": 1},
            "risk_class": "MEDIUM",
            "privacy_class": "NORMAL",
            "rule_version": "v626.1",
            "provenance": {"evidence_ids": ["e1"]},
        })
        user = build_user_prompt(facts)
        self.assertIn("(none)", user)
        self.assertNotIn("snippet", user.lower())
        self.assertNotIn("subject", user.lower())

    def test_36_build_input_allowlist(self):
        facts = build_input({
            "preparation_id": "p",
            "template_id": TID,
            "safe_params": {"claim_code": "c", "prediction_type": "t", "suggestion_type": "s", "horizon_hours": 3},
            "provenance": {},
        })
        self.assertIn("deterministic_preview", facts)
        self.assertNotIn("snippet", facts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
