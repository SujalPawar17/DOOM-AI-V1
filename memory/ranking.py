"""
DOOM V5.1 — Memory Ranker
Scores memory records for retrieval relevance.
Score = 0.4*relevance + 0.2*importance + 0.2*recency + 0.1*confidence + 0.1*project_match
All scores bounded [0.0, 1.0].
"""
import re
import time
from datetime import datetime, timezone
from typing import List, Optional

from memory.schemas import MemoryRecord, ScoredMemory
from memory.types import ConfidenceLevel, MemorySource


# Confidence weights for scoring
_CONFIDENCE_WEIGHTS = {
    ConfidenceLevel.HIGH:    1.0,
    ConfidenceLevel.MEDIUM:  0.6,
    ConfidenceLevel.LOW:     0.3,
    ConfidenceLevel.UNKNOWN: 0.1,
}

# Source quality weights (affects relevance scoring)
_SOURCE_WEIGHTS = {
    MemorySource.USER_EXPLICIT:      1.0,
    MemorySource.VERIFIED_TASK:      0.95,
    MemorySource.TOOL_RESULT:        0.75,
    MemorySource.SYSTEM_OBSERVATION: 0.65,
    MemorySource.USER_CONVERSATION:  0.60,
    MemorySource.IMPORTED_DATA:      0.55,
    MemorySource.DERIVED_CONTEXT:    0.35,
}

# Recency: how fast importance decays over time
# RECENCY_HALFLIFE_DAYS: after this many days a memory's recency score halves
RECENCY_HALFLIFE_DAYS: float = 30.0


class MemoryRanker:
    """
    Scores memory records against a query context.
    Higher scores indicate more relevant, recent, important, and confident memories.
    """

    def score(
        self,
        record: MemoryRecord,
        query: str,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> float:
        """
        Compute composite relevance score for a single MemoryRecord.
        Returns float in [0.0, 1.0].
        """
        relevance  = self._compute_relevance(record, query)
        importance = self._compute_importance(record)
        recency    = self._compute_recency(record)
        confidence = self._compute_confidence(record)
        proj_match = self._compute_project_match(record, project_id, task_id)

        score = (
            0.40 * relevance
            + 0.20 * importance
            + 0.20 * recency
            + 0.10 * confidence
            + 0.10 * proj_match
        )
        return min(max(score, 0.0), 1.0)

    def rank(
        self,
        records: List[MemoryRecord],
        query: str,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> List[ScoredMemory]:
        """
        Score and rank a list of MemoryRecords. Returns ScoredMemory list, highest first.
        """
        scored = [
            ScoredMemory(record=r, score=self.score(r, query, project_id, task_id))
            for r in records
        ]
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored

    # ------------------------------------------------------------------
    # Internal scoring components
    # ------------------------------------------------------------------

    def _compute_relevance(self, record: MemoryRecord, query: str) -> float:
        """
        Keyword relevance: how well does the memory content match the query?
        Uses term overlap. Weighted by source quality.
        """
        if not query:
            return 0.3  # Neutral relevance when no query

        query_terms = set(re.findall(r'\b\w{3,}\b', query.lower()))
        content_terms = set(re.findall(r'\b\w{3,}\b', record.content.lower()))
        tag_terms = set(t.lower() for t in (record.tags or []))

        if not query_terms:
            return 0.3

        # Term overlap between query and content
        content_overlap = len(query_terms & content_terms) / len(query_terms)
        # Bonus for tag matches
        tag_overlap = len(query_terms & tag_terms) / max(len(query_terms), 1)

        raw_relevance = min(content_overlap + 0.2 * tag_overlap, 1.0)

        # Boost by source quality weight
        source_weight = _SOURCE_WEIGHTS.get(record.source, 0.5)
        return raw_relevance * source_weight

    def _compute_importance(self, record: MemoryRecord) -> float:
        """Normalized importance (already 0.0–1.0)."""
        return max(0.0, min(float(record.importance), 1.0))

    def _compute_recency(self, record: MemoryRecord) -> float:
        """
        Exponential recency decay.
        1.0 = just created, approaches 0.0 for very old memories.
        """
        try:
            created = datetime.fromisoformat(record.created_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            age_days = (now - created).total_seconds() / 86400.0
            import math
            decay = math.exp(-age_days / RECENCY_HALFLIFE_DAYS)
            return max(0.0, min(decay, 1.0))
        except Exception:
            return 0.5  # Neutral if timestamp parsing fails

    def _compute_confidence(self, record: MemoryRecord) -> float:
        """Normalized confidence weight."""
        return _CONFIDENCE_WEIGHTS.get(record.confidence, 0.3)

    def _compute_project_match(
        self,
        record: MemoryRecord,
        project_id: Optional[str],
        task_id: Optional[str],
    ) -> float:
        """Boost for records matching the current project/task context."""
        score = 0.0
        if project_id and record.project_id and record.project_id.lower() == project_id.lower():
            score = 1.0
        elif task_id and record.task_id and record.task_id.lower() == task_id.lower():
            score = 0.8
        return score


memory_ranker = MemoryRanker()
