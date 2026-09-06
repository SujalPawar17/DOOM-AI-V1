"""
DOOM V5.2.1 — Embedding Router
Canonical entry point for embedding generation across DOOM.
Orchestrates provider selection, LRU caching, policy checks, telemetry,
and non-fatal graceful failure isolation.
"""
import threading
import time
from typing import List, Optional, Dict, Any

from memory.embedding.base import (
    EmbeddingProvider,
    EmbeddingResult,
    EmbeddingError,
    InputValidationError,
    PolicyViolationError,
    ProviderUnavailableError,
    DEFAULT_MODEL_NAME,
    DEFAULT_DIMENSION,
    DEFAULT_CACHE_SIZE,
)
from memory.embedding.cache import EmbeddingCache
from memory.embedding.fastembed_provider import FastEmbedProvider


class EmbeddingRouter:
    """
    Thread-safe router managing embedding providers and caching.
    Ensures embedding failures degrade safely without interrupting task execution.
    """

    def __init__(
        self,
        provider: Optional[EmbeddingProvider] = None,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ):
        self._provider: EmbeddingProvider = provider or FastEmbedProvider(lazy_load=True)
        self._cache = EmbeddingCache(max_size=cache_size)
        self._lock = threading.Lock()

        # Telemetry counters
        self._total_requests: int = 0
        self._successful_requests: int = 0
        self._failed_requests: int = 0
        self._total_batch_items: int = 0

    @property
    def provider(self) -> EmbeddingProvider:
        return self._provider

    @property
    def cache(self) -> EmbeddingCache:
        return self._cache

    def get_metadata(self) -> Dict[str, Any]:
        """Return active model and provider metadata."""
        return {
            "provider": self._provider.provider_name,
            "model": self._provider.model_name,
            "model_version": self._provider.model_version,
            "dimension": self._provider.dimension,
        }

    # ------------------------------------------------------------------
    # Single Item Embedding
    # ------------------------------------------------------------------
    def embed(
        self,
        text: str,
        check_policy: bool = True,
        use_cache: bool = True,
    ) -> Optional[EmbeddingResult]:
        """
        Generate embedding for a single text input with caching and failure isolation.
        Returns None on failure (never raises into caller execution).
        """
        with self._lock:
            self._total_requests += 1

        # Check in-memory LRU cache
        if use_cache and isinstance(text, str) and text.strip():
            cached = self._cache.get(
                self._provider.model_name,
                self._provider.model_version,
                text,
            )
            if cached is not None:
                return cached

        try:
            t0 = time.perf_counter()
            result = self._provider.embed(text, check_policy=check_policy)

            # Store in cache on success
            if use_cache:
                self._cache.put(result, text)

            with self._lock:
                self._successful_requests += 1

            return result

        except (InputValidationError, PolicyViolationError) as e:
            # Policy or validation violations are structured rejections
            with self._lock:
                self._failed_requests += 1
            print(f"[EMBEDDING ROUTER] Validation/Policy rejection: {e}")
            return None

        except ProviderUnavailableError as e:
            with self._lock:
                self._failed_requests += 1
            print(f"[EMBEDDING ROUTER] Provider unavailable: {e}")
            return None

        except Exception as e:
            with self._lock:
                self._failed_requests += 1
            print(f"[EMBEDDING ROUTER] Unexpected embedding failure (non-fatal): {e}")
            return None

    # ------------------------------------------------------------------
    # Batch Embedding
    # ------------------------------------------------------------------
    def embed_batch(
        self,
        texts: List[str],
        check_policy: bool = True,
        use_cache: bool = True,
    ) -> List[Optional[EmbeddingResult]]:
        """
        Generate embeddings for a batch of texts.
        Preserves strict input-to-output ordering.
        Utilizes cache for entries already computed and batches the remainder.
        Returns a list where individual failed items are None.
        """
        if not isinstance(texts, list):
            return []

        if not texts:
            return []

        with self._lock:
            self._total_batch_items += len(texts)

        results: List[Optional[EmbeddingResult]] = [None] * len(texts)
        missing_indices: List[int] = []
        missing_texts: List[str] = []

        # Step 1: Check cache for each item
        for idx, item in enumerate(texts):
            if use_cache and isinstance(item, str) and item.strip():
                cached = self._cache.get(
                    self._provider.model_name,
                    self._provider.model_version,
                    item,
                )
                if cached is not None:
                    results[idx] = cached
                    continue

            missing_indices.append(idx)
            missing_texts.append(item)

        # If all items were satisfied by cache, return immediately
        if not missing_texts:
            with self._lock:
                self._successful_requests += len(texts)
            return results

        # Step 2: Run batch embedding for missing items
        try:
            computed_results = self._provider.embed_batch(
                missing_texts, check_policy=check_policy
            )
            for idx, res, raw_text in zip(missing_indices, computed_results, missing_texts):
                results[idx] = res
                if use_cache and res is not None:
                    self._cache.put(res, raw_text)

            with self._lock:
                self._successful_requests += len(texts)

        except Exception as e:
            with self._lock:
                self._failed_requests += len(missing_texts)
            print(f"[EMBEDDING ROUTER] Batch inference failure (non-fatal): {e}")
            # Fallback: attempt item-by-item generation so one bad item doesn't drop the entire batch
            for idx, item in zip(missing_indices, missing_texts):
                results[idx] = self.embed(item, check_policy=check_policy, use_cache=use_cache)

        return results

    # ------------------------------------------------------------------
    # Observability & Diagnostics
    # ------------------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        """
        Comprehensive health check of the active embedding subsystem.
        Never throws an unhandled exception.
        """
        is_healthy = self._provider.health_check()
        return {
            "status": "HEALTHY" if is_healthy else "UNHEALTHY",
            "provider": self._provider.provider_name,
            "model": self._provider.model_name,
            "dimension": self._provider.dimension,
            "cache": self._cache.get_stats(),
            "telemetry": self.get_stats(),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Return operational telemetry metrics."""
        with self._lock:
            return {
                "total_requests": self._total_requests,
                "successful_requests": self._successful_requests,
                "failed_requests": self._failed_requests,
                "total_batch_items": self._total_batch_items,
                "cache_stats": self._cache.get_stats(),
            }

    def clear_cache(self) -> None:
        """Clear LRU cache."""
        self._cache.clear()


# Canonical shared instance
embedding_router = EmbeddingRouter()
