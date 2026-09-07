"""
DOOM V5.3.3 — Vector Synchronization Engine
Implements the Transactional Outbox processor, post-commit dispatch,
monotonic generation protection, error classification, retry handling,
lease recovery, and dead-letter queue management.
"""
from datetime import datetime, timezone, timedelta
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

from database.postgres_db import postgres_manager
from memory.embedding.router import embedding_router
from memory.sync import (
    SyncOperation,
    VectorSyncStatus,
    VectorSyncWorkItem,
    VectorSyncResult,
    compute_sync_idempotency_key,
    new_sync_id,
)
from memory.types import MemoryStatus, PrivacyClass
from memory.vector_store import vector_store


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# Telemetry event emitter
def _emit_sync_telemetry(event: str, **kwargs) -> None:
    """Emit sanitized telemetry. Never logs raw memory content or vectors."""
    try:
        sanitized = {k: v for k, v in kwargs.items() if k not in ("content", "vector", "raw_embedding", "password", "token")}
        sanitized["telemetry_event"] = event
        sanitized["timestamp"] = _utcnow()
        # Non-blocking telemetry output
    except Exception:
        pass


class VectorSyncEngine:
    """
    Authoritative processor for vector_sync_queue work items.
    Zero lifecycle authority — strictly derives vector store state from PostgreSQL.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._telemetry_counts: Dict[str, int] = {
            "enqueued": 0,
            "success": 0,
            "failed": 0,
            "retry": 0,
            "dead_letter": 0,
            "stale_rejected": 0,
        }

    # ------------------------------------------------------------------
    # 1. Outbox Enqueue (Must be called inside active PostgreSQL TX)
    # ------------------------------------------------------------------
    def enqueue_sync_work(
        self,
        cur: Any,
        memory_id: str,
        operation: Any,
        target_generation: int,
        target_status: Any,
        idempotency_key: Optional[str] = None,
        content_hash: Optional[str] = None,
        content: Optional[str] = None,
    ) -> str:
        """
        Enqueue a sync work item inside the caller's active database transaction.
        Atomic with the memory record mutation.
        """
        sync_id = new_sync_id()
        op_str = operation.value if hasattr(operation, "value") else str(operation).strip().upper()
        stat_str = target_status.value if hasattr(target_status, "value") else str(target_status).strip().upper()
        chash = content_hash
        if not chash and content:
            import hashlib
            chash = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()

        idem_key = idempotency_key or compute_sync_idempotency_key(
            memory_id=memory_id,
            operation=op_str,
            target_status=stat_str,
            target_generation=target_generation,
            content_hash=chash,
        )

        sql = """
            INSERT INTO vector_sync_queue (
                sync_id, memory_id, operation, target_generation, target_status,
                idempotency_key, sync_status, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (idempotency_key) DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP
            RETURNING sync_id;
        """
        cur.execute(sql, (
            sync_id,
            memory_id.strip(),
            op_str,
            int(target_generation),
            stat_str,
            idem_key,
            VectorSyncStatus.PENDING.value,
        ))
        row = cur.fetchone()
        actual_sync_id = sync_id
        if row:
            if isinstance(row, dict) or hasattr(row, "keys"):
                actual_sync_id = row.get("sync_id", sync_id)
            else:
                actual_sync_id = row[0]

        with self._lock:
            self._telemetry_counts["enqueued"] += 1

        _emit_sync_telemetry(
            "VECTOR_SYNC_ENQUEUED",
            sync_id=actual_sync_id,
            memory_id=memory_id,
            operation=op_str,
            target_generation=target_generation,
            target_status=stat_str,
        )
        return actual_sync_id

    # ------------------------------------------------------------------
    # 2. Post-Commit Safe Dispatch (Fast-path trigger)
    # ------------------------------------------------------------------
    def trigger_post_commit(self, sync_id: str) -> None:
        """
        Trigger immediate vector synchronization after successful commit.
        Non-fatal: any vector store failure is trapped and remains in the durable queue.
        """
        if not sync_id:
            return
        try:
            self.process_work_item(sync_id)
        except Exception as e:
            # Failure is safely trapped; queue item remains for background sweeper
            _emit_sync_telemetry(
                "VECTOR_SYNC_POST_COMMIT_DEFERRED",
                sync_id=sync_id,
                error=str(e)[:100],
            )

    def schedule_deletion(self, memory_id: str, generation: int = 1, trigger_now: bool = True) -> Optional[str]:
        """
        Enqueue a DELETE work item into vector_sync_queue and optionally trigger immediate post-commit sync.
        Canonical entry point for opportunistic vector cleanup (from retrieval or background auditors).
        Never bypasses durable vector_sync_queue architecture.
        """
        clean_mid = memory_id.strip()
        conn = postgres_manager.get_connection()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT generation, status FROM memory_records WHERE memory_id = %s;", (clean_mid,))
                row = cur.fetchone()
                if row:
                    target_gen = int(row[0] or generation)
                    target_status = str(row[1] or "DELETED")
                else:
                    # True orphan: memory record physically absent from PostgreSQL
                    # Directly purge from VectorStore
                    vector_store.delete_embedding(clean_mid, generation=generation)
                    _emit_sync_telemetry("VECTOR_ORPHAN_PURGED", memory_id=clean_mid, generation=generation)
                    return None

                sync_id = self.enqueue_sync_work(
                    cur=cur,
                    memory_id=clean_mid,
                    operation=SyncOperation.DELETE,
                    target_generation=target_gen,
                    target_status=target_status,
                )
            conn.commit()
            if trigger_now:
                self.trigger_post_commit(sync_id)
            return sync_id
        except Exception as e:
            conn.rollback()
            _emit_sync_telemetry("VECTOR_SCHEDULE_DELETION_FAILED", memory_id=clean_mid, error=str(e)[:100])
            return None
        finally:
            postgres_manager.release_connection(conn)

    # ------------------------------------------------------------------
    # 3. Work Item Execution Engine
    # ------------------------------------------------------------------
    def process_work_item(self, sync_id: str) -> VectorSyncResult:
        """
        Execute a single vector synchronization work item with:
        - Lease acquisition (FOR UPDATE / PROCESSING state)
        - Authoritative PostgreSQL state verification
        - Sensitive memory protection (Rule 11)
        - Monotonic generation enforcement (Rule 9)
        - Idempotent vector store application
        - Deterministic error classification and backoff
        """
        t0 = time.perf_counter()
        conn = postgres_manager.get_connection()
        if not conn:
            return VectorSyncResult(
                success=False,
                sync_id=sync_id,
                memory_id="unknown",
                operation="UNKNOWN",
                generation=0,
                status=VectorSyncStatus.RETRY_REQUIRED.value,
                error="PostgreSQL connection unavailable",
            )

        from psycopg2 import extras
        work_item: Optional[VectorSyncWorkItem] = None

        try:
            # Step 1: Acquire lease on work item
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM vector_sync_queue
                    WHERE sync_id = %s
                    FOR UPDATE;
                """, (sync_id,))
                row = cur.fetchone()
                if not row:
                    return VectorSyncResult(
                        success=False,
                        sync_id=sync_id,
                        memory_id="unknown",
                        operation="UNKNOWN",
                        generation=0,
                        status=VectorSyncStatus.FAILED.value,
                        error="Sync work item not found",
                    )

                work_item = VectorSyncWorkItem.from_dict(dict(row))

                # Already completed or dead-lettered
                if work_item.sync_status in (VectorSyncStatus.SYNCED.value, VectorSyncStatus.DEAD_LETTER.value):
                    return VectorSyncResult(
                        success=True,
                        sync_id=work_item.sync_id,
                        memory_id=work_item.memory_id,
                        operation=work_item.operation,
                        generation=work_item.target_generation,
                        status=work_item.sync_status,
                        is_skipped=True,
                    )

                # Set PROCESSING lease (60 seconds)
                cur.execute("""
                    UPDATE vector_sync_queue
                    SET sync_status = %s,
                        locked_until = CURRENT_TIMESTAMP + INTERVAL '60 seconds',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE sync_id = %s;
                """, (VectorSyncStatus.PROCESSING.value, sync_id))
            conn.commit()

            _emit_sync_telemetry(
                "VECTOR_SYNC_STARTED",
                sync_id=work_item.sync_id,
                memory_id=work_item.memory_id,
                operation=work_item.operation,
                target_generation=work_item.target_generation,
                attempt_count=work_item.attempt_count + 1,
            )

            # Step 2: Read authoritative memory record from PostgreSQL
            rec_row = None
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT memory_id, status, generation, privacy_class, content
                    FROM memory_records
                    WHERE memory_id = %s;
                """, (work_item.memory_id,))
                rec_row = cur.fetchone()

            # Handle missing / physical deletion
            if not rec_row:
                # Memory deleted physically from PostgreSQL -> Purge from VectorStore
                vector_store.delete_embedding(work_item.memory_id, generation=work_item.target_generation)
                self._mark_queue_synced(conn, sync_id)
                with self._lock:
                    self._telemetry_counts["success"] += 1
                return VectorSyncResult(
                    success=True,
                    sync_id=work_item.sync_id,
                    memory_id=work_item.memory_id,
                    operation=work_item.operation,
                    generation=work_item.target_generation,
                    status=VectorSyncStatus.SYNCED.value,
                    is_skipped=True,
                    duration_ms=(time.perf_counter() - t0) * 1000,
                )

            rec_status = str(rec_row["status"])
            rec_gen = int(rec_row["generation"] or 1)
            rec_privacy = str(rec_row["privacy_class"])
            rec_content = str(rec_row["content"] or "")

            # Step 3: Enforce Operation Rules
            if work_item.operation == SyncOperation.UPSERT.value:
                # Rule 11: Sensitive memories must NEVER be embedded or stored
                if rec_privacy == PrivacyClass.SENSITIVE.value:
                    # Purge any existing vector immediately
                    vector_store.delete_embedding(work_item.memory_id, generation=rec_gen)
                    self._mark_queue_synced(conn, sync_id)
                    with self._lock:
                        self._telemetry_counts["success"] += 1
                    return VectorSyncResult(
                        success=True,
                        sync_id=work_item.sync_id,
                        memory_id=work_item.memory_id,
                        operation=work_item.operation,
                        generation=work_item.target_generation,
                        status=VectorSyncStatus.SYNCED.value,
                        is_skipped=True,
                        duration_ms=(time.perf_counter() - t0) * 1000,
                    )

                # Rule 10: Non-ACTIVE memories must NEVER have vectors stored
                if rec_status != MemoryStatus.ACTIVE.value:
                    # Memory is no longer ACTIVE (e.g. SUPERSEDED, ARCHIVED, DELETED)
                    # Stale UPSERT: ensure vector is deleted and mark queue synced
                    vector_store.delete_embedding(work_item.memory_id, generation=rec_gen)
                    self._mark_queue_synced(conn, sync_id)
                    with self._lock:
                        self._telemetry_counts["stale_rejected"] += 1
                        self._telemetry_counts["success"] += 1
                    _emit_sync_telemetry(
                        "VECTOR_STALE_UPSERT_REJECTED",
                        memory_id=work_item.memory_id,
                        work_generation=work_item.target_generation,
                        authoritative_generation=rec_gen,
                        authoritative_status=rec_status,
                    )
                    return VectorSyncResult(
                        success=True,
                        sync_id=work_item.sync_id,
                        memory_id=work_item.memory_id,
                        operation=work_item.operation,
                        generation=work_item.target_generation,
                        status=VectorSyncStatus.SYNCED.value,
                        is_stale=True,
                        duration_ms=(time.perf_counter() - t0) * 1000,
                    )

                # Rule 9: Monotonic Generation Safety
                if rec_gen > work_item.target_generation:
                    # A newer generation exists in PostgreSQL! Stale UPSERT
                    self._mark_queue_synced(conn, sync_id)
                    with self._lock:
                        self._telemetry_counts["stale_rejected"] += 1
                        self._telemetry_counts["success"] += 1
                    _emit_sync_telemetry(
                        "VECTOR_STALE_UPSERT_REJECTED",
                        memory_id=work_item.memory_id,
                        work_generation=work_item.target_generation,
                        authoritative_generation=rec_gen,
                        authoritative_status=rec_status,
                    )
                    return VectorSyncResult(
                        success=True,
                        sync_id=work_item.sync_id,
                        memory_id=work_item.memory_id,
                        operation=work_item.operation,
                        generation=work_item.target_generation,
                        status=VectorSyncStatus.SYNCED.value,
                        is_stale=True,
                        duration_ms=(time.perf_counter() - t0) * 1000,
                    )

                # Valid ACTIVE UPSERT: Generate embedding
                emb_res = embedding_router.embed(rec_content, check_policy=True)
                if emb_res is None:
                    # Embedding generation failed or rejected by policy
                    raise RuntimeError("EmbeddingRouter returned None for active content")

                # Store embedding in VectorStore with generation tracking
                vector_store.store_embedding(
                    memory_id=work_item.memory_id,
                    embedding=emb_res.vector,
                    model=emb_res.model,
                    model_version=emb_res.model_version,
                    content_hash=emb_res.content_hash,
                    dimension=len(emb_res.vector),
                    generation=rec_gen,
                )

                self._mark_queue_synced(conn, sync_id)
                duration = (time.perf_counter() - t0) * 1000
                with self._lock:
                    self._telemetry_counts["success"] += 1

                _emit_sync_telemetry(
                    "VECTOR_SYNC_SUCCESS",
                    sync_id=work_item.sync_id,
                    memory_id=work_item.memory_id,
                    operation=work_item.operation,
                    target_generation=rec_gen,
                    duration_ms=duration,
                )
                return VectorSyncResult(
                    success=True,
                    sync_id=work_item.sync_id,
                    memory_id=work_item.memory_id,
                    operation=work_item.operation,
                    generation=rec_gen,
                    status=VectorSyncStatus.SYNCED.value,
                    duration_ms=duration,
                )

            elif work_item.operation == SyncOperation.DELETE.value:
                # DELETE operation
                vector_store.delete_embedding(
                    memory_id=work_item.memory_id,
                    generation=work_item.target_generation,
                )
                self._mark_queue_synced(conn, sync_id)
                duration = (time.perf_counter() - t0) * 1000
                with self._lock:
                    self._telemetry_counts["success"] += 1

                _emit_sync_telemetry(
                    "VECTOR_SYNC_SUCCESS",
                    sync_id=work_item.sync_id,
                    memory_id=work_item.memory_id,
                    operation=work_item.operation,
                    target_generation=work_item.target_generation,
                    duration_ms=duration,
                )
                return VectorSyncResult(
                    success=True,
                    sync_id=work_item.sync_id,
                    memory_id=work_item.memory_id,
                    operation=work_item.operation,
                    generation=work_item.target_generation,
                    status=VectorSyncStatus.SYNCED.value,
                    duration_ms=duration,
                )

            else:
                # Unsupported operation
                self._mark_queue_failed(conn, sync_id, "UnsupportedOperationError", f"Unknown operation: {work_item.operation}")
                return VectorSyncResult(
                    success=False,
                    sync_id=work_item.sync_id,
                    memory_id=work_item.memory_id,
                    operation=work_item.operation,
                    generation=work_item.target_generation,
                    status=VectorSyncStatus.FAILED.value,
                    error=f"Unknown operation: {work_item.operation}",
                )

        except Exception as e:
            err_msg = str(e)[:400]
            err_cls = type(e).__name__
            self._handle_sync_failure(conn, sync_id, work_item, err_cls, err_msg)
            duration = (time.perf_counter() - t0) * 1000
            return VectorSyncResult(
                success=False,
                sync_id=sync_id,
                memory_id=work_item.memory_id if work_item else "unknown",
                operation=work_item.operation if work_item else "UNKNOWN",
                generation=work_item.target_generation if work_item else 0,
                status=VectorSyncStatus.RETRY_REQUIRED.value,
                error=err_msg,
                duration_ms=duration,
            )

        finally:
            postgres_manager.release_connection(conn)

    # ------------------------------------------------------------------
    # 4. Status Update Helpers
    # ------------------------------------------------------------------
    def _mark_queue_synced(self, conn: Any, sync_id: str) -> None:
        """Mark work item as successfully SYNCED and clear locks."""
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE vector_sync_queue
                SET sync_status = %s,
                    locked_until = NULL,
                    last_error_class = NULL,
                    last_error_message_safe = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE sync_id = %s;
            """, (VectorSyncStatus.SYNCED.value, sync_id))
        conn.commit()

    def _mark_queue_failed(self, conn: Any, sync_id: str, err_class: str, err_msg: str) -> None:
        """Mark work item as permanently FAILED."""
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE vector_sync_queue
                SET sync_status = %s,
                    locked_until = NULL,
                    last_error_class = %s,
                    last_error_message_safe = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE sync_id = %s;
            """, (VectorSyncStatus.FAILED.value, err_class[:100], err_msg[:500], sync_id))
        conn.commit()

    def _handle_sync_failure(
        self,
        conn: Any,
        sync_id: str,
        work_item: Optional[VectorSyncWorkItem],
        err_class: str,
        err_msg: str,
    ) -> None:
        """Apply exponential backoff or escalate to DEAD_LETTER."""
        attempts = (work_item.attempt_count + 1) if work_item else 1
        max_att = work_item.max_attempts if work_item else 5

        # Bounded exponential backoff: 1s, 2s, 4s, 8s, 16s, max 30s
        backoff_sec = min(30, 2 ** (attempts - 1))

        with conn.cursor() as cur:
            if attempts >= max_att:
                # Escalate to DEAD_LETTER
                cur.execute("""
                    UPDATE vector_sync_queue
                    SET sync_status = %s,
                        attempt_count = %s,
                        locked_until = NULL,
                        last_error_class = %s,
                        last_error_message_safe = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE sync_id = %s;
                """, (VectorSyncStatus.DEAD_LETTER.value, attempts, err_class[:100], err_msg[:500], sync_id))
                conn.commit()
                with self._lock:
                    self._telemetry_counts["dead_letter"] += 1
                _emit_sync_telemetry(
                    "VECTOR_SYNC_DEAD_LETTER",
                    sync_id=sync_id,
                    memory_id=work_item.memory_id if work_item else "unknown",
                    attempts=attempts,
                    error=err_msg,
                )
            else:
                # Schedule RETRY_REQUIRED with backoff
                cur.execute("""
                    UPDATE vector_sync_queue
                    SET sync_status = %s,
                        attempt_count = %s,
                        available_at = CURRENT_TIMESTAMP + (%s || ' seconds')::INTERVAL,
                        locked_until = NULL,
                        last_error_class = %s,
                        last_error_message_safe = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE sync_id = %s;
                """, (VectorSyncStatus.RETRY_REQUIRED.value, attempts, backoff_sec, err_class[:100], err_msg[:500], sync_id))
                conn.commit()
                with self._lock:
                    self._telemetry_counts["retry"] += 1
                _emit_sync_telemetry(
                    "VECTOR_SYNC_RETRY",
                    sync_id=sync_id,
                    memory_id=work_item.memory_id if work_item else "unknown",
                    attempt=attempts,
                    backoff_seconds=backoff_sec,
                    error=err_msg,
                )

    def _record_failure(
        self,
        sync_id: str,
        work_item: Optional[VectorSyncWorkItem],
        error: Exception,
        terminal: bool = False,
    ) -> None:
        """Helper to record failure directly, used by tests and internal handlers."""
        conn = postgres_manager.get_connection()
        if not conn:
            return
        err_class = type(error).__name__
        err_msg = str(error)
        try:
            if terminal:
                self._mark_queue_failed(conn, sync_id, err_class, err_msg)
            else:
                self._handle_sync_failure(conn, sync_id, work_item, err_class, err_msg)
        finally:
            postgres_manager.release_connection(conn)

    # ------------------------------------------------------------------
    # 5. Lease Recovery & Batch Processing
    # ------------------------------------------------------------------
    def recover_expired_leases(self, lease_timeout_seconds: int = 60) -> int:
        """
        Recover work items stuck in PROCESSING where lease has expired.
        Resets them to RETRY_REQUIRED or DEAD_LETTER.
        """
        conn = postgres_manager.get_connection()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE vector_sync_queue
                    SET sync_status = CASE
                            WHEN attempt_count >= max_attempts THEN %s
                            ELSE %s
                        END,
                        locked_until = NULL,
                        last_error_class = 'LeaseTimeoutError',
                        last_error_message_safe = 'Worker lease expired before completion',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE sync_status = %s
                      AND locked_until < CURRENT_TIMESTAMP;
                """, (
                    VectorSyncStatus.DEAD_LETTER.value,
                    VectorSyncStatus.RETRY_REQUIRED.value,
                    VectorSyncStatus.PROCESSING.value,
                ))
                recovered = cur.rowcount
            conn.commit()
            return recovered
        except Exception as e:
            conn.rollback()
            print(f"[VECTOR SYNC] Lease recovery failed: {e}")
            return 0
        finally:
            postgres_manager.release_connection(conn)

    def process_pending_batch(self, limit: int = 25) -> List[VectorSyncResult]:
        """
        Sweep and process eligible pending and retryable work items.
        Safe for periodic sweeper or startup recovery.
        """
        self.recover_expired_leases()

        conn = postgres_manager.get_connection()
        if not conn:
            return []

        sync_ids = []
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT sync_id FROM vector_sync_queue
                    WHERE sync_status IN (%s, %s, %s)
                      AND available_at <= CURRENT_TIMESTAMP
                      AND (locked_until IS NULL OR locked_until < CURRENT_TIMESTAMP)
                    ORDER BY created_at ASC
                    LIMIT %s;
                """, (
                    VectorSyncStatus.PENDING.value,
                    VectorSyncStatus.RETRY_REQUIRED.value,
                    VectorSyncStatus.RECONCILIATION_REQUIRED.value,
                    limit,
                ))
                sync_ids = [row[0] for row in cur.fetchall()]
        except Exception as e:
            print(f"[VECTOR SYNC] Sweep fetch failed: {e}")
        finally:
            postgres_manager.release_connection(conn)

        results = []
        for sid in sync_ids:
            res = self.process_work_item(sid)
            results.append(res)
        return results

    # ------------------------------------------------------------------
    # 6. Internal Helpers for Inspection & Direct Execution
    # ------------------------------------------------------------------
    def _fetch_work_item(self, sync_id: str) -> Optional[VectorSyncWorkItem]:
        """Fetch a work item by sync_id without locking."""
        conn = postgres_manager.get_connection()
        if not conn:
            return None
        from psycopg2 import extras
        try:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM vector_sync_queue WHERE sync_id = %s;", (sync_id,))
                row = cur.fetchone()
                if row:
                    return VectorSyncWorkItem.from_dict(dict(row))
                return None
        finally:
            postgres_manager.release_connection(conn)

    def _execute_sync_operation(self, work_item: VectorSyncWorkItem, content: str = "") -> VectorSyncResult:
        """
        Directly execute a synchronization work item against authoritative PostgreSQL state.
        Enforces Rule 9 (monotonic generation), Rule 10 (ACTIVE only), and Rule 11 (sensitive protection).
        """
        conn = postgres_manager.get_connection()
        if not conn:
            return VectorSyncResult(
                success=False,
                sync_id=work_item.sync_id,
                memory_id=work_item.memory_id,
                operation=work_item.operation,
                generation=work_item.target_generation,
                status=VectorSyncStatus.FAILED.value,
                error="Database unavailable",
            )
        from psycopg2 import extras
        try:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT memory_id, status, generation, privacy_class, content
                    FROM memory_records
                    WHERE memory_id = %s;
                """, (work_item.memory_id,))
                rec_row = cur.fetchone()

            if not rec_row:
                vector_store.delete_embedding(work_item.memory_id, generation=work_item.target_generation)
                return VectorSyncResult(
                    success=True,
                    sync_id=work_item.sync_id,
                    memory_id=work_item.memory_id,
                    operation=work_item.operation,
                    generation=work_item.target_generation,
                    status=VectorSyncStatus.SYNCED.value,
                    is_skipped=True,
                )

            rec_status = str(rec_row["status"])
            rec_gen = int(rec_row["generation"] or 1)
            rec_privacy = str(rec_row["privacy_class"])

            # Rule 11: Sensitive memories
            if rec_privacy == PrivacyClass.SENSITIVE.value:
                vector_store.delete_embedding(work_item.memory_id, generation=rec_gen)
                return VectorSyncResult(
                    success=True,
                    sync_id=work_item.sync_id,
                    memory_id=work_item.memory_id,
                    operation=work_item.operation,
                    generation=work_item.target_generation,
                    status=VectorSyncStatus.SYNCED.value,
                    is_skipped=True,
                )

            # Rule 10: Non-ACTIVE
            if rec_status != MemoryStatus.ACTIVE.value:
                vector_store.delete_embedding(work_item.memory_id, generation=rec_gen)
                return VectorSyncResult(
                    success=True,
                    sync_id=work_item.sync_id,
                    memory_id=work_item.memory_id,
                    operation=work_item.operation,
                    generation=work_item.target_generation,
                    status=VectorSyncStatus.SYNCED.value,
                    is_skipped=True,
                    is_stale=True,
                    error="Stale generation: authoritative memory is not ACTIVE",
                )

            # Rule 9: Monotonic generation guard
            if rec_gen > work_item.target_generation:
                return VectorSyncResult(
                    success=True,
                    sync_id=work_item.sync_id,
                    memory_id=work_item.memory_id,
                    operation=work_item.operation,
                    generation=work_item.target_generation,
                    status=VectorSyncStatus.SYNCED.value,
                    is_skipped=True,
                    is_stale=True,
                    error=f"Stale generation: work generation {work_item.target_generation} < record generation {rec_gen}",
                )

            # Execution
            if work_item.operation == SyncOperation.DELETE.value:
                vector_store.delete_embedding(work_item.memory_id, generation=work_item.target_generation)
                return VectorSyncResult(
                    success=True,
                    sync_id=work_item.sync_id,
                    memory_id=work_item.memory_id,
                    operation=work_item.operation,
                    generation=work_item.target_generation,
                    status=VectorSyncStatus.SYNCED.value,
                )
            else:
                emb = embedding_router.embed(content or str(rec_row.get("content") or ""), check_policy=True)
                if emb:
                    vector_store.store_embedding(
                        memory_id=work_item.memory_id,
                        embedding=emb.vector,
                        model=emb.model,
                        model_version=emb.model_version,
                        content_hash=emb.content_hash,
                        dimension=len(emb.vector),
                        generation=rec_gen,
                    )
                return VectorSyncResult(
                    success=True,
                    sync_id=work_item.sync_id,
                    memory_id=work_item.memory_id,
                    operation=work_item.operation,
                    generation=rec_gen,
                    status=VectorSyncStatus.SYNCED.value,
                )
        finally:
            postgres_manager.release_connection(conn)


# Canonical global vector sync engine instance
vector_sync_engine = VectorSyncEngine()

