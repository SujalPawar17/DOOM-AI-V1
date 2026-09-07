# DOOM V5.3.3 — Vector Synchronization & Reconciliation
## Comprehensive Forensic Architecture Audit & System Design Specification (Corrected Pass)

---

### Document Information
- **Title**: DOOM V5.3.3 Vector Synchronization & Reconciliation Architecture Audit
- **Project**: DOOM AI OS (Cognitive Memory Subsystem)
- **Phase**: V5.3.3 Architecture & Forensic Audit (Post-Audit Correction Pass)
- **Protected Baseline**: `DOOM-V5.2` @ `a35399bf12143264817126434145cb61ff319a38` (`v5.3.2`)
- **Baseline Test Invariant**: 289 / 289 Tests Passing (100%) Across 11 Suites
- **Classification**: Architectural & Forensic Design Document (READ-ONLY AUDIT)
- **Status**: APPROVED — READY FOR V5.3.3 IMPLEMENTATION (Incorporating Mandatory Corrections)

---

## 1. Executive Summary

DOOM V5.3.2 successfully delivered the authoritative **State Machine & Transaction Engine** for memory lifecycle governance, establishing single-connection PostgreSQL ACID transactions, pessimistic row-level locking (`SELECT ... FOR UPDATE`), database-indexed idempotency deduplication, and strict provenance verification.

However, a fundamental distributed-systems boundary exists between PostgreSQL and vector storage:
1. **PostgreSQL** is the **sole authoritative lifecycle/state store** holding `memory_records` and append-only `memory_lifecycle_events`.
2. **Vector storage** (`memory_embeddings` in pgvector, or `NumPyVectorStorageAdapter` in Windows fallback) is a **derived, eventually consistent secondary index** designed solely for nearest-neighbor semantic candidate generation.
3. In the current V5.3.2 codebase, **no automated synchronization link exists** between lifecycle state mutations (`MemoryLifecycleEngine.transition_memory()`, `MemoryLifecycleEngine.supersede_memory()`) or memory creation (`MemoryManager.store()`) and the vector store.
4. When a memory transitions from `ACTIVE` to `SUPERSEDED`, `ARCHIVED`, or `DELETED`, its dense embedding remains untouched in the vector store. This creates a **"zombie vector"** — an active embedding for an inactive or invalid memory.
5. In semantic retrieval (`MemoryRetriever.retrieve()`), zombie vectors consume semantic candidate slots (`top_k=25`). While defensive post-filtering in `MemoryRetriever` correctly discards non-`ACTIVE` records, high densities of zombie vectors cause **Semantic Candidate Starvation**, discarding valid `ACTIVE` memories that scored below the zombies and severely degrading cognitive recall.
6. Furthermore, on process restart, the Windows `NumPyVectorStorageAdapter` loses all in-memory embeddings, resulting in complete **Vector Amnesia** unless an explicit rebuild mechanism hydratively reconstructs the index from authoritative PostgreSQL state.
7. Crucially, concurrency between asynchronous vector sync workers and rapid lifecycle transitions introduces a **stale-write race condition**: a delayed `UPSERT` work item could execute after a newer `DELETE` work item, inadvertently resurrecting a zombie vector.

This corrected forensic audit establishes:
- An explicit **Monotonic Memory Generation / Versioning Mechanism** ($G \in \mathbb{N}$) that completely neutralizes stale `UPSERT` races.
- Full integration of vector synchronization into the **Production Memory Creation Path** (`MemoryManager.store()`).
- Formal **Eventual Vector Convergence** semantics with zero valid zombie retrieval.
- Strict **NumPy Restart Recovery / Rehydration** specifications.
- Comprehensive test, concurrency, and failure injection matrices.

---

## 2. Baseline Verification

The protected baseline was verified prior to conducting this audit. The working tree is clean of any tracked modifications.

### Git Baseline Inspection
```text
$ git checkout DOOM-V5.2
Already on 'DOOM-V5.2'

$ git status
On branch DOOM-V5.2
nothing added to commit but untracked files present

$ git log -1 --oneline
a35399b feat(memory): complete DOOM V5.3.2 state machine and transaction engine

$ git tag --points-at HEAD
v5.3.2
```

### Full Regression Test Suite Execution
All 11 test suites were executed sequentially on the baseline. All 289 tests passed with 100% success rate:

| Test Suite | Component / Area | Baseline Count | Result | Status |
|---|---|---|---|---|
| `test_v51_memory.py` | V5.1 Memory Foundation (CRUD, schemas, policy) | 35 | 35 / 35 | **PASS** |
| `test_v52_embeddings.py` | V5.2.1 Embedding Router & FastEmbed Provider | 24 | 24 / 24 | **PASS** |
| `test_v52_vector_store.py` | V5.2.2 Vector Storage (NumPy + pgvector) | 30 | 30 / 30 | **PASS** |
| `test_v52_semantic_retrieval.py` | V5.2.3 Semantic Candidate Retrieval | 23 | 23 / 23 | **PASS** |
| `test_v524_hybrid_ranking.py` | V5.2.4 Six-Factor Hybrid Ranker | 29 | 29 / 29 | **PASS** |
| `test_v4_cognitive.py` | V4.0 Cognitive Engine Core Pipeline | 25 | 25 / 25 | **PASS** |
| `test_v525_context_fencing.py` | V5.2.5 Context Fencing & Injection Defense | 31 | 31 / 31 | **PASS** |
| `test_doom.py` | DOOM Master AI OS End-to-End Suite | 7 | 7 / 7 | **PASS** |
| `test_v526_hardening.py` | V5.2.6 Stress, Benchmarking & Boundary Tests | 30 | 30 / 30 | **PASS** |
| `test_v531_lifecycle_foundation.py` | V5.3.1 Lifecycle States, Events & Matrix | 25 | 25 / 25 | **PASS** |
| `test_v532_transaction_engine.py` | V5.3.2 State Machine, Transactions & Locks | 30 | 30 / 30 | **PASS** |
| **GRAND TOTAL** | **Complete DOOM Cognitive Regression** | **289** | **289 / 289** | **100% PASS** |

The baseline is intact, fully operational, and protected against regressions.

---

## 3. Current Architecture Audit

### 3.1 Memory Lifecycle Mutation Flow (V5.3.2)
All lifecycle state mutations in V5.3.2 are centralized within `MemoryLifecycleEngine` (`memory/lifecycle.py`):
```text
Caller (User/System/Task)
   │
   ▼
MemoryLifecycleEngine.transition_memory(memory_id, target_status, ...)
   │
   ▼ [database/postgres_db.py: transaction(lock_timeout_ms=3000)]
   ├── 1. Acquire PostgreSQL connection from pool
   ├── 2. BEGIN TRANSACTION; SET LOCAL lock_timeout = '3000ms'
   ├── 3. SELECT ... FROM memory_records WHERE memory_id = %s FOR UPDATE
   ├── 4. Validate canonical state transition against CANONICAL_TRANSITIONS matrix
   ├── 5. Enforce provenance verification rules (actor authority)
   ├── 6. Check database-indexed idempotency key
   ├── 7. UPDATE memory_records SET status = target_status, updated_at = NOW()
   ├── 8. INSERT INTO memory_lifecycle_events (...)
   └── 9. COMMIT TRANSACTION
   │
   ▼ [Post-Commit]
   └── _emit_lifecycle_telemetry(...) (trapped, non-fatal)
```

### 3.2 Memory Manager Write Flow (V5.1 / V5.3.2) & Required V5.3.3 Integration
When a new memory is created via `MemoryManager.store(record)`:
```text
Caller (write_experience, write_preference, write_semantic_fact)
   │
   ▼
MemoryManager.store(record)
   │
   ├── 1. Policy Evaluation (memory_write_policy.evaluate)
   │      - Enforces privacy classifications, confidence, verification status
   │      - SENSITIVE content rejected or marked
   ├── 2. Persist to PostgreSQL (memory_repository.store(record))
   │      - INSERT INTO memory_records (...) ON CONFLICT DO UPDATE
   │      - conn.commit()
   └── 3. Telemetry broadcast ("MEMORY_STORED")
```
**Forensic Audit Finding**: In V5.3.2, newly stored memories in `memory_records` never receive vector embeddings automatically because `MemoryManager.store()` has no link to `VectorStore`. 

**Required V5.3.3 Architecture Integration**:
V5.3.3 must integrate vector synchronization directly into the production memory creation path:
```text
MemoryManager.store(record)
   │
   ├── 1. Policy Evaluation (memory_write_policy.evaluate)
   ├── 2. PostgreSQL Authoritative Transaction:
   │      ├── INSERT INTO memory_records (..., status, generation=1)
   │      ├── IF status == 'ACTIVE' AND privacy_class != 'SENSITIVE':
   │      │       INSERT INTO vector_sync_queue (operation='UPSERT', target_generation=1, ...)
   │      └── COMMIT
   └── 3. Post-Commit Safe Dispatch:
          └── VectorSyncEngine.process_sync_item()
                 └── Generate embedding -> vector_store.store_embedding(..., generation=1)
```
- **ACTIVE memories**: Receive vectors automatically post-commit.
- **PENDING_VERIFICATION memories**: Must **NOT** be embedded; zero vector sync queued.
- **SENSITIVE memories**: Must **NEVER** be embedded; zero vector sync queued (hard policy guard).

### 3.3 Atomic Supersession Flow (V5.3.2)
When a record is superseded via `MemoryLifecycleEngine.supersede_memory(old_id, new_record)`:
```text
MemoryLifecycleEngine.supersede_memory(old_id, new_record)
   │
   ▼ [Single PostgreSQL Transaction]
   ├── 1. Lock old_id FOR UPDATE
   ├── 2. Validate transition (old_status -> SUPERSEDED)
   ├── 3. Idempotency check on old_id
   ├── 4. Set new_record.supersedes_memory_id = old_id
   ├── 5. INSERT new_record into memory_records
   ├── 6. UPDATE memory_records SET status = 'SUPERSEDED' WHERE memory_id = old_id
   ├── 7. INSERT audit event for old_id into memory_lifecycle_events
   └── 8. COMMIT
```
**Forensic Audit Finding**: Neither the old record's vector is deleted/invalidated, nor is a vector generated for the new record.

---

## 4. Current Vector Lifecycle

The vector subsystem consists of two abstraction layers:
1. `EmbeddingRouter` (`memory/embedding/router.py`):
   - FastEmbed provider (`sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions).
   - In-memory LRU cache (`EmbeddingCache`, default 500 items).
   - Policy filtering rejecting credentials, API keys, passwords (`PolicyViolationError`).
   - Graceful non-fatal degradation returning `None`.
2. `VectorStore` (`memory/vector_store/base.py`):
   - `NumPyVectorStorageAdapter` (`memory/vector_store/numpy_store.py`): in-memory dictionary `self._records: Dict[Tuple[str, str, str], StoredVectorRecord]`, bounded to 10,000 vectors, thread-safe with `threading.Lock()`, dot-product cosine similarity.
   - `PgVectorStorageAdapter` (`memory/vector_store/pgvector_store.py`): PostgreSQL table `memory_embeddings` with `vector(384)`, HNSW cosine index (`vector_cosine_ops`), `ON DELETE CASCADE` on `memory_records(memory_id)`.

---

## 5. Identified Consistency Problems (13-Point Matrix)

| # | Consistency Failure Mode | Current System Behavior | Root Cause | Impact on Cognitive Engine | Scope for V5.3.3 |
|---|---|---|---|---|---|
| **1** | **Zombie Vectors** | Embedding exists in vector store for a memory whose status in PostgreSQL is `SUPERSEDED`, `ARCHIVED`, or `DELETED`. | `MemoryLifecycleEngine` does not delete or invalidate vector entries on state transition. | Consumes `top_k` candidate slots in similarity search. Causes candidate starvation. | **MUST SOLVE** |
| **2** | **Missing Vectors** | Memory is `ACTIVE` in `memory_records`, but has no corresponding vector in vector store. | `MemoryManager.store()` does not trigger embedding generation post-commit; also occurs on process restart with NumPy. | Semantic retrieval cannot find the memory via conceptual similarity; falls back to lexical only. | **MUST SOLVE** |
| **3** | **Orphan Vectors** | Embedding exists in vector store, but parent `memory_id` does not exist in `memory_records`. | Physical deletion in PostgreSQL without NumPy deletion; rollback of uncommitted store; crash between stores. | Vector search returns ghost IDs; `memory_repository.get_by_id()` returns `None`. | **MUST SOLVE** |
| **4** | **Duplicate Vectors** | Multiple vector entries for the same `memory_id` or same content under identical model. | In NumPy, key is `(memory_id, model, model_version)` (no duplicates for exact key); in pgvector, unique constraint `uq_memory_model_version`. However, content drift without updating creates duplicates across versions. | Skews ranking distribution; wastes bounded memory capacity. | **MUST SOLVE** |
| **5** | **Stale Embeddings** | Memory content was updated in `memory_records`, but vector store retains embedding of old content. | In-place updates to `memory_records.content` without updating embedding. | Vector similarity reflects obsolete meaning; semantic false positives. | **MUST SOLVE** |
| **6** | **SUPERSEDED Memories Embedded** | Vector store retains embeddings for superseded memories. | Supersession marks old record `SUPERSEDED` in PostgreSQL, leaving old vector active. | Deprecated preferences/facts compete with modern facts in semantic search. | **MUST SOLVE** |
| **7** | **ARCHIVED Memories Embedded** | Archived memories remain in vector store. | Archival transitions record to `ARCHIVED`, but vector is not purged. | Cold project/session memories pollute active operational retrieval. | **MUST SOLVE** |
| **8** | **DELETED Memories Embedded** | Soft-deleted memories (`status='DELETED'`) remain in vector store. | Soft deletion does not trigger foreign key cascade in pgvector, and NumPy has no FK mechanism. | Deleted facts re-surface in candidate pools. | **MUST SOLVE** |
| **9** | **PENDING_VERIFICATION Embedded** | Unverified memories embedded prematurely. | Memories stored with `PENDING_VERIFICATION` status embedded before verification. | Uncorroborated or speculative data participates in semantic ranking. | **MUST SOLVE** |
| **10** | **PRIVATE/SENSITIVE Embedded** | Sensitive or secret memories embedded into vector store. | Missing privacy check at vector synchronization boundary. | Potential leakage of credentials or personal secrets via vector similarity or metadata. | **MUST SOLVE** (Zero Tolerance) |
| **11** | **Vectors Referencing Missing PG Records** | Vector search yields `memory_id` that returns `None` from PostgreSQL. | Out-of-band DB cleanup, truncated DB, or process restart divergence. | Wastes semantic candidate slots; log spam. | **MUST SOLVE** |
| **12** | **PG Memories Without Expected Vectors** | Active memories missing embeddings. | Process restart on NumPy fallback; initial ingestion without embedding; failed embedding router. | Asymmetric search: memory retrievable lexically but not semantically. | **MUST SOLVE** |
| **13** | **Model / Dimension Mismatches** | Stored vector has dimension $\ne 384$ or different embedding model. | Upgrading embedding model without migrating or reindexing existing vectors. | Vector distance calculations fail; `VectorValidationError` crashes search. | **MUST SOLVE** |

---

## 6. Zombie Vector Analysis & Candidate Starvation

### The Mechanics of Candidate Starvation
In `memory/retrieval.py`, semantic retrieval executes in Phase 2:
```python
# memory/retrieval.py lines 174-195
raw_matches = vector_store.search_similar(
    query_vector=emb_res.vector,
    top_k=MAX_SEMANTIC_CANDIDATES,  # Capped at 25
    model=emb_res.model,
    model_version=emb_res.model_version,
)

for m in raw_matches:
    if m.similarity < SEMANTIC_SIMILARITY_THRESHOLD:  # 0.40
        continue

    rec = memory_repository.get_by_id(m.memory_id)
    if not rec:
        continue

    # 1. Must be ACTIVE (exclude DELETED, SUPERSEDED, ARCHIVED)
    if rec.status != MemoryStatus.ACTIVE:
        continue

    # Append to semantic_candidates...
```

### The Starvation Scenario
1. Suppose a user has revised a backend preference 10 times:
   - `pref_01_v1` ("I prefer Django") -> `SUPERSEDED`
   - `pref_01_v2` ("I prefer Flask") -> `SUPERSEDED`
   - ...
   - `pref_01_v10` ("I prefer FastAPI") -> `ACTIVE`
2. In the current system, all 10 vectors exist in the vector store.
3. Because all 10 versions have nearly identical semantic phrasing regarding "backend framework preference", the vector store returns all 10 in `raw_matches` (ranked 1 through 10).
4. Now consider 20 superseded task memories that also match the query conceptually.
5. In total, 25 zombie vectors occupy slots 1 through 25 of `raw_matches`.
6. `MemoryRetriever` loops through the 25 items:
   - Evaluates `rec.status != MemoryStatus.ACTIVE`.
   - Rejects all 24 superseded items.
   - Only 1 item (`pref_01_v10`) survives.
7. Other valid, `ACTIVE` memories (e.g. database preferences, architecture rules) that had cosine similarities placing them at ranks 26–35 were **never returned** by `vector_store.search_similar()` because `top_k` truncated the search at 25.
8. **Result**: The agent receives only 1 semantic match instead of 10 relevant matches. The candidate pool was starved by zombies.

### Solution Requirements for V5.3.3:
1. **Active Vector Hygiene**: Zombie vectors must be purged from vector storage upon lifecycle transition.
2. **Candidate Over-Fetching**: `MemoryRetriever` must query `vector_store` with `top_k = MAX_SEMANTIC_CANDIDATES * 2` (50) to provide headroom against transiently unsynchronized vectors.
3. **Retrieval-Time Zombie Self-Healing**: When `MemoryRetriever` encounters a non-`ACTIVE` record during candidate resolution, it drops the candidate and asynchronously schedules vector deletion.

---

## 7. PostgreSQL Authority & Eventual Consistency Analysis

### 7.1 Authoritative vs Derived Stores
> **Rule 1**: PostgreSQL is the SOLE AUTHORITATIVE store for memory existence, identity, lifecycle status, privacy, and content.
> **Rule 2**: Vector storage is an UNPRIVILEGED, DERIVED, EVENTUALLY CONSISTENT acceleration index.
> **Rule 3**: Vector store state must NEVER override, influence, or dictate PostgreSQL lifecycle status.

### 7.2 Eventual Consistency Semantics
The architecture explicitly recognizes that PostgreSQL transitions and vector synchronization are **eventually consistent**:
- PostgreSQL commits atomically.
- Vector synchronization occurs post-commit.
- Therefore, there exists a **transient stale-vector window** ($\Delta t$) immediately following a PostgreSQL commit.
- **The Correctness Guarantees**:
  1. **Durable Intent**: Sync intent is durably committed inside PostgreSQL (`vector_sync_queue`) in the exact same transaction as the lifecycle update.
  2. **Eventual Convergence**: Vector storage state eventually converges to match authoritative PostgreSQL state.
  3. **Reconciliation Guarantee**: Discrepancies caused by unexpected process termination are repaired by reconciliation.
  4. **Zero Valid Zombie Retrieval**: Authoritative status checking in `MemoryRetriever` guarantees that even during the transient window $\Delta t$, **no non-ACTIVE memory can ever be emitted to cognition**.

---

## 8. NumPy Fallback Analysis (Windows Environment)

### Environment Reality
In the current Windows development environment:
- PostgreSQL is running (`localhost:5432`, db `Doom`, user `postgres`).
- The `pgvector` extension binary is **not installed** in PostgreSQL libraries (`[POSTGRES] [NOTE] pgvector not available; V5.2 will use NumPy fallback adapter`).
- Therefore, DOOM operates in **NumPy Fallback Mode** (`NumPyVectorStorageAdapter`).

### Architectural Characteristics of NumPy Fallback
1. **Process-Local & Non-Durable**:
   - NumPy vector storage itself is strictly in-memory (`self._records: Dict[Tuple[str, str, str], StoredVectorRecord]`) and **non-durable**.
   - PostgreSQL memory records are **durable**.
2. **Zero Cross-Process Sharing**: Independent OS processes have isolated in-memory vector dictionaries.
3. **Non-Transactional**: Cannot participate in PostgreSQL `BEGIN/COMMIT/ROLLBACK`.
4. **Capacity-Bounded**: Capped at `DEFAULT_MAX_NUMPY_VECTORS = 10000` items to prevent memory exhaustion.
5. **Exact Math**: Cosine similarity uses NumPy matrix multiplication (`np.dot(matrix, query_vector)`), which is exact ($O(N)$ brute-force) and sub-2ms for $N \le 10,000$.

### Mandatory V5.3.3 Architecture: NumPy Restart Recovery / Rehydration
Because NumPy vector storage is non-durable, V5.3.3 must implement **NumPy Restart Recovery / Rehydration**:
- Upon DOOM initialization, if `vector_store.backend == VectorStorageBackend.NUMPY_FALLBACK`:
  1. Inspect PostgreSQL: `SELECT memory_id, content, privacy_class, generation FROM memory_records WHERE status = 'ACTIVE' AND privacy_class != 'SENSITIVE';`
  2. Rebuild/rehydrate the in-memory vector dictionary from authoritative PostgreSQL state.
  3. Utilize bounded batching and in-memory LRU embedding cache to complete rehydration rapidly.

---

## 9. pgvector Compatibility Analysis

When pgvector is installed in production Linux / container environments:
1. Table `memory_embeddings` holds persistent vectors:
   ```sql
   CREATE TABLE memory_embeddings (
       embedding_id VARCHAR(100) PRIMARY KEY,
       memory_id VARCHAR(100) NOT NULL REFERENCES memory_records(memory_id) ON DELETE CASCADE,
       model VARCHAR(100) NOT NULL,
       model_version VARCHAR(30) NOT NULL,
       dimension INTEGER NOT NULL,
       embedding vector(384) NOT NULL,
       content_hash VARCHAR(64) NOT NULL,
       generation INTEGER NOT NULL DEFAULT 1,
       created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
       updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
       CONSTRAINT uq_memory_model_version UNIQUE (memory_id, model, model_version)
   );
   ```
2. **The Soft-Delete Invariant**:
   - `ON DELETE CASCADE` only activates on physical SQL `DELETE FROM memory_records`.
   - Because DOOM uses **soft deletion** (`UPDATE memory_records SET status = 'DELETED'`), `ON DELETE CASCADE` does **NOT** fire on soft deletion, supersession, or archival.
   - Therefore, pgvector requires the exact same post-commit vector synchronization engine as NumPy.
3. **Unified Abstraction**:
   - Both `NumPyVectorStorageAdapter` and `PgVectorStorageAdapter` implement the uniform `VectorStore` interface (`store_embedding`, `delete_embedding`, `has_embedding`, `search_similar`), guaranteeing 100% portability.

---

## 10. Proposed V5.3.3 Architecture

### 10.1 Evaluated Architecture Patterns

| Pattern | Description | Pros | Cons | Verdict |
|---|---|---|---|---|
| **Option A: Synchronous In-Transaction Sync** | Call `vector_store.store/delete` inside the PostgreSQL transaction block before `COMMIT`. | Immediate consistency. | Embedding generation (10–50ms) holds DB row locks; if vector store fails, aborts valid DB transaction; impossible for NumPy non-transactional store; violates Rule 4 & 6. | **REJECTED** |
| **Option B: Pure Fire-and-Forget In-Memory Worker** | Post-commit trigger fires Python thread/queue in memory. | Zero DB overhead. | If process crashes after commit, sync event is permanently lost; no crash recovery; no retry durability. | **REJECTED** |
| **Option C: Transactional Outbox Queue (`vector_sync_queue`) with Monotonic Generation** | Insert a sync work item into PostgreSQL `vector_sync_queue` in the same transaction as the lifecycle update. Process post-commit and via worker. Monotonic generation prevents stale-write races. | ACID durability of sync intent; zero lost events on crash; idempotent replay; non-blocking; monotonic ordering prevents races; supports both NumPy and pgvector. | Requires auxiliary schema in PostgreSQL. | **SELECTED & RECOMMENDED** |

### 10.2 Selected Architecture: Transactional Outbox Queue (`vector_sync_queue`)

```text
               MEMORY LIFECYCLE MUTATION OR STORE
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│             POSTGRESQL TRANSACTION BOUNDARY                 │
│                                                             │
│  1. Lock row FOR UPDATE (if lifecycle transition)           │
│  2. Validate state transition & provenance                  │
│  3. Increment generation: new_gen = current_gen + 1         │
│  4. UPDATE memory_records SET status = target_status,       │
│         generation = new_gen, updated_at = NOW()            │
│  5. INSERT INTO memory_lifecycle_events (...)               │
│  6. INSERT INTO vector_sync_queue (                         │
│        sync_id, memory_id, operation, target_status,        │
│        target_generation, idempotency_key,                  │
│        sync_status = 'PENDING'                              │
│     )                                                       │
│  7. COMMIT                                                  │
└─────────────────────────────────────────────────────────────┘
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
    [Immediate Post-Commit Hook]    [Background Worker / Startup Scan]
               │                               │
               └───────────────┬───────────────┘
                               ▼
            VectorSyncEngine.process_work_item(item)
                               │
            ┌──────────────────┴──────────────────┐
            ▼                                     ▼
   Operation == 'UPSERT'                Operation == 'DELETE'
            │                                     │
   ├── Check generation: is item stale?  ├── Check generation: is item stale?
   ├── Privacy check (SENSITIVE?)        ├── vector_store.delete_embedding(mid)
   ├── Check status == ACTIVE            └── UPDATE vector_sync_queue
   ├── Generate embedding via router           SET sync_status = 'SYNCED'
   ├── vector_store.store_embedding(..., gen)
   └── UPDATE vector_sync_queue
         SET sync_status = 'SYNCED'
```

---

## 11. Vector Sync State Model

The Vector Synchronization state model is strictly decoupled from the Memory Lifecycle state model.

### 11.1 Memory Lifecycle States (Authoritative PostgreSQL)
- `PENDING_VERIFICATION`
- `ACTIVE`
- `SUPERSEDED`
- `ARCHIVED`
- `DELETED`

### 11.2 Vector Synchronization States (`vector_sync_queue.sync_status`)
- `PENDING`: Work item enqueued within transaction; awaiting vector processing.
- `PROCESSING`: Picked up by sync runner; locked with `locked_until` lease.
- `SYNCED`: Successfully applied to `VectorStore`; vector representation is consistent with PostgreSQL.
- `RETRY_REQUIRED`: Transient failure encountered (e.g. embedding model warming up, temporary lock); scheduled for exponential backoff retry.
- `FAILED`: Terminal rejection (e.g. policy violation, unsupported dimension, corrupted vector data).
- `DEAD_LETTER`: Max retry attempts exceeded (`attempt_count >= max_attempts`). Requires manual or periodic reconciliation intervention.
- `RECONCILIATION_REQUIRED`: Flagged by reconciliation auditor as inconsistent with PostgreSQL authoritative state.

```
       [Enqueued in DB Transaction]
                     │
                     ▼
                 PENDING
                     │
        (Worker acquires work item)
                     │
                     ▼
                PROCESSING ──────────────────────────┐
                     │                               │
         ┌───────────┴───────────┐                   │
     (Success)               (Transient Error)  (Terminal Error)
         ▼                       ▼                   ▼
      SYNCED              RETRY_REQUIRED           FAILED
                                 │
                     (attempt < max_attempts)
                                 │
                     (attempt >= max_attempts)
                                 │
                                 ▼
                            DEAD_LETTER
```

---

## 12. Lifecycle → Vector Synchronization Mapping Rules

The exact rules governing vector operations for every lifecycle state and transition:

| Lifecycle Mutation / State | Pre-Condition | Target Vector Operation | Vector Store Action | Special Constraints |
|---|---|---|---|---|
| **Store New Memory** (`ACTIVE`) | Policy Approved | `UPSERT` | Generate embedding; `store_embedding(mid, vec, gen=1)` | If `privacy_class == SENSITIVE`, **DO NOT EMBED**. Mark queue `SYNCED_EXCLUDED`. |
| **Store New Memory** (`PENDING_VERIFICATION`) | Unverified Task / Fact | `NONE` | Ensure NO vector exists in `VectorStore`. | Unverified memories must never be retrievable semantically. |
| `PENDING_VERIFICATION -> ACTIVE` | Provenance Validated | `UPSERT` | Generate embedding; `store_embedding(mid, vec, gen=new_gen)` | Only embed if `privacy_class != SENSITIVE`. |
| `PENDING_VERIFICATION -> ARCHIVED` | Unverified -> Archive | `NONE` (or `DELETE`) | Verify no vector exists. | No-op if never embedded. |
| `ACTIVE -> SUPERSEDED` | Superseded by newer | `DELETE` | `delete_embedding(mid)` | Purge old vector immediately post-commit. |
| `1:1 Supersession` | Atomically links new | `DELETE` (old) + `UPSERT` (new) | Delete old vector; embed and store new vector. | Both sync items queued in the same atomic transaction. |
| `ACTIVE -> ARCHIVED` | User/System Archive | `DELETE` | `delete_embedding(mid)` | Archived records retained in DB, but removed from vector index. |
| `ACTIVE -> DELETED` | Soft Deletion | `DELETE` | `delete_embedding(mid)` | Soft-deleted records must have zero vector presence. |
| `SUPERSEDED -> ARCHIVED` | Historical Archive | `DELETE` (Idempotent) | `delete_embedding(mid)` | Safe no-op if vector was already deleted. |
| `SUPERSEDED -> DELETED` | Terminal Deletion | `DELETE` (Idempotent) | `delete_embedding(mid)` | Safe no-op if vector was already deleted. |
| `ARCHIVED -> DELETED` | Terminal Deletion | `DELETE` (Idempotent) | `delete_embedding(mid)` | Safe no-op if vector was already deleted. |

---

## 13. Post-Commit Execution Strategy

To satisfy Non-Negotiable Rule 5 (*"Vector synchronization must occur AFTER successful PostgreSQL commit"*):

1. **Dual-Trigger Architecture**:
   - **Trigger A (Immediate Fast-Path)**: Immediately after `conn.commit()` succeeds in `MemoryLifecycleEngine` or `MemoryManager`, invoke `VectorSyncEngine.trigger_post_commit(sync_id)`.
     - Executes synchronously or in a lightweight thread.
     - Fetches the queued item, performs embedding/vector operation, updates `sync_status = 'SYNCED'`.
     - Wrapped in a comprehensive `try...except` block: any failure in vector processing **never propagates** to the caller.
   - **Trigger B (Durable Sweeper / Recovery Path)**:
     - On startup and periodically (or on idle): scans `vector_sync_queue` for records where `sync_status IN ('PENDING', 'RETRY_REQUIRED')` and `(locked_until IS NULL OR locked_until < NOW())`.
     - Guarantees eventual consistency even if Trigger A crashes or is killed before execution.

---

## 14. Crash Window Analysis

| Crash Window | State of PostgreSQL | State of Vector Store | State of Queue | Recovery Action on Restart / Sweep |
|---|---|---|---|---|
| **Window 1**: Crash *before* PostgreSQL commit | Rolled back. No change to `memory_records` or `memory_lifecycle_events`. | Untouched. | Rolled back. Work item does not exist. | **Zero Inconsistency**. PostgreSQL ACID guarantees no partial state exists. |
| **Window 2**: PostgreSQL commits; application crashes *before* post-commit trigger runs | Committed. Record is in target status. | Untouched (stale vector remains or new vector missing). | Committed with `sync_status = 'PENDING'`. | **Fully Recoverable**. On restart, `VectorSyncEngine.startup_sweep()` discovers the `PENDING` item and applies it. |
| **Window 3**: Work item is picked up (`PROCESSING`); application crashes *during* vector operation | Committed. | Partially applied or unapplied. | Marked `PROCESSING` with `locked_until = T + 60s`. | **Lease Expiry Recovery**. After 60s, sweeper sees `locked_until < NOW()` and re-processes item with incremented `attempt_count`. |
| **Window 4**: Vector operation succeeds; application crashes *before* updating queue to `SYNCED` | Committed. | Vector is updated / deleted. | Remains `PROCESSING`. | **Idempotent Replay**. Sweeper re-executes operation. Since vector operations (`store_embedding`, `delete_embedding`) are strictly idempotent, replaying causes zero corruption and updates status to `SYNCED`. |

---

## 15. Idempotency Design

Every vector operation must be safe to execute multiple times with identical arguments:

### 15.1 Vector Operation Idempotency
1. **`store_embedding(memory_id, vec, model, version, hash, generation)`**:
   - In NumPy: `self._records[(memory_id, model, version)] = record`. Overwrites existing record atomically under lock, storing `generation`.
   - In pgvector: `ON CONFLICT (memory_id, model, model_version) DO UPDATE SET embedding = EXCLUDED.embedding, content_hash = EXCLUDED.content_hash, generation = EXCLUDED.generation, updated_at = NOW()`.
   - Result: Multiple repeated stores produce the exact same single record.
2. **`delete_embedding(memory_id)`**:
   - In NumPy: `self._records.pop(key, None)`. If key does not exist, returns `False` safely without error.
   - In pgvector: `DELETE FROM memory_embeddings WHERE memory_id = %s`. If row does not exist, affects 0 rows and returns `False` safely.
   - Result: Multiple repeated deletes produce the exact same result: vector does not exist.

### 15.2 Sync Queue Idempotency Keys
- Idempotency key pattern:
  $$\text{idempotency\_key} = \text{v533\_sync\_} + \text{memory\_id} + \text{\_} + \text{operation} + \text{\_} + \text{target\_status} + \text{\_gen\_} + \text{target\_generation} + \text{\_} + \text{content\_hash[:12]}$$
- Unique constraint on `vector_sync_queue(idempotency_key)`:
  - If a retry or duplicate lifecycle event attempts to enqueue identical sync work, `ON CONFLICT (idempotency_key) DO NOTHING` prevents redundant duplicate queue entries.

---

## 16. Vector Reconciliation Design

Reconciliation is the automated forensic mechanism that audits and repairs divergences between PostgreSQL and vector storage.

### 16.1 Reconciliation Scope
The reconciler scans for 5 anomaly patterns:
1. **Missing Vectors**: PostgreSQL has `status == 'ACTIVE'` and `privacy_class != 'SENSITIVE'`, but vector store has no entry for `(memory_id, model, version)`.
   - *Repair*: Enqueue `UPSERT` work item with current generation.
2. **Zombie Vectors**: Vector store has an entry for `memory_id`, but PostgreSQL has `status IN ('SUPERSEDED', 'ARCHIVED', 'DELETED')`.
   - *Repair*: Call `vector_store.delete_embedding(memory_id)`.
3. **Orphan Vectors**: Vector store has an entry for `memory_id`, but `memory_id` does not exist in `memory_records`.
   - *Repair*: Call `vector_store.delete_embedding(memory_id)`.
4. **Sensitive Leakage Vectors**: Vector store has an entry for a memory whose PostgreSQL record has `privacy_class == 'SENSITIVE'`.
   - *Repair*: **IMMEDIATE PURGE** of vector entry; log security audit alert.
5. **Model / Version / Dimension Mismatch**: Vector store has entries for deprecated models or incorrect dimensions.
   - *Repair*: Delete obsolete vector; regenerate with active model.

### 16.2 Reconciliation Triggers
- **Startup Reconciliation**: Runs upon DOOM initialization:
  - For NumPy fallback: executes full **Restart Recovery / Rehydration** of `ACTIVE` memories.
  - For pgvector: executes query-based discrepancy sweep in bounded batches (100 records per batch).
- **Manual / On-Demand API**: `reconcile_vector_store(batch_size=100, fix=True)` callable by system admin or test suites.
- **Opportunistic / Retrieval-Time Reconciliation**: When `MemoryRetriever` encounters a zombie during retrieval, it drops the candidate and queues an asynchronous vector delete.

---

## 17. ACTIVE-Only Retrieval Invariant & Starvation Defense

### 17.1 The Dual-Layer Defense Architecture
To ensure a stale vector can never become a valid cognitive memory result:

```
[Vector Store Similarity Search]
               │
               ▼ (Top 50 Candidates Over-Fetched)
┌─────────────────────────────────────────────────────────────┐
│ LAYER 1: AUTHORITATIVE POSTGRESQL VALIDATION FILTER         │
│                                                             │
│ For each candidate m in raw_matches:                        │
│   rec = memory_repository.get_by_id(m.memory_id)           │
│                                                             │
│   if not rec:                                               │
│       REJECT (Orphan) -> Queue Vector Delete                │
│                                                             │
│   if rec.status != MemoryStatus.ACTIVE:                     │
│       REJECT (Zombie) -> Queue Vector Delete                │
│                                                             │
│   if rec.privacy_class == PrivacyClass.SENSITIVE:           │
│       REJECT (Security Leak) -> Queue Immediate Purge       │
│                                                             │
│   if rec.privacy_class == PrivacyClass.PRIVATE and          │
│      not include_private:                                   │
│       REJECT (Authorization Filter)                         │
└─────────────────────────────────────────────────────────────┘
               │
               ▼ (Surviving Candidates: STRICTLY ACTIVE)
┌─────────────────────────────────────────────────────────────┐
│ LAYER 2: HYBRID RANKING & BOUNDED CONTEXT BUILDER           │
│                                                             │
│ - Merge with lexical candidates                             │
│ - Six-factor hybrid scoring (V5.2.4)                        │
│ - Truncate to MAX_RETRIEVAL_RECORDS (10)                    │
└─────────────────────────────────────────────────────────────┘
```

### 17.2 Mathematical Proof of Active Invariant
Let $C_{\text{raw}}$ be the set of candidate memory IDs returned by vector similarity search.
Let $M(id)$ be the authoritative memory record from PostgreSQL for $id \in C_{\text{raw}}$.
Let $R_{\text{final}}$ be the set of memories emitted by `MemoryRetriever.retrieve()`.

The algorithm enforces:
$$\forall m \in R_{\text{final}}: \quad m \in C_{\text{raw}} \implies \Big( M(m.\text{memory\_id}) \ne \text{None} \;\land\; M(m.\text{memory\_id}).\text{status} = \text{ACTIVE} \;\land\; M(m.\text{memory\_id}).\text{privacy\_class} \ne \text{SENSITIVE} \Big)$$

Therefore:
$$\forall m \in R_{\text{final}}, \quad m.\text{status} = \text{ACTIVE}$$
**No non-ACTIVE memory can ever enter $R_{\text{final}}$, regardless of vector store state.**

### 17.3 Over-Fetching Factor
To prevent zombie vectors from causing candidate starvation:
- Default `MAX_SEMANTIC_CANDIDATES = 25`.
- In V5.3.3: `vector_store.search_similar(top_k = MAX_SEMANTIC_CANDIDATES * 2)` ($k = 50$).
- If up to 25 zombie vectors exist in the vector store during transient sync, 25 valid `ACTIVE` candidates still pass through the filter, completely eliminating candidate starvation under normal operating conditions.

---

## 18. Concurrency & Monotonic Generation Model

### 18.1 The Stale-Write Race Condition Problem
A fundamental concurrency hazard exists in asynchronous vector synchronization:
```text
Worker 1 reads Memory A (Status=ACTIVE, Gen=10)
    │
    ▼ [Context switch / Worker 1 delayed generating embedding]
Lifecycle Engine transitions Memory A to SUPERSEDED (Gen=11)
    │
    ▼ [Transaction commits]
Vector Sync enqueues DELETE for Memory A (Gen=11)
    │
    ▼
Worker 2 processes DELETE (Gen=11) -> Deletes vector from VectorStore
    │
    ▼
Worker 1 finally completes embedding and executes UPSERT for Memory A (Gen=10)
    │
    ▼
CRITICAL DEFECT: Zombie vector for SUPERSEDED Memory A is resurrected!
```

### 18.2 Monotonic Generation Mechanism
To solve this race definitively, DOOM V5.3.3 adopts an explicit **Monotonic Generation / Versioning Mechanism**:
1. **Schema Column**: `memory_records.generation INTEGER NOT NULL DEFAULT 1`.
2. **Atomic Monotonic Increment**: Every lifecycle mutation or content update in PostgreSQL atomically increments the generation:
   $$\text{generation}_{\text{new}} = \text{generation}_{\text{current}} + 1$$
3. **Queue Snapshot**: `vector_sync_queue.target_generation` captures the exact integer generation at the moment of the authoritative transaction.
4. **Vector Store Generation Tracking**:
   - `StoredVectorRecord` includes `generation: int`.
   - `VectorStore.store_embedding(..., generation: int)`.
   - `VectorStore.delete_embedding(memory_id, generation: int)`.
   - Vector store maintains the highest generation seen for each `memory_id` ($\text{max\_gen}[mid]$).

### 18.3 Generation Enforcement Rules
Before applying any vector operation, the sync worker and vector store evaluate:
- **Rule G1 (Monotonic Stale UPSERT Rejection)**:
  An `UPSERT` with `target_generation = G_work` is **strictly rejected** if:
  $$G_{\text{work}} < \text{max\_gen}[mid]$$
  Or if PostgreSQL confirms:
  $$\text{authoritative\_gen}[mid] > G_{\text{work}} \quad\lor\quad \text{authoritative\_status}[mid] \ne \text{'ACTIVE'}$$
- **Rule G2 (Monotonic DELETE Precedence)**:
  A `DELETE` with `target_generation = G_del` sets:
  $$\text{max\_gen}[mid] = \max(\text{max\_gen}[mid], G_{\text{del}})$$
  Any subsequent or delayed `UPSERT` with $G_{\text{work}} \le G_{\text{del}}$ is immediately discarded as a no-op!
- **Rule G3 (Integer over Timestamps)**:
  Integer generations ($1, 2, 3, \dots$) are the sole authoritative ordering mechanism. Wall-clock timestamps (`updated_at`, `created_at`) remain observability metadata only and must never be used for synchronization ordering.

### 18.4 Application across Lifecycle Transitions
1. **`ACTIVE -> SUPERSEDED`**:
   - Old memory generation increments ($10 \to 11$). Queue records `DELETE(mid, target_gen=11)`.
   - Any delayed `UPSERT` from gen 10 is rejected ($10 < 11$). Zombie vector cannot be created.
2. **`ACTIVE -> ARCHIVED`**:
   - Memory generation increments ($5 \to 6$). Queue records `DELETE(mid, target_gen=6)`.
   - Any delayed `UPSERT` with gen 5 is rejected.
3. **`ACTIVE -> DELETED`**:
   - Memory generation increments ($2 \to 3$). Queue records `DELETE(mid, target_gen=3)`.
   - Any delayed `UPSERT` with gen 2 is rejected.
4. **Content Update**:
   - Memory generation increments ($3 \to 4$). Queue records `UPSERT(mid, target_gen=4)`.
   - If an old `UPSERT` for gen 3 executes late, it is rejected ($3 < 4$). Vector store only retains gen 4.
5. **`PENDING_VERIFICATION -> ACTIVE`**:
   - Memory generation increments ($1 \to 2$). Queue records `UPSERT(mid, target_gen=2)`.
   - Vector is stored with generation 2.
6. **1:1 Supersession**:
   - Old memory increments to $G_{\text{old}} + 1$ and queues `DELETE(old_id, gen=G_{\text{old}}+1)`.
   - New memory is inserted with `gen=1` and queues `UPSERT(new_id, gen=1)`.
   - Both executed atomically within the same transaction.

---

## 19. Failure Handling & Circuit Breakers

| Component Failure | Direct Consequence | Isolation & Recovery Mechanism |
|---|---|---|
| **Embedding Generation Fails** (FastEmbed OOM / timeout) | Vector cannot be created for new memory. | `EmbeddingRouter` catches exception; sync queue marks item `RETRY_REQUIRED`. PostgreSQL lifecycle state is 100% committed and uncorrupted. Memory remains retrievable via lexical search. |
| **Vector Store Store Fails** (NumPy limit / DB lock) | Vector not persisted. | Trapped by `VectorSyncEngine`. Queue item marked `RETRY_REQUIRED` with backoff. Telemetry emitted. |
| **Vector Store Delete Fails** | Zombie vector lingers in vector store. | Trapped; queue item scheduled for retry. `MemoryRetriever` Active-Filter catches and drops the zombie during retrieval. |
| **PostgreSQL Unavailable during Sync** | Worker cannot read queue. | Worker backs off exponentially (1s, 2s, 4s, ... max 30s). |
| **Deadlock in PostgreSQL** | Transaction rolled back by DB engine. | Handled by V5.3.2 retryable error classifier (`DeadlockDetectedError`). Retried up to 3 times. |

---

## 20. Security & Privacy Controls

1. **Sensitive Memory Protection (Zero Tolerance)**:
   - Memories with `privacy_class == PrivacyClass.SENSITIVE` must **NEVER** have vectors generated or stored.
   - Guard at `VectorSyncEngine`:
     ```python
     if record.privacy_class == PrivacyClass.SENSITIVE:
         # Hard reject: never generate embedding, never store vector
         return SyncResult(skipped=True, reason="SENSITIVE_MEMORIES_CANNOT_BE_EMBEDDED")
     ```
2. **Metadata Leakage Prevention**:
   - Vector store metadata dictionaries must **never** contain raw sensitive content, credentials, or decrypted tokens.
   - `StoredVectorRecord.to_metadata()` exports only: `embedding_id`, `memory_id`, `model`, `model_version`, `dimension`, `content_hash`, `generation`, timestamps.
3. **Context Fencing Compliance (V5.2.5)**:
   - Vector synchronization must not alter or bypass the context boundary delimiter rules (`<<<DOOM_MEMORY_UNTRUSTED_INJECTION_DEFENSE_V525>>>`).

---

## 21. Observability & Telemetry

V5.3.3 will introduce structured, sanitized telemetry events (zero raw text, zero vectors):

| Telemetry Event Name | Trigger Condition | Payload (Sanitized Only) |
|---|---|---|
| `VECTOR_SYNC_ENQUEUED` | Sync work item inserted into queue | `sync_id`, `memory_id`, `operation`, `target_status`, `target_generation` |
| `VECTOR_SYNC_STARTED` | Worker begins processing work item | `sync_id`, `memory_id`, `operation`, `attempt_count`, `target_generation` |
| `VECTOR_SYNC_SUCCESS` | Vector store successfully updated | `sync_id`, `memory_id`, `operation`, `target_generation`, `duration_ms` |
| `VECTOR_SYNC_FAILED` | Vector store update encountered error | `sync_id`, `memory_id`, `operation`, `error_class`, `retryable` |
| `VECTOR_SYNC_DEAD_LETTER` | Work item exhausted max retries | `sync_id`, `memory_id`, `operation`, `total_attempts` |
| `VECTOR_STALE_UPSERT_REJECTED` | Worker rejected stale UPSERT due to generation | `memory_id`, `work_generation`, `authoritative_generation` |
| `VECTOR_ZOMBIE_DETECTED` | Retrieval encountered non-ACTIVE vector | `memory_id`, `authoritative_status`, `query_hash` |
| `VECTOR_RECONCILIATION_RUN` | Reconciler executed sweep | `mode`, `scanned`, `zombies_purged`, `missing_synced`, `duration_ms` |

---

## 22. Performance & Capacity Benchmarks

### Bounded Target Metrics
- **Post-Commit Sync Overhead**: $\le 15\text{ ms}$ (including fast-path embedding and vector store update).
- **Vector Deletion Latency**: $\le 2\text{ ms}$ (NumPy dictionary pop or indexed SQL delete).
- **Retrieval Active-Filtering Overhead**: $\le 3\text{ ms}$ for 50 candidates in PostgreSQL (`SELECT ... WHERE memory_id IN (...)`).
- **NumPy Fallback Memory Footprint**: 10,000 vectors $\times 384\text{ floats} \times 4\text{ bytes} \approx 15.36\text{ MB}$ RAM.
- **Startup Rehydration**: $\le 500\text{ ms}$ for 500 active memories using batch embedding cache.

---

## 23. Production Integration Path

```text
User Request
    │
    ▼
DOOMCore.process_request()
    │
    ▼
CognitiveEngine.run_cycle()
    │
    ▼
MemoryRetriever.retrieve()
    ├── 1. VectorStore.search_similar(top_k=50) ──> Raw matches (may have transient zombies)
    ├── 2. PostgreSQL Bulk Authoritative Status Resolution (ACTIVE-only filter)
    │      └── Discards zombies/orphans; emits opportunistic cleanup
    ├── 3. Hybrid Ranker (V5.2.4)
    ├── 4. ContextBuilder (V5.2.5 Fencing)
    └── 5. Return MemoryContext to Reasoning Engine
```

On Write / State Mutation:
```text
Task Completion / User Command / MemoryManager.store()
    │
    ▼
MemoryLifecycleEngine.transition_memory() / supersede_memory() / MemoryManager.store()
    │
    ▼ [PostgreSQL Atomic Transaction]
    ├── Mutate memory_records & memory_lifecycle_events
    ├── Increment generation
    ├── INSERT vector_sync_queue work item (with target_generation)
    └── COMMIT
         │
         ▼ [Post-Commit Safe Dispatch]
         VectorSyncEngine.process_sync_item()
             ├── Check generation freshness against authoritative state
             ├── EmbeddingRouter.embed() (if UPSERT)
             └── VectorStore.store_embedding(..., gen) / delete_embedding(..., gen)
```

---

## 24. Test Strategy (V5.3.3 Test Matrix)

A dedicated test suite `test_v533_vector_sync.py` must be designed with the following categories:

| Category | Test Scenarios | Verification Target |
|---|---|---|
| **A. Sync Queue & Schema** | - Table creation and schema constraints<br>- Enqueue within transaction<br>- Rollback isolation (rolled back TX leaves 0 queue items) | Transaction atomicity |
| **B. Post-Commit Execution** | - Immediate post-commit trigger applies vector store update<br>- Vector store failure does NOT rollback or corrupt DB state<br>- Telemetry emission | Post-commit safety |
| **C. Lifecycle -> Vector Operations** | - `ACTIVE -> SUPERSEDED` deletes vector<br>- `ACTIVE -> DELETED` deletes vector<br>- `ACTIVE -> ARCHIVED` deletes vector<br>- `PENDING_VERIFICATION -> ACTIVE` creates vector<br>- 1:1 supersession deletes old vector and creates new vector | Stale vector elimination |
| **D. Active-Only Retrieval & Starvation** | - Inject 25 zombie vectors + 5 active vectors<br>- Over-fetching successfully retrieves all 5 active vectors<br>- Zero zombie vectors in final `MemoryContext` | Invariant enforcement |
| **E. Generation & Stale Sync Prevention** | - **Unit Test**: Stale generation rejection ($G_{\text{work}} < G_{\text{current}}$)<br>- **Concurrency Test**: Delayed generation-10 UPSERT racing with generation-11 DELETE<br>- **Failure-Injection Test**: Worker artificial delay; ensure generation-10 UPSERT cannot resurrect zombie vector<br>- **Replay Test**: Replaying older generation work items does not overwrite newer vector state | Monotonic race prevention |
| **F. Memory Creation Path** | - `MemoryManager.store(ACTIVE)` automatically enqueues and stores vector<br>- `MemoryManager.store(PENDING_VERIFICATION)` does NOT embed<br>- `MemoryManager.store(SENSITIVE)` throws policy reject; zero vector created | Creation path integration |
| **G. Security & Privacy** | - Storing `SENSITIVE` memory queues NO vector sync work<br>- Attempted sync of `SENSITIVE` memory throws policy rejection<br>- Metadata verification | Zero sensitive leakage |
| **H. Reconciliation & Recovery** | - Detect and repair missing vector<br>- Detect and purge zombie vector<br>- Detect and purge orphan vector<br>- Startup rehydration in NumPy fallback | Self-healing correctness |
| **I. Fault Injection & Crashes** | - Simulated crash after commit before sync<br>- Simulated crash during vector store operation<br>- Max attempt exhaustion to `DEAD_LETTER` | Resiliency and lease recovery |

---

## 25. Acceptance Criteria for V5.3.3

1. **Eventual Vector Convergence / Zero Valid Zombie Retrieval**:
   - Within $T_{\text{converge}} \le 100\text{ ms}$ of PostgreSQL commit (or upon completion of post-commit sync queue drain), vector storage reflects the exact authoritative state.
   - During any transient window before convergence, Layer 1 authoritative status check in `MemoryRetriever` mathematically guarantees that **zero non-ACTIVE memories are emitted to cognition**.
2. **Stale Generation Sync Rejection**:
   - A delayed or replayed sync work item with an older generation ($G_{\text{work}} < G_{\text{current}}$) is strictly rejected and cannot recreate a zombie vector after a newer lifecycle transition.
3. **Candidate Starvation Defense**:
   - In the presence of up to 25 zombie vectors, valid active memories are not starved out of the retrieval candidate pool due to 2x over-fetching ($k=50$).
4. **Post-Commit Guarantee**:
   - Vector store updates occur strictly after PostgreSQL commit; vector store failures cannot rollback or invalidate PostgreSQL transactions.
5. **Production Creation Integration**:
   - All memories stored via `MemoryManager.store()` with status `ACTIVE` and `privacy_class != 'SENSITIVE'` automatically receive vectors.
   - Memories stored with `PENDING_VERIFICATION` or `SENSITIVE` receive zero vectors.
6. **NumPy Restart Recovery / Rehydration**:
   - On startup in NumPy fallback mode, the in-memory vector store automatically rebuilds and rehydrates all active, non-sensitive memories from authoritative PostgreSQL state.
7. **Zero Sensitive Leakage**:
   - Memories with `privacy_class == 'SENSITIVE'` are never embedded, synchronized, or stored in vector storage.
8. **Strict Idempotency**:
   - All vector operations and sync work item replays are safe, repeatable, and idempotent.
9. **100% Regression Preservation**:
   - All 289 baseline tests pass without regression.

---

## 26. Scope Boundary & Non-Goals

### In Scope for V5.3.3
- `vector_sync_queue` table and schema management in PostgreSQL.
- `generation` column on `memory_records` and monotonic versioning engine.
- Integration of vector sync into `MemoryManager.store()`.
- `VectorSyncEngine` post-commit dispatcher and sweeper.
- Lifecycle-to-vector synchronization rules for all state transitions.
- Over-fetching and authoritative active filtering in `MemoryRetriever`.
- `VectorReconciliationEngine` (startup, manual, opportunistic).
- Startup recovery and rehydration for `NumPyVectorStorageAdapter`.
- Dedicated `test_v533_vector_sync.py` test suite.

### Strictly Deferred to Future Phases (DO NOT IMPLEMENT IN V5.3.3)
- **V5.3.4**: Supersession DAG, multi-parent consolidation ($N:1$, $1:N$), cycle detection, semantic contradiction resolution.
- **V5.3.5**: Memory freshness decay, automatic importance / confidence evolution algorithms.
- **V5.3.6**: Project-level lifecycle expansion, experience learning.
- **V5.3.7**: Cold storage archival, retention hardening, foreign key cascade removal.

---

## 27. Risks & Non-Blocking Findings

| Finding ID | Category | Description | Mitigation Strategy |
|---|---|---|---|
| **F01** | Performance | In NumPy fallback, startup rehydration of large memory corpuses ($> 5,000$ records) could increase startup time. | Implement bounded rehydration (hydrate top $N$ most important/recent memories first) and leverage `EmbeddingCache`. |
| **F02** | Consistency | Transient window between PostgreSQL commit and post-commit vector deletion (typically $< 5\text{ ms}$) where a zombie vector briefly exists in RAM. | Completely mitigated by Layer 1 authoritative status check in `MemoryRetriever`. Even during this window, the zombie is rejected on read. |
| **F03** | Database | `vector_sync_queue` table growth over time if completed (`SYNCED`) records are retained indefinitely. | Implement bounded retention: delete `SYNCED` records older than 7 days during startup reconciliation. |

---

## 28. Final Architecture Verdict

```
================================================================================
FINAL ARCHITECTURE AUDIT VERDICT:

   APPROVED — READY FOR V5.3.3 IMPLEMENTATION
   (Approved following completion of Post-Audit Correction Pass)

Baseline Status:
- Branch: DOOM-V5.2
- Commit: a35399bf12143264817126434145cb61ff319a38 (v5.3.2)
- Regression: 289 / 289 Tests PASS (100%)
- Baseline Working Tree: Intact and Protected

Architectural Prerequisites (Satisfied & Corrected):
1. Monotonic memory/vector generation mechanism ($G \in \mathbb{N}$) integrated to eliminate
   concurrent stale-UPSERT zombie resurrection races.
2. Formally defined "Eventual Vector Convergence / Zero Valid Zombie Retrieval"
   semantics with measurable convergence bounds ($T_{\text{converge}} \le 100\text{ ms}$).
3. Clarified "NumPy Restart Recovery / Rehydration" acknowledging process-local non-durability.
4. Integrated vector synchronization into the production memory creation path (MemoryManager.store()).
5. Formulated explicit generation-race test matrix (unit, concurrency, fault injection, replay).
6. Preserved strict scope boundaries (zero V5.3.4 DAG, zero V5.3.5 decay, zero V5.3.6 projects).
================================================================================
```
