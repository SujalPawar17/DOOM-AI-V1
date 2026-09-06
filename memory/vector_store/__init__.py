"""
DOOM V5.2.2 — Vector Storage Subsystem Package
Exports the VectorStore interface, storage adapters (pgvector + NumPy fallback),
and dynamic factory resolver.
"""
from typing import Optional

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
from memory.vector_store.numpy_store import NumPyVectorStorageAdapter
from memory.vector_store.pgvector_store import PgVectorStorageAdapter


def get_vector_store(
    preferred_backend: Optional[VectorStorageBackend] = None,
) -> VectorStore:
    """
    Factory function resolving the authoritative vector store instance.
    Strategy:
    1. If PGVECTOR requested or preferred:
       Probes PostgreSQL for the pgvector extension.
       If available, initializes schema and activates PgVectorStorageAdapter.
    2. If pgvector is unavailable:
       Falls back transparently to NumPyVectorStorageAdapter (in-memory, bounded).
    """
    if preferred_backend == VectorStorageBackend.NUMPY_FALLBACK:
        return NumPyVectorStorageAdapter()

    # Attempt PostgreSQL + pgvector
    pg_adapter = PgVectorStorageAdapter()
    is_avail, reason = pg_adapter.check_pgvector_available()

    if is_avail:
        schema_ok = pg_adapter.init_schema()
        if schema_ok:
            return pg_adapter

    # Graceful fallback to NumPy adapter
    return NumPyVectorStorageAdapter()


# Canonical shared vector store instance
vector_store: VectorStore = get_vector_store()


__all__ = [
    "VectorStore",
    "VectorStorageBackend",
    "StoredVectorRecord",
    "VectorSearchResult",
    "VectorStorageError",
    "VectorValidationError",
    "VectorStorageLimitError",
    "validate_vector_for_storage",
    "NumPyVectorStorageAdapter",
    "PgVectorStorageAdapter",
    "get_vector_store",
    "vector_store",
]
