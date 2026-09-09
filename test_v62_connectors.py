"""
V6.2.2 — Vault + READ calendar/GitHub connectors.
No live network. No Gmail/SUGGEST/PREPARE/ASK/LLM/ACT.
Does not weaken test_v61_proactive_foundation.py.
"""
from __future__ import annotations

import ast
import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PROACTIVE_ENABLED", "false")
os.environ.setdefault("PROACTIVE_CALENDAR_ENABLED", "false")
os.environ.setdefault("PROACTIVE_GITHUB_ENABLED", "false")

from proactive.connectors.base import FencedRecord
from proactive.connectors.calendar_google import GoogleCalendarConnector
from proactive.connectors.github import GitHubConnector, _owner_repo_from_remote
from proactive.connectors.http_safe import SafeHttp, SafeHttpError, TOKEN_POST_URL
from proactive.connectors.registry import get_enabled_readers
from proactive.poller import persist_fenced_record, poll_connectors
from proactive.store import proactive_store
from proactive.vault import delete_secret, get_secret, put_secret


def _pg():
    from database.postgres_db import postgres_manager
    return postgres_manager.is_connected()


def _q(sql, params=None, readonly=True):
    from database.postgres_db import postgres_manager
    return postgres_manager.execute_query(sql, params, readonly=readonly)


class SeqTransport:
    def __init__(self, responses):
        self.calls = []
        self.responses = list(responses)

    def __call__(self, method, url, headers, data, timeout):
        safe_headers = {
            k: ("<redacted>" if k.lower() == "authorization" else v)
            for k, v in (headers or {}).items()
        }
        self.calls.append((method, url, safe_headers, data is not None))
        if not self.responses:
            raise AssertionError("unexpected HTTP call")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class TestV622Connectors(unittest.TestCase):
    def setUp(self):
        os.environ["PROACTIVE_ENABLED"] = "false"
        os.environ["PROACTIVE_CALENDAR_ENABLED"] = "false"
        os.environ["PROACTIVE_GITHUB_ENABLED"] = "false"
        self._vault_dir = tempfile.TemporaryDirectory()
        self.vault = os.path.join(self._vault_dir.name, "connector_vault.dpapi")
        os.environ["DOOM_CONNECTOR_VAULT_PATH"] = self.vault

    def tearDown(self):
        os.environ["PROACTIVE_ENABLED"] = "false"
        os.environ["PROACTIVE_CALENDAR_ENABLED"] = "false"
        os.environ["PROACTIVE_GITHUB_ENABLED"] = "false"
        self._vault_dir.cleanup()

    def test_vault_roundtrip_no_token_in_file_bytes(self):
        put_secret("ref1", {"type": "github_pat", "token": "ghp_test_not_for_production_xxx"})
        got = get_secret("ref1")
        self.assertEqual(got["type"], "github_pat")
        self.assertEqual(got["token"], "ghp_test_not_for_production_xxx")
        raw = Path(self.vault).read_bytes()
        self.assertNotIn(b"ghp_test_not_for_production_xxx", raw)
        self.assertTrue(delete_secret("ref1"))
        self.assertIsNone(get_secret("ref1"))

    def test_http_safe_rejects_write_and_ssrf(self):
        http = SafeHttp(transport=SeqTransport([(200, {}, b"{}")]))
        with self.assertRaises(SafeHttpError):
            http.request("POST", "https://api.github.com/repos/a/b/issues", data=b"x")
        with self.assertRaises(SafeHttpError):
            http.request("PUT", "https://api.github.com/user")
        with self.assertRaises(SafeHttpError):
            http.request("DELETE", "https://api.github.com/user")
        with self.assertRaises(SafeHttpError):
            http.request("PATCH", "https://api.github.com/user")
        with self.assertRaises(SafeHttpError):
            http.get("http://api.github.com/notifications")
        with self.assertRaises(SafeHttpError):
            http.get("https://evil.example/x")
        with self.assertRaises(SafeHttpError):
            http.get("https://127.0.0.1/secrets")

    def test_http_safe_allows_get_and_token_post_only(self):
        tr = SeqTransport([
            (200, {}, b'{"ok":true}'),
            (200, {}, b'{"access_token":"x","expires_in":3600}'),
        ])
        http = SafeHttp(transport=tr)
        st, _h, body = http.get("https://api.github.com/notifications")
        self.assertEqual(st, 200)
        self.assertIn(b"ok", body)
        st, _h, _b = http.post_token(
            TOKEN_POST_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=b"grant_type=refresh_token",
        )
        self.assertEqual(st, 200)
        self.assertEqual(tr.calls[0][0], "GET")
        self.assertEqual(tr.calls[1][0], "POST")

    def test_flags_off_zero_http_and_no_readers(self):
        tr = SeqTransport([(200, {}, b"{}")])
        with patch("proactive.connectors.calendar_google._http", SafeHttp(transport=tr)):
            with patch("proactive.connectors.github._http", SafeHttp(transport=tr)):
                poll_connectors()
        self.assertEqual(tr.calls, [])
        self.assertEqual(get_enabled_readers(), {})

    def test_ast_connectors_get_only_except_oauth_token(self):
        root = Path(__file__).resolve().parent / "proactive" / "connectors"
        self.assertTrue(root.is_dir())
        self.assertFalse((root / "write.py").exists())
        self.assertFalse((root / "email_gmail.py").exists())
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    name = ""
                    if isinstance(func, ast.Attribute):
                        name = func.attr
                        recv = func.value
                        recv_name = recv.id if isinstance(recv, ast.Name) else ""
                    elif isinstance(func, ast.Name):
                        name = func.id
                        recv_name = ""
                    else:
                        continue
                    if name == "post" and recv_name in ("requests", "httpx"):
                        if path.name != "oauth_token.py":
                            self.fail(f"{path.name} contains {recv_name}.post")
                    if recv_name in ("requests", "httpx") and name in ("put", "patch", "delete"):
                        self.fail(f"{path.name} contains {recv_name}.{name}")
            src = path.read_text(encoding="utf-8")
            if path.name not in ("http_safe.py", "oauth_token.py"):
                self.assertNotIn("requests.post", src)
                self.assertNotIn("httpx.post", src)

    def test_calendar_fixture_fences_description(self):
        put_secret("calref", {
            "type": "oauth_google",
            "token": "ya29.fixture",
            "expiry": time.time() + 3600,
            "calendar_id": "primary",
        })
        fixture = {
            "items": [{
                "id": "evt_stand",
                "status": "confirmed",
                "summary": "Standup",
                "description": "secret agenda token=leak",
                "start": {"dateTime": "2026-09-10T10:00:00Z", "timeZone": "UTC"},
                "end": {"dateTime": "2026-09-10T10:30:00Z"},
                "updated": "2026-09-09T12:00:00.000Z",
                "attendees": [{"email": "a@example.com"}],
            }]
        }
        tr = SeqTransport([(200, {}, json.dumps(fixture).encode("utf-8"))])
        conn = GoogleCalendarConnector(http=SafeHttp(transport=tr))
        recs, _cur = conn.fetch_updates({"secret_ref": "calref", "privacy_class_default": "NORMAL"}, "")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].signal_type, "CALENDAR_EVENT")
        self.assertEqual(recs[0].fact_kind, "CAL_EVENT")
        blob = json.dumps(recs[0].payload).lower()
        self.assertNotIn("secret agenda", blob)
        self.assertNotIn("token=leak", blob)
        self.assertNotIn("description", blob)
        self.assertEqual(recs[0].payload.get("summary"), "Standup")
        self.assertEqual(recs[0].payload.get("attendee_count"), 1)

    def test_github_fixture_no_body(self):
        put_secret("ghref", {"type": "github_pat", "token": "ghp_fixture"})
        notes = [{
            "id": "note1",
            "reason": "review_requested",
            "updated_at": "2026-09-09T12:00:00Z",
            "subject": {"title": "Please review", "type": "PullRequest", "url": "https://api.github.com/repos/acme/widgets/pulls/7"},
            "repository": {"full_name": "acme/widgets", "html_url": "https://github.com/acme/widgets"},
        }]
        issues = [{
            "id": 99,
            "number": 7,
            "state": "open",
            "title": "Fix login",
            "body": "password=should-not-store",
            "updated_at": "2026-09-09T12:01:00Z",
            "html_url": "https://github.com/acme/widgets/issues/7",
            "labels": [{"name": "bug"}],
            "milestone": {"due_on": "2026-09-15T00:00:00Z"},
        }]
        tr = SeqTransport([
            (200, {"etag": '"abc"'}, json.dumps(notes).encode("utf-8")),
        ])
        conn = GitHubConnector(http=SafeHttp(transport=tr))
        recs, _cur = conn.fetch_updates({"secret_ref": "ghref"}, "")
        self.assertTrue(recs)
        blob = json.dumps([r.payload for r in recs]).lower()
        self.assertNotIn("password=", blob)
        self.assertTrue(any(r.signal_type == "GITHUB_REVIEW_REQUEST" for r in recs))
        self.assertEqual(recs[0].payload.get("html_host"), "github.com")
        self.assertEqual(_owner_repo_from_remote("git@github.com:acme/widgets.git"), "acme/widgets")

    def test_pg_schema_has_no_token_columns(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        rows = _q(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name IN ('connector_accounts','connector_sync_state','external_facts')
              AND (
                    lower(column_name) LIKE '%%token%%'
                 OR lower(column_name) IN ('password','api_key','refresh','client_secret','authorization')
              )
            """
        )
        self.assertIsInstance(rows, list)
        if rows and "error" in rows[0]:
            self.fail(rows[0]["error"])
        self.assertEqual(rows, [])

    def test_persist_calendar_fact_and_signal(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        aid = "acct-" + uuid.uuid4().hex[:12]
        src = "evt-" + uuid.uuid4().hex[:12]
        ok = proactive_store.upsert_connector_account({
            "account_id": aid,
            "connector_type": "calendar_google",
            "project_id": "iso-" + uuid.uuid4().hex[:8],
            "secret_ref": "vault-" + aid[:8],
            "status": "ACTIVE",
        })
        self.assertEqual(ok, aid)
        rec = FencedRecord(
            connector_type="calendar_google",
            source_record_id=src,
            occurred_at=time.time(),
            privacy_class="NORMAL",
            payload={"event_id": src, "status": "confirmed", "summary": "Sync"},
            fact_kind="CAL_EVENT",
            signal_type="CALENDAR_EVENT",
        )
        persist_fenced_record({"account_id": aid, "owner_id": "sujal"}, rec)
        facts = _q("SELECT payload, fact_kind FROM external_facts WHERE source_record_id = %s", (src,))
        self.assertTrue(facts and "error" not in facts[0])
        payload = facts[0]["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        self.assertEqual(facts[0]["fact_kind"], "CAL_EVENT")
        self.assertNotIn("token", json.dumps(payload).lower())
        sigs = _q("SELECT signal_type, payload FROM proactive_signals WHERE entity_id = %s", (src,))
        self.assertTrue(sigs)
        self.assertEqual(sigs[0]["signal_type"], "CALENDAR_EVENT")
        _q("DELETE FROM proactive_signals WHERE entity_id = %s", (src,), readonly=False)

    def test_git_remote_maps_to_project(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        pid = "p-" + uuid.uuid4().hex[:10]
        repo = "acme/w-" + uuid.uuid4().hex[:8]
        _q(
            """
            INSERT INTO projects (project_id, name, description, git_remote, lifecycle_status, privacy_class)
            VALUES (%s, 'maptest', '', %s, 'ACTIVE', 'NORMAL')
            """,
            (pid, f"https://github.com/{repo}.git"),
            readonly=False,
        )
        mapped = proactive_store.map_github_repo_to_project(repo)
        self.assertEqual(mapped, pid)

    def test_outage_backoff_skips_second_fetch(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_CALENDAR_ENABLED"] = "true"
        os.environ["PROACTIVE_GITHUB_ENABLED"] = "false"
        aid = "acct-" + uuid.uuid4().hex[:12]
        account = {
            "account_id": aid,
            "owner_id": "sujal",
            "connector_type": "calendar_google",
            "project_id": "iso-" + uuid.uuid4().hex[:8],
            "secret_ref": "missing",
            "status": "ACTIVE",
            "privacy_class_default": "NORMAL",
            "health": "OK",
            "health_detail": "",
            "policy_version": 1,
        }
        proactive_store.upsert_connector_account(account)
        class Boom:
            connector_type = "calendar_google"
            def fetch_updates(self, account, cursor):
                calls["n"] += 1
                raise SafeHttpError("http 429", status=429)
        calls = {"n": 0}
        with patch.object(proactive_store, "list_active_accounts", return_value=[account]):
            with patch("proactive.connectors.registry.get_enabled_readers", return_value={"calendar_google": Boom()}):
                poll_connectors()
                poll_connectors()
        sync = proactive_store.get_sync_state(aid)
        self.assertGreater(float(sync.get("backoff_until") or 0), time.time())
        self.assertEqual(calls["n"], 1)
        rows = _q("SELECT health, health_detail FROM connector_accounts WHERE account_id = %s", (aid,))
        self.assertEqual(rows[0]["health"], "DEGRADED")
        self.assertEqual(rows[0]["health_detail"], "rate_limit")

    def test_poll_connectors_flag_on_uses_reader_once(self):
        if not _pg():
            self.skipTest("postgres unavailable")
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_CALENDAR_ENABLED"] = "true"
        aid = "acct-" + uuid.uuid4().hex[:12]
        src = "evt-" + uuid.uuid4().hex[:12]
        account = {
            "account_id": aid,
            "owner_id": "sujal",
            "connector_type": "calendar_google",
            "project_id": "iso-" + uuid.uuid4().hex[:8],
            "secret_ref": "r",
            "status": "ACTIVE",
            "privacy_class_default": "NORMAL",
            "health": "OK",
            "health_detail": "",
            "policy_version": 1,
        }
        proactive_store.upsert_connector_account(account)
        rec = FencedRecord(
            connector_type="calendar_google",
            source_record_id=src,
            occurred_at=time.time(),
            privacy_class="NORMAL",
            payload={"event_id": src, "status": "confirmed"},
            fact_kind="CAL_EVENT",
            signal_type="CALENDAR_EVENT",
        )
        calls = {"n": 0}

        class Ok:
            connector_type = "calendar_google"
            def fetch_updates(self, acct, cursor):
                calls["n"] += 1
                return [rec], "2026-09-09T12:00:00Z"

        with patch.object(proactive_store, "list_active_accounts", return_value=[account]):
            with patch("proactive.connectors.registry.get_enabled_readers", return_value={"calendar_google": Ok()}):
                poll_connectors()
        self.assertEqual(calls["n"], 1)
        facts = _q("SELECT 1 FROM external_facts WHERE source_record_id = %s", (src,))
        self.assertTrue(facts)
        _q("DELETE FROM proactive_signals WHERE entity_id = %s", (src,), readonly=False)


if __name__ == "__main__":
    unittest.main()
