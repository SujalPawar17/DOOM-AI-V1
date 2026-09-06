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
        self._lock = threading.Lock()

        # Telemetry counters
        self._store_count: int = 0
        self._search_count: int = 0
        self._delete_count: int = 0

    @property
    def backend(self) -> VectorStorageBackend:
        return VectorStorageBackend.NUMPY_FALLBACK

    # ------------------------------------------------------------------
    # CRUD Operations
    # ------------------------------------------------------------------
    def store_embedding(
        self,
        memory_id: str,
        embedding: List[float],
        model: str,
        model_version: str,
        content_hash: str,
        dimension: int = 384,
    ) -> StoredVectorRecord:
        """
        Store an embedding vector idempotently.
        Replaces existing record if (memory_id, model, model_version) exists.
        """
        if not memory_id or not isinstance(memory_id, str):
            raise VectorValidationError("memory_id must be a non-empty string.")

        clean_vector = validate_vector_for_storage(embedding, expected_dimension=dimension)
        key = (memory_id.strip(), model.strip(), model_version.strip())

        with self._lock:
            # Capacity check (only for new entries)
            if key not in self._records and len(self._records) >= self._max_vectors:
                raise VectorStorageLimitError(
                    f"NumPy vector storage capacity exceeded (max {self._max_vectors} vectors)."
                )

            now_iso = datetime.now(timezone.utc).isoformat()
            existing = self._records.get(key)
            created_at = existing.created_at if existing else now_iso

            record = StoredVectorRecord(
                embedding_id=f"emb_{memory_id[:16]}_{hash(key) & 0xFFFFFFFF:08x}",
                memory_id=memory_id.strip(),
                model=model.strip(),
                model_version=model_version.strip(),
                dimension=dimension,
                embedding=clean_vector,
                content_hash=content_hash.strip(),
                created_at=created_at,
                updated_at=now_iso,
                backend=self.backend.value,
            )

            self._records[key] = record
            self._store_count += 1
            return record

    def get_embedding(
        self,
        memory_id: str,
        model: str,
        model_version: str,
    ) -> Optional[StoredVectorRecord]:
        """Retrieve stored record by memory_id, model, and version."""
        key = (memory_id.strip(), model.strip(), model_version.strip())
        with self._lock:
            return self._records.get(key)

    def delete_embedding(
        self,
        memory_id: str,
        model: Optional[str] = None,
        model_version: Optional[str] = None,
    ) -> bool:
        """
        Delete embedding record. Idempotent — repeated deletions return False safely.
        """
        clean_mid = memory_id.strip()
        deleted = False

        with self._lock:
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

            # Stack matrix: shape (N, dimension)
            matrix = np.vstack([c.embedding for c in candidates])
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
            }

    def clear(self) -> None:
        """Clear all vectors from memory."""
        with self._lock:
            self._records.clear()
