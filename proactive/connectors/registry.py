"""Flag-gated READ connector instances. Worker imports this, not WRITE modules."""

from __future__ import annotations

from typing import Dict, Optional

from proactive.config import is_calendar_enabled, is_email_enabled, is_github_enabled
from proactive.connectors.base import ReadConnector
from proactive.connectors.calendar_google import GoogleCalendarConnector
from proactive.connectors.email_gmail import GmailReadConnector
from proactive.connectors.github import GitHubConnector


def get_reader(connector_type: str) -> Optional[ReadConnector]:
    if connector_type == "calendar_google" and is_calendar_enabled():
        return GoogleCalendarConnector()
    if connector_type == "github" and is_github_enabled():
        return GitHubConnector()
    if connector_type == "gmail" and is_email_enabled():
        return GmailReadConnector()
    return None


def get_enabled_readers() -> Dict[str, ReadConnector]:
    out: Dict[str, ReadConnector] = {}
    if is_calendar_enabled():
        out["calendar_google"] = GoogleCalendarConnector()
    if is_github_enabled():
        out["github"] = GitHubConnector()
    if is_email_enabled():
        out["gmail"] = GmailReadConnector()
    return out
