"""
DOOM V5.2.1 — In-Memory LRU Vector Cache
Provides thread-safe, bounded, in-memory caching of computed embeddings.
Uses deterministic SHA-256 digests as cache keys so raw text is never stored.
"""
from collections import OrderedDict
import hashlib
import threading
from typing import Optional, Dict, Any

from memory.embedding.base import EmbeddingResult, DEFAULT_CACHE_SIZE


class EmbeddingCache:
    """
    Thread-safe bounded Least Recently Used (LRU) cache for EmbeddingResult objects.
    Ensures recurring queries or repeated memory evaluations execute in <1ms without
    re-running model inference.
    """

    def __init__(self, max_size: int = DEFAULT_CACHE_SIZE):
        if max_size <= 0:
            raise ValueError("Cache max_size must be a positive integer.")
        self._max_size = max_size
        self._cache: OrderedDict[str, EmbeddingResult] = OrderedDict()
        self._lock = threading.Lock()
        self._hits: int = 0
        self._misses: int = 0

    @staticmethod
    def generate_key(model: str, model_version: str, text: str) -> str:
        """
        Generate a deterministic, anonymized hash key.
        Raw text is hashed immediately with SHA-256 and never retained in the key.
        """
        norm_text = text.strip()
        payload = f"{model}:{model_version}:{norm_text}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def get(self, model: str, model_version: str, text: str) -> Optional[EmbeddingResult]:
        """
        Retrieve an embedding result from the cache.
        Returns None on a cache miss.
        """
        key = self.generate_key(model, model_version, text)
        with self._lock:
            if key in self._cache:
                self._hits += 1
                # Move to end to record recent usage
                self._cache.move_to_end(key)
                return self._cache[key]
            self._misses += 1
            return None

    def put(self, result: EmbeddingResult, text: str) -> None:
        """
        Store an embedding result in the cache.
        Evicts the oldest entry if max_size is exceeded.
        """
        key = self.generate_key(result.model, result.model_version, text)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = result
                return

            if len(self._cache) >= self._max_size:
                # Evict oldest (FIFO item from beginning)
                self._cache.popitem(last=False)

            self._cache[key] = result

    def invalidate(self, model: str, model_version: str, text: str) -> bool:
        """Invalidate a specific cached entry. Returns True if evicted."""
        key = self.generate_key(model, model_version, text)
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        """Clear all entries from the cache."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    @property
    def size(self) -> int:
        """Current number of items in cache."""
        with self._lock:
            return len(self._cache)

    @property
    def max_size(self) -> int:
        """Maximum allowed items in cache."""
        return self._max_size

    def get_stats(self) -> Dict[str, Any]:
        """Return cache telemetry and hit/miss statistics."""
        with self._lock:
            total_lookups = self._hits + self._misses
            hit_ratio = (self._hits / total_lookups) if total_lookups > 0 else 0.0
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "total_lookups": total_lookups,
                "hit_ratio": round(hit_ratio, 4),
            }
