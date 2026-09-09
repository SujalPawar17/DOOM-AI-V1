from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum


class ProviderCostTier(str, Enum):
    """Cost tier classification for provider routing preference."""
    LOCAL = "LOCAL"           # Runs locally, no API cost
    FREE_TIER = "FREE_TIER"   # Hosted free tier with limits
    LOW_COST = "LOW_COST"     # Low-cost cloud API
    PAID = "PAID"             # Paid cloud API
    DISABLED = "DISABLED"     # Explicitly disabled


class ProviderDeploymentMode(str, Enum):
    """Deployment mode of the provider."""
    LOCAL = "LOCAL"
    HOSTED_CLOUD = "HOSTED_CLOUD"
    SELF_HOSTED = "SELF_HOSTED"
    PARTNER_HOSTED = "PARTNER_HOSTED"


@dataclass
class ProviderMetadata:
    """Rich provider metadata for routing decisions and dashboard display."""
    provider_id: str
    name: str
    enabled: bool = True
    configured: bool = False
    authenticated: bool = False
    available: bool = False
    healthy: bool = False
    deployment_mode: ProviderDeploymentMode = ProviderDeploymentMode.HOSTED_CLOUD
    cost_tier: ProviderCostTier = ProviderCostTier.PAID
    billing_possible: bool = False
    capabilities: List[str] = None
    models: List[str] = None
    context_limit: int = 0
    streaming: bool = False
    tool_calling: bool = False
    multimodal: bool = False
    latency_ms: float = 0.0
    reliability_score: float = 0.0

    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = []
        if self.models is None:
            self.models = []


@dataclass
class LLMResponse:
    text: str
    tool_calls: List[Dict[str, Any]]
    model_name: str
    usage: Optional[Dict[str, int]] = None


class ProviderError(Exception):
    """Base exception for provider errors."""
    def __init__(self, message: str, provider: str = "", retryable: bool = False):
        self.provider = provider
        self.retryable = retryable
        super().__init__(message)


class ProviderAuthError(ProviderError):
    """Authentication/authorization failure."""
    def __init__(self, message: str, provider: str = ""):
        super().__init__(message, provider, retryable=False)


class ProviderRateLimitError(ProviderError):
    """Rate limit exceeded."""
    def __init__(self, message: str, provider: str = "", retry_after: float = 60.0):
        self.retry_after = retry_after
        super().__init__(message, provider, retryable=True)


class ProviderModelNotFoundError(ProviderError):
    """Model not found or not accessible."""
    def __init__(self, message: str, provider: str = "", model: str = ""):
        self.model = model
        super().__init__(message, provider, retryable=False)


class ProviderTimeoutError(ProviderError):
    """Request timeout."""
    def __init__(self, message: str, provider: str = "", timeout: float = 0.0):
        self.timeout = timeout
        super().__init__(message, provider, retryable=True)


class ProviderUnavailableError(ProviderError):
    """Provider completely unavailable."""
    def __init__(self, message: str, provider: str = ""):
        super().__init__(message, provider, retryable=True)


class BaseLLMProvider(ABC):
    """Abstract interface for all DOOM Model Providers"""
    name: str = ""
    
    # Provider metadata - subclasses should override
    cost_tier: str = "PAID"
    deployment_mode: str = "HOSTED_CLOUD"
    billing_possible: bool = True
    capabilities: List[str] = []
    models: List[str] = []
    context_limit: int = 4096
    streaming: bool = False
    tool_calling: bool = False
    multimodal: bool = False

    @abstractmethod
    def is_available(self) -> bool:
        """Check if API key or local daemon is ready"""
        pass

    @abstractmethod
    def generate(self,
                 prompt: str,
                 system_prompt: str = "",
                 tools: Optional[List[Dict[str, Any]]] = None,
                 temperature: float = 0.7) -> LLMResponse:
        """Generate response or function tool calls"""
        pass

    def get_metadata(self) -> Dict[str, Any]:
        """Return provider metadata for routing and dashboard."""
        return {
            "provider_id": self.name,
            "name": getattr(self, 'display_name', self.name),
            "enabled": getattr(self, '_enabled', True),
            "configured": self.is_configured(),
            "authenticated": self.is_authenticated(),
            "available": self.is_available(),
            "healthy": self.is_healthy(),
            "deployment_mode": self.deployment_mode,
            "cost_tier": self.cost_tier,
            "billing_possible": self.billing_possible,
            "capabilities": self.capabilities,
            "models": self.models,
            "context_limit": self.context_limit,
            "streaming": self.streaming,
            "tool_calling": self.tool_calling,
            "multimodal": self.multimodal,
        }

    def is_configured(self) -> bool:
        """Check if provider has required configuration (API keys, endpoints)."""
        return True

    def is_authenticated(self) -> bool:
        """Check if credentials are valid (beyond just presence)."""
        return self.is_available()

    def is_healthy(self) -> bool:
        """Check if provider is currently healthy (no circuit breaker, no recent failures)."""
        return self.is_available()

    def enable(self):
        """Enable provider for routing."""
        self._enabled = True

    def disable(self):
        """Disable provider from routing (but keep registered)."""
        self._enabled = False

    def is_enabled(self) -> bool:
        """Check if provider is enabled for routing."""
        return getattr(self, '_enabled', True)
