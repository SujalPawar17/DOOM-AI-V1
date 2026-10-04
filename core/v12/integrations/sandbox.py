"""V12.3 connector sandbox: disposable local fixtures (workspace, git repo, SQLite db)
wired into a registry + gateway for one owner. Test / demonstration harness only."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

from core.v12.integrations.framework import ConnectorRegistry, IntegrationGateway
from core.v12.integrations.connectors import (
    GithubCloudConnector, LocalFilesystemConnector, LocalGitConnector, LocalSqliteConnector,
)


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
