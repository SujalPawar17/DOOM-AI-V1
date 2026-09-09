"""READ-only connector types. No WRITE HTTP."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

from abc import ABC, abstractmethod


@dataclass
class FencedRecord:
    connector_type: str
    source_record_id: str
    occurred_at: float
    privacy_class: str
    payload: Dict[str, Any] = field(default_factory=dict)
    fact_kind: str = ""
    signal_type: str = ""
    project_id: str = ""
    ephemeral: Dict[str, Any] = field(default_factory=dict)  # in-memory only; never persisted


class ReadConnector(ABC):
    connector_type: str = ""

    @abstractmethod
    def fetch_updates(self, account: Dict[str, Any], cursor: str) -> Tuple[list, str]:
        """Return (fenced records, new cursor). GET-only. Never mutates providers."""
        raise NotImplementedError
