"""
DOOM V5.1 — Memory Ranker
Scores memory records for retrieval relevance.
Score = 0.4*relevance + 0.2*importance + 0.2*recency + 0.1*confidence + 0.1*project_match
All scores bounded [0.0, 1.0].
"""
import math
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

from memory.schemas import (
    MemoryRecord,
    ScoredMemory,
    HybridScoreBreakdown,
    HybridRankedMemory,
)
from memory.types import (
    ConfidenceLevel,
    MemorySource,
    VerificationStatus,
    HybridRankingWeights,
    DEFAULT_HYBRID_WEIGHTS,
    RECENCY_HALFLIFE_DAYS,
)


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

    # ------------------------------------------------------------------
    # V5.2.4 Pure Factor Calculators (Zero double-counting)
    # ------------------------------------------------------------------

    def compute_lexical_score(self, record: MemoryRecord, query: str) -> float:
        """
        Compute pure keyword relevance score (S_lex) in [0.0, 1.0].
        Does NOT mix importance, recency, confidence, or project match.
        """
        if not query or not query.strip():
            return 0.0
        relevance = self._compute_relevance(record, query)
        return max(0.0, min(float(relevance), 1.0))

    def compute_importance_score(self, record: MemoryRecord) -> float:
        """
        Compute pure importance score (S_imp) in [0.0, 1.0].
        Defaults to 0.5 if None or unparseable.
        """
        try:
            val = float(record.importance) if record.importance is not None else 0.5
            if math.isnan(val) or math.isinf(val):
                return 0.5
            return max(0.0, min(val, 1.0))
        except Exception:
            return 0.5

    def compute_recency_score(self, record: MemoryRecord, halflife_days: float = RECENCY_HALFLIFE_DAYS) -> float:
        """
        Compute pure recency score (S_rec) in [0.0, 1.0] using exponential half-life decay.
        S_rec = exp(-delta_days / tau), where tau = halflife_days / ln(2).
        Strictly a ranking factor — never mutates, archives, or expires memory.
        """
        if not record.created_at:
            return 0.5
        try:
            created_str = record.created_at.replace("Z", "+00:00")
            created = datetime.fromisoformat(created_str)
            now = datetime.now(timezone.utc)
            delta_seconds = (now - created).total_seconds()
            if delta_seconds <= 0.0:
                # Future timestamp or clock anomaly: clamp age to 0 -> score 1.0
                return 1.0
            delta_days = delta_seconds / 86400.0
            tau = float(halflife_days) / math.log(2.0)
            decay = math.exp(-delta_days / tau)
            return max(0.0, min(decay, 1.0))
        except Exception:
            return 0.5

    def compute_confidence_score(self, record: MemoryRecord) -> float:
        """
        Compute pure confidence score (S_conf) in [0.0, 1.0].
        If verification_status is CONTRADICTED, clamps to 0.0.
        """
        if getattr(record, "verification_status", None) == VerificationStatus.CONTRADICTED:
            return 0.0
        conf = getattr(record, "confidence", None) or ConfidenceLevel.UNKNOWN
        weight = _CONFIDENCE_WEIGHTS.get(conf, 0.1)
        return max(0.0, min(float(weight), 1.0))

    def compute_project_score(
        self,
        record: MemoryRecord,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> float:
        """
        Compute project relevance score (S_proj) in [0.0, 1.0].
        Semantics:
          - Exact project match: 1.0
          - Task match: 0.8
          - Current project exists, memory is global: 0.5
          - No current project, memory is project-specific: 0.5
          - No current project, memory is global: 1.0
          - Explicit cross-project: 0.0
        """
        rec_proj = record.project_id.strip() if record.project_id else None
        curr_proj = project_id.strip() if project_id else None
        rec_task = record.task_id.strip() if record.task_id else None
        curr_task = task_id.strip() if task_id else None

        if curr_proj:
            if rec_proj and rec_proj.lower() == curr_proj.lower():
                return 1.0
            if curr_task and rec_task and rec_task.lower() == curr_task.lower():
                return 0.8
            if not rec_proj:
                return 0.5  # Global memory in project context
            return 0.0     # Explicit cross-project
        else:
            # General / no project context
            if not rec_proj:
                return 1.0  # General memory in general context
            return 0.5      # Project-specific memory in general context

    def score_hybrid(
        self,
        record: MemoryRecord,
        lexical_score: float,
        semantic_score: float,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        weights: Optional[HybridRankingWeights] = None,
    ) -> Tuple[float, HybridScoreBreakdown]:
        """
        Compute the V5.2.4 six-factor composite score and breakdown for a candidate.
        Final Score = w_lex*S_lex + w_sem*S_sem + w_imp*S_imp + w_rec*S_rec + w_conf*S_conf + w_proj*S_proj
        """
        w = weights or DEFAULT_HYBRID_WEIGHTS
        w.validate()

        s_lex = max(0.0, min(float(lexical_score), 1.0))
        s_sem = max(0.0, min(float(semantic_score), 1.0))
        s_imp = self.compute_importance_score(record)
        s_rec = self.compute_recency_score(record)
        s_conf = self.compute_confidence_score(record)
        s_proj = self.compute_project_score(record, project_id, task_id)

        final_score = (
            w.weight_lexical * s_lex
            + w.weight_semantic * s_sem
            + w.weight_importance * s_imp
            + w.weight_recency * s_rec
            + w.weight_confidence * s_conf
            + w.weight_project * s_proj
        )
        final_score = max(0.0, min(final_score, 1.0))

        breakdown = HybridScoreBreakdown(
            lexical_score=s_lex,
            semantic_score=s_sem,
            importance_score=s_imp,
            recency_score=s_rec,
            confidence_score=s_conf,
            project_score=s_proj,
            final_score=final_score,
        )
        return final_score, breakdown

    def rank_hybrid(
        self,
        candidates: List[Tuple[MemoryRecord, float, float]],
        query: str,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        weights: Optional[HybridRankingWeights] = None,
    ) -> List[HybridRankedMemory]:
        """
        Score and deterministically rank a list of hybrid candidates: (record, lexical_score, semantic_score).
        Deduplication tie-breaker:
          1. final_score DESC (round to 6 decimals)
          2. importance_score DESC (round to 4 decimals)
          3. recency_score DESC (round to 4 decimals)
          4. memory_id ASC (lexicographical)
        """
        w = weights or DEFAULT_HYBRID_WEIGHTS
        ranked_list: List[HybridRankedMemory] = []

        for item in candidates:
            rec, lex_s, sem_s = item
            score, breakdown = self.score_hybrid(
                record=rec,
                lexical_score=lex_s,
                semantic_score=sem_s,
                project_id=project_id,
                task_id=task_id,
                weights=w,
            )
            ranked_list.append(HybridRankedMemory(record=rec, score=score, breakdown=breakdown))

        # Deterministic multi-level sorting
        ranked_list.sort(
            key=lambda x: (
                round(-x.score, 6),
                round(-x.breakdown.importance_score, 4),
                round(-x.breakdown.recency_score, 4),
                x.record.memory_id,
            )
        )
        return ranked_list


memory_ranker = MemoryRanker()
