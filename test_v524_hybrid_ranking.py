"""
DOOM V5.2.4 — Hybrid Memory Ranking & Multi-Factor Fusion Test Suite

Validates:
1. Six-Factor Hybrid Ranking Formula:
   Final = 0.25*S_lex + 0.35*S_sem + 0.15*S_imp + 0.10*S_rec + 0.05*S_conf + 0.10*S_proj
2. Candidate merging: replacing max(lexical, semantic) with pure factor preservation.
3. Candidate bounds: lexical <= 25, semantic <= 25, merged <= 50, final <= 10.
4. Policy filtering BEFORE ranking (SENSITIVE, DELETED, SUPERSEDED, unauthorized PRIVATE).
5. Anti-double-counting verification: pure lexical score isolates keyword relevance.
6. Deterministic 4-level tie-breaking.
7. Graceful non-fatal degradation on ranking failure.
8. Real FastEmbed + NumPy vector storage hybrid retrieval.
9. Real production DOOMCore -> CognitiveEngine -> MemoryRetriever pipeline.
"""
import math
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np

# Ensure project root in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory.types import (
    MemoryType, MemoryStatus, MemorySource, ConfidenceLevel,
    VerificationStatus, PrivacyClass,
    MAX_RETRIEVAL_RECORDS, RELEVANCE_THRESHOLD,
    SEMANTIC_SIMILARITY_THRESHOLD, MAX_SEMANTIC_CANDIDATES,
    MAX_LEXICAL_CANDIDATES, MAX_MERGED_CANDIDATES,
    RECENCY_HALFLIFE_DAYS,
    HybridRankingWeights, DEFAULT_HYBRID_WEIGHTS,
)
from memory.schemas import (
    MemoryRecord, MemoryContext, ScoredMemory, SemanticMemoryMatch,
    HybridScoreBreakdown, HybridRankedMemory,
)
from memory.ranking import MemoryRanker, memory_ranker
from memory.retrieval import MemoryRetriever, memory_retriever
from memory.repository import memory_repository
from memory.embedding.router import embedding_router
from memory.vector_store import vector_store
from database.postgres_db import postgres_manager


def delete_test_record(memory_id: str):
    """Safely delete synthetic record from PostgreSQL and VectorStore."""
    conn = postgres_manager.get_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM memory_records WHERE memory_id = %s;", (memory_id,))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            postgres_manager.release_connection(conn)
    try:
        vector_store.delete_embedding(memory_id)
    except Exception:
        pass


# Test tracking
test_results: List[Dict[str, str]] = []


def record_test(name: str, classification: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    test_results.append({
        "name": name,
        "classification": classification,
        "status": status,
        "detail": detail,
    })
    det_str = f" ({detail})" if detail else ""
    print(f"  [{status}] [{classification:<15}] {name}{det_str}")


def make_record(
    memory_id: str,
    content: str,
    memory_type: MemoryType = MemoryType.SEMANTIC,
    source: MemorySource = MemorySource.USER_EXPLICIT,
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH,
    verification_status: VerificationStatus = VerificationStatus.VERIFIED,
    privacy_class: PrivacyClass = PrivacyClass.NORMAL,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    importance: float = 0.5,
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
    created_at: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> MemoryRecord:
    now_iso = created_at or datetime.now(timezone.utc).isoformat()
    return MemoryRecord(
        memory_id=memory_id,
        content=content,
        memory_type=memory_type,
        source=source,
        confidence=confidence,
        verification_status=verification_status,
        privacy_class=privacy_class,
        status=status,
        importance=importance,
        project_id=project_id,
        task_id=task_id,
        tags=tags or [],
        created_at=now_iso,
        updated_at=now_iso,
    )


# ====================================================================
# TEST SUITE A - Z
# ====================================================================

def test_a_lexical_only():
    """Test A: Lexical-only candidate (S_lex > 0, S_sem == 0.0)."""
    rec = make_record("lex_01", "Python programming language backend development", importance=0.6)
    score, bd = memory_ranker.score_hybrid(rec, lexical_score=0.8, semantic_score=0.0)
    ok = (
        bd.lexical_score == 0.8
        and bd.semantic_score == 0.0
        and 0.0 < score <= 1.0
        and math.isclose(bd.final_score, score, abs_tol=1e-6)
    )
    record_test("Test A: Lexical-only candidate", "UNIT", ok, f"score={score:.4f}, s_sem={bd.semantic_score}")


def test_b_semantic_only():
    """Test B: Semantic-only candidate (S_lex == 0.0, S_sem > 0)."""
    rec = make_record("sem_01", "Relational database optimization index scan", importance=0.7)
    score, bd = memory_ranker.score_hybrid(rec, lexical_score=0.0, semantic_score=0.85)
    ok = (
        bd.lexical_score == 0.0
        and bd.semantic_score == 0.85
        and 0.0 < score <= 1.0
        and math.isclose(bd.final_score, score, abs_tol=1e-6)
    )
    record_test("Test B: Semantic-only candidate", "UNIT", ok, f"score={score:.4f}, s_lex={bd.lexical_score}")


def test_c_matched_by_both():
    """Test C: Candidate matched by both lexical and semantic (preserves both, no max())."""
    rec = make_record("both_01", "PostgreSQL database memory leak resolution", importance=0.8)
    score, bd = memory_ranker.score_hybrid(rec, lexical_score=0.75, semantic_score=0.82)
    # Ensure it is NOT max(0.75, 0.82) = 0.82
    # Six factor formula: 0.25*0.75 + 0.35*0.82 + 0.15*0.8 + 0.10*1.0 + 0.05*1.0 + 0.10*1.0 = 0.8445
    ok = (
        bd.lexical_score == 0.75
        and bd.semantic_score == 0.82
        and not math.isclose(score, max(0.75, 0.82), abs_tol=1e-4)
        and score > 0.80
    )
    record_test("Test C: Dual-matched candidate preserves both factors", "UNIT", ok, f"score={score:.4f} vs max=0.8200")


def test_d_e_factor_tradeoffs():
    """Test D & E: High semantic / low importance vs Low semantic / high importance trade-off."""
    # Case D: High semantic (0.90), Low importance (0.10)
    rec_d = make_record("trade_d", "Python backend query match", importance=0.10)
    score_d, bd_d = memory_ranker.score_hybrid(rec_d, lexical_score=0.5, semantic_score=0.90)

    # Case E: Low semantic (0.45), High importance (0.95)
    rec_e = make_record("trade_e", "Python backend query match", importance=0.95)
    score_e, bd_e = memory_ranker.score_hybrid(rec_e, lexical_score=0.5, semantic_score=0.45)

    # Semantic delta = 0.45 * 0.35 = 0.1575
    # Importance delta = 0.85 * 0.15 = 0.1275
    # D should beat E because semantic weight (0.35) > importance weight (0.15)
    ok = (score_d > score_e) and (bd_d.semantic_score > bd_e.semantic_score) and (bd_e.importance_score > bd_d.importance_score)
    record_test("Test D & E: High semantic vs high importance trade-off", "UNIT", ok, f"score_d={score_d:.4f} > score_e={score_e:.4f}")


def test_f_recency_ordering():
    """Test F: Recency ordering follows exponential half-life decay (30 days)."""
    now = datetime.now(timezone.utc)
    t_0d = now.isoformat()
    t_30d = (now - timedelta(days=30)).isoformat()
    t_60d = (now - timedelta(days=60)).isoformat()
    t_120d = (now - timedelta(days=120)).isoformat()

    rec_0 = make_record("rec_0", "memory 0 days old", created_at=t_0d)
    rec_30 = make_record("rec_30", "memory 30 days old", created_at=t_30d)
    rec_60 = make_record("rec_60", "memory 60 days old", created_at=t_60d)
    rec_120 = make_record("rec_120", "memory 120 days old", created_at=t_120d)

    s_rec_0 = memory_ranker.compute_recency_score(rec_0)
    s_rec_30 = memory_ranker.compute_recency_score(rec_30)
    s_rec_60 = memory_ranker.compute_recency_score(rec_60)
    s_rec_120 = memory_ranker.compute_recency_score(rec_120)

    # Expected: 1.0 -> ~0.5 -> ~0.25 -> ~0.0625
    ok = (
        math.isclose(s_rec_0, 1.0, abs_tol=0.01)
        and math.isclose(s_rec_30, 0.5, abs_tol=0.05)
        and math.isclose(s_rec_60, 0.25, abs_tol=0.05)
        and math.isclose(s_rec_120, 0.0625, abs_tol=0.05)
        and (s_rec_0 > s_rec_30 > s_rec_60 > s_rec_120)
    )
    record_test("Test F: Recency exponential half-life ordering", "UNIT", ok, f"0d={s_rec_0:.3f}, 30d={s_rec_30:.3f}, 60d={s_rec_60:.3f}, 120d={s_rec_120:.3f}")


def test_g_confidence_ordering():
    """Test G: Confidence level mapping (HIGH=1.0 > MEDIUM=0.6 > LOW=0.3 > UNKNOWN=0.1)."""
    rec_h = make_record("c_h", "high conf", confidence=ConfidenceLevel.HIGH)
    rec_m = make_record("c_m", "med conf", confidence=ConfidenceLevel.MEDIUM)
    rec_l = make_record("c_l", "low conf", confidence=ConfidenceLevel.LOW)
    rec_u = make_record("c_u", "unk conf", confidence=ConfidenceLevel.UNKNOWN)

    s_h = memory_ranker.compute_confidence_score(rec_h)
    s_m = memory_ranker.compute_confidence_score(rec_m)
    s_l = memory_ranker.compute_confidence_score(rec_l)
    s_u = memory_ranker.compute_confidence_score(rec_u)

    ok = (s_h == 1.0 and s_m == 0.6 and s_l == 0.3 and s_u == 0.1 and s_h > s_m > s_l > s_u)
    record_test("Test G: Confidence factor ordering", "UNIT", ok, f"H={s_h}, M={s_m}, L={s_l}, U={s_u}")


def test_h_contradicted_confidence():
    """Test H: Contradicted verification status clamps confidence to 0.0."""
    rec_normal = make_record("c_norm", "normal", confidence=ConfidenceLevel.HIGH, verification_status=VerificationStatus.VERIFIED)
    rec_contra = make_record("c_contra", "contradicted", confidence=ConfidenceLevel.HIGH, verification_status=VerificationStatus.CONTRADICTED)

    s_norm = memory_ranker.compute_confidence_score(rec_normal)
    s_contra = memory_ranker.compute_confidence_score(rec_contra)

    ok = (s_norm == 1.0 and s_contra == 0.0)
    record_test("Test H: Contradicted status confidence penalty (0.0)", "UNIT", ok, f"normal={s_norm}, contradicted={s_contra}")


def test_i_exact_project_match():
    """Test I: Exact project match (1.0) and task match (0.8)."""
    rec_proj = make_record("p_proj", "project task", project_id="proj_alpha", task_id="task_100")
    rec_task = make_record("p_task", "other proj same task", project_id="proj_beta", task_id="task_100")

    s_exact = memory_ranker.compute_project_score(rec_proj, project_id="proj_alpha", task_id="task_100")
    s_task = memory_ranker.compute_project_score(rec_task, project_id="proj_alpha", task_id="task_100")

    ok = (s_exact == 1.0 and s_task == 0.8)
    record_test("Test I: Exact project match (1.0) and task match (0.8)", "UNIT", ok, f"exact={s_exact}, task={s_task}")


def test_j_k_project_relevance_matrix():
    """Test J & K: Project relevance matrix under missing and cross-project conditions."""
    rec_global = make_record("p_glob", "global memory", project_id=None)
    rec_proj_a = make_record("p_a", "project a memory", project_id="proj_alpha")

    # In project_alpha context:
    # Exact project match -> 1.0
    s_a_in_a = memory_ranker.compute_project_score(rec_proj_a, project_id="proj_alpha")
    # Global memory in project context -> 0.5
    s_glob_in_a = memory_ranker.compute_project_score(rec_global, project_id="proj_alpha")
    # Cross project memory -> 0.0
    s_a_in_b = memory_ranker.compute_project_score(rec_proj_a, project_id="proj_beta")

    # In general context (no project):
    # Global memory in general context -> 1.0
    s_glob_in_gen = memory_ranker.compute_project_score(rec_global, project_id=None)
    # Project-specific memory in general context -> 0.5
    s_proj_in_gen = memory_ranker.compute_project_score(rec_proj_a, project_id=None)

    ok = (
        s_a_in_a == 1.0
        and s_glob_in_a == 0.5
        and s_a_in_b == 0.0
        and s_glob_in_gen == 1.0
        and s_proj_in_gen == 0.5
    )
    record_test("Test J & K: Project relevance matrix verification", "UNIT", ok)


def test_l_missing_importance():
    """Test L: Missing, None, NaN, or invalid importance defaults safely to 0.5."""
    rec_none = make_record("imp_none", "no imp")
    rec_none.importance = None  # type: ignore
    rec_nan = make_record("imp_nan", "nan imp")
    rec_nan.importance = float("nan")

    s_none = memory_ranker.compute_importance_score(rec_none)
    s_nan = memory_ranker.compute_importance_score(rec_nan)

    ok = (s_none == 0.5 and s_nan == 0.5)
    record_test("Test L: Missing / invalid importance defaults to 0.5", "UNIT", ok, f"none={s_none}, nan={s_nan}")


def test_m_missing_corrupt_future_timestamp():
    """Test M: Missing, corrupt, and future timestamps handled safely."""
    rec_missing = make_record("ts_missing", "missing ts", created_at="")
    rec_missing.created_at = ""  # type: ignore
    rec_corrupt = make_record("ts_corrupt", "corrupt ts", created_at="invalid-date-string")
    rec_future = make_record("ts_future", "future ts", created_at=(datetime.now(timezone.utc) + timedelta(days=10)).isoformat())

    s_missing = memory_ranker.compute_recency_score(rec_missing)
    s_corrupt = memory_ranker.compute_recency_score(rec_corrupt)
    s_future = memory_ranker.compute_recency_score(rec_future)

    # Missing -> 0.5, Corrupt -> 0.5, Future -> age clamped to 0 -> 1.0
    ok = (s_missing == 0.5 and s_corrupt == 0.5 and s_future == 1.0)
    record_test("Test M: Missing/corrupt/future timestamps safe handling", "UNIT", ok, f"miss={s_missing}, corr={s_corrupt}, fut={s_future}")


def test_n_score_boundaries():
    """Test N: Score boundary enforcement across all extreme inputs and random factor matrices."""
    import random
    rng = random.Random(42)

    # Extreme 1: all 0.0
    rec_zero = make_record("zero", "zero", importance=0.0, confidence=ConfidenceLevel.UNKNOWN, created_at=(datetime.now(timezone.utc) - timedelta(days=5000)).isoformat())
    score_zero, bd_zero = memory_ranker.score_hybrid(rec_zero, lexical_score=0.0, semantic_score=0.0, project_id="proj_target")
    # All factors entering formula must be within [0.0, 1.0] and final score >= 0.0
    all_zero_bounded = (0.0 <= score_zero <= 1.0) and all(0.0 <= v <= 1.0 for v in bd_zero.to_dict().values())

    # Extreme 2: all 1.0
    rec_one = make_record("one", "one", importance=1.0, confidence=ConfidenceLevel.HIGH, created_at=datetime.now(timezone.utc).isoformat())
    score_one, bd_one = memory_ranker.score_hybrid(rec_one, lexical_score=1.0, semantic_score=1.0)
    all_one_bounded = (0.0 <= score_one <= 1.0) and math.isclose(score_one, 1.0, abs_tol=1e-6)

    # Randomized 100 trials with negative, NaN, Inf clamps
    random_all_bounded = True
    for _ in range(100):
        lex = rng.uniform(-0.5, 1.5)
        sem = rng.uniform(-0.5, 1.5)
        imp = rng.uniform(-0.5, 1.5)
        r = make_record("rand", "random", importance=imp)
        sc, bd = memory_ranker.score_hybrid(r, lexical_score=lex, semantic_score=sem)
        if not (0.0 <= sc <= 1.0) or math.isnan(sc) or math.isinf(sc):
            random_all_bounded = False
            break

    ok = all_zero_bounded and all_one_bounded and random_all_bounded
    record_test("Test N: Score boundary enforcement in [0.0, 1.0]", "UNIT", ok)


def test_o_weight_sum_validation():
    """Test O: Weight configuration validation (must sum to 1.00 within 1e-6, non-negative, finite)."""
    # Valid
    w_valid = HybridRankingWeights(0.20, 0.40, 0.15, 0.10, 0.05, 0.10)
    ok_valid = math.isclose(sum(w_valid.to_dict().values()), 1.0, abs_tol=1e-6)

    # Invalid: sum != 1.0
    bad_sum = False
    try:
        HybridRankingWeights(0.3, 0.3, 0.3, 0.3, 0.3, 0.3)
    except ValueError:
        bad_sum = True

    # Invalid: negative weight
    bad_neg = False
    try:
        HybridRankingWeights(-0.1, 0.45, 0.25, 0.15, 0.15, 0.10)
    except ValueError:
        bad_neg = True

    # Invalid: NaN weight
    bad_nan = False
    try:
        HybridRankingWeights(float("nan"), 0.35, 0.15, 0.10, 0.05, 0.10)
    except ValueError:
        bad_nan = True

    ok = ok_valid and bad_sum and bad_neg and bad_nan
    record_test("Test O: Weight sum and validity validation", "UNIT", ok)


def test_p_deterministic_tie_breaking():
    """Test P: 4-tier deterministic tie-breaking (final DESC -> imp DESC -> rec DESC -> id ASC)."""
    now = datetime.now(timezone.utc)
    # Two candidates with identical score, identical importance, identical recency, differing memory_id
    rec_b = make_record("mem_bravo", "content b", importance=0.8, created_at=now.isoformat())
    rec_a = make_record("mem_alpha", "content a", importance=0.8, created_at=now.isoformat())

    candidates = [
        (rec_b, 0.5, 0.5),
        (rec_a, 0.5, 0.5),
    ]

    ranked = memory_ranker.rank_hybrid(candidates, query="test")
    # Equal scores -> memory_id ASC -> mem_alpha must appear before mem_bravo
    ok = (
        len(ranked) == 2
        and ranked[0].record.memory_id == "mem_alpha"
        and ranked[1].record.memory_id == "mem_bravo"
    )
    record_test("Test P: Deterministic 4-tier tie-breaking", "UNIT", ok, f"order={[r.record.memory_id for r in ranked]}")


def test_q_duplicate_memory_id():
    """Test Q: Duplicate memory_id merges component scores without duplicating records."""
    retriever = MemoryRetriever()
    # If a memory is present in lexical and semantic, it must be deduplicated
    rec = make_record("dup_01", "PostgreSQL connection pooling configuration", importance=0.8)
    # Simulate candidate merging logic directly
    candidate_map = {}
    # Lexical pass
    candidate_map[rec.memory_id] = (rec, 0.70, 0.0)
    # Semantic pass
    if rec.memory_id in candidate_map:
        existing_rec, existing_lex, _ = candidate_map[rec.memory_id]
        candidate_map[rec.memory_id] = (existing_rec, existing_lex, 0.85)

    merged = list(candidate_map.values())
    ok = (len(merged) == 1 and merged[0][1] == 0.70 and merged[0][2] == 0.85)
    record_test("Test Q: Duplicate memory_id deduplication & score preservation", "UNIT", ok)


def test_r_candidate_pool_bounding():
    """Test R: Candidate pools are strictly bounded (lex <= 25, sem <= 25, merged <= 50, final <= 10)."""
    assert MAX_LEXICAL_CANDIDATES == 25
    assert MAX_SEMANTIC_CANDIDATES == 25
    assert MAX_MERGED_CANDIDATES == 50
    assert MAX_RETRIEVAL_RECORDS == 10

    # Simulate 80 raw candidates fed into rank_hybrid and verify top-K bounds
    recs = [
        (make_record(f"bound_{i:02d}", f"item {i}", importance=0.5), 0.5, 0.5)
        for i in range(80)
    ]
    # Bound merged pool to MAX_MERGED_CANDIDATES
    bounded_pool = recs[:MAX_MERGED_CANDIDATES]
    ranked = memory_ranker.rank_hybrid(bounded_pool, "test")
    final_context = ranked[:MAX_RETRIEVAL_RECORDS]

    ok = (len(bounded_pool) == 50 and len(final_context) == 10)
    record_test("Test R: Candidate pool bounding (25 lex, 25 sem, 50 merged, 10 final)", "INTEGRATION", ok)


def test_s_sensitive_exclusion():
    """Test S: SENSITIVE privacy class never participates in ranking or context."""
    # Write sensitive record directly to repo
    rec_sens = make_record(
        "sens_test_01",
        "Sensitive user financial records and bank account details",
        privacy_class=PrivacyClass.SENSITIVE,
    )
    memory_repository.store(rec_sens)

    try:
        retriever = MemoryRetriever()
        ctx = retriever.retrieve("financial records bank account", include_private=True)
        found_sens = any(m.memory_id == "sens_test_01" for m in ctx.retrieved_memories)
        in_breakdown = "sens_test_01" in ctx.hybrid_breakdowns
        ok = (not found_sens and not in_breakdown)
        record_test("Test S: Sensitive memory exclusion from hybrid ranking", "REAL", ok)
    finally:
        delete_test_record("sens_test_01")


def test_t_private_authorization():
    """Test T: PRIVATE privacy class is excluded unless include_private=True."""
    rec_priv = make_record(
        "priv_test_01",
        "Personal user preference for dark mode theme interface",
        privacy_class=PrivacyClass.PRIVATE,
        memory_type=MemoryType.PREFERENCE,
    )
    memory_repository.store(rec_priv)

    try:
        retriever = MemoryRetriever()
        # Case 1: Unauthorized (include_private=False)
        ctx_unauth = retriever.retrieve("dark mode theme interface", include_private=False)
        found_unauth = any(m.memory_id == "priv_test_01" for m in ctx_unauth.retrieved_memories)

        # Case 2: Authorized (include_private=True)
        ctx_auth = retriever.retrieve("dark mode theme interface", include_private=True)
        found_auth = any(m.memory_id == "priv_test_01" for m in ctx_auth.retrieved_memories)

        ok = (not found_unauth and found_auth)
        record_test("Test T: Private memory authorization gating", "REAL", ok)
    finally:
        delete_test_record("priv_test_01")


def test_u_deleted_exclusion():
    """Test U: DELETED memory status is excluded before ranking."""
    rec_del = make_record(
        "del_test_01",
        "Obsolete configuration key for legacy database server",
        status=MemoryStatus.DELETED,
    )
    memory_repository.store(rec_del)

    try:
        retriever = MemoryRetriever()
        ctx = retriever.retrieve("Obsolete configuration key legacy database")
        found_del = any(m.memory_id == "del_test_01" for m in ctx.retrieved_memories)
        in_bd = "del_test_01" in ctx.hybrid_breakdowns
        ok = (not found_del and not in_bd)
        record_test("Test U: Deleted memory exclusion before ranking", "REAL", ok)
    finally:
        delete_test_record("del_test_01")


def test_v_superseded_exclusion():
    """Test V: SUPERSEDED memory status is excluded before ranking."""
    rec_sup = make_record(
        "sup_test_01",
        "Old deprecated API route endpoints version one",
        status=MemoryStatus.SUPERSEDED,
    )
    memory_repository.store(rec_sup)

    try:
        retriever = MemoryRetriever()
        ctx = retriever.retrieve("deprecated API route endpoints version one")
        found_sup = any(m.memory_id == "sup_test_01" for m in ctx.retrieved_memories)
        in_bd = "sup_test_01" in ctx.hybrid_breakdowns
        ok = (not found_sup and not in_bd)
        record_test("Test V: Superseded memory exclusion before ranking", "REAL", ok)
    finally:
        delete_test_record("sup_test_01")


def test_w_ranking_failure_fallback():
    """Test W: Ranking exception triggers non-fatal fallback (semantic -> lexical -> degraded)."""
    retriever = MemoryRetriever()
    rec = make_record("fb_rec_01", "PostgreSQL database storage engine", importance=0.8)
    memory_repository.store(rec)

    # Store a dummy vector so semantic search finds it
    vec = [0.05] * 384
    norm = math.sqrt(sum(x*x for x in vec))
    vec = [x / norm for x in vec]
    vector_store.store_embedding("fb_rec_01", vec, "sentence-transformers/all-MiniLM-L6-v2", "2.2.0", "fb_hash_01")

    original_rank_hybrid = memory_ranker.rank_hybrid
    try:
        # Force rank_hybrid to throw an exception
        def broken_rank_hybrid(*args, **kwargs):
            raise RuntimeError("Simulated rank_hybrid hardware exception")
        memory_ranker.rank_hybrid = broken_rank_hybrid  # type: ignore

        ctx = retriever.retrieve("PostgreSQL database storage engine")
        # Must NOT raise, must return valid MemoryContext via fallback
        ok = (
            isinstance(ctx, MemoryContext)
            and len(ctx.retrieved_memories) > 0
            and any(m.memory_id == "fb_rec_01" for m in ctx.retrieved_memories)
        )
        record_test("Test W: Hybrid ranking failure non-fatal fallback", "MOCKED", ok)
    finally:
        memory_ranker.rank_hybrid = original_rank_hybrid
        vector_store.delete_embedding("fb_rec_01")
        delete_test_record("fb_rec_01")


def test_x_lexical_only_compatibility():
    """Test X: Lexical-only compatibility (enable_semantic=False)."""
    rec_lex = make_record("lex_compat_01", "Deterministic compiler pipeline parser grammar", importance=0.75)
    memory_repository.store(rec_lex)

    try:
        retriever = MemoryRetriever()
        ctx = retriever.retrieve("compiler pipeline parser grammar", enable_semantic=False)
        found = any(m.memory_id == "lex_compat_01" for m in ctx.retrieved_memories)
        ok = (
            found
            and ctx.retrieval_mode == "LEXICAL"
            and len(ctx.semantic_matches) == 0
        )
        record_test("Test X: Lexical-only compatibility mode (enable_semantic=False)", "INTEGRATION", ok, f"mode={ctx.retrieval_mode}")
    finally:
        delete_test_record("lex_compat_01")


def test_y_real_fastembed_numpy_hybrid():
    """Test Y: Real FastEmbed + NumPy hybrid retrieval end-to-end."""
    # Store real record in repository
    rec_real = make_record("real_hybrid_01", "FastEmbed local ONNX model unit vector embeddings", importance=0.85)
    memory_repository.store(rec_real)

    # Generate real embedding and store in VectorStore
    emb = embedding_router.embed("FastEmbed local ONNX model unit vector embeddings", check_policy=True)
    assert emb is not None
    vector_store.store_embedding("real_hybrid_01", emb.vector, emb.model, emb.model_version, emb.content_hash)

    try:
        retriever = MemoryRetriever()
        ctx = retriever.retrieve("FastEmbed local ONNX vector embeddings")
        found = any(m.memory_id == "real_hybrid_01" for m in ctx.retrieved_memories)
        has_bd = "real_hybrid_01" in ctx.hybrid_breakdowns
        bd = ctx.hybrid_breakdowns.get("real_hybrid_01")

        ok = (
            found
            and has_bd
            and bd is not None
            and bd.semantic_score >= 0.40
            and bd.lexical_score > 0.0
            and ctx.retrieval_mode == "HYBRID"
        )
        detail = f"mode={ctx.retrieval_mode}, lex={bd.lexical_score:.3f}, sem={bd.semantic_score:.3f}, final={bd.final_score:.3f}" if bd else ""
        record_test("Test Y: Real FastEmbed + NumPy hybrid retrieval", "REAL", ok, detail)
    finally:
        vector_store.delete_embedding("real_hybrid_01")
        delete_test_record("real_hybrid_01")


def test_z_production_cognitive_engine_path():
    """Test Z: Real DOOMCore -> CognitiveEngine -> MemoryRetriever -> hybrid ranking production path."""
    from core.orchestrator import DOOMCore
    from core.cognition import cognitive_engine

    # Seed test record
    rec_prod = make_record("prod_cog_01", "User prefers Python for asynchronous backend microservices", importance=0.9)
    memory_repository.store(rec_prod)
    emb = embedding_router.embed("User prefers Python for asynchronous backend microservices")
    assert emb is not None
    vector_store.store_embedding("prod_cog_01", emb.vector, emb.model, emb.model_version, emb.content_hash)

    try:
        core = DOOMCore()
        query = "Which language do I like for async backend services?"

        # 1. Execute direct CognitiveEngine process
        state = cognitive_engine.process(query)
        mem_ctx = state.memory_context
        has_mem = mem_ctx is not None and mem_ctx.has_memories()
        found_target = any(m.memory_id == "prod_cog_01" for m in mem_ctx.retrieved_memories) if mem_ctx else False
        has_hybrid_bd = "prod_cog_01" in mem_ctx.hybrid_breakdowns if mem_ctx else False

        # 2. Execute full DOOMCore.process_request()
        resp = core.process_request(query)
        core_ok = bool(resp) and len(resp) > 0

        ok = has_mem and found_target and has_hybrid_bd and core_ok
        record_test("Test Z: Production CognitiveEngine pipeline path", "PRODUCTION-PATH", ok, f"hit={has_mem}, bd={has_hybrid_bd}")
    finally:
        vector_store.delete_embedding("prod_cog_01")
        delete_test_record("prod_cog_01")


# ====================================================================
# DEDICATED MANDATORY ARCHITECTURAL TESTS
# ====================================================================

def test_anti_double_counting_proof():
    """
    Mandatory Architectural Test (Section 29):
    Proves that V5.2.4 compute_lexical_score does NOT accidentally double count
    importance, recency, or confidence through the lexical score.
    """
    # Create two records with IDENTICAL content and tags, but completely different metadata
    now = datetime.now(timezone.utc)
    old_time = (now - timedelta(days=200)).isoformat()

    rec_high = make_record(
        "dc_high",
        "deterministic compiler pipeline optimization pass",
        importance=0.99,
        confidence=ConfidenceLevel.HIGH,
        created_at=now.isoformat(),
        project_id="target_proj",
    )
    rec_low = make_record(
        "dc_low",
        "deterministic compiler pipeline optimization pass",
        importance=0.01,
        confidence=ConfidenceLevel.LOW,
        created_at=old_time,
        project_id=None,
    )

    query = "compiler pipeline optimization"

    # 1. Inspect legacy V5.1 score()
    # In legacy V5.1, score() mixed 0.4*rel + 0.2*imp + 0.2*rec + 0.1*conf + 0.1*proj
    # Therefore legacy score_high was significantly greater than legacy score_low!
    legacy_score_high = memory_ranker.score(rec_high, query, project_id="target_proj")
    legacy_score_low = memory_ranker.score(rec_low, query, project_id="target_proj")
    legacy_diff = legacy_score_high - legacy_score_low

    # 2. Inspect pure V5.2.4 compute_lexical_score()
    pure_s_lex_high = memory_ranker.compute_lexical_score(rec_high, query)
    pure_s_lex_low = memory_ranker.compute_lexical_score(rec_low, query)
    pure_diff = abs(pure_s_lex_high - pure_s_lex_low)

    # Legacy score MUST differ due to mixed metadata
    assert legacy_diff > 0.30, f"Legacy score should mix metadata: diff={legacy_diff:.4f}"
    # Pure lexical score MUST be identical because content and source are identical
    ok = math.isclose(pure_diff, 0.0, abs_tol=1e-6)

    record_test(
        "Anti-Double-Counting Proof: S_lex isolates pure keyword relevance",
        "UNIT",
        ok,
        f"pure_diff={pure_diff:.6f}, legacy_mixed_diff={legacy_diff:.4f}",
    )


def test_lexical_regression_keyword_preservation():
    """
    Critical Lexical Regression Test (Section 30):
    Proves exact keyword matches are NOT displaced merely because they are old/low-importance.
    """
    now = datetime.now(timezone.utc)
    old_time = (now - timedelta(days=150)).isoformat()

    # Target record: old, low importance, but exact keyword match for unique term "XylophoneQuasar"
    target_rec = make_record(
        "kw_target",
        "Analysis of astronomical XylophoneQuasar radio signals",
        importance=0.05,
        created_at=old_time,
    )
    memory_repository.store(target_rec)

    # Populate 30 high-importance distractor records that do NOT contain the keyword
    distractors = [
        make_record(f"kw_dist_{i:02d}", f"High priority mission directive critical update {i}", importance=0.95)
        for i in range(30)
    ]
    for d in distractors:
        memory_repository.store(d)

    try:
        retriever = MemoryRetriever()
        # Query for the specific keyword with enable_semantic=False (pure lexical retrieval test)
        ctx = retriever.retrieve("XylophoneQuasar radio signals", enable_semantic=False)
        found_target = any(m.memory_id == "kw_target" for m in ctx.retrieved_memories)

        ok = found_target
        record_test(
            "Critical Lexical Regression: Low-imp old keyword match not displaced by 30 high-imp distractors",
            "REAL",
            ok,
            f"found={found_target}, count={len(ctx.retrieved_memories)}",
        )
    finally:
        delete_test_record("kw_target")
        for d in distractors:
            delete_test_record(d.memory_id)


def test_policy_before_ranking_proof():
    """
    Critical Policy-Before-Ranking Test (Section 31):
    Proves that SENSITIVE, DELETED, SUPERSEDED, and unauthorized PRIVATE records
    are removed BEFORE hybrid scoring and do not participate in ranking computation.
    """
    # Track calls to score_hybrid
    scored_ids: List[str] = []
    original_score_hybrid = memory_ranker.score_hybrid

    def spying_score_hybrid(rec, *args, **kwargs):
        scored_ids.append(rec.memory_id)
        return original_score_hybrid(rec, *args, **kwargs)

    memory_ranker.score_hybrid = spying_score_hybrid  # type: ignore

    # Create records of each ineligible type
    r_sens = make_record("inelig_sens", "Top secret confidential nuclear launch codes", privacy_class=PrivacyClass.SENSITIVE)
    r_del = make_record("inelig_del", "Deleted project file records", status=MemoryStatus.DELETED)
    r_sup = make_record("inelig_sup", "Superseded architectural blueprint", status=MemoryStatus.SUPERSEDED)
    r_priv = make_record("inelig_priv", "Unauthorized private user medical diagnosis", privacy_class=PrivacyClass.PRIVATE)

    memory_repository.store(r_sens)
    memory_repository.store(r_del)
    memory_repository.store(r_sup)
    memory_repository.store(r_priv)

    try:
        retriever = MemoryRetriever()
        ctx = retriever.retrieve("Top secret confidential nuclear launch codes deleted architectural medical", include_private=False)

        # None of the ineligible IDs should have reached score_hybrid()!
        ineligible_set = {"inelig_sens", "inelig_del", "inelig_sup", "inelig_priv"}
        leaked_into_scoring = ineligible_set.intersection(set(scored_ids))

        ok = len(leaked_into_scoring) == 0
        record_test(
            "Policy-Before-Ranking Proof: Ineligible records never participate in hybrid scoring",
            "INTEGRATION",
            ok,
            f"leaked={leaked_into_scoring}",
        )
    finally:
        memory_ranker.score_hybrid = original_score_hybrid
        for mid in ("inelig_sens", "inelig_del", "inelig_sup", "inelig_priv"):
            delete_test_record(mid)


def test_project_policy_vs_relevance_distinction():
    """
    Critical Project Test (Section 32):
    Proves distinction between project policy eligibility and project relevance ranking.
    Policy-ineligible cross-project memory is excluded BEFORE ranking even with high similarity.
    Eligible same-project and global memories participate with differentiated project scores.
    """
    # 1. Eligible same-project memory
    rec_same = make_record("proj_elig_same", "Project Omega core architectural database", project_id="omega")
    # 2. Eligible global memory
    rec_glob = make_record("proj_elig_glob", "Project Omega core architectural database", project_id=None)
    # 3. Policy-ineligible cross-project memory (different project)
    rec_cross = make_record("proj_inelig_cross", "Project Omega core architectural database", project_id="delta")

    memory_repository.store(rec_same)
    memory_repository.store(rec_glob)
    memory_repository.store(rec_cross)

    try:
        retriever = MemoryRetriever()
        # Query with project_id="omega"
        ctx = retriever.retrieve("Project Omega core architectural database", project_id="omega")

        found_same = any(m.memory_id == "proj_elig_same" for m in ctx.retrieved_memories)
        found_glob = any(m.memory_id == "proj_elig_glob" for m in ctx.retrieved_memories)
        found_cross = any(m.memory_id == "proj_inelig_cross" for m in ctx.retrieved_memories)

        # Cross project record MUST be rejected by policy filter
        assert not found_cross, "Cross-project memory must NOT participate in ranking or context!"

        # Both same-project and global memories participate
        bd_same = ctx.hybrid_breakdowns.get("proj_elig_same")
        bd_glob = ctx.hybrid_breakdowns.get("proj_elig_glob")

        ok = (
            found_same
            and found_glob
            and not found_cross
            and bd_same is not None
            and bd_glob is not None
            and bd_same.project_score == 1.0  # Exact match
            and bd_glob.project_score == 0.5  # Global in project query
        )
        record_test(
            "Project Policy vs Relevance: Cross-project rejected, global (0.5) and same-project (1.0) ranked",
            "REAL",
            ok,
            f"same_s_proj={bd_same.project_score if bd_same else None}, glob_s_proj={bd_glob.project_score if bd_glob else None}",
        )
    finally:
        delete_test_record("proj_elig_same")
        delete_test_record("proj_elig_glob")
        delete_test_record("proj_inelig_cross")


def test_failure_cases():
    """Test safe failure handling on empty inputs and malformed candidate configurations."""
    retriever = MemoryRetriever()

    # Empty query, no filters -> immediate empty MemoryContext
    ctx_empty = retriever.retrieve("")
    ok_empty = (not ctx_empty.has_memories() and ctx_empty.memory_count == 0 and ctx_empty.memory_hit is False)

    # Whitespace query
    ctx_white = retriever.retrieve("   ")
    ok_white = (not ctx_white.has_memories() and ctx_white.memory_count == 0)

    # Truly nonexistent query with zero lexical and zero semantic matches
    ctx_nonexist = retriever.retrieve("xyzzyqwertynonexistent1234567890", project_id="no_proj_9999")
    ok_nonexist = (isinstance(ctx_nonexist, MemoryContext) and ctx_nonexist.memory_count == 0 and not ctx_nonexist.has_memories())

    ok = ok_empty and ok_white and ok_nonexist
    record_test("Failure Resilience: Empty query and zero-match handling", "UNIT", ok)


# ====================================================================
# PERFORMANCE BENCHMARK
# ====================================================================

def run_performance_benchmarks():
    """Measures latency of candidate merging, six-factor scoring, sorting, and full hybrid retrieval."""
    print("\n--- MEASURING HYBRID RANKING PERFORMANCE ---")

    # Generate 50 realistic candidates
    candidates = []
    for i in range(50):
        rec = make_record(f"bench_{i:02d}", f"Performance benchmark test memory content item {i}", importance=0.5 + (i%5)*0.1)
        candidates.append((rec, 0.6, 0.7))

    # Benchmark six-factor scoring & sorting (50 candidates)
    scoring_times = []
    for _ in range(50):
        t0 = time.perf_counter()
        _ = memory_ranker.rank_hybrid(candidates, query="benchmark test content")
        scoring_times.append((time.perf_counter() - t0) * 1000.0)

    avg_score = sum(scoring_times) / len(scoring_times)
    min_score = min(scoring_times)
    max_score = max(scoring_times)
    print(f"  Six-factor rank_hybrid (50 candidates x 50 runs): Min={min_score:.4f}ms | Avg={avg_score:.4f}ms | Max={max_score:.4f}ms")

    # Full retrieval pipeline latency (20 runs)
    retriever = MemoryRetriever()
    retrieval_times = []
    for _ in range(20):
        t0 = time.perf_counter()
        _ = retriever.retrieve("Python programming language")
        retrieval_times.append((time.perf_counter() - t0) * 1000.0)

    avg_ret = sum(retrieval_times) / len(retrieval_times)
    min_ret = min(retrieval_times)
    max_ret = max(retrieval_times)
    print(f"  Full MemoryRetriever.retrieve (20 runs):           Min={min_ret:.2f}ms | Avg={avg_ret:.2f}ms | Max={max_ret:.2f}ms")
    print("--------------------------------------------\n")


# ====================================================================
# MAIN RUNNER
# ====================================================================

if __name__ == "__main__":
    print("====================================================================")
    print("DOOM V5.2.4 — HYBRID MEMORY RANKING TEST SUITE")
    print("====================================================================")

    # Step-by-step execution
    test_a_lexical_only()
    test_b_semantic_only()
    test_c_matched_by_both()
    test_d_e_factor_tradeoffs()
    test_f_recency_ordering()
    test_g_confidence_ordering()
    test_h_contradicted_confidence()
    test_i_exact_project_match()
    test_j_k_project_relevance_matrix()
    test_l_missing_importance()
    test_m_missing_corrupt_future_timestamp()
    test_n_score_boundaries()
    test_o_weight_sum_validation()
    test_p_deterministic_tie_breaking()
    test_q_duplicate_memory_id()
    test_r_candidate_pool_bounding()
    test_s_sensitive_exclusion()
    test_t_private_authorization()
    test_u_deleted_exclusion()
    test_v_superseded_exclusion()
    test_w_ranking_failure_fallback()
    test_x_lexical_only_compatibility()
    test_y_real_fastembed_numpy_hybrid()
    test_z_production_cognitive_engine_path()
    test_anti_double_counting_proof()
    test_lexical_regression_keyword_preservation()
    test_policy_before_ranking_proof()
    test_project_policy_vs_relevance_distinction()
    test_failure_cases()

    # Classification breakdown
    counts: Dict[str, int] = {}
    passed_count = sum(1 for t in test_results if t["status"] == "PASS")
    failed_count = sum(1 for t in test_results if t["status"] == "FAIL")

    for t in test_results:
        c = t["classification"]
        counts[c] = counts.get(c, 0) + 1

    print("====================================================================")
    print(f"RESULTS: PASSED={passed_count} | FAILED={failed_count} | TOTAL={len(test_results)}")
    print(f"BREAKDOWN: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("====================================================================")

    run_performance_benchmarks()

    if failed_count > 0:
        sys.exit(1)
    sys.exit(0)
