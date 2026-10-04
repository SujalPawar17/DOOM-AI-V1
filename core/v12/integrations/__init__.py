"""V12.3 integration framework: declared connectors behind Cost Guard, authorization
and verification. See core/v12/integrations/framework.py."""

from __future__ import annotations

import re
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


from core.v12.integrations.sandbox import ConnectorSandbox  # noqa: E402,F401  (test/demo harness)
