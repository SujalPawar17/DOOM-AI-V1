"""V6.2.8 privacy filter + bounded generate. No tools. LOCAL-only for PRIVATE."""

from __future__ import annotations

import concurrent.futures
from typing import Any, Dict, List, Optional, Tuple

from models.base_provider import LLMResponse
from proactive.config import (
    LLM_DRAFT_MAX_FAILOVER_HOPS,
    LLM_DRAFT_TIMEOUT_SEC,
    is_llm_draft_normal_enabled,
    is_llm_draft_private_enabled,
)

HOSTED_MODES = frozenset({"HOSTED_CLOUD", "PARTNER_HOSTED"})
NORMAL_NAMES = ("ollama", "groq", "nim", "openai", "gemini")


def _mode(p: Any) -> str:
    return str(getattr(p, "deployment_mode", "") or "")


def allowed_providers_for(privacy_class: str) -> List[str]:
    """Privacy is enforced here, before any generate call. LOCAL is not a confidentiality proof."""
    pc = str(privacy_class or "NORMAL")
    if pc == "SENSITIVE":
        return []
    from core.model_router import model_router
    if pc == "PRIVATE":
        if not is_llm_draft_private_enabled():
            return []
        names = []
        p = model_router.providers.get("ollama")
        if p and p.is_enabled() and p.is_available() and _mode(p) == "LOCAL":
            from core.cost_guard.invoke import llm_request_for_provider
            from core.cost_guard.guard import cost_guard
            if cost_guard.authorize(llm_request_for_provider(p, capability="bounded_draft", privacy_class=pc)).is_allow:
                names.append("ollama")
        return names[:LLM_DRAFT_MAX_FAILOVER_HOPS]
    if pc != "NORMAL":
        return []
    if not is_llm_draft_normal_enabled():
        return []
    out: List[str] = []
    for name in NORMAL_NAMES:
        p = model_router.providers.get(name)
        if not p or not p.is_enabled() or not p.is_available():
            continue
        if name == "fallback":
            continue
        from core.cost_guard.invoke import llm_request_for_provider
        from core.cost_guard.guard import cost_guard
        if not cost_guard.authorize(llm_request_for_provider(p, capability="bounded_draft", privacy_class=pc)).is_allow:
            continue
        out.append(name)
        if len(out) >= LLM_DRAFT_MAX_FAILOVER_HOPS:
            break
    return out


def generate_bounded_draft(
    system_prompt: str,
    user_prompt: str,
    privacy_class: str,
) -> Tuple[Optional[LLMResponse], str, str, str]:
    allowed = allowed_providers_for(privacy_class)
    if not allowed:
        return None, "", "", "NO_COMPLIANT_PROVIDER"
    from core.model_router import NoCapableProviderError, model_router

    def _run() -> LLMResponse:
        return model_router.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            tools=None,
            task_type="bounded_draft",
            allowed_providers=allowed,
        )

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(_run)
    try:
        resp = fut.result(timeout=float(LLM_DRAFT_TIMEOUT_SEC))
    except concurrent.futures.TimeoutError:
        return None, "", "", "TIMEOUT"
    except NoCapableProviderError:
        return None, "", "", "PROVIDER"
    except Exception as exc:
        name = type(exc).__name__.lower()
        msg = str(exc).lower()
        if "rate" in name or "429" in msg or "rate limit" in msg:
            return None, "", "", "PROVIDER"
        return None, "", "", "PROVIDER"
    finally:
        ex.shutdown(wait=False)
    if resp is None:
        return None, "", "", "EMPTY"
    if getattr(resp, "tool_calls", None):
        return resp, allowed[0], str(getattr(resp, "model_name", "") or "")[:80], "TOOL_CALLS"
    text = str(getattr(resp, "text", "") or "")
    if not text.strip():
        return resp, allowed[0], str(getattr(resp, "model_name", "") or "")[:80], "EMPTY"
    model = str(getattr(resp, "model_name", "") or "")[:80]
    return resp, allowed[0], model, ""
