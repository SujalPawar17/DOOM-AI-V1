"""V12.3 initial connectors — local, safe, testable.

- LocalFilesystemConnector: per-owner sandbox root; list/read (LOW), write (MEDIUM,
  mutating -> approval); traversal/symlink escape and sensitive files refused;
  writes verified by re-reading the content hash.
- LocalGitConnector: read-only status / log / current_branch on a per-owner repo;
  fixed argv (no shell), bounded timeout, no prompts.
- LocalSqliteConnector: read-only SELECT/WITH on a per-owner SQLite file opened with
  mode=ro; single statement, parameter binding, bounded rows.
- GithubCloudConnector: declaration of an external cloud connector. Disabled by default
  and its resource is unattested, so Cost Guard blocks it even if enabled.

Cost resources use EXISTING Cost Guard attestations only (local_filesystem for local
files, git working trees and SQLite files). No Cost Guard entries are added.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import subprocess
from typing import Any, Dict

from core.cost_guard.types import ResourceType
from core.v12.integrations.framework import (
    ActionSpec, Connector, ConnectorError, ConnectorSpec, IntegrationIdentity, ParamSpec,
)

LOCAL_FILES = (ResourceType.OTHER, "local_filesystem", "")
MAX_READ_BYTES = 64 * 1024
MAX_WRITE_CHARS = 64 * 1024
MAX_LIST_ENTRIES = 200
_SENSITIVE_NAME = re.compile(
    r"(?i)(^\.env(\..*)?$|\.pem$|\.key$|\.pfx$|\.p12$|^id_(rsa|dsa|ecdsa|ed25519)|credential|secret|"
    r"password|token|cookie|\.kdbx$)")


def _owner_root(roots: Dict[str, str], identity: IntegrationIdentity) -> str:
    root = roots.get(identity.owner_id)
    if not root:
        raise ConnectorError("no workspace is configured for this owner")
    return os.path.realpath(root)


def _inside(root: str, relative: str) -> str:
    if os.path.isabs(relative) or relative.startswith(("\\\\", "//")):
        raise ConnectorError("absolute paths are not allowed")
    target = os.path.realpath(os.path.join(root, relative))
    try:
        escaped = os.path.commonpath([root, target]) != root
    except ValueError:  # different drive on Windows
        escaped = True
    if escaped:
        raise ConnectorError("path escapes the workspace")
    for part in os.path.relpath(target, root).split(os.sep):
        if part not in (".", "") and _SENSITIVE_NAME.search(part):
            raise ConnectorError("access to sensitive files is not allowed")
    return target


class LocalFilesystemConnector(Connector):
    spec = ConnectorSpec(
        connector_id="filesystem",
        capability="local_files",
        description="Owner workspace files (sandboxed)",
        actions=(
            ActionSpec("list_dir", (ParamSpec("path", str, required=False, max_len=260),), risk="LOW",
                       verification="listing_present"),
            ActionSpec("read_text", (ParamSpec("path", str, max_len=260),), risk="LOW",
                       verification="content_present"),
            ActionSpec("write_text", (ParamSpec("path", str, max_len=260),
                                      ParamSpec("content", str, max_len=MAX_WRITE_CHARS)),
                       risk="MEDIUM", mutates=True, idempotent=False, verification="reread_sha256_match"),
        ),
        cost_resource=LOCAL_FILES,
        permissions=("workspace:read", "workspace:write"),
        timeout_ms=3000,
        max_retries=1,
    )

    def __init__(self, roots: Dict[str, str]):
        self.roots = dict(roots)

    def execute(self, action: str, args: Dict[str, Any], identity: IntegrationIdentity) -> Any:
        root = _owner_root(self.roots, identity)
        if action == "list_dir":
            target = _inside(root, args.get("path") or ".")
            if not os.path.isdir(target):
                raise ConnectorError("not a directory")
            names = sorted(n for n in os.listdir(target) if not _SENSITIVE_NAME.search(n))
            return {"entries": names[:MAX_LIST_ENTRIES], "truncated": len(names) > MAX_LIST_ENTRIES}
        if action == "read_text":
            target = _inside(root, args["path"])
            if not os.path.isfile(target):
                raise ConnectorError("file not found")
            with open(target, "rb") as fh:
                data = fh.read(MAX_READ_BYTES + 1)
            return {"text": data[:MAX_READ_BYTES].decode("utf-8", errors="replace"),
                    "truncated": len(data) > MAX_READ_BYTES}
        if action == "write_text":
            target = _inside(root, args["path"])
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = target + ".doom_tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                fh.write(args["content"])
            os.replace(tmp, target)
            return {"written": os.path.relpath(target, root),
                    "sha256": hashlib.sha256(args["content"].encode("utf-8")).hexdigest()}
        raise ConnectorError("unsupported action")

    def verify(self, action, args, output, identity) -> bool:
        if action == "list_dir":
            return isinstance(output, dict) and isinstance(output.get("entries"), list)
        if action == "read_text":
            return isinstance(output, dict) and isinstance(output.get("text"), str)
        if action == "write_text":
            root = _owner_root(self.roots, identity)
            with open(_inside(root, args["path"]), "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest() == output.get("sha256")
        return False


class LocalGitConnector(Connector):
    spec = ConnectorSpec(
        connector_id="git",
        capability="local_git_read",
        description="Read-only git inspection of the owner's repository",
        actions=(
            ActionSpec("status", (), risk="LOW", verification="git_exit_zero"),
            ActionSpec("current_branch", (), risk="LOW", verification="git_exit_zero"),
            ActionSpec("log", (ParamSpec("limit", int, required=False, min_value=1, max_value=20),),
                       risk="LOW", verification="git_exit_zero"),
        ),
        cost_resource=LOCAL_FILES,
        permissions=("repo:read",),
        timeout_ms=5000,
        max_retries=1,
    )

    def __init__(self, repos: Dict[str, str]):
        self.repos = dict(repos)

    def _git(self, repo: str, *argv: str) -> str:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
        proc = subprocess.run(["git", "-C", repo, "--no-pager", *argv], shell=False, capture_output=True,
                              text=True, timeout=4, env=env, stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            raise ConnectorError(f"git exited with {proc.returncode}")
        return proc.stdout

    def execute(self, action: str, args: Dict[str, Any], identity: IntegrationIdentity) -> Any:
        repo = _owner_root(self.repos, identity)
        if action == "status":
            return {"porcelain": self._git(repo, "status", "--porcelain=v1", "--branch").splitlines()[:200]}
        if action == "current_branch":
            return {"branch": self._git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()}
        if action == "log":
            limit = int(args.get("limit") or 5)
            lines = self._git(repo, "log", f"-n{limit}", "--pretty=format:%h %s").splitlines()
            return {"commits": lines[:limit]}
        raise ConnectorError("unsupported action")

    def verify(self, action, args, output, identity) -> bool:
        return isinstance(output, dict) and bool(output)


_SELECT = re.compile(r"(?is)^\s*(select|with)\b")
_FORBIDDEN_SQL = re.compile(r"(?i)\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum)\b")


class LocalSqliteConnector(Connector):
    spec = ConnectorSpec(
        connector_id="sqlite",
        capability="local_db_read",
        description="Read-only queries on the owner's local SQLite database",
        actions=(
            ActionSpec("query", (ParamSpec("sql", str, max_len=2000),
                                 ParamSpec("limit", int, required=False, min_value=1, max_value=200)),
                       risk="LOW", verification="rows_present"),
        ),
        cost_resource=LOCAL_FILES,
        permissions=("db:read",),
        timeout_ms=3000,
        max_retries=1,
    )

    def __init__(self, databases: Dict[str, str]):
        self.databases = dict(databases)

    def execute(self, action: str, args: Dict[str, Any], identity: IntegrationIdentity) -> Any:
        if action != "query":
            raise ConnectorError("unsupported action")
        path = self.databases.get(identity.owner_id)
        if not path or not os.path.isfile(path):
            raise ConnectorError("no database is configured for this owner")
        sql = args["sql"].strip().rstrip(";").strip()
        if not _SELECT.match(sql) or ";" in sql or _FORBIDDEN_SQL.search(sql):
            raise ConnectorError("only a single read-only SELECT is allowed")
        limit = int(args.get("limit") or 50)
        uri = "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            conn.execute("PRAGMA query_only = ON")
            cur = conn.execute(sql)
            columns = [d[0] for d in (cur.description or ())]
            rows = [list(r) for r in cur.fetchmany(limit)]
        finally:
            conn.close()
        return {"columns": columns, "rows": rows}

    def verify(self, action, args, output, identity) -> bool:
        return isinstance(output, dict) and isinstance(output.get("rows"), list)


class GithubCloudConnector(Connector):
    """External cloud connector declaration. Disabled by default; unattested -> Cost Guard blocks."""
    spec = ConnectorSpec(
        connector_id="github_api",
        capability="cloud_repo",
        description="GitHub REST API (external cloud service)",
        actions=(ActionSpec("list_issues", (ParamSpec("repo", str, max_len=100),), risk="LOW"),),
        cost_resource=(ResourceType.CONNECTOR, "github", "api.github.com"),
        permissions=("cloud:read",),
        enabled_by_default=False,
    )

    def execute(self, action, args, identity):
        raise ConnectorError("cloud connectors are not implemented under HARD $0")
