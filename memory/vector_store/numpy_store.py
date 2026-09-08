"""
DOOM V5.2.2 — In-Memory NumPy Vector Storage Adapter
Provides bounded, thread-safe in-memory vector storage with exact cosine similarity search.
Serves as the zero-dependency fallback when PostgreSQL pgvector is unavailable.
"""
from datetime import datetime, timezone
import math
import threading
import time
from typing import List, Optional, Dict, Any, Tuple
import numpy as np

from memory.vector_store.base import (
    VectorStore,
    VectorStorageBackend,
    StoredVectorRecord,
    VectorSearchResult,
    VectorStorageError,
    VectorValidationError,
    VectorStorageLimitError,
    validate_vector_for_storage,
)


DEFAULT_MAX_NUMPY_VECTORS: int = 10000


class NumPyVectorStorageAdapter(VectorStore):
    """
    In-memory vector store backed by NumPy arrays.
    Thread-safe, bounded memory footprint, mathematically rigorous cosine similarity search.
    """

    def __init__(self, max_vectors: int = DEFAULT_MAX_NUMPY_VECTORS):
        self._max_vectors = max_vectors
        # Primary storage dict: key = (memory_id, model, model_version) -> StoredVectorRecord
        self._records: Dict[Tuple[str, str, str], StoredVectorRecord] = {}
        # Monotonic generation registry: tracks highest generation observed per memory_id
        self._max_generation: Dict[str, int] = {}
        self._lock = threading.RLock()

        # Telemetry counters
        self._store_count: int = 0
        self._search_count: int = 0
        self._delete_count: int = 0
        self._stale_upsert_rejections: int = 0
        self._matrix_cache: Optional[Tuple[np.ndarray, List[StoredVectorRecord]]] = None
        self._sync_generations_from_db()

    @property
    def backend(self) -> VectorStorageBackend:
        return VectorStorageBackend.NUMPY_FALLBACK

    def _sync_generations_from_db(self) -> None:
        """Hydrate monotonic generation registry from PostgreSQL memory_vector_state."""
        try:
            from database.postgres_db import postgres_manager
            conn = postgres_manager.get_connection()
            if not conn:
                return
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT memory_id, max_generation FROM memory_vector_state;")
                    for row in cur.fetchall():
                        mid, gen = str(row[0]), int(row[1])
                        self._max_generation[mid] = max(self._max_generation.get(mid, 0), gen)
            finally:
                postgres_manager.release_connection(conn)
        except Exception:
            pass

    def _persist_vector_state(self, memory_id: str, generation: int, present: bool) -> None:
        """Persist generation state and tombstone to PostgreSQL memory_vector_state."""
        try:
            from database.postgres_db import postgres_manager
            conn = postgres_manager.get_connection()
            if not conn:
                return
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO memory_vector_state (memory_id, max_generation, vector_present, updated_at)
                        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (memory_id) DO UPDATE SET
                            max_generation = GREATEST(memory_vector_state.max_generation, EXCLUDED.max_generation),
                            vector_present = EXCLUDED.vector_present,
                            updated_at = CURRENT_TIMESTAMP;
                    """, (memory_id, generation, present))
                conn.commit()
            except Exception:
                conn.rollback()
            finally:
                postgres_manager.release_connection(conn)
        except Exception:
            pass

    def _get_db_generation_unlocked(self, clean_mid: str) -> int:
        """Probe PostgreSQL memory_vector_state without locking."""
        try:
            from database.postgres_db import postgres_manager
            conn = postgres_manager.get_connection()
            if conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT max_generation FROM memory_vector_state WHERE memory_id = %s;", (clean_mid,))
                        row = cur.fetchone()
                        if row and row[0] is not None:
                            return int(row[0])
                finally:
                    postgres_manager.release_connection(conn)
        except Exception:
            pass
        return 0

    def get_max_generation(self, memory_id: str) -> int:
        """Return the highest generation observed for the given memory_id."""
        clean_mid = memory_id.strip()
        with self._lock:
            if clean_mid in self._max_generation:
                return self._max_generation[clean_mid]
            gen = self._get_db_generation_unlocked(clean_mid)
            self._max_generation[clean_mid] = gen
            return gen

    # ------------------------------------------------------------------
    # CRUD Operations
    # ------------------------------------------------------------------
    def store_embedding(
        self,
        memory_id: str,
        embedding: Optional[List[float]] = None,
        model: str = "",
        model_version: str = "",
        content_hash: str = "",
        dimension: int = 384,
        generation: int = 1,
        vector: Optional[List[float]] = None,
    ) -> StoredVectorRecord:
        """
        Store an embedding vector idempotently.
        Replaces existing record if (memory_id, model, model_version) exists,
        provided generation >= max_generation observed for this memory_id.
        Stale UPSERTs with generation < max_generation are strictly rejected.
        Supports both embedding= and vector= keyword arguments.
        """
        if not memory_id or not isinstance(memory_id, str):
            raise VectorValidationError("memory_id must be a non-empty string.")

        actual_vector = embedding if embedding is not None else vector
        if actual_vector is None:
            raise VectorValidationError("Either embedding or vector must be provided.")

        clean_mid = memory_id.strip()
        clean_model = model.strip() if model else "default"
        clean_ver = model_version.strip() if model_version else "v1"
        clean_vector = validate_vector_for_storage(actual_vector, expected_dimension=dimension)
        key = (clean_mid, clean_model, clean_ver)

        with self._lock:
            # Monotonic generation check: Reject stale UPSERTs
            highest_gen = self._max_generation.get(clean_mid)
            if highest_gen is None:
                highest_gen = self.get_max_generation(clean_mid)
                self._max_generation[clean_mid] = highest_gen

            if generation < highest_gen:
                self._stale_upsert_rejections += 1
                existing = self._records.get(key)
                if existing:
                    return existing
                return StoredVectorRecord(
                    embedding_id=f"emb_stale_{clean_mid[:16]}",
                    memory_id=clean_mid,
                    model=clean_model,
                    model_version=clean_ver,
                    dimension=dimension,
                    embedding=clean_vector,
                    content_hash=content_hash.strip(),
                    generation=generation,
                    backend=self.backend.value,
                )

            # Capacity check (only for new entries)
            if key not in self._records and len(self._records) >= self._max_vectors:
                raise VectorStorageLimitError(
                    f"NumPy vector storage capacity exceeded (max {self._max_vectors} vectors)."
                )

            now_iso = datetime.now(timezone.utc).isoformat()
            existing = self._records.get(key)
            created_at = existing.created_at if existing else now_iso

            # Update highest observed generation
            self._max_generation[clean_mid] = max(highest_gen, generation)

            record = StoredVectorRecord(
                embedding_id=f"emb_{clean_mid[:16]}_{hash(key) & 0xFFFFFFFF:08x}",
                memory_id=clean_mid,
                model=clean_model,
                model_version=clean_ver,
                dimension=dimension,
                embedding=clean_vector,
                content_hash=content_hash.strip(),
                generation=generation,
                created_at=created_at,
                updated_at=now_iso,
                backend=self.backend.value,
            )

            self._records[key] = record
            self._store_count += 1
            self._matrix_cache = None

        # Persist durable vector state
        self._persist_vector_state(clean_mid, generation, True)
        return record

    def get_embedding(
        self,
        memory_id: str,
        model: Optional[str] = None,
        model_version: Optional[str] = None,
    ) -> Optional[StoredVectorRecord]:
        """Retrieve stored record by memory_id and optional model/version."""
        clean_mid = memory_id.strip()
        with self._lock:
            if model is not None and model_version is not None:
                key = (clean_mid, model.strip(), model_version.strip())
                return self._records.get(key)
            # Find any record matching memory_id
            for k, r in self._records.items():
                if k[0] == clean_mid:
                    return r
            return None

    def delete_embedding(
        self,
        memory_id: str,
        model: Optional[str] = None,
        model_version: Optional[str] = None,
        generation: Optional[int] = None,
    ) -> bool:
        """
        Delete embedding record. Idempotent — repeated deletions return False safely.
        If generation is provided, sets max_generation so stale UPSERTs cannot resurrect.
        """
        clean_mid = memory_id.strip()
        deleted = False

        with self._lock:
            target_gen = generation
            current_max = self._max_generation.get(clean_mid)
            if current_max is None:
                current_max = self.get_max_generation(clean_mid)
                self._max_generation[clean_mid] = current_max

            existing_records = [r for k, r in self._records.items() if k[0] == clean_mid]
            existing_gen = max([r.generation for r in existing_records], default=0)
            highest_observed = max(current_max, existing_gen)

            # Symmetrical Generation Protection (V5.3.7.2):
            # If an existing vector or recorded state has a strictly higher generation than target_gen,
            # this DELETE is stale! Reject deletion to prevent purging newer vectors.
            if target_gen is not None and highest_observed > target_gen:
                return False

            if target_gen is not None:
                self._max_generation[clean_mid] = max(current_max, target_gen)
            effective_gen = self._max_generation.get(clean_mid, target_gen or 0)

            if model is not None and model_version is not None:
                key = (clean_mid, model.strip(), model_version.strip())
                if key in self._records:
                    del self._records[key]
                    deleted = True
            else:
                # Delete all model variants for this memory_id
                keys_to_del = [k for k in self._records if k[0] == clean_mid]
                for k in keys_to_del:
                    del self._records[k]
                    deleted = True

            if deleted:
                self._delete_count += 1
                self._matrix_cache = None

        # Persist tombstone to durable memory_vector_state
        self._persist_vector_state(clean_mid, effective_gen, False)
        return deleted

    def has_embedding(
        self,
        memory_id: str,
        model: str,
        model_version: str,
    ) -> bool:
        """Check if vector exists."""
        key = (memory_id.strip(), model.strip(), model_version.strip())
        with self._lock:
            return key in self._records

    # ------------------------------------------------------------------
    # Similarity Search
    # ------------------------------------------------------------------
    def search_similar(
        self,
        query_vector: List[float],
        top_k: int = 5,
        model: Optional[str] = None,
        model_version: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[VectorSearchResult]:
        """
        Compute cosine similarities against stored vectors and return top_k matches.
        Since vectors are unit-normalized, cosine_sim(q, v) = dot(q, v).
        """
        clean_q = np.asarray(
            validate_vector_for_storage(query_vector),
            dtype=np.float32,
        )

        with self._lock:
            self._search_count += 1

            if filters is None and model is None and model_version is None:
                if self._matrix_cache is not None:
                    matrix, candidates = self._matrix_cache
                else:
                    candidates = list(self._records.values())
                    if not candidates:
                        return []
                    matrix = np.asarray([c.embedding for c in candidates], dtype=np.float32)
                    self._matrix_cache = (matrix, candidates)
            else:
                candidates: List[StoredVectorRecord] = []
                for key, rec in self._records.items():
                    if model is not None and rec.model != model.strip():
                        continue
                    if model_version is not None and rec.model_version != model_version.strip():
                        continue
                    # Optional metadata filters (e.g. memory_id exclusion)
                    if filters and "exclude_memory_ids" in filters:
                        if rec.memory_id in filters["exclude_memory_ids"]:
                            continue
                    candidates.append(rec)

                if not candidates:
                    return []

                matrix = np.asarray([c.embedding for c in candidates], dtype=np.float32)

            # Dot products: shape (N,)
            sims = np.dot(matrix, clean_q)
            # Clip numerical precision drift
            sims = np.clip(sims, -1.0, 1.0)

            results: List[VectorSearchResult] = []
            for sim_val, rec in zip(sims, candidates):
                sim_float = float(sim_val)
                dist_float = max(0.0, 1.0 - sim_float)
                results.append(
                    VectorSearchResult(
                        memory_id=rec.memory_id,
                        similarity=sim_float,
                        distance=dist_float,
                        model=rec.model,
                        model_version=rec.model_version,
                        content_hash=rec.content_hash,
                    )
                )

            # Sort descending by similarity
            results.sort(key=lambda x: x.similarity, reverse=True)
            return results[:top_k]

    def count(self, model: Optional[str] = None) -> int:
        """Return count of stored vectors."""
        with self._lock:
            if model is None:
                return len(self._records)
            clean_m = model.strip()
            return sum(1 for k in self._records if k[1] == clean_m)

    def health_check(self) -> Dict[str, Any]:
        """Return status and operational metadata."""
        with self._lock:
            return {
                "status": "HEALTHY",
                "backend": self.backend.value,
                "vector_count": len(self._records),
                "max_vectors": self._max_vectors,
                "store_ops": self._store_count,
                "search_ops": self._search_count,
                "delete_ops": self._delete_count,
                "stale_upsert_rejections": self._stale_upsert_rejections,
            }

    def clear(self) -> None:
        """Clear all vectors from memory."""
        with self._lock:
            self._records.clear()
            self._max_generation.clear()
            self._matrix_cache = None
