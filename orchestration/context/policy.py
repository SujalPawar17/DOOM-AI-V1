"""Hard V8.8 context ceilings. Callers may lower limits, never raise them."""

from __future__ import annotations

from dataclasses import dataclass


MAX_CONTEXT_ITEMS = 8
MAX_MEMORY_ITEMS = 4
MAX_EXPERIENCE_ITEMS = 4
MAX_ITEM_CHARS = 2000
MAX_TOTAL_CONTEXT_CHARS = 12000
MAX_ID_CHARS = 128
MAX_QUERY_CHARS = 2048
MAX_PROVENANCE_CHARS = 64
MAX_OUTCOME_CHARS = 32


def _clamp(requested: int, hard: int) -> int:
    try:
        value = int(requested)
    except (TypeError, ValueError):
        return hard
    if value < 0:
        return 0
    if value > hard:
        return hard
    return value


@dataclass(frozen=True)
class ContextPolicy:
    max_items: int = MAX_CONTEXT_ITEMS
    max_memory_items: int = MAX_MEMORY_ITEMS
    max_experience_items: int = MAX_EXPERIENCE_ITEMS
    max_item_chars: int = MAX_ITEM_CHARS
    max_total_chars: int = MAX_TOTAL_CONTEXT_CHARS

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_items", _clamp(self.max_items, MAX_CONTEXT_ITEMS))
        object.__setattr__(self, "max_memory_items", _clamp(self.max_memory_items, MAX_MEMORY_ITEMS))
        object.__setattr__(self, "max_experience_items", _clamp(self.max_experience_items, MAX_EXPERIENCE_ITEMS))
        object.__setattr__(self, "max_item_chars", _clamp(self.max_item_chars, MAX_ITEM_CHARS))
        object.__setattr__(self, "max_total_chars", _clamp(self.max_total_chars, MAX_TOTAL_CONTEXT_CHARS))


DEFAULT_POLICY = ContextPolicy()
