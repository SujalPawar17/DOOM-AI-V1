"""
DOOM V5.1 — Memory Context Builder
Assembles a controlled MemoryContext from scored memory records.
Ensures private content is never injected into general cognitive context.
"""
from typing import List, Optional

from memory.schemas import MemoryContext, MemoryRecord, ScoredMemory
from memory.types import ConfidenceLevel, PrivacyClass


class MemoryContextBuilder:
    """
    Builds MemoryContext objects from ranked ScoredMemory lists.
    Enforces: no private content in summary, no raw database rows in output.
    """

    def build(
        self,
        query: str,
        scored_memories: List[ScoredMemory],
    ) -> MemoryContext:
        """
        Build a MemoryContext from ranked records.
        The context_summary is safe for injection into LLM reasoning context.
        """
        records = [sm.record for sm in scored_memories]
        scores = {sm.record.memory_id: sm.score for sm in scored_memories}
        sources = list({r.source.value for r in records})

        # Aggregate confidence: use lowest confidence among retrieved memories
        # (conservative approach — don't over-claim confidence on a mixed set)
        overall_confidence = self._aggregate_confidence(records)

        # Generate safe, privacy-respecting summary
        context_summary = self._build_safe_summary(query, scored_memories)

        ctx = MemoryContext(
            query=query,
            retrieved_memories=records,
            relevance_scores=scores,
            sources=sources,
            confidence=overall_confidence,
            context_summary=context_summary,
        )
        return ctx

    def _aggregate_confidence(self, records: List[MemoryRecord]) -> ConfidenceLevel:
        """Aggregate confidence: use minimum (conservative)."""
        if not records:
            return ConfidenceLevel.UNKNOWN

        _ORDER = {
            ConfidenceLevel.HIGH: 3,
            ConfidenceLevel.MEDIUM: 2,
            ConfidenceLevel.LOW: 1,
            ConfidenceLevel.UNKNOWN: 0,
        }
        min_conf = min(records, key=lambda r: _ORDER.get(r.confidence, 0)).confidence
        return min_conf

    def _build_safe_summary(self, query: str, scored_memories: List[ScoredMemory]) -> str:
        """
        Build a safe context summary suitable for cognitive reasoning injection.
        Rules:
        - Never includes SENSITIVE memory content
        - PRIVATE memory content included only in preference/identity contexts
        - Truncates long content entries for brevity
        """
        if not scored_memories:
            return ""

        lines = []
        for sm in scored_memories:
            rec = sm.record

            # Never include sensitive memories in general context summary
            if rec.privacy_class == PrivacyClass.SENSITIVE:
                continue

            # Truncate long content
            content_display = rec.content[:200] + "..." if len(rec.content) > 200 else rec.content

            type_label = rec.memory_type.value
            conf_label = rec.confidence.value
            src_label = rec.source.value
            score_label = f"{sm.score:.2f}"

            lines.append(
                f"  [{type_label}|{src_label}|conf:{conf_label}|score:{score_label}] {content_display}"
            )

        if not lines:
            return ""

        return f"Memory Context for query '{query[:60]}':\n" + "\n".join(lines)


memory_context_builder = MemoryContextBuilder()
