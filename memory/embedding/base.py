"""
DOOM V5.2.1 — Embedding Foundation Base Abstractions
Defines the core provider interface, result schemas, policy decisions,
and operational constants for dense vector embedding generation.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import List, Optional, Dict, Any


# ---------------------------------------------------------------------------
# Centralized Model & Operational Constants
# ---------------------------------------------------------------------------
DEFAULT_MODEL_NAME: str = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_MODEL_VERSION: str = "1.0"
DEFAULT_DIMENSION: int = 384
DEFAULT_PROVIDER_NAME: str = "fastembed"

# Maximum input character length to prevent denial-of-service / memory exhaustion.
# 4000 chars covers ~800-1000 tokens, well within typical chunking limits.
MAX_INPUT_LENGTH: int = 4000

DEFAULT_BATCH_SIZE: int = 32
DEFAULT_CACHE_SIZE: int = 256
DEFAULT_TIMEOUT_SEC: float = 5.0


# ---------------------------------------------------------------------------
# Policy & Classification Enums
# ---------------------------------------------------------------------------
class EmbeddingPolicyDecision(str, Enum):
    """Policy validation decision prior to embedding."""
    ALLOWED = "ALLOWED"
    BLOCKED_SENSITIVE = "BLOCKED_SENSITIVE"
    BLOCKED_SECRET = "BLOCKED_SECRET"
    INVALID_INPUT = "INVALID_INPUT"


# ---------------------------------------------------------------------------
# Structured Exceptions (Non-fatal, controlled)
# ---------------------------------------------------------------------------
class EmbeddingError(Exception):
    """Base exception for all embedding operations."""
    pass


class InputValidationError(EmbeddingError):
    """Raised when input text violates formatting, type, or length constraints."""
    pass


class PolicyViolationError(EmbeddingError):
    """Raised when input violates security/privacy policies (secrets, sensitive tags)."""
    pass


class ProviderUnavailableError(EmbeddingError):
    """Raised when the embedding engine or model cannot be loaded or is offline."""
    pass


# ---------------------------------------------------------------------------
# Embedding Result Schema
# ---------------------------------------------------------------------------
@dataclass
class EmbeddingResult:
    """
    Structured metadata and vector representation produced by an EmbeddingProvider.
    Contains all required fields for future V5.2 vector storage and auditability.
    """
    vector: List[float]
    dimension: int
    provider: str
    model: str
    model_version: str
    normalized: bool
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    latency_ms: float = 0.0
    content_hash: str = ""

    def __post_init__(self):
        # Validate vector invariants
        if not isinstance(self.vector, list):
            if hasattr(self.vector, "tolist"):
                self.vector = self.vector.tolist()
            else:
                self.vector = list(self.vector)

        if len(self.vector) != self.dimension:
            raise ValueError(
                f"Vector dimension mismatch: expected {self.dimension}, got {len(self.vector)}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Convert metadata to dictionary (excluding raw vector by default for safety)."""
        return {
            "dimension": self.dimension,
            "provider": self.provider,
            "model": self.model,
            "model_version": self.model_version,
            "normalized": self.normalized,
            "created_at": self.created_at,
            "latency_ms": round(self.latency_ms, 2),
            "content_hash": self.content_hash,
        }


# ---------------------------------------------------------------------------
# Abstract Embedding Provider Interface
# ---------------------------------------------------------------------------
class EmbeddingProvider(ABC):
    """
    Abstract interface for all embedding engines in DOOM.
    The rest of the system interacts solely through this contract.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the provider (e.g. 'fastembed', 'sentence_transformers')."""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier (e.g. 'sentence-transformers/all-MiniLM-L6-v2')."""
        pass

    @property
    @abstractmethod
    def model_version(self) -> str:
        """Version string of the model."""
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Vector output dimension (e.g. 384)."""
        pass

    @abstractmethod
    def embed(self, text: str) -> EmbeddingResult:
        """
        Generate dense vector embedding for a single text input.
        Must validate input, normalize output, and compute latency.
        """
        pass

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[EmbeddingResult]:
        """
        Generate dense vector embeddings for a batch of text inputs.
        Preserves input ordering and enforces batch bounds.
        """
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """Check whether the model is loaded and ready for inference."""
        pass

    def close(self) -> None:
        """Optional resource cleanup."""
        pass
