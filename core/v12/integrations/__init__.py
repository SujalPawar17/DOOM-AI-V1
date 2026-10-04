"""V12.3 integration framework: declared connectors behind Cost Guard, authorization
and verification. See core/v12/integrations/framework.py."""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

from core.v12.integrations.framework import (  # noqa: F401
    ActionSpec, Connector, ConnectorAuthorizationStore, ConnectorError, ConnectorRegistry,
    ConnectorRequest, ConnectorResult, ConnectorSpec, ConnectorStatus, IntegrationGateway,
    IntegrationIdentity, ParamSpec, record_connector_experience,
)
from core.v12.integrations.connectors import (  # noqa: F401
    GithubCloudConnector, LocalFilesystemConnector, LocalGitConnector, LocalSqliteConnector,
)


class ToolSelector:
    """Deterministic intent -> connector proposal. Proposes only; never executes."""

    _RULES = (
        (re.compile(r"(?i)^\s*(git\s+status|what(?:'s| is) the (?:git|repo(?:sitory)?) status)\b"), "git", "status", None),
        (re.compile(r"(?i)^\s*(which|what|current)\s+branch\b|\bwhat branch am i on\b"), "git", "current_branch", None),
        (re.compile(r"(?i)^\s*(git\s+log|show (?:me )?(?:the )?(?:recent|last) commits)\b"), "git", "log", None),
        (re.compile(r"(?i)^\s*list (?:the )?files(?: in (?P<path>[\w./\\-]+))?\s*$"), "filesystem", "list_dir", "path"),
        (re.compile(r"(?i)^\s*(?:read|show|open) (?:the )?file (?P<path>[\w./\\-]+)\s*$"), "filesystem", "read_text", "path"),
    )

    def propose(self, text: str, owner_id: str, session_id: str) -> Optional[ConnectorRequest]:
        for pattern, connector_id, action, arg in self._RULES:
            m = pattern.search(str(text or ""))
            if m:
                args = {}
                if arg and m.groupdict().get(arg):
                    args[arg] = m.group(arg)
                return ConnectorRequest.make(owner_id, session_id, connector_id, action, args)
        return None


@dataclass
class ConnectorSandbox:
    """Disposable local fixtures (workspace dir, git repo, SQLite db) wired into a
    registry + gateway for one owner. Used by tests and demonstrations."""
    owner_id: str
    root: str = ""
    registry: Optional[ConnectorRegistry] = None
    gateway: Optional[IntegrationGateway] = None

    def __enter__(self) -> "ConnectorSandbox":
        self._tmp = tempfile.TemporaryDirectory(prefix="doom_connectors_", ignore_cleanup_errors=True)
        self.root = self._tmp.name
        self.workspace = os.path.join(self.root, "workspace")
        self.repo = os.path.join(self.root, "repo")
        self.db = os.path.join(self.root, "data.sqlite")
        os.makedirs(self.workspace)
        with open(os.path.join(self.workspace, "notes.txt"), "w", encoding="utf-8", newline="") as fh:
            fh.write("hello from the sandbox\n")
        os.makedirs(self.repo)
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        for argv in (["init", "-q", "-b", "main"], ["config", "user.email", "sandbox@example.invalid"],
                     ["config", "user.name", "Sandbox"], ["config", "commit.gpgsign", "false"]):
            subprocess.run(["git", "-C", self.repo, *argv], check=True, capture_output=True, env=env)
        with open(os.path.join(self.repo, "README.md"), "w", encoding="utf-8", newline="") as fh:
            fh.write("# sandbox\n")
        subprocess.run(["git", "-C", self.repo, "add", "README.md"], check=True, capture_output=True, env=env)
        subprocess.run(["git", "-C", self.repo, "commit", "-q", "-m", "initial commit"], check=True,
                       capture_output=True, env=env)
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT, done INTEGER)")
        conn.executemany("INSERT INTO tasks (title, done) VALUES (?, ?)",
                         [("write report", 0), ("review code", 1), ("plan week", 0)])
        conn.commit()
        conn.close()
        self.registry = ConnectorRegistry()
        self.registry.register(LocalFilesystemConnector({self.owner_id: self.workspace}))
        self.registry.register(LocalGitConnector({self.owner_id: self.repo}))
        self.registry.register(LocalSqliteConnector({self.owner_id: self.db}))
        self.registry.register(GithubCloudConnector())
        self.gateway = IntegrationGateway(self.registry)
        return self

    def __exit__(self, *exc) -> bool:
        if self.gateway is not None:
            self.gateway.shutdown()
        self._tmp.cleanup()
        return False
