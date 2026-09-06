"""
DOOM V5.1 — Memory Retriever
Converts a user query + context into a bounded, filtered, ranked MemoryContext.
This is the ONLY path through which memory is injected into cognition.
Does NOT return the entire memory database.
"""
import re
import time
from typing import Dict, List, Optional, Tuple

from memory.schemas import (
    MemoryRecord,
    MemoryContext,
    ScoredMemory,
    SemanticMemoryMatch,
    HybridScoreBreakdown,
    HybridRankedMemory,
)
from memory.types import (
    MemoryType,
    MemoryStatus,
    PrivacyClass,
    MAX_RETRIEVAL_RECORDS,
    RELEVANCE_THRESHOLD,
    SEMANTIC_SIMILARITY_THRESHOLD,
    MAX_SEMANTIC_CANDIDATES,
    MAX_LEXICAL_CANDIDATES,
    MAX_MERGED_CANDIDATES,
    HybridRankingWeights,
    DEFAULT_HYBRID_WEIGHTS,
)


class MemoryRetriever:
    """
    Retrieves only relevant memories for a given query + context.

    Hard rules:
    1. DELETED and SUPERSEDED memories are never returned.
    2. SENSITIVE memories are never returned in general retrieval.
    3. Total records fetched from DB is bounded (MAX_LEXICAL_CANDIDATES).
    4. Policy filtering occurs BEFORE ranking.
    5. Final output is capped at MAX_RETRIEVAL_RECORDS (10).
    6. Memory retrieval failure degrades gracefully (returns empty MemoryContext).
    """

    MAX_CANDIDATE_RECORDS: int = MAX_LEXICAL_CANDIDATES  # Max lexical fetched before scoring (25)

    def retrieve(
        self,
        query: str,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        memory_types: Optional[List[MemoryType]] = None,
        include_private: bool = False,
        max_results: int = MAX_RETRIEVAL_RECORDS,
        enable_semantic: bool = True,
        weights: Optional[HybridRankingWeights] = None,
    ) -> MemoryContext:
        """
        Main retrieval entry point.
        Combines V5.1 lexical candidate retrieval with V5.2.3 semantic vector candidate retrieval
        and scores via V5.2.4 six-factor hybrid ranking engine.
        Applies policy filtering, deduplication, and bounds results safely.
        On any failure, returns an empty MemoryContext (never raises).
        """
        t_start = time.time()
        ctx = MemoryContext(query=query)

        try:
            from memory.repository import memory_repository
            from memory.ranking import memory_ranker
            from memory.context import memory_context_builder

            # Handle empty query without filters
            if (not query or not query.strip()) and not memory_types and not project_id:
                ctx.retrieval_latency_ms = (time.time() - t_start) * 1000.0
                ctx.memory_hit = False
                ctx.memory_count = 0
                return ctx

            # ---- Phase 1: Fetch Lexical Candidates (V5.1 + V5.2.4 Bounded) ----
            privacy_classes = [PrivacyClass.NORMAL]
            if include_private:
                privacy_classes.append(PrivacyClass.PRIVATE)
            # Never include SENSITIVE in automatic retrieval

            raw_lexical_map: Dict[str, MemoryRecord] = {}

            if memory_types:
                for mt in memory_types:
                    cands = memory_repository.search(
                        query=None,
                        memory_type=mt,
                        status=MemoryStatus.ACTIVE,
                        privacy_classes=privacy_classes,
                        limit=MAX_LEXICAL_CANDIDATES // max(len(memory_types), 1),
                    )
                    for c in cands:
                        raw_lexical_map[c.memory_id] = c
            else:
                if project_id:
                    for c in memory_repository.search(
                        query=None,
                        status=MemoryStatus.ACTIVE,
                        project_id=project_id,
                        privacy_classes=privacy_classes,
                        limit=MAX_LEXICAL_CANDIDATES,
                    ):
                        raw_lexical_map[c.memory_id] = c
                for c in memory_repository.search(
                    query=None,
                    status=MemoryStatus.ACTIVE,
                    privacy_classes=privacy_classes,
                    limit=MAX_LEXICAL_CANDIDATES,
                ):
                    raw_lexical_map[c.memory_id] = c

            # Exact keyword match preservation: if text query present, search by keywords so old/low-importance matches are not displaced
            if query and query.strip():
                raw_words = re.findall(r'\b\w{3,}\b', query.strip())
                stopwords = {"what", "which", "where", "when", "that", "this", "with", "from", "have", "been", "about"}
                significant_words = [w for w in raw_words if w.lower() not in stopwords]
                search_terms = significant_words[:3] if significant_words else raw_words[:2]
                for term in search_terms:
                    kw_cands = memory_repository.search(
                        query=term,
                        status=MemoryStatus.ACTIVE,
                        privacy_classes=privacy_classes,
                        limit=MAX_LEXICAL_CANDIDATES,
                    )
                    for c in kw_cands:
                        raw_lexical_map[c.memory_id] = c

            # Score lexical candidates using pure lexical score (S_lex) to prevent double counting
            lexical_candidates: List[Tuple[MemoryRecord, float]] = []
            for r in raw_lexical_map.values():
                # Policy & security enforcement (defense-in-depth BEFORE ranking)
                if r.status != MemoryStatus.ACTIVE:
                    continue
                if r.privacy_class == PrivacyClass.SENSITIVE:
                    continue
                if r.privacy_class == PrivacyClass.PRIVATE and not include_private:
                    continue
                if project_id and r.project_id and r.project_id != project_id:
                    continue
                if memory_types and r.memory_type not in memory_types:
                    continue

                s_lex = memory_ranker.compute_lexical_score(r, query)
                if query and query.strip():
                    if s_lex > 0.0:
                        lexical_candidates.append((r, s_lex))
                else:
                    lexical_candidates.append((r, s_lex))

            # Bounded to MAX_LEXICAL_CANDIDATES (25), ordered by pure lexical score DESC
            lexical_candidates.sort(key=lambda x: x[1], reverse=True)
            lexical_candidates = lexical_candidates[:MAX_LEXICAL_CANDIDATES]

            # ---- Phase 2: Fetch Semantic Candidates (V5.2.3) ----
            semantic_matches: List[SemanticMemoryMatch] = []
            semantic_candidates: Dict[str, Tuple[MemoryRecord, float]] = {}

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
                            # Hard similarity threshold check (0.40)
                            if m.similarity < SEMANTIC_SIMILARITY_THRESHOLD:
                                continue

                            # Fetch parent record from repository
                            rec = memory_repository.get_by_id(m.memory_id)
                            if not rec:
                                continue

                            # Policy & security enforcement (defense-in-depth BEFORE ranking)
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
                            semantic_candidates[rec.memory_id] = (rec, float(m.similarity))

                except Exception as sem_e:
                    # Semantic failure is non-fatal: log notice, fallback to lexical
                    print(f"[MEMORY RETRIEVER] Semantic retrieval degraded gracefully: {sem_e}")
                    semantic_matches = []
                    semantic_candidates = {}

            # ---- Phase 3: Deduplication & Candidate Merging ----
            # Combine lexical candidates and semantic matches by memory_id
            # Mapping: memory_id -> (MemoryRecord, lexical_score, semantic_score)
            candidate_map: Dict[str, Tuple[MemoryRecord, float, float]] = {}

            for rec, lex_s in lexical_candidates:
                candidate_map[rec.memory_id] = (rec, float(lex_s), 0.0)

            for mid, (rec, sem_s) in semantic_candidates.items():
                if mid in candidate_map:
                    existing_rec, existing_lex, _ = candidate_map[mid]
                    candidate_map[mid] = (existing_rec, existing_lex, float(sem_s))
                else:
                    candidate_map[mid] = (rec, 0.0, float(sem_s))

            # Bound merged candidate pool to MAX_MERGED_CANDIDATES (50)
            merged_candidate_list: List[Tuple[MemoryRecord, float, float]] = list(candidate_map.values())[:MAX_MERGED_CANDIDATES]

            if not merged_candidate_list:
                ctx.retrieval_latency_ms = (time.time() - t_start) * 1000.0
                ctx.memory_hit = False
                ctx.memory_count = 0
                return ctx

            # ---- Phase 4: Six-Factor Hybrid Ranking & Deterministic Sorting ----
            hybrid_breakdowns: Dict[str, HybridScoreBreakdown] = {}
            scored_memories: List[ScoredMemory] = []

            try:
                ranked_hybrid = memory_ranker.rank_hybrid(
                    candidates=merged_candidate_list,
                    query=query,
                    project_id=project_id,
                    task_id=task_id,
                    weights=weights,
                )
                for hrm in ranked_hybrid:
                    if hrm.breakdown:
                        hybrid_breakdowns[hrm.record.memory_id] = hrm.breakdown

                top_ranked = ranked_hybrid[:max_results]
                scored_memories = [
                    ScoredMemory(record=hrm.record, score=hrm.score)
                    for hrm in top_ranked
                ]
            except Exception as rank_err:
                # Non-fatal ranking fallback
                print(f"[MEMORY RETRIEVER] Hybrid ranking failed, executing safe fallback: {rank_err}")
                if semantic_candidates:
                    fb_scored = [
                        ScoredMemory(record=rec, score=sem_s)
                        for rec, sem_s in semantic_candidates.values()
                    ]
                    fb_scored.sort(key=lambda x: x.score, reverse=True)
                    scored_memories = fb_scored[:max_results]
                elif lexical_candidates:
                    fb_scored = [
                        ScoredMemory(record=rec, score=lex_s)
                        for rec, lex_s in lexical_candidates
                    ]
                    fb_scored.sort(key=lambda x: x.score, reverse=True)
                    scored_memories = fb_scored[:max_results]
                else:
                    scored_memories = []

            # Determine retrieval mode
            has_lex = len(lexical_candidates) > 0
            has_sem = len(semantic_matches) > 0
            if has_lex and has_sem:
                mode = "HYBRID"
            elif has_sem:
                mode = "SEMANTIC"
            elif has_lex:
                mode = "LEXICAL"
            else:
                mode = "DEGRADED"

            # ---- Phase 5: Build MemoryContext ----
            ctx = memory_context_builder.build(
                query=query,
                scored_memories=scored_memories,
                semantic_matches=semantic_matches,
                semantic_scores={mid: sem_s for mid, (_, sem_s) in semantic_candidates.items()},
                hybrid_breakdowns=hybrid_breakdowns,
                retrieval_mode=mode,
            )
            ctx.retrieval_latency_ms = (time.time() - t_start) * 1000.0
            ctx.memory_hit = len(ctx.retrieved_memories) > 0
            ctx.memory_count = len(ctx.retrieved_memories)

            # Touch access timestamps (non-blocking, non-fatal)
            try:
                for sm in scored_memories:
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
