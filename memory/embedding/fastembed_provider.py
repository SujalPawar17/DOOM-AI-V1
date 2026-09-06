"""
DOOM V5.2.1 — FastEmbed Local Embedding Provider
Implements dense vector generation using FastEmbed (ONNX Runtime)
with the sentence-transformers/all-MiniLM-L6-v2 model (384 dimensions).
Completely local, offline-capable, thread-safe, and zero external cloud egress.
"""
import hashlib
import math
import os
import threading
import time
from typing import List, Optional, Any, Tuple
import numpy as np

# Suppress HuggingFace symlink warnings on Windows if not already set
if "HF_HUB_DISABLE_SYMLINKS_WARNING" not in os.environ:
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

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
    DEFAULT_PROVIDER_NAME,
    MAX_INPUT_LENGTH,
    DEFAULT_BATCH_SIZE,
)
from memory.validators import memory_validator


class FastEmbedProvider(EmbeddingProvider):
    """
    Local FastEmbed provider running ONNX Runtime models in-process.
    Provides lazy loading, thread-safe initialization, strict input validation,
    secret pattern filtering, and vector normalization.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        model_version: str = DEFAULT_MODEL_VERSION,
        dimension: int = DEFAULT_DIMENSION,
        max_batch_size: int = DEFAULT_BATCH_SIZE,
        lazy_load: bool = True,
    ):
        self._provider_name: str = DEFAULT_PROVIDER_NAME
        self._model_name: str = model_name
        self._model_version: str = model_version
        self._dimension: int = dimension
        self._max_batch_size: int = max_batch_size

        self._model: Optional[Any] = None
        self._init_lock = threading.Lock()
        self._init_time_ms: float = 0.0

        if not lazy_load:
            self._ensure_model_loaded()

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def model_version(self) -> str:
        return self._model_version

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def is_loaded(self) -> bool:
        """True if the underlying model is initialized in memory."""
        return self._model is not None

    def _ensure_model_loaded(self) -> None:
        """
        Thread-safe lazy initialization with double-checked locking.
        Prevents multiple concurrent threads from redundantly loading the ONNX session.
        """
        if self._model is None:
            with self._init_lock:
                if self._model is None:
                    t_start = time.perf_counter()
                    try:
                        from fastembed import TextEmbedding
                        # Initialize local ONNX model
                        self._model = TextEmbedding(model_name=self._model_name)
                        self._init_time_ms = (time.perf_counter() - t_start) * 1000.0
                    except Exception as e:
                        raise ProviderUnavailableError(
                            f"Failed to initialize FastEmbed model '{self._model_name}': {e}"
                        ) from e

    # ------------------------------------------------------------------
    # Input Validation & Policy Gates
    # ------------------------------------------------------------------
    def validate_input(self, text: Any, check_policy: bool = True) -> str:
        """
        Validate input string for embedding.
        Never logs raw input in error messages.
        """
        if text is None:
            raise InputValidationError("Embedding input cannot be None.")

        if not isinstance(text, str):
            raise InputValidationError(
                f"Embedding input must be a string, got {type(text).__name__}."
            )

        stripped = text.strip()
        if not stripped:
            raise InputValidationError(
                "Embedding input cannot be empty or whitespace-only."
            )

        if len(text) > MAX_INPUT_LENGTH:
            raise InputValidationError(
                f"Embedding input exceeds maximum length of {MAX_INPUT_LENGTH} characters "
                f"(received {len(text)} characters)."
            )

        if check_policy:
            # Check for credential patterns (passwords, tokens, keys)
            ok_secret, reason = memory_validator.check_secret(stripped)
            if not ok_secret:
                raise PolicyViolationError(
                    "Embedding rejected: content matches protected credential pattern."
                )

            # Check for raw chain-of-thought leakage
            ok_cot, reason_cot = memory_validator.check_chain_of_thought(stripped)
            if not ok_cot:
                raise PolicyViolationError(
                    "Embedding rejected: content matches raw chain-of-thought pattern."
                )

        return stripped

    # ------------------------------------------------------------------
    # Vector Normalization & Validation
    # ------------------------------------------------------------------
    def validate_and_normalize_vector(self, raw_vector: Any) -> Tuple[List[float], bool]:
        """
        Ensure vector is 384-dimensional, finite, and unit-normalized for cosine similarity.
        Returns (normalized_vector_list, is_normalized).
        """
        if raw_vector is None:
            raise EmbeddingError("Model returned None for vector.")

        arr = np.asarray(raw_vector, dtype=np.float32).flatten()

        if arr.shape[0] != self._dimension:
            raise ValueError(
                f"Vector dimension mismatch: expected {self._dimension}, got {arr.shape[0]}"
            )

        if not np.all(np.isfinite(arr)):
            raise ValueError("Vector contains non-finite values (NaN or Inf).")

        norm = float(np.linalg.norm(arr))
        if norm == 0.0 or math.isnan(norm):
            raise ValueError("Vector has zero or undefined norm.")

        # Unit normalize if not already within float tolerance
        if abs(norm - 1.0) > 1e-4:
            arr = arr / norm
            normalized = True
        else:
            normalized = True

        return arr.tolist(), normalized

    # ------------------------------------------------------------------
    # Embedding Execution
    # ------------------------------------------------------------------
    def embed(self, text: str, check_policy: bool = True) -> EmbeddingResult:
        """
        Generate a 384-dimensional dense vector for a single text input.
        """
        t0 = time.perf_counter()
        clean_text = self.validate_input(text, check_policy=check_policy)
        self._ensure_model_loaded()

        try:
            # fastembed.embed takes an iterable and returns a generator of numpy arrays
            raw_gen = self._model.embed([clean_text])
            raw_vector = next(raw_gen)
        except Exception as e:
            raise EmbeddingError(f"FastEmbed inference failure: {e}") from e

        vector, normalized = self.validate_and_normalize_vector(raw_vector)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        content_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()

        return EmbeddingResult(
            vector=vector,
            dimension=self._dimension,
            provider=self._provider_name,
            model=self._model_name,
            model_version=self._model_version,
            normalized=normalized,
            latency_ms=latency_ms,
            content_hash=content_hash,
        )

    def embed_batch(
        self, texts: List[str], check_policy: bool = True
    ) -> List[EmbeddingResult]:
        """
        Generate embeddings for a batch of text inputs.
        Preserves strict input order. Chunks batches to prevent unbounded memory spikes.
        """
        if not isinstance(texts, list):
            raise InputValidationError("texts must be a list of strings.")

        if not texts:
            return []

        t0_total = time.perf_counter()

        # Validate all inputs first
        clean_texts = [self.validate_input(t, check_policy=check_policy) for t in texts]
        self._ensure_model_loaded()

        all_results: List[EmbeddingResult] = []
        batch_size = max(1, self._max_batch_size)

        for i in range(0, len(clean_texts), batch_size):
            chunk = clean_texts[i : i + batch_size]
            t0_chunk = time.perf_counter()
            try:
                raw_vectors = list(self._model.embed(chunk))
            except Exception as e:
                raise EmbeddingError(f"FastEmbed batch inference failure: {e}") from e

            chunk_latency = (time.perf_counter() - t0_chunk) * 1000.0
            per_item_latency = chunk_latency / max(1, len(chunk))

            for text_str, raw_vec in zip(chunk, raw_vectors):
                vec, normalized = self.validate_and_normalize_vector(raw_vec)
                content_hash = hashlib.sha256(text_str.encode("utf-8")).hexdigest()
                all_results.append(
                    EmbeddingResult(
                        vector=vec,
                        dimension=self._dimension,
                        provider=self._provider_name,
                        model=self._model_name,
                        model_version=self._model_version,
                        normalized=normalized,
                        latency_ms=per_item_latency,
                        content_hash=content_hash,
                    )
                )

        return all_results

    def health_check(self) -> bool:
        """
        Check if provider is operational.
        Performs a lightweight single-token inference test. Never raises.
        """
        try:
            res = self.embed("health", check_policy=False)
            return (
                res is not None
                and len(res.vector) == self._dimension
                and res.normalized
            )
        except Exception:
            return False

    def close(self) -> None:
        """Release reference to underlying model session."""
        with self._init_lock:
            self._model = None
