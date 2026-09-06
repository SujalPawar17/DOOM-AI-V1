"""
DOOM V5.2.1 — Embedding Foundation Package
Exports the public embedding provider interface, results, cache, and router.
"""
from memory.embedding.base import (
    EmbeddingProvider,
    EmbeddingResult,
    EmbeddingPolicyDecision,
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
    DEFAULT_CACHE_SIZE,
)
from memory.embedding.fastembed_provider import FastEmbedProvider
from memory.embedding.cache import EmbeddingCache
from memory.embedding.router import EmbeddingRouter, embedding_router

__all__ = [
    "EmbeddingProvider",
    "EmbeddingResult",
    "EmbeddingPolicyDecision",
    "EmbeddingError",
    "InputValidationError",
    "PolicyViolationError",
    "ProviderUnavailableError",
    "DEFAULT_MODEL_NAME",
    "DEFAULT_MODEL_VERSION",
    "DEFAULT_DIMENSION",
    "DEFAULT_PROVIDER_NAME",
    "MAX_INPUT_LENGTH",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_CACHE_SIZE",
    "FastEmbedProvider",
    "EmbeddingCache",
    "EmbeddingRouter",
    "embedding_router",
]
