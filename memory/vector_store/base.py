"""
DOOM V5.2.2 — Vector Storage Subsystem Base Abstractions
Defines the provider-independent VectorStore interface, stored vector schemas,
search result schemas, and operational validation functions.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import List, Optional, Dict, Any
import numpy as np


class VectorStorageBackend(str, Enum):
    """Vector storage implementation engine."""
    PGVECTOR = "PGVECTOR"
    NUMPY_FALLBACK = "NUMPY_FALLBACK"
    DISABLED = "DISABLED"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class VectorStorageError(Exception):
    """Base exception for vector storage errors."""
    pass


class VectorValidationError(VectorStorageError):
    """Raised when vector violates dimension, finiteness, or normalization."""
    pass


class VectorStorageLimitError(VectorStorageError):
    """Raised when in-memory storage capacity limit is exceeded."""
    pass


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
@dataclass
class StoredVectorRecord:
    """
    Structured record representing a persisted dense embedding vector.
    """
    embedding_id: str
    memory_id: str
    model: str
    model_version: str
    dimension: int
    embedding: List[float]
    content_hash: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    backend: str = "UNKNOWN"

    def to_metadata(self) -> Dict[str, Any]:
        """Return metadata dict without the raw float vector."""
        return {
            "embedding_id": self.embedding_id,
            "memory_id": self.memory_id,
            "model": self.model,
            "model_version": self.model_version,
            "dimension": self.dimension,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "backend": self.backend,
        }


@dataclass
class VectorSearchResult:
    """
    Result item returned by similarity search.
    """
    memory_id: str
    similarity: float  # Cosine similarity in [-1.0, 1.0], typically [0.0, 1.0]
    distance: float    # Cosine distance (1.0 - similarity)
    model: str
    model_version: str
    content_hash: str
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Validation Helpers
# ---------------------------------------------------------------------------
def validate_vector_for_storage(vector: Any, expected_dimension: int = 384) -> List[float]:
    """
    Validate vector strictly before any database or memory write:
    - Must exist and be iterable
    - Dimension must exactly match expected_dimension (384)
    - Values must be finite (no NaN, no Inf)
    - Norm must be non-zero
    """
    if vector is None:
        raise VectorValidationError("Vector cannot be None.")

    try:
        arr = np.asarray(vector, dtype=np.float32).flatten()
    except Exception as e:
        raise VectorValidationError(f"Cannot convert vector to float array: {e}") from e

    if arr.shape[0] != expected_dimension:
        raise VectorValidationError(
            f"Vector dimension mismatch: expected {expected_dimension}, got {arr.shape[0]}"
        )

    if not np.all(np.isfinite(arr)):
        raise VectorValidationError("Vector contains non-finite values (NaN or Inf).")

    norm = float(np.linalg.norm(arr))
    if norm == 0.0 or math.isnan(norm):
        raise VectorValidationError("Vector has zero or undefined norm.")

    # Unit-normalize if not already within float tolerance
    if abs(norm - 1.0) > 1e-4:
        arr = arr / norm

    return arr.tolist()


# ---------------------------------------------------------------------------
# Abstract VectorStore Interface
# ---------------------------------------------------------------------------
class VectorStore(ABC):
    """
    Abstract interface for vector storage engines.
    Exposes uniform CRUD, similarity search, counting, and health check.
    """

    @property
    @abstractmethod
    def backend(self) -> VectorStorageBackend:
        """Active storage backend identifier."""
        pass

    @abstractmethod
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
        If a record with (memory_id, model, model_version) already exists,
        updates it atomically.
        """
        pass

    @abstractmethod
    def get_embedding(
        self,
        memory_id: str,
        model: str,
        model_version: str,
    ) -> Optional[StoredVectorRecord]:
        """
        Retrieve a stored embedding record by memory_id and model version.
        Returns None if not found.
        """
        pass

    @abstractmethod
    def delete_embedding(
        self,
        memory_id: str,
        model: Optional[str] = None,
        model_version: Optional[str] = None,
    ) -> bool:
        """
        Delete embedding for a memory_id.
        If model is specified, deletes only that model's embedding.
        Returns True if a record was removed, False otherwise.
        """
        pass

    @abstractmethod
    def has_embedding(
        self,
        memory_id: str,
        model: str,
        model_version: str,
    ) -> bool:
        """Check whether an embedding exists for the given memory and model."""
        pass

    @abstractmethod
    def search_similar(
        self,
        query_vector: List[float],
        top_k: int = 5,
        model: Optional[str] = None,
        model_version: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[VectorSearchResult]:
        """
        Execute cosine similarity search against stored vectors.
        Returns results sorted from highest similarity to lowest.
        """
        pass

    @abstractmethod
    def count(self, model: Optional[str] = None) -> int:
        """Return total number of stored vectors."""
        pass

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """Return health status and operational metadata."""
        pass

    def close(self) -> None:
        """Optional resource cleanup."""
        pass
