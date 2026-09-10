"""DOOM-owned conservative cost registry. Provider self-labels are ignored."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from core.cost_guard.types import CostClass, ResourceType


@dataclass(frozen=True)
class ResourceAttestation:
    """Independent DOOM classification. verified_free_tier must be True to ALLOW FREE_TIER."""

    resource_type: ResourceType
    provider: str
    cost_class: CostClass
    verified_free_tier: bool = False
    models_required: bool = False
    allowed_models: Tuple[str, ...] = ()


def _key(resource_type: ResourceType, provider: str) -> Tuple[str, str]:
    return (resource_type.value, (provider or "").strip().lower())


def _att(
    resource_type: ResourceType,
    provider: str,
    cost_class: CostClass,
    *,
    verified_free_tier: bool = False,
    models_required: bool = False,
    allowed_models: Tuple[str, ...] = (),
) -> ResourceAttestation:
    return ResourceAttestation(
        resource_type=resource_type,
        provider=provider,
        cost_class=cost_class,
        verified_free_tier=verified_free_tier,
        models_required=models_required,
        allowed_models=allowed_models,
    )


def build_default_registry() -> Dict[Tuple[str, str], ResourceAttestation]:
    """HARD $0 conservative map. No Groq/NIM FREE_TIER attestation."""
    rows = [
        _att(ResourceType.LLM, "ollama", CostClass.LOCAL_FREE),
        _att(ResourceType.LLM, "fallback", CostClass.LOCAL_FREE),
        _att(ResourceType.LLM, "openai", CostClass.PAID),
        _att(ResourceType.LLM, "gemini", CostClass.PAID),
        _att(ResourceType.LLM, "bedrock", CostClass.PAID),
        _att(ResourceType.LLM, "groq", CostClass.UNKNOWN),
        _att(ResourceType.LLM, "nim", CostClass.UNKNOWN),
        _att(ResourceType.TTS, "pyttsx3", CostClass.LOCAL_FREE),
        _att(ResourceType.TTS, "elevenlabs", CostClass.PAID),
        _att(ResourceType.TTS, "edge_tts", CostClass.UNKNOWN),
        _att(ResourceType.TTS, "gtts", CostClass.UNKNOWN),
        _att(ResourceType.STT, "google_web_speech", CostClass.UNKNOWN),
        _att(ResourceType.STT, "local_whisper", CostClass.LOCAL_FREE),
        _att(ResourceType.STT_DOWNLOAD, "huggingface", CostClass.UNKNOWN),
        _att(ResourceType.TRANSLATION, "google_translate", CostClass.UNKNOWN),
        _att(ResourceType.SEARCH, "duckduckgo", CostClass.UNKNOWN),
        _att(ResourceType.SEARCH, "ip_api", CostClass.UNKNOWN),
        _att(ResourceType.SEARCH, "youtube", CostClass.UNKNOWN),
        _att(ResourceType.HTTP, "arbitrary", CostClass.UNKNOWN),
        _att(ResourceType.CONNECTOR, "gmail", CostClass.UNKNOWN),
        _att(ResourceType.CONNECTOR, "google_calendar", CostClass.UNKNOWN),
        _att(ResourceType.CONNECTOR, "github", CostClass.UNKNOWN),
        _att(ResourceType.CONNECTOR, "google_oauth", CostClass.UNKNOWN),
        _att(ResourceType.CONNECTOR_WRITE, "google_calendar", CostClass.UNKNOWN),
        _att(ResourceType.EMBEDDING, "fastembed", CostClass.LOCAL_FREE),
        _att(ResourceType.EMBEDDING_DOWNLOAD, "huggingface", CostClass.UNKNOWN),
        _att(ResourceType.DATABASE, "postgres", CostClass.LOCAL_FREE),
        _att(ResourceType.VISION, "local_uia", CostClass.LOCAL_FREE),
        _att(ResourceType.OTHER, "local_filesystem", CostClass.LOCAL_FREE),
        _att(ResourceType.OTHER, "local_experience", CostClass.LOCAL_FREE),
        _att(ResourceType.OTHER, "wolfram", CostClass.UNKNOWN),
        _att(ResourceType.OTHER, "newsapi", CostClass.UNKNOWN),
        _att(ResourceType.OTHER, "speedtest", CostClass.UNKNOWN),
        _att(ResourceType.OTHER, "wikipedia", CostClass.UNKNOWN),
    ]
    return {_key(r.resource_type, r.provider): r for r in rows}


DEFAULT_REGISTRY = build_default_registry()


def lookup_attestation(
    registry: Dict[Tuple[str, str], ResourceAttestation],
    resource_type: ResourceType,
    provider: str,
) -> Optional[ResourceAttestation]:
    return registry.get(_key(resource_type, provider))
