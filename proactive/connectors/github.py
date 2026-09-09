"""GitHub READ. notifications + issues list GET only. No comments/merge/create."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode, urlparse

from proactive.connectors.base import FencedRecord, ReadConnector
from proactive.connectors.http_safe import SafeHttp, SafeHttpError
from proactive.fence import fence_payload
from proactive.store import proactive_store
from proactive.vault import get_secret, import_github_pat_from_env

_http = SafeHttp()
_ALLOWED_HTML_HOST = "github.com"


def _parse_ts(raw: str) -> float:
    s = str(raw or "").strip()
    if not s:
        return 0.0
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def _html_host(url: str) -> str:
    host = (urlparse(str(url or "")).hostname or "").lower()
    if host == _ALLOWED_HTML_HOST or host.endswith(".github.com"):
        return "github.com"
    return ""


class GitHubConnector(ReadConnector):
    connector_type = "github"

    def __init__(self, http: Optional[SafeHttp] = None):
        self._http = http or _http

    def _token(self, secret_ref: str) -> str:
        blob = get_secret(secret_ref)
        if blob and blob.get("token"):
            return str(blob["token"])
        import_github_pat_from_env(secret_ref)
        blob = get_secret(secret_ref)
        return str((blob or {}).get("token") or "")

    def _get_json(self, url: str, token: str, extra_headers: Optional[Dict[str, str]] = None):
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if extra_headers:
            headers.update(extra_headers)
        status, hdrs, raw = self._http.get(url, headers=headers)
        if status in (403, 429):
            raise SafeHttpError(f"http {status}", status=status)
        if status == 304:
            return status, hdrs, []
        if status != 200:
            raise SafeHttpError(f"http {status}", status=status)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return status, hdrs, []
        return status, hdrs, data

    def fetch_updates(self, account: Dict[str, Any], cursor: str) -> Tuple[List[FencedRecord], str]:
        secret_ref = str(account.get("secret_ref") or "")
        token = self._token(secret_ref)
        if not token:
            raise SafeHttpError("github auth missing", status=401)
        records: List[FencedRecord] = []
        etag = ""
        since = ""
        if cursor.startswith("W/") or cursor.startswith('"') or cursor.startswith("sha="):
            etag = cursor
        elif cursor:
            since = cursor
        extra = {"If-None-Match": etag} if etag else None
        nurl = "https://api.github.com/notifications?" + urlencode({"all": "false", "participating": "true"})
        _st, hdrs, notes = self._get_json(nurl, token, extra_headers=extra)
        new_cursor = hdrs.get("etag") or hdrs.get("ETag") or cursor
        if isinstance(notes, list):
            for item in notes[:50]:
                if isinstance(item, dict):
                    rec = self._from_notification(item)
                    if rec:
                        records.append(rec)
        repo = self._repo_from_account(account)
        if repo:
            params = {"state": "all", "per_page": "20", "sort": "updated"}
            if since:
                params["since"] = since
            iurl = f"https://api.github.com/repos/{quote(repo, safe='/_-')}/issues?" + urlencode(params)
            _st2, _h2, issues = self._get_json(iurl, token)
            if isinstance(issues, list):
                for item in issues[:20]:
                    if isinstance(item, dict) and not item.get("pull_request"):
                        rec = self._from_issue(item, repo)
                        if rec:
                            records.append(rec)
            newest = ""
            for rec in records:
                ts = rec.payload.get("updated_at") or ""
                if str(ts) > newest:
                    newest = str(ts)
            if newest:
                new_cursor = newest
        return records, new_cursor or cursor

    def _repo_from_account(self, account: Dict[str, Any]) -> str:
        pid = account.get("project_id")
        if not pid:
            return ""
        try:
            from database.postgres_db import postgres_manager
            if not postgres_manager.is_connected():
                return ""
            rows = postgres_manager.execute_query(
                "SELECT git_remote FROM projects WHERE project_id = %s",
                (pid,),
            )
            if not isinstance(rows, list) or not rows or "error" in rows[0]:
                return ""
            remote = str(rows[0].get("git_remote") or "")
            return _owner_repo_from_remote(remote)
        except Exception:
            return ""

    def _from_notification(self, item: Dict[str, Any]) -> Optional[FencedRecord]:
        nid = str(item.get("id") or "")[:128]
        if not nid:
            return None
        subject = item.get("subject") if isinstance(item.get("subject"), dict) else {}
        repo_obj = item.get("repository") if isinstance(item.get("repository"), dict) else {}
        repo = str(repo_obj.get("full_name") or "")[:80]
        reason = str(item.get("reason") or "")
        title = str(subject.get("title") or "")[:80]
        html = str(repo_obj.get("html_url") or "")
        payload = {
            "notif_id": nid,
            "state": str(subject.get("type") or "")[:32],
            "title": title,
            "updated_at": str(item.get("updated_at") or "")[:40],
            "repo": repo,
            "html_host": _html_host(html),
            "reviewer_count": 1 if reason == "review_requested" else 0,
        }
        fenced, dropped = fence_payload(payload)
        if dropped:
            return None
        st = "GITHUB_REVIEW_REQUEST" if reason == "review_requested" else "GITHUB_NOTIFICATION"
        kind = "GH_REVIEW" if st == "GITHUB_REVIEW_REQUEST" else "GH_ISSUE"
        if str(subject.get("type") or "") == "PullRequest" and st != "GITHUB_REVIEW_REQUEST":
            kind = "GH_PR"
            st = "GITHUB_NOTIFICATION"
        project_id = proactive_store.map_github_repo_to_project(repo) or ""
        return FencedRecord(
            connector_type="github",
            source_record_id=nid,
            occurred_at=_parse_ts(str(item.get("updated_at") or "")),
            privacy_class="NORMAL",
            payload=fenced,
            fact_kind=kind,
            signal_type=st,
            project_id=project_id,
        )

    def _from_issue(self, item: Dict[str, Any], repo: str) -> Optional[FencedRecord]:
        number = item.get("number")
        iid = str(item.get("id") or number or "")[:128]
        if not iid:
            return None
        labels = item.get("labels") if isinstance(item.get("labels"), list) else []
        names = []
        for lab in labels[:8]:
            if isinstance(lab, dict) and lab.get("name"):
                names.append(str(lab.get("name"))[:24])
            elif isinstance(lab, str):
                names.append(lab[:24])
        milestone = item.get("milestone") if isinstance(item.get("milestone"), dict) else {}
        html = str(item.get("html_url") or "")
        payload = {
            "issue_id": iid,
            "state": str(item.get("state") or "")[:16],
            "title": str(item.get("title") or "")[:80],
            "updated_at": str(item.get("updated_at") or "")[:40],
            "labels": ",".join(names)[:80],
            "milestone_due": str(milestone.get("due_on") or "")[:40],
            "repo": repo[:80],
            "html_host": _html_host(html),
            "number": int(number or 0),
        }
        fenced, dropped = fence_payload(payload)
        if dropped:
            return None
        project_id = proactive_store.map_github_repo_to_project(repo) or ""
        return FencedRecord(
            connector_type="github",
            source_record_id=iid,
            occurred_at=_parse_ts(str(item.get("updated_at") or "")),
            privacy_class="NORMAL",
            payload=fenced,
            fact_kind="GH_ISSUE",
            signal_type="GITHUB_ISSUE",
            project_id=project_id,
        )


def _owner_repo_from_remote(remote: str) -> str:
    rem = str(remote or "").strip().replace("\\", "/")
    if rem.endswith(".git"):
        rem = rem[:-4]
    low = rem.lower()
    marker = "github.com/"
    if marker in low:
        idx = low.index(marker) + len(marker)
        rest = rem[idx:]
        parts = rest.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    if "github.com:" in low:
        rest = rem.split("github.com:", 1)[-1]
        parts = rest.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    return ""
