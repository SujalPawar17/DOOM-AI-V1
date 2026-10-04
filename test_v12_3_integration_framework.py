#!/usr/bin/env python
"""DOOM V12.3 — External Integration Framework tests (local sandbox, HARD $0)."""

import os
import sys
import threading
import time
import unittest

os.environ["PROACTIVE_V8_ENABLED"] = "true"
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.cost_guard.types import ResourceType  # noqa: E402
from core.v12.integrations import (  # noqa: E402
    ActionSpec, Connector, ConnectorError, ConnectorRegistry, ConnectorRequest, ConnectorSandbox,
    ConnectorSpec, ConnectorStatus, IntegrationGateway, IntegrationIdentity, ParamSpec, ToolSelector,
    ConnectorAuthorizationStore,
)
from orchestration.experience.store import (  # noqa: E402
    use_test_experience_store, reset_experience_for_tests, list_experiences,
)

OWNER, SESSION = "conn_owner", "conn_sess"
ME = IntegrationIdentity(OWNER, SESSION)


def req(connector, action, **args):
    return ConnectorRequest.make(OWNER, SESSION, connector, action, args)


class SandboxBase(unittest.TestCase):
    def setUp(self):
        use_test_experience_store(True)
        reset_experience_for_tests()
        self.box = ConnectorSandbox(OWNER).__enter__()
        self.gw = self.box.gateway

    def tearDown(self):
        self.box.__exit__(None, None, None)
        reset_experience_for_tests()

    def experiences(self):
        return list(list_experiences(OWNER).experiences)


class TestSuccessfulExecution(SandboxBase):

    def test_filesystem_list_and_read(self):
        listed = self.gw.invoke(req("filesystem", "list_dir"), ME)
        self.assertTrue(listed.ok, listed)
        self.assertIn("notes.txt", listed.output["entries"])
        read = self.gw.invoke(req("filesystem", "read_text", path="notes.txt"), ME)
        self.assertTrue(read.ok)
        self.assertEqual(read.output["text"], "hello from the sandbox\n")
        self.assertTrue(read.experience_recorded)

    def test_git_read_only(self):
        branch = self.gw.invoke(req("git", "current_branch"), ME)
        self.assertTrue(branch.ok, branch)
        self.assertEqual(branch.output["branch"], "main")
        log = self.gw.invoke(req("git", "log", limit=3), ME)
        self.assertTrue(log.ok)
        self.assertIn("initial commit", log.output["commits"][0])
        status = self.gw.invoke(req("git", "status"), ME)
        self.assertTrue(status.ok)

    def test_sqlite_read_only(self):
        res = self.gw.invoke(req("sqlite", "query", sql="SELECT title FROM tasks WHERE done = 0 ORDER BY id"), ME)
        self.assertTrue(res.ok, res)
        self.assertEqual(res.output["rows"], [["write report"], ["plan week"]])

    def test_experience_recorded_for_executed_attempts_only(self):
        self.gw.invoke(req("filesystem", "read_text", path="notes.txt"), ME)
        self.gw.invoke(req("nope", "x"), ME)
        self.gw.invoke(req("filesystem", "write_text", path="a.txt", content="x"), ME)  # pending only
        titles = [e.title for e in self.experiences()]
        self.assertEqual(titles, ["Connector filesystem.read_text"])


class TestRefusals(SandboxBase):

    def test_unknown_connector_and_action(self):
        self.assertEqual(self.gw.invoke(req("nope", "x"), ME).status, ConnectorStatus.UNKNOWN_CONNECTOR)
        self.assertEqual(self.gw.invoke(req("git", "push"), ME).status, ConnectorStatus.UNKNOWN_ACTION)

    def test_identity_owner_session(self):
        self.assertEqual(self.gw.invoke(req("git", "status"), None).status, ConnectorStatus.IDENTITY_REQUIRED)
        other = ConnectorRequest.make("intruder", SESSION, "git", "status")
        self.assertEqual(self.gw.invoke(other, ME).status, ConnectorStatus.OWNER_MISMATCH)
        wrong_session = ConnectorRequest.make(OWNER, "other_session", "git", "status")
        self.assertEqual(self.gw.invoke(wrong_session, ME).status, ConnectorStatus.SESSION_MISMATCH)
        # An owner without a configured workspace gets nothing, even with a valid identity.
        stranger = IntegrationIdentity("stranger", "s")
        r = self.gw.invoke(ConnectorRequest.make("stranger", "s", "filesystem", "list_dir"), stranger)
        self.assertEqual(r.status, ConnectorStatus.CONNECTOR_FAILED)

    def test_malformed_arguments(self):
        cases = [
            req("filesystem", "read_text"),                                   # missing
            req("filesystem", "read_text", path="notes.txt", extra=1),        # undeclared
            req("filesystem", "read_text", path=123),                         # wrong type
            req("git", "log", limit=500),                                     # out of range
            req("git", "log", limit=True),                                    # bool is not int
            req("filesystem", "read_text", path="x" * 5000),                  # too long
            req("filesystem", "read_text", path="notes.txt", command="rm"),   # forbidden key
        ]
        for c in cases:
            self.assertEqual(self.gw.invoke(c, ME).status, ConnectorStatus.INVALID_ARGUMENTS, c)

    def test_sandbox_escape_and_sensitive_files(self):
        for path in ("../repo/README.md", "..\\data.sqlite", os.path.abspath(__file__), "sub/../../x"):
            r = self.gw.invoke(req("filesystem", "read_text", path=path), ME)
            self.assertEqual(r.status, ConnectorStatus.CONNECTOR_FAILED, path)
        with open(os.path.join(self.box.workspace, ".env"), "w") as fh:
            fh.write("API_KEY=should_not_leak")
        r = self.gw.invoke(req("filesystem", "read_text", path=".env"), ME)
        self.assertEqual(r.status, ConnectorStatus.CONNECTOR_FAILED)
        listed = self.gw.invoke(req("filesystem", "list_dir"), ME)
        self.assertNotIn(".env", listed.output["entries"])

    def test_sqlite_refuses_writes_and_multi_statements(self):
        for sql in ("DELETE FROM tasks", "SELECT 1; DROP TABLE tasks", "UPDATE tasks SET done=1",
                    "ATTACH DATABASE 'x' AS y", "PRAGMA table_info(tasks)"):
            r = self.gw.invoke(req("sqlite", "query", sql=sql), ME)
            self.assertEqual(r.status, ConnectorStatus.CONNECTOR_FAILED, sql)
        count = self.gw.invoke(req("sqlite", "query", sql="SELECT COUNT(*) FROM tasks"), ME)
        self.assertEqual(count.output["rows"], [[3]])

    def test_cloud_connector_disabled_then_cost_blocked(self):
        r = self.gw.invoke(req("github_api", "list_issues", repo="a/b"), ME)
        self.assertEqual(r.status, ConnectorStatus.DISABLED)
        self.box.registry.set_enabled("github_api", True)
        r = self.gw.invoke(req("github_api", "list_issues", repo="a/b"), ME)
        self.assertEqual(r.status, ConnectorStatus.COST_BLOCKED)
        self.assertEqual(self.experiences(), [])


class TestAuthorization(SandboxBase):

    def test_write_requires_claimed_single_use_approval(self):
        write = req("filesystem", "write_text", path="out/report.txt", content="draft 1")
        first = self.gw.invoke(write, ME)
        self.assertEqual(first.status, ConnectorStatus.APPROVAL_REQUIRED)
        self.assertFalse(os.path.exists(os.path.join(self.box.workspace, "out", "report.txt")))
        self.assertEqual(self.gw.invoke(write, ME).pending_id, first.pending_id, "same request -> same approval")

        # Knowing (computing) the hash is not authorization.
        forged = self.gw.invoke(write, ME, authorized_request_hash=write.request_hash())
        self.assertEqual(forged.status, ConnectorStatus.AUTHORIZATION_INVALID)

        # Only the right identity can claim.
        _h, code = self.gw.approvals.claim(first.pending_id, IntegrationIdentity(OWNER, "other"))
        self.assertEqual(code, "AUTHORIZATION_INVALID")
        authorized, code = self.gw.approvals.claim(first.pending_id, ME)
        self.assertEqual(code, "OK")

        # A different (tampered) request cannot use this approval.
        tampered = req("filesystem", "write_text", path="out/report.txt", content="draft EVIL")
        self.assertEqual(self.gw.invoke(tampered, ME, authorized_request_hash=authorized).status,
                         ConnectorStatus.AUTHORIZATION_INVALID)

        done = self.gw.invoke(write, ME, authorized_request_hash=authorized)
        self.assertTrue(done.ok, done)
        with open(os.path.join(self.box.workspace, "out", "report.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "draft 1")

        replay = self.gw.invoke(write, ME, authorized_request_hash=authorized)
        self.assertEqual(replay.status, ConnectorStatus.AUTHORIZATION_INVALID, "approvals are single-use")
        self.assertEqual(self.gw.approvals.claim(first.pending_id, ME)[1], "CONSUMED")

    def test_approvals_expire(self):
        clock = [1000.0]
        store = ConnectorAuthorizationStore(clock=lambda: clock[0], ttl=60)
        gw = IntegrationGateway(self.box.registry, approvals=store, clock=lambda: clock[0])
        try:
            write = req("filesystem", "write_text", path="late.txt", content="x")
            pid = gw.invoke(write, ME).pending_id
            clock[0] += 61
            self.assertEqual(store.claim(pid, ME)[1], "EXPIRED")
            self.assertEqual(store.size(), 0, "expired approvals are dropped")
        finally:
            gw.shutdown()

    def test_pending_store_is_bounded(self):
        for i in range(400):
            self.gw.invoke(req("filesystem", "write_text", path=f"f{i}.txt", content="x"), ME)
        self.assertLessEqual(self.gw.approvals.size(), 256)


# --- custom connectors for failure behaviour -----------------------------------

class _Flaky(Connector):
    def __init__(self, fail_times, idempotent=True, verify_ok=True, sleep=0.0, mutates=False):
        self.calls = 0
        self.fail_times = fail_times
        self.verify_ok = verify_ok
        self.sleep = sleep
        self.spec = ConnectorSpec(
            connector_id="flaky", capability="test", description="test connector",
            actions=(ActionSpec("run", (), risk="LOW", idempotent=idempotent, mutates=mutates),),
            cost_resource=(ResourceType.OTHER, "local_filesystem", ""), permissions=("test",),
            timeout_ms=300, max_retries=2)

    def execute(self, action, args, identity):
        self.calls += 1
        if self.sleep:
            time.sleep(self.sleep)
        if self.calls <= self.fail_times:
            raise ConnectorError("transient failure")
        return {"value": "token=abc123secret ok"}

    def verify(self, action, args, output, identity):
        return self.verify_ok


class TestFailureBehaviour(unittest.TestCase):
    def setUp(self):
        use_test_experience_store(True)
        reset_experience_for_tests()

    def gateway(self, connector):
        registry = ConnectorRegistry()
        registry.register(connector)
        gw = IntegrationGateway(registry)
        self.addCleanup(gw.shutdown)
        return gw

    def test_bounded_retry_for_idempotent_actions(self):
        c = _Flaky(fail_times=2)
        r = self.gateway(c).invoke(req("flaky", "run"), ME)
        self.assertTrue(r.ok)
        self.assertEqual(r.attempts, 3)
        self.assertNotIn("abc123secret", str(r.output), "outputs are redacted")

    def test_connector_failure_after_retries(self):
        c = _Flaky(fail_times=10)
        r = self.gateway(c).invoke(req("flaky", "run"), ME)
        self.assertEqual(r.status, ConnectorStatus.CONNECTOR_FAILED)
        self.assertEqual(c.calls, 3)
        self.assertTrue(r.experience_recorded)
        self.assertEqual(str(list_experiences(OWNER).experiences[0].outcome).split(".")[-1], "ABANDONED")

    def test_non_idempotent_actions_never_retry(self):
        c = _Flaky(fail_times=1, idempotent=False)
        r = self.gateway(c).invoke(req("flaky", "run"), ME)
        self.assertEqual(r.status, ConnectorStatus.CONNECTOR_FAILED)
        self.assertEqual(c.calls, 1)

    def test_verification_failure(self):
        r = self.gateway(_Flaky(fail_times=0, verify_ok=False)).invoke(req("flaky", "run"), ME)
        self.assertEqual(r.status, ConnectorStatus.VERIFICATION_FAILED)
        self.assertFalse(r.ok)

    def test_timeout(self):
        c = _Flaky(fail_times=0, sleep=1.0)
        t0 = time.time()
        r = self.gateway(c).invoke(req("flaky", "run"), ME)
        self.assertEqual(r.status, ConnectorStatus.TIMEOUT)
        self.assertLess(time.time() - t0, 0.9)

    def test_registry_rejects_unbounded_retry_policy(self):
        c = _Flaky(0)
        c.spec = ConnectorSpec(connector_id="bad", capability="t", description="", actions=(),
                               cost_resource=(ResourceType.OTHER, "local_filesystem", ""), permissions=(),
                               max_retries=10)
        with self.assertRaises(ValueError):
            ConnectorRegistry().register(c)

    def test_worker_threads_are_bounded(self):
        gw = self.gateway(_Flaky(fail_times=0, sleep=0.05))
        before = threading.active_count()
        threads = [threading.Thread(target=gw.invoke, args=(req("flaky", "run"), ME)) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        workers = [t for t in threading.enumerate() if t.name.startswith("doom-connector")]
        self.assertLessEqual(len(workers), 4)


class TestToolSelection(unittest.TestCase):

    def test_proposals_are_deterministic_and_never_execute(self):
        sel = ToolSelector()
        self.assertEqual(sel.propose("git status", OWNER, SESSION), req("git", "status"))
        self.assertEqual(sel.propose("What branch am I on?", OWNER, SESSION).action, "current_branch")
        self.assertEqual(sel.propose("list files in docs", OWNER, SESSION), req("filesystem", "list_dir", path="docs"))
        self.assertEqual(sel.propose("read file notes.txt", OWNER, SESSION).args_dict(), {"path": "notes.txt"})
        self.assertIsNone(sel.propose("What is the capital of France?", OWNER, SESSION))
        self.assertIsNone(sel.propose("delete everything", OWNER, SESSION))


if __name__ == "__main__":
    unittest.main(verbosity=2)
