"""
DOOM V5.1 — Memory Retriever
Converts a user query + context into a bounded, filtered, ranked MemoryContext.
This is the ONLY path through which memory is injected into cognition.
Does NOT return the entire memory database.
"""
import re
import time
from typing import Any, Dict, List, Optional, Tuple

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
                for c in memory_repository.search(
                    query=None,
                    status=MemoryStatus.ACTIVE,
                    project_id=project_id,
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
                    cands = memory_repository.search(
                        query=term,
                        status=[MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED],
                        privacy_classes=privacy_classes,
                        limit=MAX_LEXICAL_CANDIDATES,
                    )
                    sup_ids = []
                    for c in cands:
                        if c.status == MemoryStatus.ACTIVE:
                            raw_lexical_map[c.memory_id] = c
                        elif c.status == MemoryStatus.SUPERSEDED:
                            sup_ids.append(c.memory_id)

                    # V5.3.4: Check superseded records for active successors
                    if sup_ids:
                        try:
                            from memory.relationship_engine import relationship_engine
                            resolved_map = relationship_engine.resolve_lineage(sup_ids)
                            if resolved_map:
                                succ_records = memory_repository.get_by_ids(list(resolved_map.values()))
                                for old_id, succ_id in resolved_map.items():
                                    succ_rec = succ_records.get(succ_id)
                                    if succ_rec and succ_rec.status == MemoryStatus.ACTIVE:
                                        raw_lexical_map[succ_rec.memory_id] = succ_rec
                        except Exception:
                            pass

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
                        # Vector search with bounded over-fetching (Phase 20: 50 raw candidates)
                        raw_matches = vector_store.search_similar(
                            query_vector=emb_res.vector,
                            top_k=50,
                            model=emb_res.model,
                            model_version=emb_res.model_version,
                        )
                        valid_matches = [m for m in raw_matches if m.similarity >= SEMANTIC_SIMILARITY_THRESHOLD]
                        matched_ids = [m.memory_id for m in valid_matches]
                        records_by_id = memory_repository.get_by_ids(matched_ids) if hasattr(memory_repository, "get_by_ids") else {}

                        for m in valid_matches:
                            # Authoritative PostgreSQL validation (Phase 19)
                            rec = records_by_id.get(m.memory_id) if m.memory_id in records_by_id else memory_repository.get_by_id(m.memory_id)
                            if not rec:
                                # Orphan vector: exists in VectorStore but missing from PostgreSQL
                                try:
                                    from memory.sync_engine import vector_sync_engine, _emit_sync_telemetry
                                    _emit_sync_telemetry("VECTOR_ORPHAN_DETECTED", memory_id=m.memory_id)
                                    vector_sync_engine.schedule_deletion(m.memory_id)
                                except Exception:
                                    pass
                                continue

                            # 1. Must be ACTIVE (exclude DELETED, SUPERSEDED, ARCHIVED)
                            if rec.status != MemoryStatus.ACTIVE:
                                # V5.3.4: If superseded, attempt forward lineage resolution
                                if rec.status == MemoryStatus.SUPERSEDED:
                                    try:
                                        from memory.relationship_engine import relationship_engine
                                        resolved_map = relationship_engine.resolve_lineage([rec.memory_id])
                                        if rec.memory_id in resolved_map:
                                            succ_id = resolved_map[rec.memory_id]
                                            succ_rec = memory_repository.get_by_id(succ_id)
                                            if succ_rec and succ_rec.status == MemoryStatus.ACTIVE:
                                                rec = succ_rec
                                    except Exception:
                                        pass
                                if rec.status != MemoryStatus.ACTIVE:
                                    # Zombie vector: non-ACTIVE memory has vector
                                    try:
                                        from memory.sync_engine import vector_sync_engine, _emit_sync_telemetry
                                        _emit_sync_telemetry("VECTOR_ZOMBIE_DETECTED", memory_id=rec.memory_id, status=rec.status.value, generation=rec.generation)
                                        vector_sync_engine.schedule_deletion(rec.memory_id, generation=rec.generation)
                                    except Exception:
                                        pass
                                    continue

                            # 2. Never allow SENSITIVE
                            if rec.privacy_class == PrivacyClass.SENSITIVE:
                                try:
                                    from memory.sync_engine import vector_sync_engine
                                    vector_sync_engine.schedule_deletion(rec.memory_id, generation=rec.generation)
                                except Exception:
                                    pass
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

            # V5.3.4: Relationship Intelligence Resolution (Conflicts & Associations)
            try:
                retrieved_ids = [r.memory_id for r in ctx.retrieved_memories]
                if len(retrieved_ids) >= 2:
                    from database.postgres_db import postgres_manager
                    conn = postgres_manager.get_connection()
                    if conn:
                        try:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    SELECT source_memory_id, target_memory_id, confidence, reason
                                    FROM memory_relationships
                                    WHERE relationship_type = 'CONFLICTS_WITH'
                                      AND source_memory_id = ANY(%s)
                                      AND target_memory_id = ANY(%s);
                                """, (retrieved_ids, retrieved_ids))
                                c_rows = cur.fetchall()
                                for row in c_rows:
                                    ctx.conflicts.append({
                                        "source_memory_id": row[0],
                                        "target_memory_id": row[1],
                                        "confidence": float(row[2]),
                                        "reason": row[3],
                                    })
                        finally:
                            postgres_manager.release_connection(conn)
            except Exception:
                pass

            # Touch access timestamps (non-blocking, non-fatal)
            try:
                if scored_memories:
                    memory_repository.touch_accessed_batch([sm.record.memory_id for sm in scored_memories])
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

    # =========================================================================
    # V5.3.6 Project & Experience Intelligence Retrieval (Strictly Read-Only)
    # =========================================================================

    def retrieve_strategies(
        self,
        project_id: str,
        query: Optional[str] = None,
        max_results: int = 5,
        evaluate_transfers: bool = True,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves active and high-reliability execution strategies for project_id.
        Evaluates cross-project transfer eligibility for transferable strategies.
        Guarantees STRICT READ-ONLY behavior: zero database mutations.
        """
        if limit is not None:
            max_results = limit
        results: List[Dict[str, Any]] = []
        try:
            import json
            from database.postgres_db import postgres_manager
            from memory.project_engine import project_experience_engine
            from memory.project_models import TransferDecision

            conn = postgres_manager.get_connection()
            if not conn:
                return results

            try:
                with conn.cursor() as cur:
                    # 1. Fetch project-local active strategies
                    cur.execute("""
                        SELECT strategy_id, name, intent_category, procedure_template,
                               recommended_tools, environmental_preconditions,
                               reliability_score, successful_attempts, failed_attempts
                        FROM strategies
                        WHERE is_deprecated = FALSE
                          AND (intent_category = %s OR intent_category = 'general')
                        ORDER BY reliability_score DESC
                        LIMIT %s;
                    """, (project_id, max_results * 2))
                    local_rows = cur.fetchall()

                    for r in local_rows:
                        proc = r[3] if isinstance(r[3], dict) else (json.loads(r[3]) if r[3] else {})
                        results.append({
                            "strategy_id": r[0],
                            "name": r[1],
                            "description": proc.get("description", ""),
                            "applicable_project_id": r[2],
                            "scope": proc.get("scope", "PROJECT_LOCAL"),
                            "prerequisites": r[4] if isinstance(r[4], list) else (json.loads(r[4]) if r[4] else []),
                            "environmental_conditions": r[5] if isinstance(r[5], dict) else (json.loads(r[5]) if r[5] else {}),
                            "reliability_score": float(r[6]),
                            "success_count": r[7],
                            "failure_count": r[8],
                            "is_transferred": False,
                        })

                    # 2. Query pre-authorized transferable strategies (STRICTLY READ-ONLY)
                    if evaluate_transfers:
                        cur.execute("""
                            SELECT s.strategy_id, s.name, s.intent_category, s.procedure_template,
                                   s.recommended_tools, s.environmental_preconditions,
                                   s.reliability_score, s.successful_attempts, s.failed_attempts,
                                   m.transfer_confidence, m.status, m.source_project_id
                            FROM strategies s
                            JOIN project_transfer_matrix m ON s.strategy_id = m.strategy_id
                            JOIN projects p_src ON m.source_project_id = p_src.project_id
                            JOIN projects p_tgt ON m.target_project_id = p_tgt.project_id
                            WHERE m.target_project_id = %s
                              AND m.status = 'APPROVED'
                              AND s.is_deprecated = FALSE
                              AND p_src.privacy_class != 'SENSITIVE'
                              AND (p_src.privacy_class != 'PRIVATE' OR p_tgt.privacy_class IN ('PRIVATE', 'SENSITIVE'))
                            ORDER BY s.reliability_score DESC
                            LIMIT %s;
                        """, (project_id, max_results))
                        cand_rows = cur.fetchall()

                        for cr in cand_rows:
                            proc = cr[3] if isinstance(cr[3], dict) else (json.loads(cr[3]) if cr[3] else {})
                            results.append({
                                "strategy_id": cr[0],
                                "name": cr[1],
                                "description": proc.get("description", ""),
                                "applicable_project_id": cr[2],
                                "scope": proc.get("scope", "CROSS_PROJECT_ELIGIBLE"),
                                "prerequisites": cr[4] if isinstance(cr[4], list) else (json.loads(cr[4]) if cr[4] else []),
                                "environmental_conditions": cr[5] if isinstance(cr[5], dict) else (json.loads(cr[5]) if cr[5] else {}),
                                "reliability_score": float(cr[6]),
                                "success_count": cr[7],
                                "failure_count": cr[8],
                                "is_transferred": True,
                                "transfer_confidence": float(cr[9]),
                                "transfer_decision": "ALLOWED",
                                "source_project_id": cr[11],
                            })

            finally:
                postgres_manager.release_connection(conn)

            # Sort combined results by reliability score
            results.sort(key=lambda x: x.get("reliability_score", 0.0), reverse=True)
            try:
                from memory.conflict_engine import ConflictEngine
                resolved = ConflictEngine.resolve_conflicts(results, target_project_id=project_id)
                return resolved[:max_results]
            except Exception:
                return results[:max_results]

        except Exception as e:
            return []

    def retrieve_negative_experiences(
        self,
        project_id: str,
        error_signature: Optional[str] = None,
        max_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves negative experiences ("What NOT to do") for project_id.
        Strictly READ-ONLY.
        """
        warnings: List[Dict[str, Any]] = []
        try:
            import json
            from database.postgres_db import postgres_manager
            conn = postgres_manager.get_connection()
            if not conn:
                return warnings

            try:
                with conn.cursor() as cur:
                    if error_signature:
                        cur.execute("""
                            SELECT experience_id, task_id, outcome_status, error_signature,
                                   root_cause_analysis, context_conditions, confidence_score
                            FROM experiences
                            WHERE project_id = %s
                              AND outcome_status IN ('FAILURE', 'ABORTED')
                              AND error_signature = %s
                            ORDER BY created_at DESC
                            LIMIT %s;
                        """, (project_id, error_signature, max_results))
                    else:
                        cur.execute("""
                            SELECT experience_id, task_id, outcome_status, error_signature,
                                   root_cause_analysis, context_conditions, confidence_score
                            FROM experiences
                            WHERE project_id = %s
                              AND outcome_status IN ('FAILURE', 'ABORTED')
                            ORDER BY created_at DESC
                            LIMIT %s;
                        """, (project_id, max_results))

                    rows = cur.fetchall()
                    for r in rows:
                        ctx_dict = r[5] if isinstance(r[5], dict) else (json.loads(r[5]) if r[5] else {})
                        warnings.append({
                            "experience_id": r[0],
                            "task_id": r[1],
                            "outcome": r[2],
                            "error_signature": r[3] or "GENERAL_FAILURE",
                            "failure_reason": r[4] or "Unspecified execution failure",
                            "conditions": ctx_dict.get("conditions", {}),
                            "avoidance_recommendation": f"Avoid configuration leading to {r[3] or 'failure'}",
                            "confidence": float(r[6]) if r[6] is not None else 0.8,
                        })
            finally:
                postgres_manager.release_connection(conn)

            return warnings[:max_results]

        except Exception:
            return []



memory_retriever = MemoryRetriever()
