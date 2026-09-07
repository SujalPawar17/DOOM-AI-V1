"""
DOOM V5.3.3 — Vector Reconciliation Engine
Audits and repairs discrepancies between authoritative PostgreSQL memory state
and derived VectorStore state. Handles NumPy startup rehydration and bounded sweeps.
"""
from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional

from database.postgres_db import postgres_manager
from memory.embedding.router import embedding_router
from memory.sync import ReconciliationReport, VectorSyncStatus
from memory.types import MemoryStatus, PrivacyClass
from memory.vector_store import vector_store
from memory.vector_store.base import VectorStorageBackend


class VectorReconciliationEngine:
    """
    Reconciliation authority:
    PostgreSQL is authoritative -> inspects VectorStore and repairs derived divergence.
    Never alters PostgreSQL lifecycle state.
    """

    def reconcile(self, batch_size: int = 100, fix: bool = True) -> ReconciliationReport:
        """
        Scan for missing, zombie, orphan, sensitive, and stale generation vectors in bounded batches.
        Idempotently repairs any detected divergence when fix=True.
        """
        t0 = time.perf_counter()
        report = ReconciliationReport()
        conn = postgres_manager.get_connection()
        if not conn:
            report.errors.append("PostgreSQL connection unavailable for reconciliation")
            report.duration_ms = (time.perf_counter() - t0) * 1000
            return report

        from psycopg2 import extras
        active_model = embedding_router.provider.model_name
        active_version = embedding_router.provider.model_version

        try:
            # 1. Scan PostgreSQL records in bounded batches
            offset = 0
            while True:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT memory_id, status, generation, privacy_class, content
                        FROM memory_records
                        ORDER BY memory_id ASC
                        LIMIT %s OFFSET %s;
                    """, (batch_size, offset))
                    rows = cur.fetchall()

                if not rows:
                    break

                for row in rows:
                    report.scanned_records += 1
                    mid = str(row["memory_id"])
                    status = str(row["status"])
                    gen = int(row["generation"] or 1)
                    pclass = str(row["privacy_class"])
                    content = str(row["content"] or "")

                    has_vec = vector_store.has_embedding(mid, active_model, active_version)
                    stored_rec = vector_store.get_embedding(mid, active_model, active_version) if has_vec else None

                    # Check A: Sensitive memory must NEVER have vector
                    if pclass == PrivacyClass.SENSITIVE.value:
                        if has_vec:
                            report.sensitive_vectors_detected += 1
                            report.sensitive_vectors.append(mid)
                            if fix:
                                vector_store.delete_embedding(mid, generation=gen)
                                report.sensitive_vectors_purged += 1
                        continue

                    # Check B: Non-ACTIVE memory must NEVER have vector (Zombie vector)
                    if status != MemoryStatus.ACTIVE.value:
                        if has_vec:
                            report.zombie_vectors_detected += 1
                            report.zombie_vectors.append(mid)
                            if fix:
                                vector_store.delete_embedding(mid, generation=gen)
                                report.zombie_vectors_purged += 1
                        continue

                    # Check C: ACTIVE non-sensitive memory missing vector (Missing vector)
                    if status == MemoryStatus.ACTIVE.value and not has_vec:
                        report.missing_vectors_detected += 1
                        report.missing_vectors.append(mid)
                        if fix:
                            emb = embedding_router.embed(content, check_policy=True)
                            if emb:
                                vector_store.store_embedding(
                                    memory_id=mid,
                                    embedding=emb.vector,
                                    model=emb.model,
                                    model_version=emb.model_version,
                                    content_hash=emb.content_hash,
                                    dimension=len(emb.vector),
                                    generation=gen,
                                )
                                report.missing_vectors_repaired += 1

                    # Check D: Stale generation in vector store
                    if has_vec and stored_rec and getattr(stored_rec, "generation", 1) < gen:
                        report.stale_generations_detected += 1
                        report.stale_generations.append(mid)
                        if fix:
                            emb = embedding_router.embed(content, check_policy=True)
                            if emb:
                                vector_store.store_embedding(
                                    memory_id=mid,
                                    embedding=emb.vector,
                                    model=emb.model,
                                    model_version=emb.model_version,
                                    content_hash=emb.content_hash,
                                    dimension=len(emb.vector),
                                    generation=gen,
                                )
                                report.stale_generations_repaired += 1

                offset += len(rows)

            # 2. Orphan check for in-memory NumPy store
            if vector_store.backend == VectorStorageBackend.NUMPY_FALLBACK and hasattr(vector_store, "_records"):
                keys_to_check = list(vector_store._records.keys())
                for key in keys_to_check:
                    mid = key[0]
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 FROM memory_records WHERE memory_id = %s;", (mid,))
                        exists = cur.fetchone()
                    if not exists:
                        report.orphan_vectors_detected += 1
                        report.orphan_vectors.append(mid)
                        if fix:
                            vector_store.delete_embedding(mid)
                            report.orphan_vectors_purged += 1

        except Exception as e:
            report.errors.append(str(e))
        finally:
            postgres_manager.release_connection(conn)
            report.duration_ms = (time.perf_counter() - t0) * 1000

        return report

    # ------------------------------------------------------------------
    # NumPy Startup Rehydration
    # ------------------------------------------------------------------
    def rehydrate_numpy_store(self, batch_size: int = 100, max_records: int = 10000) -> Dict[str, Any]:
        """
        Rebuild and rehydrate in-memory NumPy vector storage from PostgreSQL on startup.
        Excludes SENSITIVE memories and non-ACTIVE memories.
        """
        t0 = time.perf_counter()
        stats = {
            "rehydrated": 0,
            "rehydrated_count": 0,
            "skipped_sensitive": 0,
            "failed_embeddings": 0,
            "total_active_scanned": 0,
            "capacity_hit": False,
            "duration_ms": 0.0,
        }

        if vector_store.backend != VectorStorageBackend.NUMPY_FALLBACK:
            stats["duration_ms"] = (time.perf_counter() - t0) * 1000
            return stats

        conn = postgres_manager.get_connection()
        if not conn:
            stats["duration_ms"] = (time.perf_counter() - t0) * 1000
            return stats

        from psycopg2 import extras
        try:
            offset = 0
            while stats["rehydrated"] < max_records:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT memory_id, content, privacy_class, generation
                        FROM memory_records
                        WHERE status = 'ACTIVE'
                        ORDER BY importance DESC, created_at DESC
                        LIMIT %s OFFSET %s;
                    """, (batch_size, offset))
                    rows = cur.fetchall()

                if not rows:
                    break

                for row in rows:
                    stats["total_active_scanned"] += 1
                    if stats["rehydrated"] >= max_records:
                        stats["capacity_hit"] = True
                        break

                    mid = str(row["memory_id"])
                    pclass = str(row["privacy_class"])
                    gen = int(row["generation"] or 1)
                    content = str(row["content"] or "")

                    if pclass == PrivacyClass.SENSITIVE.value:
                        stats["skipped_sensitive"] += 1
                        continue

                    # Generate embedding
                    emb = embedding_router.embed(content, check_policy=True)
                    if emb is None:
                        stats["failed_embeddings"] += 1
                        continue

                    vector_store.store_embedding(
                        memory_id=mid,
                        embedding=emb.vector,
                        model=emb.model,
                        model_version=emb.model_version,
                        content_hash=emb.content_hash,
                        dimension=len(emb.vector),
                        generation=gen,
                    )
                    stats["rehydrated"] += 1

                offset += len(rows)

        except Exception as e:
            print(f"[RECONCILIATION] Rehydration error: {e}")
        finally:
            postgres_manager.release_connection(conn)
            stats["rehydrated_count"] = stats["rehydrated"]
            stats["duration_ms"] = (time.perf_counter() - t0) * 1000

        return stats


# Canonical reconciliation engine instance
vector_reconciliation_engine = VectorReconciliationEngine()


def rehydrate_numpy_store(batch_size: int = 100, max_records: int = 10000) -> Dict[str, Any]:
    """Module-level convenience function for NumPy startup rehydration."""
    return vector_reconciliation_engine.rehydrate_numpy_store(batch_size=batch_size, max_records=max_records)

