"""
DOOM V5.2.5 — Memory Context Builder
Assembles a controlled, structurally fenced MemoryContext from scored memory records.
Ensures memory is treated strictly as UNTRUSTED DATA, never as executable instructions.
Enforces deterministic per-memory and total context budgeting.
"""
import logging
from typing import Dict, List, Optional

from memory.schemas import (
    MemoryContext,
    MemoryRecord,
    ScoredMemory,
    SemanticMemoryMatch,
    HybridScoreBreakdown,
)
from memory.types import ConfidenceLevel, PrivacyClass
from memory.fencing import (
    ContextBudgetConfig,
    DEFAULT_BUDGET_CONFIG,
    MemoryContextFencer,
    memory_context_fencer,
)

logger = logging.getLogger("DOOM.MemoryContextBuilder")


class MemoryContextBuilder:
    """
    Builds MemoryContext objects from ranked ScoredMemory lists.
    Enforces:
      - Structural [DATA_ONLY] fencing (V5.2.5)
      - Delimiter neutralization and control character stripping
      - Strict per-memory (<= 500 chars) and total (<= 4000 chars) context budgets
      - Conservative aggregated confidence
      - Fail-closed isolation on any construction failure
    """

    def __init__(self, config: Optional[ContextBudgetConfig] = None):
        self.config = config or DEFAULT_BUDGET_CONFIG
        self.fencer = MemoryContextFencer(self.config)

    def build(
        self,
        query: str,
        scored_memories: List[ScoredMemory],
        semantic_matches: Optional[List[SemanticMemoryMatch]] = None,
        semantic_scores: Optional[Dict[str, float]] = None,
        hybrid_breakdowns: Optional[Dict[str, HybridScoreBreakdown]] = None,
        retrieval_mode: str = "LEXICAL",
    ) -> MemoryContext:
        """
        Build a MemoryContext from ranked records with full V5.2.5 context fencing.
        Fails closed: returns safe empty context on unexpected exceptions.
        """
        try:
            # Defensive copy to avoid mutating caller inputs
            scored_copy = list(scored_memories) if scored_memories else []

            # Run canonical context fencing & budget enforcement
            fenced_res = self.fencer.fence_memories(query, scored_copy, self.config)

            records = fenced_res.included_memories
            scores = {sm.record.memory_id: sm.score for sm in scored_copy if sm.record in records}
            sources = list({r.source.value for r in records})

            # Aggregate confidence: use lowest confidence among included memories
            overall_confidence = self._aggregate_confidence(records)

            ctx = MemoryContext(
                query=query,
                retrieved_memories=records,
                relevance_scores=scores,
                sources=sources,
                confidence=overall_confidence,
                context_summary=fenced_res.context_summary,
                fenced_context=fenced_res.fenced_context,
                semantic_matches=semantic_matches or [],
                semantic_scores=semantic_scores or {},
                hybrid_breakdowns=hybrid_breakdowns or {},
                retrieval_mode=retrieval_mode,
                memory_count=len(records),
                memory_hit=len(records) > 0,
                fencing_applied=True,
                context_char_count=fenced_res.context_char_count,
                budget_exceeded=fenced_res.budget_exceeded,
                omitted_count=fenced_res.omitted_count,
            )
            return ctx

        except Exception as e:
            # Fail closed: never return raw unsanitized memory on failure
            logger.warning(f"[MEMORY CONTEXT] Context building failed safely (fail-closed): {e}")
            return MemoryContext(
                query=query,
                retrieved_memories=[],
                relevance_scores={},
                sources=[],
                confidence=ConfidenceLevel.UNKNOWN,
                context_summary="",
                fenced_context="",
                retrieval_mode=retrieval_mode,
                retrieval_latency_ms=0.0,
                memory_hit=False,
                memory_count=0,
                fencing_applied=True,
                context_char_count=0,
                budget_exceeded=False,
                omitted_count=len(scored_memories) if scored_memories else 0,
            )

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
        Backward compatibility delegation to canonical MemoryContextFencer.
        """
        res = self.fencer.fence_memories(query, scored_memories, self.config)
        return res.context_summary


memory_context_builder = MemoryContextBuilder()
