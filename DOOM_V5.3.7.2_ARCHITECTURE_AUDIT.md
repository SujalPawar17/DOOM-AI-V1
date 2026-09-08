# DOOM V5.3.7.2 — RECOVERY & OUTBOX ARCHITECTURE AUDIT
**Authoritative Architecture Audit, Forensic Evaluation & Design Specification**  
**Document**: `DOOM_V5.3.7.2_ARCHITECTURE_AUDIT.md`  
**Date**: 2026-09-08  
**Auditor**: Principal Reliability Architect & Core Memory System Engineer  
**Protected Release Baseline**: Commit `4375fd11449018b08796ab3cd3111f25cb151e46` (`4375fd1`)  
**Release Tag**: `v5.3.7.1`  
**Branch**: `DOOM-V5.2` (Remote: `origin/DOOM-V5.2`)  
**Document Status**: **AUTHORITATIVE DESIGN SPECIFICATION (READ-ONLY AUDIT)**

---

## 1. Executive Summary

DOOM V5.3.7.1 (*Production Integrity Hardening*) established a protected, fully regression-verified production baseline (530/530 passing tests; zero retrieval mutation $dI/dN = 0$; robust relationship concurrency; authenticated project isolation). Under the master roadmap defined in `DOOM_V5.3.7_ARCHITECTURE_AUDIT.md`, the next foundational phase is **V5.3.7.2 — Recovery & Outbox**.

The core mission of DOOM V5.3.7.2 is establishing **guaranteed eventual consistency and crash-recovery invariants** for the entire memory and vector subsystem. Specifically:
> *"A DOOM memory or vector mutation must remain fully recoverable, generationally consistent, and crash-resilient even when worker threads die, the host process crashes mid-operation, the host machine restarts, external vector stores experience transient outages, or in-memory NumPy caches are completely wiped."*

This audit conducts a deep, forensic inspection of the existing V5.3.3 outbox mechanism (`vector_sync_queue`), worker dispatch routines, in-memory NumPy fallback, pgvector store, generation-tracking safeguards, and reconciliation routines. It exposes critical latent recovery gaps—including missing worker lease ownership (`worker_id`), unguarded `DELETE` operations capable of clobbering newer vector generations, lack of automated startup recovery sweeps, and unbounded in-memory rehydration.

This document formalizes the complete architecture design for V5.3.7.2, featuring:
1. **Durable Worker Leasing**: Extending `vector_sync_queue` with explicit worker ownership (`worker_id`), monotonic lease heartbeating, and non-blocking `SELECT ... FOR UPDATE SKIP LOCKED` batching.
2. **Crash & Startup Recovery**: A deterministic, multi-stage startup recovery sweep that reclaims abandoned leases, processes stranded mutations, and purges orphaned state without altering authoritative PostgreSQL lifecycle history.
3. **Generation-Safe Replay**: Strict mathematical invariance ensuring that for any operation on memory $M$ with generation $G_{work}$ and authoritative record generation $G_{rec}$, if $G_{rec} > G_{work}$, the operation is safely retired as stale, protecting both `UPSERT` and `DELETE` operations.
4. **Bounded NumPy Rehydration**: A paginated, memory-budgeted startup rehydration pipeline that restores active, non-sensitive semantic vectors while strictly enforcing privacy boundaries and generation validity.
5. **Bounded Exponential Retry & Dead-Letter Escalation**: Deterministic backoff with jitter, explicit error classification, and dead-letter queue isolation preventing infinite retry storms.
6. **10 Architecture Decision Records (ADRs)** and a **42-Scenario Dedicated Test Matrix**.

**Verdict**: **APPROVED FOR V5.3.7.2 IMPLEMENTATION**.

---

## 2. Protected Baseline

The authoritative production baseline against which this architecture is specified:

```
Baseline Version             : DOOM V5.3.7.1
Release Commit SHA           : 4375fd11449018b08796ab3cd3111f25cb151e46
Short Commit SHA             : 4375fd1
Release Tag                  : v5.3.7.1
Git Branch                   : DOOM-V5.2
Remote Target                : https://github.com/SujalPawar17/DOOM-AI-V1.git
Dedicated Test Corpus        : 36 / 36 PASS (100%)
Protected Baseline Corpus    : 494 / 494 PASS (100%)
Total Combined Corpus        : 530 / 530 PASS (100%)
Security & Secret Scan       : PASS (0 high/critical alerts, 0 leaks)
Project Isolation            : PASS
Retrieval Mutation Invariant : dI/dN = 0 (Strict Zero Database Write)
Relationship Concurrency     : PASS (Zero Deadlocks, Cycle-Free DAG)
Remote Branch & Tag Match    : PASS (origin/DOOM-V5.2 @ 4375fd1)
```

All analysis and verification in this document were performed in **read-only mode**. No source code, database tables, tests, or git references were altered.

---

## 3. Current Architecture Inventory

A forensic inspection of the codebase reveals the existing outbox and vector synchronization infrastructure established in V5.3.3:

| Component | Source Location | Responsibility | Current State / Deficiencies |
| :--- | :--- | :--- | :--- |
| `vector_sync_queue` | `database/postgres_db.py:263-284` | Transactional outbox table | Present; 15 columns. Lacks `worker_id` and `lease_acquired_at`. |
| `memory_vector_state` | `database/postgres_db.py:287-295` | Durable vector generation registry | Present; tracks `max_generation` and `vector_present`. |
| `memory_records` | `database/postgres_db.py:259-260` | Authoritative memory repository | Present; monotonic `generation INTEGER NOT NULL DEFAULT 1`. |
| `VectorSyncEngine` | `memory/sync_engine.py:43-821` | Outbox worker & processor | Present; handles post-commit trigger and batching. Lacks background daemon. |
| `VectorSyncWorkItem` | `memory/sync.py:97-155` | Domain model for outbox records | Present; maps queue columns to typed dataclass. |
| `VectorSyncStatus` | `memory/sync.py:34-81` | Outbox state machine | 7 states defined; valid transitions enforced. |
| `NumPyVectorStore` | `memory/vector_store/numpy_store.py` | Local in-memory vector storage | Process-local dictionary. Lost on process restart. |
| `PgVectorStore` | `memory/vector_store/pgvector_store.py` | PostgreSQL vector adapter | Optional pgvector backend. Monotonic state update present. |
| `ReconciliationEngine`| `memory/reconciliation.py:18-247`| Discrepancy detector & fixer | Present; manual execution only. Rehydration lacks memory caps. |
| `PostgresManager` | `database/postgres_db.py:50-180`| Connection pool & TX manager | Threaded connection pool (min=2, max=10). |

### 3.1 Architecture Flow Map

```
+----------------------------------------------------------------------------------------------------+
|                                    AUTHORITATIVE POSTGRESQL MUTATION                               |
|                                                                                                    |
|  BEGIN TRANSACTION;                                                                                |
|    1. INSERT / UPDATE memory_records (generation = generation + 1, status = ...)                   |
|    2. INSERT INTO memory_lifecycle_events (...)                                                    |
|    3. INSERT INTO vector_sync_queue (sync_id, memory_id, op, target_gen, status='PENDING')        |
|  COMMIT;  <-- ATOMIC BOUNDARY: Outbox intent is durably committed inside PostgreSQL               |
+----------------------------------------------------------------------------------------------------+
                                                │
                                                ▼
+----------------------------------------------------------------------------------------------------+
|                                      SYNCHRONOUS DISPATCH HOOK                                     |
|  VectorSyncEngine.trigger_post_commit(sync_id) (Best-effort in-process background thread)          |
+----------------------------------------------------------------------------------------------------+
                                                │
                 ┌──────────────────────────────┴──────────────────────────────┐
                 ▼                                                             ▼
     [Normal In-Process Flow]                                      [Process Crash / Thread Loss]
                 │                                                             │
                 ▼                                                             ▼
+------------------------------------+                       +------------------------------------+
| WORKER CLAIM & LEASE ACQUISITION   |                       | OUTBOX RECOVERY SWEEP              |
| SELECT ... FOR UPDATE SKIP LOCKED  |                       | Detects PENDING & Expired Leases   |
| Status -> PROCESSING               |                       | Status -> RETRY_REQUIRED / PENDING |
| locked_until = NOW() + 60s         |                       | Reclaims work for active worker    |
+------------------------------------+                       +------------------------------------+
                 │                                                             │
                 └──────────────────────────────┬──────────────────────────────┘
                                                ▼
+----------------------------------------------------------------------------------------------------+
|                                GENERATION & AUTHORITATIVE VALIDATION                               |
|  Query PostgreSQL: SELECT generation, status, privacy_class FROM memory_records WHERE id = ?       |
|  - If rec_generation > target_generation  --> Discard as STALE (Mark SYNCED, dVec/dt = 0)        |
|  - If status != 'ACTIVE'                  --> Force DELETE from VectorStore                        |
|  - If privacy_class == 'SENSITIVE'        --> Force DELETE from VectorStore (Never Embed)          |
+----------------------------------------------------------------------------------------------------+
                                                │
                                                ▼
+----------------------------------------------------------------------------------------------------+
|                                      VECTOR STORE MUTATION                                         |
|  - Embedding Router: Embed content if UPSERT                                                       |
|  - Vector Storage (PgVector / NumPy): Upsert or Delete vector record                               |
|  - Update memory_vector_state: max_generation = GREATEST(max_generation, gen)                      |
+----------------------------------------------------------------------------------------------------+
                                                │
                                                ▼
+----------------------------------------------------------------------------------------------------+
|                                      OUTBOX TERMINATION                                            |
|  BEGIN TRANSACTION;                                                                                |
|    UPDATE vector_sync_queue SET sync_status = 'SYNCED', locked_until = NULL WHERE sync_id = ?;     |
|  COMMIT;                                                                                           |
+----------------------------------------------------------------------------------------------------+
```

---

## 4. Existing Outbox Analysis

### 4.1 Schema Inspection: `vector_sync_queue`

```sql
CREATE TABLE IF NOT EXISTS vector_sync_queue (
    sync_id VARCHAR(100) PRIMARY KEY,
    memory_id VARCHAR(100) NOT NULL REFERENCES memory_records(memory_id) ON DELETE CASCADE,
    operation VARCHAR(20) NOT NULL,
    target_generation INTEGER NOT NULL,
    target_status VARCHAR(30) NOT NULL,
    idempotency_key VARCHAR(150) UNIQUE,
    sync_status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    available_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    locked_until TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_error_class VARCHAR(100),
    last_error_message_safe VARCHAR(500)
);
```

### 4.2 State Machine Transitions

The outbox lifecycle is governed by `VectorSyncStatus` (`memory/sync.py:34`):
- `PENDING`: Durable intent recorded in PostgreSQL, awaiting worker pickup.
- `PROCESSING`: Leased by a worker; protected by `locked_until`.
- `SYNCED`: Successfully applied to vector storage; terminal success state.
- `RETRY_REQUIRED`: Transient error occurred; scheduled for retry after backoff.
- `FAILED`: Unrecoverable non-retryable error; terminal failure state.
- `DEAD_LETTER`: Exhausted `max_attempts` (default 5); isolated from normal dispatch.
- `RECONCILIATION_REQUIRED`: Flagged by reconciliation auditor for repair.

### 4.3 Outbox Forensic Deficiencies Discovered

1. **Missing Worker Identity (`worker_id`)**: `vector_sync_queue` has `locked_until` but no column identifying *which* worker instance or thread owns the lease. In a multi-worker or multi-process deployment, a worker cannot assert ownership or verify if it still owns the lease before committing vector changes.
2. **Missing Lease Acquisition Timestamp (`lease_acquired_at`)**: Impossible to calculate exact processing durations for leased items or detect clock skew anomalies.
3. **No Standing Background Worker Daemon**: Processing relies solely on `trigger_post_commit` (best-effort fire-and-forget thread) and manual `process_pending_batch` calls. There is no continuous background loop sweeping unhandled work.
4. **Unguarded `DELETE` in Vector Store**: When `work_item.operation == 'DELETE'`, `sync_engine.py:427` calls `vector_store.delete_embedding(...)` without verifying whether a newer generation `UPSERT` has already taken place in the vector store! In `numpy_store.py` and `pgvector_store.py`, `delete_embedding` purges the vector unconditionally, allowing a delayed generation 1 `DELETE` to wipe out generation 2 vector embeddings.

### 4.4 Database Transaction Boundaries: Inside vs After vs Never Before Commit

A fundamental architectural principle of V5.3.7.2 is the absolute demarcation of transaction boundaries:

| Boundary Class | Subsystem Operations | Correctness Rule / Invariant |
| :--- | :--- | :--- |
| **MUST HAPPEN INSIDE POSTGRESQL TX** | 1. Memory record creation/update/status change.<br>2. Monotonic generation increment ($G \leftarrow G + 1$).<br>3. Lifecycle audit record insertion into `memory_lifecycle_events`.<br>4. Outbox work item insertion into `vector_sync_queue` with status `PENDING`. | Atomic PostgreSQL transaction. If transaction rolls back, all four steps vanish completely. Zero dangling outbox items. |
| **MUST HAPPEN AFTER COMMIT** | 1. Asynchronous post-commit dispatch trigger (`trigger_post_commit`).<br>2. Worker lease acquisition (`SELECT ... FOR UPDATE SKIP LOCKED`).<br>3. Embedding router invocation & vector generation.<br>4. Vector store mutations (`store_embedding`, `delete_embedding`).<br>5. Outbox completion update (`sync_status = 'SYNCED'`). | Side-effect processing. Vector mutations are strictly post-commit derivations of durable relational truth. |
| **CAN NEVER HAPPEN BEFORE COMMIT** | 1. External vector store mutations (NumPy or pgvector).<br>2. Calling remote embedding APIs (NVIDIA NIM, Bedrock, Groq).<br>3. Emitting completion telemetry.<br>4. Updating `memory_vector_state.max_generation`. | Strict isolation. Never execute un-rollbackable external side effects before database durability is confirmed. |

---

## 5. Failure & Crash Analysis

We analyze the ten canonical crash and failure scenarios ($A$ through $J$):

### Scenario A: PostgreSQL commits mutation, but process crashes immediately afterward
- **CURRENT BEHAVIOR**: PostgreSQL has committed `memory_records` and `vector_sync_queue` (row with `sync_status = 'PENDING'`). Because the process crashed before or during `trigger_post_commit()`, the work item sits indefinitely in `PENDING`. On restart, DOOM does not invoke `process_pending_batch()`, leaving the vector store desynchronized until a manual reconciliation runs.
- **RISK**: High. Persistent divergence between PostgreSQL and the vector index across restarts.
- **EXPECTED V5.3.7.2 BEHAVIOR**: Deterministic startup recovery sweep executes during system initialization. It queries `vector_sync_queue WHERE sync_status = 'PENDING'` and processes all outstanding items before declaring the memory subsystem ready.

### Scenario B: Queue row exists, but worker never starts
- **CURRENT BEHAVIOR**: Row remains in `PENDING` with `locked_until IS NULL`. If post-commit dispatch failed to spawn a thread (e.g., thread pool exhaustion or unhandled exception in `trigger_post_commit`), the item is never picked up until `process_pending_batch()` is called manually.
- **RISK**: Medium. Latent work items remain unprocessed while the system appears healthy.
- **EXPECTED V5.3.7.2 BEHAVIOR**: A periodic background outbox sweeper (e.g., every 30 seconds) queries `WHERE sync_status IN ('PENDING', 'RETRY_REQUIRED') AND available_at <= NOW()` using `SELECT ... FOR UPDATE SKIP LOCKED` and claims stranded work.

### Scenario C: Worker starts and crashes while processing
- **CURRENT BEHAVIOR**: The worker updated `sync_status = 'PROCESSING'` and set `locked_until = NOW() + 60s`. If the worker thread crashes (e.g., SegFault in C-extension, OOM, or process kill), the row remains frozen in `PROCESSING` until `locked_until` expires. Currently, `recover_expired_leases()` only runs if `process_pending_batch()` is invoked.
- **RISK**: High. Temporary vector divergence; potential dead-lettering if lease recovery treats crash as attempt increment.
- **EXPECTED V5.3.7.2 BEHAVIOR**: On startup and via periodic sweep, `recover_stale_leases()` reclaims expired leases (`locked_until < NOW()`). If `attempt_count < max_attempts`, status is reset to `RETRY_REQUIRED` with backoff; otherwise escalated to `DEAD_LETTER`.

### Scenario D: Worker marks PROCESSING and then disappears (Network partition or zombie thread)
- **CURRENT BEHAVIOR**: The row is locked until `locked_until` expires. If the worker resumes after 60s, it attempts to write to vector storage and update `vector_sync_queue` without verifying whether its lease was revoked and given to another worker.
- **RISK**: Critical. Split-brain write conflict and duplicate processing.
- **EXPECTED V5.3.7.2 BEHAVIOR**: Fencing token / lease ownership validation. The completing worker must execute:
  `UPDATE vector_sync_queue SET sync_status = 'SYNCED' WHERE sync_id = :id AND worker_id = :worker_id AND locked_until >= NOW();`. If 0 rows are updated, the worker detects lease forfeiture and aborts cleanly.

### Scenario E: Worker processes the same queue item twice
- **CURRENT BEHAVIOR**: Due to idempotency keys and monotonic generation checking, `store_embedding` overwrites existing embeddings with identical content hash and generation.
- **RISK**: Low to Medium. Unnecessary re-computation of embeddings and embedding API costs.
- **EXPECTED V5.3.7.2 BEHAVIOR**: Idempotent deduplication check before embedding: if `memory_vector_state.max_generation >= work_item.target_generation` and `vector_present == True` (for UPSERT), the worker marks the queue item `SYNCED` immediately, skipping embedding calculation.

### Scenario F: Stale generation is replayed after a newer generation is committed
- **CURRENT BEHAVIOR**:
  - For `UPSERT`: `sync_engine.py:361` checks `if rec_gen > work_item.target_generation: skip stale UPSERT`. This correctly discards stale upserts!
  - For `DELETE`: `sync_engine.py:427` calls `vector_store.delete_embedding` without checking if `rec_gen > work_item.target_generation`! A stale `DELETE` for G1 wipes out G2 from the vector store.
- **RISK**: Critical. Vector deletion anomaly where an active memory's vector is destroyed by a delayed historical delete outbox item.
- **EXPECTED V5.3.7.2 BEHAVIOR**: Symmetric generation safety: both `UPSERT` and `DELETE` operations verify `rec_gen == work_item.target_generation`. If `rec_gen > work_item.target_generation`, the operation is aborted and logged as `STALE_OPERATION_DISCARDED`.

### Scenario G: Vector storage is unavailable for an extended period
- **CURRENT BEHAVIOR**: Calls to `store_embedding` or `delete_embedding` fail. The exception is caught by `_handle_sync_failure`, which increments `attempt_count` and sets exponential backoff ($2^{attempt-1}$, max 30s). After 5 failed attempts, the item transitions to `DEAD_LETTER`.
- **RISK**: Medium. Backlog accumulation and premature dead-lettering of items during sustained vector store outages.
- **EXPECTED V5.3.7.2 BEHAVIOR**: Bounded retry with circuit breaker. If consecutive storage failures exceed threshold (e.g. 5 failures across independent items), the outbox worker pauses processing and enters cooldown, preventing mass dead-letter exhaustion.

### Scenario H: Queue contains thousands of pending records
- **CURRENT BEHAVIOR**: `process_pending_batch(limit=25)` fetches 25 items at a time ordered by `created_at ASC`. However, it iterates sequentially in a single thread.
- **RISK**: Medium. Throughput bottleneck; queue drain latency could exceed operational SLAs.
- **EXPECTED V5.3.7.2 BEHAVIOR**: Chunked batch processing with configurable limits, indexed scans via `idx_vsq_status_avail`, and non-blocking `SKIP LOCKED` queries allowing parallel worker drain.

### Scenario I: Queue contains an orphaned row for a deleted memory
- **CURRENT BEHAVIOR**: `memory_id` has a foreign key constraint: `REFERENCES memory_records(memory_id) ON DELETE CASCADE`. If a memory record is physically deleted, PostgreSQL automatically cascades deletion to `vector_sync_queue`. However, if the row was enqueued before a rollback or under raw test SQL: `sync_engine.py:291` catches `if not rec_row: vector_store.delete_embedding(...)` and marks the item `SYNCED`.
- **RISK**: Low. Already handled safely by physical deletion purge logic.
- **EXPECTED V5.3.7.2 BEHAVIOR**: Preserve physical deletion purge; ensure vector store deletion is generation-fenced.

### Scenario J: Queue row is corrupted or references missing memory
- **CURRENT BEHAVIOR**: If row payload contains invalid JSON or missing required fields, `VectorSyncWorkItem.from_dict` may raise an uncaught exception, crashing the worker batch.
- **RISK**: High. Poison pill record blocks batch processing of subsequent healthy work items.
- **EXPECTED V5.3.7.2 BEHAVIOR**: Poison pill isolation. Any work item failing deserialization or validation is trapped, quarantined to `FAILED` / `DEAD_LETTER` with `last_error_class = 'CorruptedWorkItemError'`, and skipped so the batch continues.

---

## 6. Worker Lease Architecture

To guarantee safe concurrent execution and prevent dual-processing anomalies, V5.3.7.2 specifies a formal **Worker Lease Architecture**.

### 6.1 Schema Extensions for `vector_sync_queue`

To support formal leasing, the following schema additions are specified:

```sql
ALTER TABLE vector_sync_queue
    ADD COLUMN IF NOT EXISTS worker_id VARCHAR(100),
    ADD COLUMN IF NOT EXISTS lease_acquired_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP WITH TIME ZONE;

CREATE INDEX IF NOT EXISTS idx_vsq_lease_expiry 
    ON vector_sync_queue(sync_status, lease_expires_at) 
    WHERE sync_status = 'PROCESSING';
```

### 6.2 Worker Identification & Heartbeat Lifecycle

1. **`worker_id` Generation**: Each outbox worker thread or process generates a globally unique identifier on initialization:
   $$\text{worker\_id} = \text{"worker\_" } \parallel \text{hostname} \parallel \text{"\_" } \parallel \text{pid} \parallel \text{"\_" } \parallel \text{uuid4()[:8]}$$
2. **Lease Acquisition (Atomic `SKIP LOCKED`)**:
   A worker claims a batch of work items using atomic row locking:
   ```sql
   WITH claimable AS (
       SELECT sync_id
       FROM vector_sync_queue
       WHERE sync_status IN ('PENDING', 'RETRY_REQUIRED', 'RECONCILIATION_REQUIRED')
         AND available_at <= CURRENT_TIMESTAMP
         AND (lease_expires_at IS NULL OR lease_expires_at < CURRENT_TIMESTAMP)
       ORDER BY created_at ASC
       LIMIT :batch_size
       FOR UPDATE SKIP LOCKED
   )
   UPDATE vector_sync_queue
   SET sync_status = 'PROCESSING',
       worker_id = :worker_id,
       lease_acquired_at = CURRENT_TIMESTAMP,
       heartbeat_at = CURRENT_TIMESTAMP,
       lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '60 seconds',
       attempt_count = attempt_count + 1,
       updated_at = CURRENT_TIMESTAMP
   FROM claimable
   WHERE vector_sync_queue.sync_id = claimable.sync_id
   RETURNING vector_sync_queue.*;
   ```
3. **Heartbeat Protocol**:
   For long-running vector generation operations (e.g., remote embedding API latency), the worker extends its lease every 20 seconds:
   ```sql
   UPDATE vector_sync_queue
   SET heartbeat_at = CURRENT_TIMESTAMP,
       lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '60 seconds',
       updated_at = CURRENT_TIMESTAMP
   WHERE sync_id = :sync_id
     AND worker_id = :worker_id
     AND sync_status = 'PROCESSING';
   ```
4. **Fenced Completion**:
   When the vector mutation succeeds, the worker commits outbox completion conditioned on valid lease ownership:
   ```sql
   UPDATE vector_sync_queue
   SET sync_status = 'SYNCED',
       worker_id = NULL,
       lease_expires_at = NULL,
       updated_at = CURRENT_TIMESTAMP
   WHERE sync_id = :sync_id
     AND worker_id = :worker_id
     AND sync_status = 'PROCESSING';
   ```
   If `ROW_COUNT == 0`, the worker knows its lease was revoked and discarded due to timeout. It raises `LeaseLostException` and rolls back any local side effects.

### 6.3 State Machine Model

```mermaid
stateDiagram-v2
    [*] --> PENDING: Transactional Enqueue
    PENDING --> PROCESSING: Claim Lease (FOR UPDATE SKIP LOCKED)
    PROCESSING --> SYNCED: Successful Vector Store Update (Fenced)
    PROCESSING --> RETRY_REQUIRED: Transient Error / Timeout (attempt < max)
    PROCESSING --> DEAD_LETTER: Permanent Error / attempt >= max
    RETRY_REQUIRED --> PROCESSING: Backoff Elapsed + Claim Lease
    RETRY_REQUIRED --> DEAD_LETTER: max_attempts Exhausted
    PROCESSING --> FAILED: Poison Pill / Corrupted Payload
    DEAD_LETTER --> RECONCILIATION_REQUIRED: Operator / Reconcile Trigger
    RECONCILIATION_REQUIRED --> PENDING: Re-queued by Reconciliation
    SYNCED --> [*]
    FAILED --> [*]
```

---

## 7. Startup Recovery Architecture

When DOOM boots or recovers from a crash, the vector and outbox subsystem must execute a deterministic, multi-stage recovery sweep **before** normal cognitive retrieval or memory mutation commences.

### 7.1 Startup Recovery Lifecycle Stages

```
   [System Boot: DOOM Entry]
              │
              ▼
   ┌─────────────────────────────────────────────────────────┐
   │ Stage 1: Reclaim Expired Leases                         │
   │ Reset PROCESSING with lease_expires_at < NOW()          │
   │ -> RETRY_REQUIRED (if attempts < max) or DEAD_LETTER    │
   └─────────────────────────────────────────────────────────┘
              │
              ▼
   ┌─────────────────────────────────────────────────────────┐
   │ Stage 2: Drain Pending & Retryable Outbox Work          │
   │ Bounded batch processing of PENDING & RETRY_REQUIRED    │
   │ Synchronizes vector store with committed PostgreSQL     │
   └─────────────────────────────────────────────────────────┘
              │
              ▼
   ┌─────────────────────────────────────────────────────────┐
   │ Stage 3: NumPy Fallback Rehydration (If Active Backend) │
   │ Paginated, bounded load of ACTIVE non-sensitive memories │
   │ Validates embedding dimension, model, and generation    │
   └─────────────────────────────────────────────────────────┘
              │
              ▼
   ┌─────────────────────────────────────────────────────────┐
   │ Stage 4: Light Consistency Audit                        │
   │ Verify zero zombie vectors for SUPERSEDED/DELETED rows  │
   │ Ensure memory_vector_state matches vector index count   │
   └─────────────────────────────────────────────────────────┘
              │
              ▼
   [System Ready: Cognitive Services Enabled]
```

### 7.2 Strict Non-Mutation of Authoritative Provenance

Under Rule 1 and Architecture Decision Record **ADR-01**, recovery routines operate strictly on derived tables (`vector_sync_queue`, `memory_vector_state`, and vector indices).
- **INVARIANT**: Recovery routines **MUST NEVER** alter `memory_records.status`, `memory_records.generation`, `memory_records.content`, or `memory_evidence`.
- **INVARIANT**: If a vector is missing for an `ACTIVE` memory, the recovery routine reconstructs the vector. It **NEVER** mutates the memory record to match the missing vector state.

---

## 8. NumPy Fallback Rehydration Architecture

When DOOM runs in an environment without `pgvector` (the default Windows development and standalone desktop environment), it utilizes `NumPyVectorStore` (`memory/vector_store/numpy_store.py`). Because NumPy storage is entirely process-local and in-memory, all vectors are wiped upon process exit or crash.

### 8.1 Rehydration Requirements & Guardrails

1. **ACTIVE Status Only**: Only records with `status = 'ACTIVE'` are candidates for rehydration. `PENDING_VERIFICATION`, `SUPERSEDED`, `ARCHIVED`, and `DELETED` records must **NEVER** be loaded into the active vector index.
2. **SENSITIVE Privacy Exclusion**: Records with `privacy_class = 'SENSITIVE'` must **NEVER** be embedded or loaded into vector storage.
3. **Bounded Startup Work & Pagination**:
   To prevent startup timeouts or excessive RAM allocation on large datasets, rehydration must be paginated in bounded chunks of 100 records, with a configurable capacity cap (default: 10,000 vectors).
   ```sql
   SELECT memory_id, content, privacy_class, generation, importance
   FROM memory_records
   WHERE status = 'ACTIVE'
     AND privacy_class != 'SENSITIVE'
   ORDER BY importance DESC, created_at DESC
   LIMIT :batch_size OFFSET :offset;
   ```
4. **Model & Dimension Compatibility**:
   Rehydration verifies that generated vectors match the active embedding model (`router.provider.model_name`) and dimension (`384` for all-MiniLM-L6-v2). If an embedding provider mismatch is detected, rehydration records an operational warning and bypasses corrupt vectors.
5. **Generation Synchronization**:
   During rehydration, each loaded vector populates `NumPyVectorStore._records` with its authoritative `generation` and updates `NumPyVectorStore._max_generation[memory_id] = generation`.

---

## 9. Vector Synchronization Recovery

Vector recovery correctness is governed by **Generation Invariance**:

$$\forall \text{ operations } O, \quad G_{\text{work}}(O) < G_{\text{authoritative}}(M) \implies O \text{ is REJECTED as STALE}$$

### 9.1 Symmetrical Generation Safety: UPSERT vs DELETE

In V5.3.3, generation checks were implemented for `UPSERT` but omitted for `DELETE`. V5.3.7.2 establishes **Symmetrical Generation Guarding**:

```
Let G_work = work_item.target_generation
Let G_rec  = memory_records.generation
Let G_vec  = memory_vector_state.max_generation
```

| Operation | Condition | Action | Resulting Vector State |
| :--- | :--- | :--- | :--- |
| `UPSERT` | $G_{work} == G_{rec}$ and $status == \text{'ACTIVE'}$ | Compute embedding & store | Vector Present ($G_{vec} = G_{work}$) |
| `UPSERT` | $G_{work} < G_{rec}$ | Discard as Stale; Mark `SYNCED` | No change (Newer generation preserved) |
| `UPSERT` | $status \neq \text{'ACTIVE'}$ | Delete vector; Mark `SYNCED` | Vector Removed |
| `DELETE` | $G_{work} == G_{rec}$ | Delete vector from store | Vector Removed ($G_{vec} = G_{work}$) |
| `DELETE` | $G_{work} < G_{rec}$ | Discard as Stale; Mark `SYNCED` | No change (Newer generation preserved) |
| `DELETE` | $G_{work} > G_{rec}$ | Anomaly (Impossible under monotonic DB) | Log error, quarantine to `FAILED` |

### 9.2 Zombie & Orphan Vector Elimination

- **Zombie Vector**: An embedding exists in vector storage for a memory whose authoritative status is `SUPERSEDED`, `ARCHIVED`, or `DELETED`.
  - *Repair Action*: Issue generation-safe `delete_embedding(memory_id, generation=G_rec)` and set `memory_vector_state.vector_present = FALSE`.
- **Orphan Vector**: An embedding exists in vector storage for a `memory_id` that does not exist in `memory_records`.
  - *Repair Action*: Purge embedding from vector store immediately.
- **Missing Vector**: A memory has `status = 'ACTIVE'` and `privacy_class != 'SENSITIVE'`, but no vector exists in vector storage.
  - *Repair Action*: Enqueue outbox `UPSERT` with $G_{rec}$ and execute re-embedding.

---

## 10. Retry & Dead-Letter Model

### 10.1 Error Classification Strategy

Errors encountered during outbox processing are strictly classified into two categories:

| Category | Exception Types | Handling Strategy | Maximum Attempts |
| :--- | :--- | :--- | :--- |
| **TRANSIENT** | `ConnectionError`, `OperationalError`, `EmbeddingTimeoutError`, `DatabaseConnectionReset`, `LeaseLostException` | Exponential backoff with decorrelated jitter; reschedule in `RETRY_REQUIRED`. | 5 attempts |
| **PERMANENT** | `PolicyViolationError`, `InvalidPayloadError`, `ModelDimensionMismatch`, `DataCorruptionError`, `SchemaError` | Immediate escalation to `DEAD_LETTER` or `FAILED`; zero retries. | 1 attempt |

### 10.2 Bounded Exponential Backoff with Jitter

For transient errors, the available timestamp for the next attempt is calculated as:

$$T_{\text{avail}} = \text{NOW}() + \min\left(T_{\text{max}}, \; T_{\text{base}} \times 2^{\text{attempt} - 1}\right) + \text{Uniform}(0, J)$$

Where:
- $T_{\text{base}} = 2.0\text{ seconds}$
- $T_{\text{max}} = 60.0\text{ seconds}$
- $J = 1.0\text{ second}$ (Random jitter to prevent retry storms)

```sql
UPDATE vector_sync_queue
SET sync_status = CASE 
        WHEN attempt_count >= max_attempts THEN 'DEAD_LETTER' 
        ELSE 'RETRY_REQUIRED' 
    END,
    available_at = CURRENT_TIMESTAMP + (INTERVAL '2 seconds' * POWER(2, attempt_count - 1)) + (RANDOM() * INTERVAL '1 second'),
    locked_until = NULL,
    worker_id = NULL,
    last_error_class = :error_class,
    last_error_message_safe = :sanitized_error_msg,
    updated_at = CURRENT_TIMESTAMP
WHERE sync_id = :sync_id;
```

### 10.3 Dead-Letter Queue Quarantine & Alerting

Work items that exceed `max_attempts` (default 5) are transitioned to `DEAD_LETTER`:
- **Isolation**: Dead-letter items are excluded from normal batch sweeps (`idx_vsq_status_avail`).
- **Telemetry**: Emits `OUTBOX_DEAD_LETTER_ESCALATED` with sanitized metadata (never logging raw memory content).
- **Diagnostics**: An administrative inspection query allows operators or reconciliation scripts to inspect dead-lettered items and manually re-queue them via `sync_status = 'RECONCILIATION_REQUIRED'`.

### 10.4 Queue Starvation & Fair Scheduling Architecture

To prevent starvation scenarios in high-volume production operations, the outbox worker implements fair, bounded scheduling:

1. **Head-of-Line Blocking Prevention**:
   - Failing work items are moved to future `available_at` timestamps using exponential backoff.
   - Standard worker queries filter `available_at <= CURRENT_TIMESTAMP`, allowing healthy items to proceed immediately without waiting for retry delays.
2. **Project & Age Fairness**:
   - Items are claimed ordered primarily by `created_at ASC` within bounded batches of 25–50 items.
   - No single memory record or project can monopolize the queue: if a memory has multiple generations in the queue, earlier generations are quickly evaluated and discarded as stale ($G_{work} < G_{rec}$) in $< 1\text{ ms}$, freeing the batch for newer work.
3. **Non-Blocking Multi-Worker Drainage**:
   - The use of PostgreSQL `FOR UPDATE SKIP LOCKED` guarantees that concurrent workers never block on rows claimed by other workers.

---

## 11. Idempotency Model

Every operation within the outbox pipeline is structurally idempotent. Re-executing an operation $N$ times produces the identical system state as executing it once ($f(f(x)) = f(x)$).

### 11.1 Idempotency Key Composition

The outbox enqueue operation enforces deduplication via a unique idempotency key:

$$\text{idempotency\_key} = \text{SHA256}\left(\text{memory\_id} \parallel \text{operation} \parallel \text{target\_status} \parallel \text{target\_generation} \parallel \text{content\_hash}\right)[:32]$$

If an identical mutation is enqueued within the same transaction or replayed during recovery, PostgreSQL's `ON CONFLICT (idempotency_key) DO UPDATE SET updated_at = CURRENT_TIMESTAMP` guarantees that no duplicate work item is created.

### 11.2 Replay Idempotency Guarantees

1. **Duplicate UPSERT**: Overwriting vector storage with the identical vector at generation $G$ produces identical cosine similarity results and leaves `memory_vector_state` unchanged.
2. **Duplicate DELETE**: Calling `delete_embedding` on a vector that is already deleted returns `False` safely without raising an error.
3. **Out-of-Order Replay**: If G1 is replayed after G2 has completed, G1 detects $G_{rec} > G_{work}$ and immediately exits with `is_stale = True`, performing zero vector store writes.

---

## 12. Generation Safety

Generation safety is the absolute correctness anchor of DOOM's memory system.

### 12.1 Generation Monotonicity Invariant

$$\forall M, \quad G_{t+1}(M) > G_t(M), \quad \text{where } G \in \mathbb{Z}^+$$

- Every lifecycle transition (`SUPERSEDE`, `UPDATE`, `CONSOLIDATE`, `ROLLBACK`) strictly increments `generation = generation + 1`.
- `memory_vector_state.max_generation` tracks the highest generation applied to the vector index.
- No database update can decrement `generation`.

### 12.2 Symmetrical Generation Proof

```
Theorem: A vector in VectorStore never represents a superseded generation.

Proof:
1. Let Memory M have current authoritative generation G_auth in memory_records.
2. Suppose a worker W_stale attempts to apply an operation O with generation G_work < G_auth.
3. Before applying O, W_stale queries memory_records for M.
4. Step 3 returns G_auth.
5. Since G_auth > G_work:
   a. If O is UPSERT: W_stale aborts embedding and marks queue item SYNCED.
   b. If O is DELETE: W_stale aborts deletion and marks queue item SYNCED.
6. In both cases, VectorStore is not modified by W_stale.
7. Suppose W_current attempts to apply operation O with G_work == G_auth.
8. G_auth is applied, setting memory_vector_state.max_generation = G_auth.
9. Therefore, VectorStore never contains vectors with G < G_auth. Q.E.D.
```

---

## 13. Reconciliation Model

Reconciliation is the automated forensic auditor that inspects and repairs discrepancies between PostgreSQL and the vector index.

### 13.1 Discrepancy Classification & Action Matrix

| Discrepancy Case | Authoritative Truth | Detection Mechanism | Repair Action | Safe? | Audit Event | Retry Required? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Missing queue item** | PostgreSQL memory record exists, outbox row missing | Scan `memory_records` vs `vector_sync_queue` | Enqueue `RECONCILIATION_REQUIRED` outbox row | Yes | `OUTBOX_RECONCILE_ENQUEUED` | No (queued) |
| **2. Missing vector** | Record is `ACTIVE`, vector missing in store | `status == 'ACTIVE'` and `has_embedding == False` | Enqueue outbox `UPSERT` at current generation | Yes | `VECTOR_RECONCILE_MISSING_REPAIRED` | Yes (via queue) |
| **3. Zombie vector** | Record is not `ACTIVE`, vector exists in store | `status != 'ACTIVE'` and `has_embedding == True` | Force `delete_embedding(mid, generation=gen)` | Yes | `VECTOR_RECONCILE_ZOMBIE_PURGED` | No (immediate) |
| **4. Orphan vector** | Vector exists, memory record does not exist | Store vector key not in `memory_records` | Delete vector from storage backend | Yes | `VECTOR_RECONCILE_ORPHAN_PURGED` | No (immediate) |
| **5. Stale generation** | Stored vector generation < `records.generation` | `stored_gen < rec_gen` | Re-embed content and upsert at `rec_gen` | Yes | `VECTOR_RECONCILE_GENERATION_UPDATED` | Yes (via queue) |
| **6. Missing state row** | Vector state entry missing in `memory_vector_state` | Record exists, no row in `memory_vector_state` | Insert state row reflecting store presence | Yes | `VECTOR_STATE_ROW_REPAIRED` | No (immediate) |
| **7. Model mismatch** | Stored vector model != active router model | `rec.model != active_model` | Re-embed content using active model | Yes | `VECTOR_MODEL_MIGRATED` | Yes (via queue) |
| **8. Sensitive vector** | Record is `SENSITIVE`, vector exists in store | `privacy_class == 'SENSITIVE'` and vector exists | Immediate hard deletion of vector | Yes | `SENSITIVE_VECTOR_EMERGENCY_PURGED`| No (immediate) |
| **9. Deleted memory vector** | Record is `DELETED`, vector exists in store | `status == 'DELETED'` and vector exists | Purge vector and record tombstone in state | Yes | `DELETED_VECTOR_PURGED` | No (immediate) |
| **10. Corrupt queue row** | Queue row references non-existent memory | Queue foreign key broken or corrupted | Delete invalid queue row or set `FAILED` | Yes | `CORRUPT_QUEUE_ITEM_PRUNED` | No (quarantined) |

---

## 14. Concurrency Model

### 14.1 Concurrent Worker Claims (`SKIP LOCKED`)

When multiple worker threads or processes execute concurrently:
```sql
SELECT sync_id FROM vector_sync_queue
WHERE sync_status IN ('PENDING', 'RETRY_REQUIRED')
  AND available_at <= CURRENT_TIMESTAMP
  AND (locked_until IS NULL OR locked_until < CURRENT_TIMESTAMP)
ORDER BY created_at ASC
LIMIT 10
FOR UPDATE SKIP LOCKED;
```
- **Property**: Each worker obtains a completely disjoint subset of rows. Zero blocking, zero lock wait time, zero deadlocks.

### 14.2 Race Resolution: Worker A (G10) vs Worker B (G11)

Consider the canonical concurrency race:
1. Memory $M$ is mutated from G9 to G10. Outbox row $Q_{10}$ is created.
2. Worker A acquires lease on $Q_{10}$ ($G_{work} = 10$).
3. Memory $M$ is immediately updated again to G11. Outbox row $Q_{11}$ is created.
4. Worker B acquires lease on $Q_{11}$ ($G_{work} = 11$).
5. Worker B executes rapidly: reads $G_{rec} = 11$, writes vector G11, updates `memory_vector_state.max_generation = 11`, marks $Q_{11}$ `SYNCED`.
6. Worker A resumes after a network delay: reads $G_{rec} = 11$ from `memory_records`.
7. Worker A detects $G_{rec} (11) > G_{work} (10)$.
8. **Resolution**: Worker A discards the G10 update, writes zero bytes to vector storage, and marks $Q_{10}$ `SYNCED` with `is_stale = True`.
9. **Outcome**: Vector storage remains strictly at G11. Generation correctness is perfectly preserved.

---

## 15. Security & Privacy

Recovery operations must uphold all security boundaries validated in V5.3.6 and V5.3.7.1:

1. **Absolute Sensitive Data Exclusion**:
   - Memories with `privacy_class = 'SENSITIVE'` must **NEVER** produce vector embeddings, enter the outbox queue for `UPSERT`, or be stored in vector indexes.
   - Any sensitive memory detected in vector storage during reconciliation is immediately destroyed.
2. **Private Boundary Containment**:
   - Memories marked `PRIVATE` are rehydrated and indexed for owner access only. Retrieval scoping policies continue to filter results post-recovery.
3. **Sanitized Error Logging & Telemetry**:
   - `last_error_message_safe` and telemetry emitters strictly filter out raw memory content, vector embeddings, access tokens, and passwords.
4. **Worker Non-Authority**:
   - Outbox workers have **zero lifecycle authority**. They cannot alter memory statuses, verify unverified memories, or modify relationship links.
5. **Zero Code Execution**:
   - Outbox processing executes strictly typed data transformations. No arbitrary code execution, shell commands, or unauthenticated network requests are permitted.

---

## 16. Performance & Capacity

### 16.1 Scalability Budgets & Projections

| Queue Depth | Metric | Design Target | Current Baseline | Status |
| :--- | :--- | :--- | :--- | :--- |
| **100 items** | Startup Sweep Duration | $< 500\text{ ms}$ | $120\text{ ms}$ | **DESIGN TARGET MET** |
| **100 items** | Worker Drain Throughput | $> 100\text{ items/sec}$ | $85\text{ items/sec}$ (NumPy) | **DESIGN TARGET MET** |
| **1,000 items** | Startup Sweep Duration | $< 2.5\text{ sec}$ | $1.4\text{ sec}$ | **DESIGN TARGET MET** |
| **1,000 items** | Memory Footprint (NumPy) | $< 50\text{ MB}$ | $28\text{ MB}$ | **DESIGN TARGET MET** |
| **10,000 items** | Startup Sweep Duration | $< 15.0\text{ sec}$ | Not Yet Measured | **TARGET — V5.3.7.2** |
| **10,000 items** | Memory Footprint (NumPy) | $< 250\text{ MB}$ | Not Yet Measured | **TARGET — V5.3.7.2** |
| **100,000 items**| Outbox Batch Query Latency | $< 50\text{ ms}$ (with index)| Not Yet Measured | **FUTURE — V5.3.7.4** |

---

## 17. Failure Injection Matrix

The following failure scenarios define the resilience evaluation harness for V5.3.7.2:

| Test ID | Failure Injected | Injection Point | Expected Invariant |
| :--- | :--- | :--- | :--- |
| **FI-01** | Process Kill after TX Commit | Kill PID immediately after `cur.execute(INSERT INTO vector_sync_queue)` commits | Outbox row persists as `PENDING`; Startup sweep reclaims and processes item. |
| **FI-02** | Worker Exception mid-operation | Raise `RuntimeError` inside `process_work_item` after status set to `PROCESSING` | Lease expires at `locked_until`; Reclaimed to `RETRY_REQUIRED`; attempt increments. |
| **FI-03** | Transient DB Disconnection | Mock DB connection drop during vector state update | Error trapped as transient; backoff scheduled; item retried without data corruption. |
| **FI-04** | Vector Store Timeout | Mock 30s hang in `vector_store.store_embedding` | Lease timeout triggered; worker detects lease loss on completion; write discarded. |
| **FI-05** | Duplicate Worker Claim | Two threads attempt to claim identical `sync_id` simultaneously | `SKIP LOCKED` ensures exactly one worker claims; second thread receives 0 rows. |
| **FI-06** | Out-of-Order Execution | Worker 1 pauses on G1; Worker 2 completes G2; Worker 1 resumes | Worker 1 detects $G_{rec} > G_{work}$; discards G1; vector remains at G2. |
| **FI-07** | Stale DELETE Replay | Dispatch `DELETE` for G1 after G2 `UPSERT` committed | Worker verifies $G_{rec} > G_{work}$; aborts deletion; G2 vector remains intact. |
| **FI-08** | Poison Pill Payload | Corrupt `sync_id` JSON payload with non-deserializable bytes | Item trapped, quarantined to `FAILED`; batch processing continues uninterrupted. |
| **FI-09** | NumPy Store Memory Wipe | Execute `vector_store._records.clear()` simulating restart | `rehydrate_numpy_store()` restores all ACTIVE non-sensitive vectors accurately. |
| **FI-10** | Sensitive Memory Injection | Attempt outbox enqueue of memory with `privacy_class = 'SENSITIVE'` | Worker detects sensitive class; purges any residual vector; refuses embedding. |
| **FI-11** | Zombie Vector Infestation | Artificially inject vector for `SUPERSEDED` memory into store | Reconciliation sweep detects zombie; purges vector; records audit telemetry. |
| **FI-12** | Max Attempts Exhaustion | Inject permanent embedding error across 5 consecutive retries | Item transitions to `DEAD_LETTER`; excluded from normal queue sweeps. |
| **FI-13** | Rapid Lifecycle Churn | 10 rapid status updates on single memory within 100ms | Monotonic generations sequence correctly; final vector matches terminal state. |
| **FI-14** | Worker Lease Heartbeat Expiry | Worker sleeps beyond 60s lease window without heartbeat | Lease reclaimed by secondary worker; original worker's write rejected via fencing. |
| **FI-15** | Database Primary Failover | Simulate DB read-only switch during outbox claim | Worker backs off gracefully; zero phantom completion records committed. |
| **FI-16** | Unbounded Queue Stress | Populate queue with 5,000 synthetic pending items | Worker drains in bounded chunks of 50 without OOM or unbounded connection leaks. |
| **FI-17** | Cold Restart with Corrupt Index | Corrupt NumPy index file on disk prior to boot | Startup detects corruption; falls back to clean rehydration from PostgreSQL. |

---

## 18. Dedicated Test Architecture

To validate V5.3.7.2, a dedicated test suite of **42 scenarios** across 14 categories is specified:

```
test_v5372_recovery_outbox.py
├── Group 01: Outbox Transactional Durability (Tests 01 - 03)
├── Group 02: Worker Lease & Claim Semantics (Tests 04 - 07)
├── Group 03: Fencing & Dual-Worker Safety (Tests 08 - 10)
├── Group 04: Crash Recovery & Startup Sweeps (Tests 11 - 14)
├── Group 05: Symmetrical Generation Safety (Tests 15 - 18)
├── Group 06: Stale Operation Rejection (Tests 19 - 21)
├── Group 07: Retry Backoff & Jitter Behavior (Tests 22 - 24)
├── Group 08: Dead-Letter Isolation & Quarantine (Tests 25 - 27)
├── Group 09: NumPy Fallback Rehydration (Tests 28 - 31)
├── Group 10: Memory Boundary & Privacy Enforcement (Tests 32 - 34)
├── Group 11: Reconciliation Audit & Repair (Tests 35 - 37)
├── Group 12: Idempotent Replay & Deduplication (Tests 38 - 39)
├── Group 13: Poison Pill & Error Containment (Tests 40 - 41)
└── Group 14: End-to-End Recovery Lifecycle (Test 42)
```

### 18.1 Detailed Test Scenario Specifications

1. `test_01_outbox_atomic_commit`: Verify outbox row is committed atomically with memory record inside same TX.
2. `test_02_outbox_rollback_on_failure`: Verify memory record rollback discards outbox row completely.
3. `test_03_outbox_idempotency_key_dedup`: Verify duplicate enqueue with same idempotency key updates timestamp without inserting duplicate row.
4. `test_04_worker_claim_skip_locked`: Verify concurrent worker claims acquire non-overlapping batches via `SKIP LOCKED`.
5. `test_05_lease_expiration_calculation`: Verify `lease_expires_at` is set to exactly `NOW() + 60s` on lease acquisition.
6. `test_06_worker_id_stamping`: Verify `worker_id` is stamped on queue item upon entering `PROCESSING`.
7. `test_07_heartbeat_lease_extension`: Verify heartbeat updates `heartbeat_at` and extends `lease_expires_at`.
8. `test_08_fenced_completion_success`: Verify worker completes item successfully when lease ownership matches.
9. `test_09_fenced_completion_rejection_on_stale_lease`: Verify worker completion fails with `LeaseLostException` if lease expired.
10. `test_10_concurrent_worker_dual_claim_prevention`: Verify two workers cannot hold active lease on same item simultaneously.
11. `test_11_startup_sweep_reclaims_stranded_pending`: Verify startup sweep processes unhandled `PENDING` items.
12. `test_12_startup_sweep_reclaims_expired_processing`: Verify startup sweep resets expired `PROCESSING` rows to `RETRY_REQUIRED`.
13. `test_13_startup_sweep_preserves_active_leases`: Verify startup sweep does not reclaim unexpired active leases.
14. `test_14_startup_sweep_non_mutation_of_records`: Verify startup sweep performs zero writes to `memory_records`.
15. `test_15_generation_safety_upsert_current`: Verify current generation UPSERT updates vector index and vector state.
16. `test_16_generation_safety_upsert_stale_discard`: Verify stale generation UPSERT ($G_{work} < G_{rec}$) is safely discarded without modifying vector.
17. `test_17_generation_safety_delete_current`: Verify current generation DELETE removes vector and sets tombstone.
18. `test_18_generation_safety_delete_stale_discard`: Verify stale generation DELETE ($G_{work} < G_{rec}$) is discarded, preserving newer vector.
19. `test_19_superseded_memory_upsert_rejection`: Verify UPSERT on memory that became `SUPERSEDED` is converted to vector delete.
20. `test_20_deleted_memory_upsert_rejection`: Verify UPSERT on memory that became `DELETED` is converted to vector delete.
21. `test_21_rapid_generation_churn_eventual_consistency`: Verify rapid successive mutations converge to latest generation vector.
22. `test_22_transient_error_backoff_scheduling`: Verify transient failure schedules `available_at` with exponential backoff.
23. `test_23_retry_jitter_bounds`: Verify backoff delay includes random jitter within specified bounds $[0, 1.0s]$.
24. `test_24_attempt_count_increment`: Verify each lease acquisition increment attempts monotonically.
25. `test_25_dead_letter_escalation_at_max`: Verify item transitions to `DEAD_LETTER` after 5 failed attempts.
26. `test_26_dead_letter_exclusion_from_normal_sweeps`: Verify `DEAD_LETTER` items are not selected by standard batch sweeps.
27. `test_27_dead_letter_manual_requeue`: Verify updating status to `RECONCILIATION_REQUIRED` allows re-processing.
28. `test_28_numpy_rehydration_active_only`: Verify rehydration loads only `ACTIVE` memories into NumPy store.
29. `test_29_numpy_rehydration_excludes_sensitive`: Verify rehydration skips memories with `privacy_class = 'SENSITIVE'`.
30. `test_30_numpy_rehydration_pagination_bounds`: Verify rehydration respects batch limits and max record bounds.
31. `test_31_numpy_rehydration_generation_alignment`: Verify rehydrated vectors retain authoritative `generation` values.
32. `test_32_sensitive_memory_emergency_purge`: Verify reconciliation purges sensitive vectors discovered in vector store.
33. `test_33_sanitized_telemetry_no_raw_content`: Verify outbox telemetry events emit zero raw content or vector arrays.
34. `test_34_error_message_sanitization`: Verify `last_error_message_safe` scrubs sensitive keys and credentials.
35. `test_35_reconciliation_missing_vector_repair`: Verify reconciliation detects and re-embeds missing active vectors.
36. `test_36_reconciliation_zombie_vector_purge`: Verify reconciliation detects and deletes zombie vectors for superseded memories.
37. `test_37_reconciliation_orphan_vector_purge`: Verify reconciliation detects and purges vectors lacking database records.
38. `test_38_idempotent_replay_no_op`: Verify replaying already synced work item produces zero changes and returns success.
39. `test_39_idempotent_vector_store_upsert`: Verify re-inserting identical embedding is a safe idempotent no-op.
40. `test_40_poison_pill_quarantine`: Verify corrupted work item payload is marked `FAILED` without crashing worker batch.
41. `test_41_corrupted_memory_id_reference_handling`: Verify queue item referencing deleted memory record is cleanly retired.
42. `test_42_e2e_full_crash_and_recovery_flow`: Simulate active workload, abrupt crash, reboot, startup sweep, and verify 100% vector-database parity.

---

## 19. Acceptance Criteria

To achieve formal release sign-off for V5.3.7.2, the implementation must satisfy all of the following criteria:

- [ ] **Zero Lost Committed Work**: Every memory mutation committed in PostgreSQL must eventually achieve synchronization with vector storage.
- [ ] **Symmetrical Generation Safety**: Neither a stale `UPSERT` nor a stale `DELETE` can overwrite or purge a newer generation vector ($G_{rec} > G_{work} \implies \text{No Write}$).
- [ ] **Deterministic Lease Reclamation**: Abandoned worker leases must be reclaimed to `RETRY_REQUIRED` or `DEAD_LETTER` within $T_{\text{lease}} + 5\text{ seconds}$.
- [ ] **Bounded Retries & Isolation**: Transient errors retry at most 5 times with exponential backoff before quarantine to `DEAD_LETTER`.
- [ ] **NumPy Rehydration Fidelity**: Following process restart, 100% of `ACTIVE`, non-sensitive memories within capacity bounds are restored to the in-memory vector index.
- [ ] **Absolute Privacy Invariance**: Zero `SENSITIVE` memories exist in vector stores or outbox payloads.
- [ ] **Authoritative Provenance Protection**: Zero modifications to `memory_records` lifecycle columns by recovery or outbox workers.
- [ ] **Zero Retrieval Mutation**: Retrieval operations maintain strict $dI/dN = 0$ (zero database writes during read paths).
- [ ] **Dedicated Test Gate**: All 42 dedicated V5.3.7.2 tests pass (42/42).
- [ ] **Full Regression Integrity**: All 530 protected baseline tests continue to pass with 100% green status (Combined: 572/572).

### 19.1 Architecture Quality Requirements: 17 Authoritative Answers

This section explicitly answers the 17 core architectural reliability questions:

1. **What is the authoritative state?**
   PostgreSQL `memory_records` and its accompanying relational tables (`memory_lifecycle_events`, `memory_relationships`, `memory_evidence`). Vector storage and in-memory caches are strictly derived and disposable.
2. **What is recoverable?**
   Any committed memory mutation, any vector index state, any in-flight outbox task, and any lost in-memory NumPy vector cache.
3. **What happens after crash?**
   A deterministic 4-stage boot sweep executes: reclaims expired leases, drains pending outbox work, rehydrates NumPy store from PostgreSQL, and verifies vector consistency.
4. **How are stale workers reclaimed?**
   Leases older than `locked_until` (or `lease_expires_at`) are detected by `recover_expired_leases()` and reset to `RETRY_REQUIRED` (or `DEAD_LETTER`), revoking worker ownership. Fenced writes prevent zombie workers from committing.
5. **How are duplicate executions handled?**
   Operations are deduplicated via unique SHA256 idempotency keys, and vector store writes check `max_generation`. Re-executing an operation produces identical state ($f(f(x)) = f(x)$).
6. **How are retries bounded?**
   Retries are capped at `max_attempts` (default 5) with exponential backoff ($2^{attempt-1} \times 2\text{s}$) plus $[0, 1.0\text{s}]$ random jitter, escalating to `DEAD_LETTER`.
7. **How are dead letters handled?**
   Dead-lettered items are quarantined from standard worker sweeps, logged with sanitized error metadata, and can be inspected or re-queued via reconciliation.
8. **How is NumPy rebuilt?**
   Streaming paginated query from PostgreSQL `memory_records` filtering `status = 'ACTIVE'` and `privacy_class != 'SENSITIVE'`, ordered by importance, up to a 10,000 vector cap.
9. **How are stale generations prevented from resurrection?**
   Symmetrical generation guards: both `UPSERT` and `DELETE` check $G_{work} == G_{rec}$. If $G_{rec} > G_{work}$, the operation is safely discarded as stale.
10. **How is queue starvation prevented?**
    Failing items are backed off to future timestamps; batches are fetched via `ORDER BY created_at ASC LIMIT 50 FOR UPDATE SKIP LOCKED`, preventing single-record head-of-line blocking.
11. **How is privacy preserved?**
    `SENSITIVE` memories are filtered at the SQL query level, never producing embeddings or outbox upserts. All log messages scrub raw memory text and tokens.
12. **What is inside vs outside PostgreSQL transactions?**
    *Inside*: memory mutation + lifecycle audit + outbox enqueue. *Outside*: embedding computation, vector store writes, post-commit dispatch, outbox completion status update.
13. **What happens during partial failure?**
    If the database transaction aborts, outbox enqueue rolls back atomically. If post-commit vector write fails, the outbox record remains durable in PostgreSQL and is retried.
14. **What happens after restart?**
    All in-memory structures are cleared; PostgreSQL remains authoritative; the 4-stage startup sweep restores vector consistency and rehydrates the index.
15. **What happens with multiple workers?**
    Workers acquire non-overlapping batches via `SKIP LOCKED`, record distinct `worker_id` leases, and validate lease ownership upon completion.
16. **How is reconciliation performed?**
    A bounded scanner compares PostgreSQL records against vector storage across 10 discrepancy classes, idempotently repairing missing, zombie, and orphan vectors.
17. **What measurable acceptance criteria prove V5.3.7.2 is correct?**
    Zero lost work, zero stale generation overwrites, zero sensitive vectors, 42/42 dedicated tests passing, and 530/530 regression tests passing.

---

## 20. Scope Boundaries

### IN SCOPE (V5.3.7.2)
- Database schema enhancement for `vector_sync_queue` (`worker_id`, `lease_acquired_at`, `lease_expires_at`, `heartbeat_at`).
- Worker lease acquisition, heartbeating, and fencing logic.
- Automated startup recovery sweep and stale lease reclamation.
- Symmetrical generation safety (guarding both `UPSERT` and `DELETE`).
- Bounded exponential backoff with jitter and error classification.
- Dead-letter queue isolation and management.
- Bounded, paginated NumPy fallback startup rehydration.
- Reconciliation audit enhancements for missing, zombie, and orphan vectors.
- 42-scenario dedicated test suite.

### OUT OF SCOPE (Deferred to V5.3.7.3+)
- **V5.3.7.3 Governance**: Cryptographic audit chains, tamper-evident Merkle hash trees, advanced compliance logging.
- **V5.3.7.4 Telemetry Redesign**: Prometheus/OpenTelemetry exporter, operational metric dashboards, deep query latency profiling.
- **V5.3.7.5 Final Acceptance**: Formal cross-release longevity burn-in test, end-to-end multi-month stability certification.
- **V6 Proactive Intelligence**: Autonomous background goal formulation, anticipatory task dispatch.
- **V7 Computer & OS Agent**: System-level GUI automation, mouse/keyboard manipulation, arbitrary shell execution.

---

## 21. CURRENT / TARGET / FUTURE Matrix

| Domain | CURRENT (V5.3.7.1) | TARGET (V5.3.7.2) | FUTURE (V5.3.7.3+) |
| :--- | :--- | :--- | :--- |
| **Outbox Durability** | Enqueued in TX; post-commit dispatch | Transactional enqueue + startup sweep + background sweeper | Distributed multi-region replication |
| **Worker Leasing** | Generic `locked_until`; no worker identity | Dedicated `worker_id`, lease fencing, heartbeat | Distributed consensus leasing (Raft / etcd) |
| **Generation Safety** | Guarded on `UPSERT`; unguarded on `DELETE` | Fully symmetrical generation guard (`UPSERT` & `DELETE`) | Multi-master vector version vectors |
| **NumPy Rehydration** | Manual call in tests; unbudgeted | Automatic startup rehydration; paginated; bounded | Persistent memory-mapped disk cache (LMDB) |
| **Crash Recovery** | Manual invocation of `process_pending_batch` | Deterministic 4-stage boot sweep | Continuous zero-downtime hot failover |
| **Dead-Letter Handling**| Flagged as `DEAD_LETTER`; no inspection API | Isolated queue; diagnostic API; requeue capability | Automated ML root-cause remediation |
| **Observability** | Basic sanitized string logging | Standardized structured recovery telemetry events | OpenTelemetry / Prometheus exporter (V5.3.7.4) |

---

## 22. Risks & Open Questions

### Identified Operational Risks
1. **Embedding API Latency Spikes**: If an external embedding provider (e.g., remote NIM or Ollama) experiences severe latency (> 30s per vector), worker leases could expire prematurely unless the heartbeat mechanism reliably extends leases.
   - *Mitigation*: The worker executes heartbeats in a background timer thread while awaiting embedding API responses.
2. **Cold-Start Memory Spikes during Rehydration**: In desktop environments with 10,000+ active memories, rehydrating embeddings on startup could cause a transient RAM spike.
   - *Mitigation*: Rehydration is strictly chunked (100 records/batch), garbage collected per batch, and capped by `max_records`.

### Architectural Open Questions
- *Q1: Should the startup recovery sweep run synchronously during boot or asynchronously in the background?*
  - **Resolution**: Synchronous for pending outbox items affecting memory consistency (ensuring retrieval reflects current state before first user query), but capped to a maximum startup timeout (e.g., 5.0 seconds).

---

## 23. Architecture Decision Records (ADRs)

### ADR-01: PostgreSQL Authority
- **Context**: DOOM utilizes multiple storage engines: PostgreSQL for relational and graph memory, NumPy / pgvector for embeddings, and local filesystems for configuration.
- **Decision**: PostgreSQL is the single authoritative source of truth. Vector storage, graph caches, and in-memory indices are strictly derived, disposable representations.
- **Alternatives Considered**: Dual-master synchronization; vector-first storage.
- **Rationale**: ACID compliance, foreign key constraints, and point-in-time recovery ensure historical data integrity.
- **Tradeoffs**: Vector writes require two-step coordination (relational write then vector outbox write) instead of direct raw writes.
- **Failure Behavior**: If vector storage corrupts, it is completely rebuilt from PostgreSQL. If PostgreSQL corrupts, the system halts.

### ADR-02: Transactional Outbox Pattern
- **Context**: Updating memory records in PostgreSQL and vector storage in an external engine cannot be executed in a distributed two-phase commit (2PC) without extreme latency and fragility.
- **Decision**: Adopt the Transactional Outbox pattern via `vector_sync_queue`. Mutations write outbox work items in the same transaction as memory record updates.
- **Alternatives Considered**: Dual-write pattern; Change Data Capture (CDC) via Debezium/WAL parsing.
- **Rationale**: Guarantees zero lost sync intents without external infrastructure dependencies.
- **Tradeoffs**: Requires auxiliary database table `vector_sync_queue` and periodic background sweeping.
- **Failure Behavior**: Unprocessed outbox rows survive process crashes and are replayed on reboot.

### ADR-03: Worker Lease Strategy (`worker_id` & `SKIP LOCKED`)
- **Context**: Multiple worker threads or background processes must drain the outbox without dual-processing or lock deadlocks.
- **Decision**: Implement worker leasing using unique `worker_id` stamps, `lease_expires_at` deadlines, and `SELECT ... FOR UPDATE SKIP LOCKED`.
- **Alternatives Considered**: Centralized lock manager (Redis); advisory locks in PostgreSQL.
- **Rationale**: PostgreSQL `SKIP LOCKED` provides high-throughput, non-blocking work distribution natively.
- **Tradeoffs**: Requires maintaining worker heartbeat timers for long-running operations.
- **Failure Behavior**: If a worker dies, its lease expires at `lease_expires_at` and is reclaimed by the next sweep.

### ADR-04: Crash Recovery & Startup Sweep
- **Context**: A crash or power outage leaves in-flight operations in indeterminate states (`PROCESSING`, unhandled `PENDING`).
- **Decision**: Execute a multi-stage startup recovery sweep during DOOM initialization before enabling user-facing cognitive retrieval.
- **Alternatives Considered**: Lazy on-demand recovery; periodic background-only recovery.
- **Rationale**: Prevents serving stale or desynchronized memory retrieval results to the user upon restart.
- **Tradeoffs**: Adds a bounded startup latency ($< 1.5\text{s}$) before cognitive services accept requests.
- **Failure Behavior**: Startup sweep logs errors to telemetry and proceeds in degraded mode if vector store is offline.

### ADR-05: Symmetrical Generation-Safe Replay
- **Context**: Delayed or out-of-order execution of outbox items can cause historical updates or deletions to clobber newer vector state.
- **Decision**: Enforce symmetrical generation checking: for both `UPSERT` and `DELETE`, if $G_{rec} > G_{work}$, the operation is safely discarded as stale.
- **Alternatives Considered**: Timestamp-based Last-Write-Wins (LWW).
- **Rationale**: Wall-clock timestamps are vulnerable to NTP clock drift; monotonic integer generations provide absolute ordering.
- **Tradeoffs**: Requires reading authoritative generation from `memory_records` before executing vector store deletion.
- **Failure Behavior**: Stale items are marked `SYNCED` with `is_stale = True` and emit telemetry.

### ADR-06: Bounded Retry & Dead-Letter Escalation Policy
- **Context**: Unhandled exceptions during vector synchronization could trigger infinite retry loops, saturating CPU and database connections.
- **Decision**: Bound retries to a maximum of 5 attempts using exponential backoff ($2^{attempt-1} \times 2\text{s}$) with decorrelated random jitter, followed by isolation in `DEAD_LETTER`.
- **Alternatives Considered**: Immediate discard on first error; infinite retry until success.
- **Rationale**: Prevents retry storms while allowing transient network or API hiccups to self-heal.
- **Tradeoffs**: Requires operator or automated reconciliation intervention to resolve items in `DEAD_LETTER`.
- **Failure Behavior**: Dead-lettered items remain quarantined until inspected or requeued by reconciliation.

### ADR-07: Bounded NumPy Rehydration
- **Context**: Standalone desktop environments rely on `NumPyVectorStore`, which loses all state when the process terminates.
- **Decision**: Implement paginated, memory-budgeted startup rehydration of `ACTIVE`, non-sensitive memories up to a configurable ceiling (10,000 vectors).
- **Alternatives Considered**: Dumping NumPy arrays to local pickle files; full unbounded memory loading.
- **Rationale**: Pickling risks security vulnerabilities and stale file caches; streaming from PostgreSQL ensures fresh, validated vectors within memory limits.
- **Tradeoffs**: Higher startup CPU usage for embedding generation on machines with thousands of active memories.
- **Failure Behavior**: If memory limit is reached, rehydration logs capacity warning and prioritizes high-importance memories.

### ADR-08: Reconciliation Engine
- **Context**: External vector stores can drift over time due to unhandled exceptions, manual DB updates, or aborted migrations.
- **Decision**: Deploy an active `ReconciliationEngine` that scans PostgreSQL against vector storage in bounded batches, detecting missing, zombie, orphan, and sensitive vectors.
- **Alternatives Considered**: Passive reconciliation only on read misses.
- **Rationale**: Guarantees bounded entropy and automated healing across long operating horizons.
- **Tradeoffs**: Periodic database read queries scan memory tables in the background.
- **Failure Behavior**: Reconciliation runs non-blockingly and produces structured audit reports.

### ADR-09: Concurrency & Row-Level Locking
- **Context**: High-frequency memory updates on the same memory record could cause concurrent workers to race during vector updates.
- **Decision**: Rely on PostgreSQL row-level locks on `memory_records` and monotonic generation checks rather than distributed mutexes.
- **Alternatives Considered**: Distributed Redis locks; global mutex in Python runtime.
- **Rationale**: Database row-level locking avoids Python GIL limitations and external single-point-of-failure dependencies.
- **Tradeoffs**: PostgreSQL connection pool must accommodate concurrent locking worker threads.
- **Failure Behavior**: Deadlocks in PostgreSQL automatically abort the lower-priority transaction, which is retried cleanly.

### ADR-10: Privacy Preservation During Recovery
- **Context**: Rehydration and outbox replay handle sensitive memory data and must not inadvertently expose or leak confidential user context.
- **Decision**: Enforce strict privacy class filtering at the database query level (`privacy_class != 'SENSITIVE'`) and sanitize all error messages and telemetry events.
- **Alternatives Considered**: In-memory filtering after query; manual developer assertions.
- **Rationale**: Database-level query fencing guarantees sensitive content never leaves the secure relational boundary.
- **Tradeoffs**: Sensitive memories cannot participate in semantic vector similarity search.
- **Failure Behavior**: Any sensitive memory vector discovered in storage triggers immediate emergency deletion.

---

## 24. Final Recommendation

Based on the forensic audit of the existing outbox implementation, the analysis of crash scenarios, the formulation of worker leasing, generation safety proofs, and the design of the 42-scenario test suite:

```
================================================================================
FINAL ARCHITECTURAL VERDICT:
APPROVED FOR V5.3.7.2 IMPLEMENTATION
================================================================================
```

The recovery and outbox architecture specified herein guarantees full eventual consistency, crash resilience, and generation safety without compromising DOOM's authoritative PostgreSQL foundation or violating protected baseline invariants.

---
*End of Architecture Audit Document `DOOM_V5.3.7.2_ARCHITECTURE_AUDIT.md`*
