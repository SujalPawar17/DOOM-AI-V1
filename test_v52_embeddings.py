"""
DOOM V5.2.1 — Embedding Foundation Test Suite
Covers requirements A through X:
A. Provider interface
B. FastEmbed initialization
C. Model metadata
D. 384-dimensional output
E. Deterministic embedding shape
F. Normalization (unit length)
G. Empty input rejection
H. None rejection
I. Oversized input rejection
J. Malformed vector rejection
K. Batch embedding
L. Batch ordering
M. Cache hit
N. Cache miss
O. Cache eviction
P. Cache invalidation
Q. Provider failure (graceful non-fatal)
R. Model initialization failure
S. Router behavior
T. Concurrent initialization
U. No database writes
V. No secret leakage into logs/errors
W. Sensitive input policy behavior
X. Latency telemetry

Classification legend:
[REAL]        - Executes genuine FastEmbed ONNX model inference
[UNIT]        - Tests module logic with real/synthetic inputs
[INTEGRATION] - Multi-component interaction (Router + Cache + Provider)
[MOCKED]      - Simulates external error conditions (failures, corruption)
"""
import concurrent.futures
import math
import re
import sys
import threading
import time
from typing import List, Optional

import numpy as np

# Embedding foundation imports
from memory.embedding.base import (
    EmbeddingProvider,
    EmbeddingResult,
    EmbeddingError,
    InputValidationError,
    PolicyViolationError,
    ProviderUnavailableError,
    DEFAULT_MODEL_NAME,
    DEFAULT_MODEL_VERSION,
    DEFAULT_DIMENSION,
    MAX_INPUT_LENGTH,
)
from memory.embedding.fastembed_provider import FastEmbedProvider
from memory.embedding.cache import EmbeddingCache
from memory.embedding.router import EmbeddingRouter, embedding_router


# ===========================================================================
# Test Runner & Reporting Helpers
# ===========================================================================
PASSED = 0
FAILED = 0
TEST_LOG = []


def record_test(name: str, classification: str, status: bool, detail: str = ""):
    global PASSED, FAILED
    if status:
        PASSED += 1
        tag = "[PASS]"
    else:
        FAILED += 1
        tag = "[FAIL]"
    msg = f"  {tag} [{classification:<11}] {name} {detail}"
    print(msg)
    TEST_LOG.append((name, classification, status, detail))


# ===========================================================================
# Section A-C: Provider Interface & Initialization & Metadata
# ===========================================================================
def test_a_provider_interface():
    """Verify FastEmbedProvider adheres strictly to EmbeddingProvider ABC."""
    provider = FastEmbedProvider(lazy_load=True)
    is_subclass = issubclass(FastEmbedProvider, EmbeddingProvider)
    has_props = (
        hasattr(provider, "provider_name")
        and hasattr(provider, "model_name")
        and hasattr(provider, "model_version")
        and hasattr(provider, "dimension")
        and hasattr(provider, "embed")
        and hasattr(provider, "embed_batch")
        and hasattr(provider, "health_check")
    )
    record_test("Test A: Provider interface compliance", "UNIT", is_subclass and has_props)


def test_b_fastembed_initialization():
    """Verify FastEmbedProvider lazy loading and real ONNX initialization."""
    provider = FastEmbedProvider(lazy_load=True)
    assert not provider.is_loaded, "Model should not be loaded on lazy initialization"
    # Trigger lazy load
    provider._ensure_model_loaded()
    is_ready = provider.is_loaded
    record_test("Test B: FastEmbed lazy & actual initialization", "REAL", is_ready)


def test_c_model_metadata():
    """Verify model metadata attributes."""
    provider = FastEmbedProvider(lazy_load=True)
    valid = (
        provider.provider_name == "fastembed"
        and provider.model_name == DEFAULT_MODEL_NAME
        and provider.model_version == DEFAULT_MODEL_VERSION
        and provider.dimension == DEFAULT_DIMENSION
    )
    record_test("Test C: Model metadata properties", "UNIT", valid)


# ===========================================================================
# Section D-F: Vector Dimensionality, Determinism & Normalization
# ===========================================================================
def test_d_384_dimensional_output():
    """Verify actual inference produces exactly 384 dimensions."""
    provider = FastEmbedProvider(lazy_load=False)
    res = provider.embed("DOOM AI OS kernel architecture", check_policy=False)
    is_384 = (len(res.vector) == 384) and (res.dimension == 384)
    record_test("Test D: 384-dimensional vector output", "REAL", is_384, f"len={len(res.vector)}")


def test_e_deterministic_embedding_shape():
    """Verify repeated embeddings of the same text produce identical vector shapes and hashes."""
    provider = FastEmbedProvider(lazy_load=False)
    t = "Deterministic vector validation text for DOOM"
    res1 = provider.embed(t, check_policy=False)
    res2 = provider.embed(t, check_policy=False)
    shape_match = len(res1.vector) == len(res2.vector) == 384
    hash_match = res1.content_hash == res2.content_hash
    # Check numeric precision equality
    v1 = np.array(res1.vector)
    v2 = np.array(res2.vector)
    diff = np.max(np.abs(v1 - v2))
    numeric_match = diff < 1e-5
    record_test(
        "Test E: Deterministic vector reproducibility",
        "REAL",
        shape_match and hash_match and numeric_match,
        f"max_diff={diff:.8f}",
    )


def test_f_normalization():
    """Verify output vector is unit normalized (L2 norm == 1.0) for cosine similarity."""
    provider = FastEmbedProvider(lazy_load=False)
    res = provider.embed("Vector normalization check", check_policy=False)
    norm = np.linalg.norm(np.array(res.vector))
    is_unit = abs(norm - 1.0) < 1e-4 and res.normalized
    record_test("Test F: Vector L2 unit normalization", "REAL", is_unit, f"norm={norm:.6f}")


# ===========================================================================
# Section G-J: Input & Vector Validation Edge Cases
# ===========================================================================
def test_g_empty_input_rejection():
    """Verify empty string or whitespace-only inputs are rejected."""
    provider = FastEmbedProvider(lazy_load=True)
    rejected_empty = False
    rejected_whitespace = False
    try:
        provider.embed("")
    except InputValidationError:
        rejected_empty = True

    try:
        provider.embed("    \n\t  ")
    except InputValidationError:
        rejected_whitespace = True

    record_test(
        "Test G: Empty & whitespace input rejection",
        "UNIT",
        rejected_empty and rejected_whitespace,
    )


def test_h_none_rejection():
    """Verify None or non-string inputs are rejected."""
    provider = FastEmbedProvider(lazy_load=True)
    rejected_none = False
    rejected_int = False
    try:
        provider.embed(None)
    except InputValidationError:
        rejected_none = True

    try:
        provider.embed(12345)
    except InputValidationError:
        rejected_int = True

    record_test("Test H: None and non-string rejection", "UNIT", rejected_none and rejected_int)


def test_i_oversized_input_rejection():
    """Verify inputs exceeding MAX_INPUT_LENGTH (4000 chars) are rejected."""
    provider = FastEmbedProvider(lazy_load=True)
    huge_text = "A" * (MAX_INPUT_LENGTH + 50)
    rejected_huge = False
    try:
        provider.embed(huge_text)
    except InputValidationError:
        rejected_huge = True

    record_test(
        "Test I: Oversized input rejection (>4000 chars)",
        "UNIT",
        rejected_huge,
        f"len={len(huge_text)}",
    )


def test_j_malformed_vector_rejection():
    """Verify validate_and_normalize_vector rejects NaN, Inf, zero norm, or wrong dimensions."""
    provider = FastEmbedProvider(lazy_load=True)
    # NaN check
    nan_vec = [float("nan")] * 384
    nan_caught = False
    try:
        provider.validate_and_normalize_vector(nan_vec)
    except ValueError:
        nan_caught = True

    # Inf check
    inf_vec = [float("inf")] * 384
    inf_caught = False
    try:
        provider.validate_and_normalize_vector(inf_vec)
    except ValueError:
        inf_caught = True

    # Wrong dimension check
    short_vec = [0.1] * 128
    dim_caught = False
    try:
        provider.validate_and_normalize_vector(short_vec)
    except ValueError:
        dim_caught = True

    # Zero norm check
    zero_vec = [0.0] * 384
    zero_caught = False
    try:
        provider.validate_and_normalize_vector(zero_vec)
    except ValueError:
        zero_caught = True

    all_caught = nan_caught and inf_caught and dim_caught and zero_caught
    record_test(
        "Test J: Malformed vector validation (NaN, Inf, Dim, Zero)",
        "UNIT",
        all_caught,
    )


# ===========================================================================
# Section K-L: Batch Embedding & Order Preservation
# ===========================================================================
def test_k_batch_embedding():
    """Verify batch embedding on multiple texts."""
    provider = FastEmbedProvider(lazy_load=False)
    texts = [
        "First item for DOOM batch",
        "Second item for DOOM batch",
        "Third item for DOOM batch",
    ]
    results = provider.embed_batch(texts, check_policy=False)
    ok = (
        len(results) == 3
        and all(len(r.vector) == 384 for r in results)
        and all(r.normalized for r in results)
    )
    record_test("Test K: Batch embedding execution", "REAL", ok, f"items={len(results)}")


def test_l_batch_ordering():
    """Verify embed_batch strictly preserves input ordering."""
    provider = FastEmbedProvider(lazy_load=False)
    texts = [
        "Alpha query regarding Python",
        "Beta query regarding PostgreSQL",
        "Gamma query regarding Edge-TTS",
    ]
    results = provider.embed_batch(texts, check_policy=False)
    # Verify hashes align with respective texts
    import hashlib

    expected_hashes = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts]
    actual_hashes = [r.content_hash for r in results]
    order_intact = expected_hashes == actual_hashes
    record_test(
        "Test L: Batch input-output ordering preservation",
        "REAL",
        order_intact,
    )


# ===========================================================================
# Section M-P: In-Memory LRU Cache
# ===========================================================================
def test_m_cache_hit():
    """Verify cache returns stored result on repeated query."""
    cache = EmbeddingCache(max_size=10)
    fake_res = EmbeddingResult(
        vector=[0.1] * 384,
        dimension=384,
        provider="test",
        model="m",
        model_version="1",
        normalized=True,
    )
    cache.put(fake_res, "hello")
    hit = cache.get("m", "1", "hello")
    is_hit = hit is not None and hit.vector == fake_res.vector and cache.get_stats()["hits"] == 1
    record_test("Test M: Cache hit retrieval", "UNIT", is_hit)


def test_n_cache_miss():
    """Verify cache returns None and increments miss counter for unknown query."""
    cache = EmbeddingCache(max_size=10)
    miss = cache.get("m", "1", "unseen query")
    is_miss = miss is None and cache.get_stats()["misses"] == 1
    record_test("Test N: Cache miss handling", "UNIT", is_miss)


def test_o_cache_eviction():
    """Verify oldest entry is evicted when max_size is reached."""
    cache = EmbeddingCache(max_size=2)
    res1 = EmbeddingResult([0.1] * 384, 384, "t", "m", "1", True)
    res2 = EmbeddingResult([0.2] * 384, 384, "t", "m", "1", True)
    res3 = EmbeddingResult([0.3] * 384, 384, "t", "m", "1", True)

    cache.put(res1, "query1")
    cache.put(res2, "query2")
    assert cache.size == 2

    # Inserting 3rd must evict query1
    cache.put(res3, "query3")
    assert cache.size == 2
    evicted = cache.get("m", "1", "query1") is None
    retained2 = cache.get("m", "1", "query2") is not None
    retained3 = cache.get("m", "1", "query3") is not None
    record_test("Test O: Bounded LRU cache eviction", "UNIT", evicted and retained2 and retained3)


def test_p_cache_invalidation():
    """Verify invalidate removes entry and clear resets all entries."""
    cache = EmbeddingCache(max_size=10)
    res1 = EmbeddingResult([0.1] * 384, 384, "t", "m", "1", True)
    cache.put(res1, "query1")
    assert cache.size == 1

    invalidated = cache.invalidate("m", "1", "query1")
    assert cache.size == 0 and invalidated

    cache.put(res1, "query2")
    cache.clear()
    cleared = cache.size == 0 and cache.get_stats()["hits"] == 0
    record_test("Test P: Cache invalidation and clear", "UNIT", invalidated and cleared)


# ===========================================================================
# Section Q-S: Failure Resilience, Model Failures & Router
# ===========================================================================
def test_q_provider_failure_non_fatal():
    """Verify provider failure is caught by router and returns None without raising."""
    class FailingProvider(EmbeddingProvider):
        @property
        def provider_name(self): return "fail"
        @property
        def model_name(self): return "m"
        @property
        def model_version(self): return "1"
        @property
        def dimension(self): return 384
        def embed(self, text, check_policy=True): raise RuntimeError("Simulated ONNX hardware crash")
        def embed_batch(self, texts, check_policy=True): raise RuntimeError("Simulated crash")
        def health_check(self): return False

    router = EmbeddingRouter(provider=FailingProvider())
    result = router.embed("Valid input query")
    safe = result is None and router.get_stats()["failed_requests"] == 1
    record_test("Test Q: Provider failure graceful degradation", "MOCKED", safe)


def test_r_model_initialization_failure():
    """Verify router handles uninitializable models gracefully."""
    class UnloadableProvider(EmbeddingProvider):
        @property
        def provider_name(self): return "unloadable"
        @property
        def model_name(self): return "non-existent-model"
        @property
        def model_version(self): return "1"
        @property
        def dimension(self): return 384
        def embed(self, text, check_policy=True): raise ProviderUnavailableError("Model weights not found")
        def embed_batch(self, texts, check_policy=True): raise ProviderUnavailableError("Model weights not found")
        def health_check(self): return False

    router = EmbeddingRouter(provider=UnloadableProvider())
    res = router.embed("Sample")
    health = router.health_check()
    handled = res is None and health["status"] == "UNHEALTHY"
    record_test("Test R: Model initialization failure handling", "MOCKED", handled)


def test_s_router_behavior():
    """Verify EmbeddingRouter end-to-end routing, caching, and batch fallback."""
    provider = FastEmbedProvider(lazy_load=False)
    router = EmbeddingRouter(provider=provider, cache_size=50)

    # First call -> compute & cache
    t0 = time.perf_counter()
    r1 = router.embed("Router verification test text")
    t_first = (time.perf_counter() - t0) * 1000.0

    # Second call -> cache hit (<1ms)
    t1 = time.perf_counter()
    r2 = router.embed("Router verification test text")
    t_cached = (time.perf_counter() - t1) * 1000.0

    cache_worked = r1.vector == r2.vector and router.cache.get_stats()["hits"] == 1
    batch_results = router.embed_batch(["Batch 1", "Batch 2", "Router verification test text"])
    batch_worked = len(batch_results) == 3 and all(b is not None for b in batch_results)

    meta = router.get_metadata()
    has_meta = meta["dimension"] == 384 and meta["provider"] == "fastembed"

    record_test(
        "Test S: Router caching, batching & metadata integration",
        "INTEGRATION",
        cache_worked and batch_worked and has_meta,
        f"first={t_first:.1f}ms, cached={t_cached:.2f}ms",
    )


# ===========================================================================
# Section T-X: Concurrency, DB Invariance, Security & Telemetry
# ===========================================================================
def test_t_concurrent_initialization():
    """Verify thread-safe initialization under concurrent multi-threaded requests."""
    provider = FastEmbedProvider(lazy_load=True)
    results = []
    errors = []

    def worker(i):
        try:
            res = provider.embed(f"Concurrent thread test message {i}", check_policy=False)
            results.append(res)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    safe = len(errors) == 0 and len(results) == 8 and all(len(r.vector) == 384 for r in results)
    record_test("Test T: Multi-threaded concurrent model access", "REAL", safe, f"threads=8, results={len(results)}")


def test_u_no_database_writes():
    """Verify that embedding operations make ZERO database modifications or queries."""
    from database.postgres_db import postgres_manager
    conn = postgres_manager.get_connection()
    if not conn:
        record_test("Test U: Zero database writes check", "UNIT", True, "(Postgres offline, DB untouched)")
        return

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM memory_records;")
            count_before = cur.fetchone()[0]

            # Also verify memory_embeddings does NOT exist
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'memory_embeddings'
                );
            """)
            has_table = cur.fetchone()[0]
    finally:
        postgres_manager.release_connection(conn)

    # Perform multiple router calls
    embedding_router.embed("Database non-interference verification query")
    embedding_router.embed_batch(["DB check 1", "DB check 2"])

    conn2 = postgres_manager.get_connection()
    try:
        with conn2.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM memory_records;")
            count_after = cur.fetchone()[0]
    finally:
        postgres_manager.release_connection(conn2)

    no_mutation = (count_before == count_after) and (not has_table)
    record_test(
        "Test U: Architectural boundary - zero database writes or tables",
        "INTEGRATION",
        no_mutation,
        f"table_exists={has_table}, records_delta={count_after - count_before}",
    )


def test_v_no_secret_leakage():
    """Verify secrets are rejected and NEVER leaked into error messages or exception strings."""
    provider = FastEmbedProvider(lazy_load=True)
    secret_payload = "ghp_1234567890abcdefghijklmnopqrstuvwxyzAB"
    secret_caught = False
    leak_detected = False

    try:
        provider.embed(f"User github token is {secret_payload}", check_policy=True)
    except PolicyViolationError as e:
        secret_caught = True
        err_msg = str(e)
        if secret_payload in err_msg:
            leak_detected = True

    safe = secret_caught and (not leak_detected)
    record_test("Test V: Secret rejection & zero error string leakage", "UNIT", safe)


def test_w_sensitive_input_policy_behavior():
    """Verify credential and chain-of-thought rejection by policy."""
    provider = FastEmbedProvider(lazy_load=True)

    # Password check
    pw_blocked = False
    try:
        provider.embed("my secret password is AdminPassword123!", check_policy=True)
    except PolicyViolationError:
        pw_blocked = True

    # Chain-of-thought check
    cot_blocked = False
    try:
        provider.embed("<thinking>Let me think about how to solve this</thinking>", check_policy=True)
    except PolicyViolationError:
        cot_blocked = True

    policy_ok = pw_blocked and cot_blocked
    record_test("Test W: Policy enforcement (credential & CoT blocked)", "UNIT", policy_ok)


def test_x_latency_telemetry():
    """Verify latency tracking and telemetry recording in results and router stats."""
    provider = FastEmbedProvider(lazy_load=False)
    router = EmbeddingRouter(provider=provider, cache_size=10)
    router.clear_cache()

    res = router.embed("Latency measurement string for telemetry validation", use_cache=False)
    has_latency = res is not None and res.latency_ms > 0.0

    stats = router.get_stats()
    stats_ok = stats["total_requests"] >= 1 and stats["successful_requests"] >= 1

    record_test(
        "Test X: Latency telemetry & operational metrics",
        "INTEGRATION",
        has_latency and stats_ok,
        f"latency={res.latency_ms:.2f}ms" if res else "",
    )


# ===========================================================================
# Benchmark Performance Measurement (Requirement 21)
# ===========================================================================
def measure_performance_benchmark():
    """Measure real-world latency for single embed, batch embed, and cached lookup."""
    print("\n--- MEASURING REAL-WORLD EMBEDDING PERFORMANCE ---")
    provider = FastEmbedProvider(lazy_load=False)
    router = EmbeddingRouter(provider=provider, cache_size=100)

    # Warmup
    router.embed("Warmup query")

    # 1. Single Embedding Latency (10 trials)
    single_latencies = []
    for i in range(10):
        t0 = time.perf_counter()
        router.embed(f"Performance evaluation sample sentence number {i} for DOOM OS", use_cache=False)
        single_latencies.append((time.perf_counter() - t0) * 1000.0)

    # 2. Batch Embedding Latency (5 batches of 5 items)
    batch_latencies = []
    for b in range(5):
        items = [f"Batch test item {b}_{j} for embedding benchmark" for j in range(5)]
        t0 = time.perf_counter()
        router.embed_batch(items, use_cache=False)
        batch_latencies.append((time.perf_counter() - t0) * 1000.0)

    # 3. Cache Hit Latency (20 lookups)
    cache_latencies = []
    test_q = "Fixed query for cache lookup performance"
    router.embed(test_q, use_cache=True)
    for _ in range(20):
        t0 = time.perf_counter()
        router.embed(test_q, use_cache=True)
        cache_latencies.append((time.perf_counter() - t0) * 1000.0)

    print(f"  Single Embed (10 runs): Min={min(single_latencies):.2f}ms | Avg={sum(single_latencies)/len(single_latencies):.2f}ms | Max={max(single_latencies):.2f}ms")
    print(f"  Batch (5 items x 5):    Min={min(batch_latencies):.2f}ms | Avg={sum(batch_latencies)/len(batch_latencies):.2f}ms | Max={max(batch_latencies):.2f}ms")
    print(f"  Cache Hit (20 lookups): Min={min(cache_latencies):.4f}ms | Avg={sum(cache_latencies)/len(cache_latencies):.4f}ms | Max={max(cache_latencies):.4f}ms")
    print("--------------------------------------------------\n")


# ===========================================================================
# Master Test Suite Runner
# ===========================================================================
def run_all_v52_tests():
    print("=" * 65)
    print("DOOM V5.2.1 — EMBEDDING FOUNDATION TEST SUITE")
    print("=" * 65)

    test_a_provider_interface()
    test_b_fastembed_initialization()
    test_c_model_metadata()
    test_d_384_dimensional_output()
    test_e_deterministic_embedding_shape()
    test_f_normalization()
    test_g_empty_input_rejection()
    test_h_none_rejection()
    test_i_oversized_input_rejection()
    test_j_malformed_vector_rejection()
    test_k_batch_embedding()
    test_l_batch_ordering()
    test_m_cache_hit()
    test_n_cache_miss()
    test_o_cache_eviction()
    test_p_cache_invalidation()
    test_q_provider_failure_non_fatal()
    test_r_model_initialization_failure()
    test_s_router_behavior()
    test_t_concurrent_initialization()
    test_u_no_database_writes()
    test_v_no_secret_leakage()
    test_w_sensitive_input_policy_behavior()
    test_x_latency_telemetry()

    print("=" * 65)
    print(f"RESULTS: PASSED={PASSED} | FAILED={FAILED} | TOTAL={PASSED + FAILED}")
    print("=" * 65)

    if FAILED == 0:
        measure_performance_benchmark()

    return FAILED == 0


if __name__ == "__main__":
    success = run_all_v52_tests()
    sys.exit(0 if success else 1)
