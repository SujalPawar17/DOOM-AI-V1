"""
DOOM V5.1 — Memory Retriever
Converts a user query + context into a bounded, filtered, ranked MemoryContext.
This is the ONLY path through which memory is injected into cognition.
Does NOT return the entire memory database.
"""
import time
from typing import List, Optional

from memory.schemas import MemoryRecord, MemoryContext, ScoredMemory
from memory.types import (
    MemoryType, MemoryStatus, PrivacyClass,
    MAX_RETRIEVAL_RECORDS, RELEVANCE_THRESHOLD,
)


class MemoryRetriever:
    """
    Retrieves only relevant memories for a given query + context.

    Hard rules:
    1. DELETED and SUPERSEDED memories are never returned.
    2. SENSITIVE memories are never returned in general retrieval.
    3. Total records fetched from DB is bounded (MAX_CANDIDATE_RECORDS).
    4. After scoring, only records above RELEVANCE_THRESHOLD are returned.
    5. Final output is capped at MAX_RETRIEVAL_RECORDS.
    6. Memory retrieval failure degrades gracefully (returns empty MemoryContext).
    """

    MAX_CANDIDATE_RECORDS: int = 50  # Max fetched before scoring

    def retrieve(
        self,
        query: str,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        memory_types: Optional[List[MemoryType]] = None,
        include_private: bool = False,
        max_results: int = MAX_RETRIEVAL_RECORDS,
    ) -> MemoryContext:
        """
        Main retrieval entry point.
        Returns a MemoryContext with scored, filtered, relevant records.
        On any failure, returns an empty MemoryContext (never raises).
        """
        t_start = time.time()
        ctx = MemoryContext(query=query)

        try:
            from memory.repository import memory_repository
            from memory.ranking import memory_ranker
            from memory.context import memory_context_builder

            # ---- Phase 1: Fetch candidates ----
            privacy_classes = [PrivacyClass.NORMAL]
            if include_private:
                privacy_classes.append(PrivacyClass.PRIVATE)
            # Never include SENSITIVE in automatic retrieval

            all_candidates: List[MemoryRecord] = []

            if memory_types:
                for mt in memory_types:
                    candidates = memory_repository.search(
                        query=None,  # Full-text filter applied after scoring
                        memory_type=mt,
                        status=MemoryStatus.ACTIVE,
                        project_id=project_id,
                        privacy_classes=privacy_classes,
                        limit=self.MAX_CANDIDATE_RECORDS // max(len(memory_types), 1),
                    )
                    all_candidates.extend(candidates)
            else:
                all_candidates = memory_repository.search(
                    query=None,
                    status=MemoryStatus.ACTIVE,
                    project_id=project_id,
                    privacy_classes=privacy_classes,
                    limit=self.MAX_CANDIDATE_RECORDS,
                )

            if not all_candidates:
                ctx.retrieval_latency_ms = (time.time() - t_start) * 1000.0
                ctx.memory_hit = False
                ctx.memory_count = 0
                return ctx

            # ---- Phase 2: Score & rank ----
            scored: List[ScoredMemory] = memory_ranker.rank(
                all_candidates, query, project_id=project_id, task_id=task_id
            )

            # ---- Phase 3: Hard relevance threshold ----
            above_threshold = [s for s in scored if s.score >= RELEVANCE_THRESHOLD]

            # ---- Phase 4: Cap at max_results ----
            top_scored = above_threshold[:max_results]

            # ---- Phase 5: Build MemoryContext ----
            ctx = memory_context_builder.build(
                query=query,
                scored_memories=top_scored,
            )
            ctx.retrieval_latency_ms = (time.time() - t_start) * 1000.0
            ctx.memory_hit = len(ctx.retrieved_memories) > 0
            ctx.memory_count = len(ctx.retrieved_memories)

            # Touch access timestamps (non-blocking, non-fatal)
            try:
                for sm in top_scored:
                    memory_repository.touch_accessed(sm.record.memory_id)
            except Exception:
                pass

            return ctx

        except Exception as e:
            print(f"[MEMORY RETRIEVER] Retrieval failed (degrading gracefully): {e}")
            ctx.retrieval_latency_ms = (time.time() - t_start) * 1000.0
            ctx.memory_hit = False
            ctx.memory_count = 0
            return ctx

    def retrieve_by_project(self, project_id: str, max_results: int = MAX_RETRIEVAL_RECORDS) -> MemoryContext:
        """Retrieve all relevant memories for a specific project."""
        return self.retrieve(
            query=project_id,
            project_id=project_id,
            memory_types=[MemoryType.PROJECT, MemoryType.EXPERIENCE, MemoryType.SEMANTIC],
            max_results=max_results,
        )

    def retrieve_preferences(self, query: str = "") -> MemoryContext:
        """Retrieve user preference memories (includes PRIVATE class)."""
        return self.retrieve(
            query=query,
            memory_types=[MemoryType.PREFERENCE],
            include_private=True,
            max_results=5,
        )

    def is_relevant(self, query: str, record: MemoryRecord) -> bool:
        """Quick relevance check for a single record against a query."""
        from memory.ranking import memory_ranker
        score = memory_ranker.score(record, query)
        return score >= RELEVANCE_THRESHOLD


memory_retriever = MemoryRetriever()
