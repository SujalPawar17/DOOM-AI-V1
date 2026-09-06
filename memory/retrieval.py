"""
DOOM V5.1 — Memory Retriever
Converts a user query + context into a bounded, filtered, ranked MemoryContext.
This is the ONLY path through which memory is injected into cognition.
Does NOT return the entire memory database.
"""
import time
from typing import List, Optional

from memory.schemas import MemoryRecord, MemoryContext, ScoredMemory, SemanticMemoryMatch
from memory.types import (
    MemoryType, MemoryStatus, PrivacyClass,
    MAX_RETRIEVAL_RECORDS, RELEVANCE_THRESHOLD,
    SEMANTIC_SIMILARITY_THRESHOLD, MAX_SEMANTIC_CANDIDATES,
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
        enable_semantic: bool = True,
    ) -> MemoryContext:
        """
        Main retrieval entry point.
        Combines V5.1 lexical candidate retrieval with V5.2.3 semantic vector candidate retrieval.
        Applies policy filtering, deduplication, and bounds results safely.
        On any failure, returns an empty MemoryContext (never raises).
        """
        t_start = time.time()
        ctx = MemoryContext(query=query)

        try:
            from memory.repository import memory_repository
            from memory.ranking import memory_ranker
            from memory.context import memory_context_builder
            from memory.schemas import SemanticMemoryMatch, ScoredMemory
            from memory.types import (
                SEMANTIC_SIMILARITY_THRESHOLD,
                MAX_SEMANTIC_CANDIDATES,
            )

            # Handle empty query without filters
            if (not query or not query.strip()) and not memory_types and not project_id:
                ctx.retrieval_latency_ms = (time.time() - t_start) * 1000.0
                ctx.memory_hit = False
                ctx.memory_count = 0
                return ctx

            # ---- Phase 1: Fetch Lexical Candidates (V5.1) ----
            privacy_classes = [PrivacyClass.NORMAL]
            if include_private:
                privacy_classes.append(PrivacyClass.PRIVATE)
            # Never include SENSITIVE in automatic retrieval

            lexical_candidates: List[MemoryRecord] = []

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
                    lexical_candidates.extend(candidates)
            else:
                lexical_candidates = memory_repository.search(
                    query=None,
                    status=MemoryStatus.ACTIVE,
                    project_id=project_id,
                    privacy_classes=privacy_classes,
                    limit=self.MAX_CANDIDATE_RECORDS,
                )

            # Score lexical candidates
            scored_lexical: List[ScoredMemory] = []
            if lexical_candidates:
                scored_lexical = memory_ranker.rank(
                    lexical_candidates, query, project_id=project_id, task_id=task_id
                )

            # Filter lexical by RELEVANCE_THRESHOLD (require keyword relevance if text query provided)
            lexical_above_threshold = [
                s for s in scored_lexical
                if s.score >= RELEVANCE_THRESHOLD
                and (not query or not query.strip() or memory_ranker._compute_relevance(s.record, query) > 0.0)
            ]

            # ---- Phase 2: Fetch Semantic Candidates (V5.2.3) ----
            semantic_matches: List[SemanticMemoryMatch] = []
            semantic_scores: dict = {}

            if enable_semantic and query and query.strip():
                try:
                    from memory.embedding.router import embedding_router
                    from memory.vector_store import vector_store

                    # Generate query embedding
                    emb_res = embedding_router.embed(query, check_policy=True)
                    if emb_res is not None:
                        # Vector search (bounded to MAX_SEMANTIC_CANDIDATES = 25)
                        raw_matches = vector_store.search_similar(
                            query_vector=emb_res.vector,
                            top_k=MAX_SEMANTIC_CANDIDATES,
                            model=emb_res.model,
                            model_version=emb_res.model_version,
                        )

                        for m in raw_matches:
                            # Hard similarity threshold check (0.45)
                            if m.similarity < SEMANTIC_SIMILARITY_THRESHOLD:
                                continue

                            # Fetch parent record from repository
                            rec = memory_repository.get_by_id(m.memory_id)
                            if not rec:
                                continue

                            # Policy & security enforcement (defense-in-depth)
                            # 1. Must be ACTIVE (exclude DELETED, SUPERSEDED, ARCHIVED)
                            if rec.status != MemoryStatus.ACTIVE:
                                continue

                            # 2. Never allow SENSITIVE
                            if rec.privacy_class == PrivacyClass.SENSITIVE:
                                continue

                            # 3. Privacy level check
                            if rec.privacy_class == PrivacyClass.PRIVATE and not include_private:
                                continue

                            # 4. Project filter check
                            if project_id and rec.project_id and rec.project_id != project_id:
                                continue

                            # 5. Memory type filter check
                            if memory_types and rec.memory_type not in memory_types:
                                continue

                            match_obj = SemanticMemoryMatch(
                                record=rec,
                                similarity=m.similarity,
                                distance=m.distance,
                                model=m.model,
                                model_version=m.model_version,
                            )
                            semantic_matches.append(match_obj)
                            semantic_scores[rec.memory_id] = m.similarity

                except Exception as sem_e:
                    # Semantic failure is non-fatal: log notice, fallback to lexical
                    print(f"[MEMORY RETRIEVER] Semantic retrieval degraded gracefully: {sem_e}")
                    semantic_matches = []
                    semantic_scores = {}

            # ---- Phase 3: Deduplication & Candidate Merging ----
            # Combine lexical candidates and semantic matches by memory_id
            combined_records: dict = {}
            combined_scores: dict = {}

            for sl in lexical_above_threshold:
                mid = sl.record.memory_id
                combined_records[mid] = sl.record
                combined_scores[mid] = sl.score

            for sm in semantic_matches:
                mid = sm.record.memory_id
                combined_records[mid] = sm.record
                if mid in combined_scores:
                    # In V5.2.3: Preserve max score for deduplication (V5.2.4 will implement hybrid fusion)
                    combined_scores[mid] = max(combined_scores[mid], sm.similarity)
                else:
                    combined_scores[mid] = sm.similarity

            if not combined_records:
                ctx.retrieval_latency_ms = (time.time() - t_start) * 1000.0
                ctx.memory_hit = False
                ctx.memory_count = 0
                return ctx

            # Build final scored list sorted by composite score descending
            top_scored: List[ScoredMemory] = [
                ScoredMemory(record=combined_records[mid], score=combined_scores[mid])
                for mid in combined_records
            ]
            top_scored.sort(key=lambda x: x.score, reverse=True)
            top_scored = top_scored[:max_results]

            # Determine retrieval mode
            has_lex = len(lexical_above_threshold) > 0
            has_sem = len(semantic_matches) > 0
            if has_lex and has_sem:
                mode = "HYBRID"
            elif has_sem:
                mode = "SEMANTIC"
            else:
                mode = "LEXICAL"

            # ---- Phase 4: Build MemoryContext ----
            ctx = memory_context_builder.build(
                query=query,
                scored_memories=top_scored,
                semantic_matches=semantic_matches,
                semantic_scores=semantic_scores,
                retrieval_mode=mode,
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
