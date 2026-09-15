"""V8.22 decision formatting. Template-first; optional single Ollama polish."""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Set, Tuple

from orchestration.decision.types import (
    DecisionConfidence,
    DecisionResult,
    DecisionStatus,
)

_CONF_LABEL = {
    DecisionConfidence.HIGH: "High",
    DecisionConfidence.MEDIUM: "Medium",
    DecisionConfidence.LOW: "Low",
}

_SECTION_HEADERS = (
    "recommendation",
    "why",
    "trade-offs",
    "tradeoffs",
    "confidence",
    "assumptions",
    "options",
    "options considered",
    "facts",
    "constraints",
    "preferences",
)

_ACTION_UNSAFE = re.compile(
    r"(?i)\b("
    r"click|type|execute|run|delete|send|purchase|authorize|approve|"
    r"bypass\s+security|ignore\s+security|override\s+(doom\s+)?safety|"
    r"reveal\s+(credentials?|secrets?)|password|api[_-]?key|"
    r"filesystem|browser\s+session|computer\s+session|"
    r"authorized_plan_hash|plan_hash|csrf(_token)?|owner_id|"
    r"executionidentity|session_id(_hash)?"
    r")\b"
)

_AUTH_LEAK = re.compile(
    r"(?i)\b("
    r"authorized_plan_hash|plan_hash|csrf(_token)?|owner_id|"
    r"executionidentity|session_id(_hash)?|cookie"
    r")\b\s*[:=]"
)

_STOPWORDS = frozenset(
    {
        "a", "an", "the", "this", "that", "these", "those", "it", "its",
        "is", "are", "was", "were", "be", "been", "being", "to", "of", "for",
        "and", "or", "with", "your", "you", "their", "there", "here", "from",
        "into", "onto", "over", "under", "also", "more", "very", "just",
        "only", "still", "than", "then", "when", "which", "while", "where",
        "what", "have", "has", "had", "will", "would", "could", "should",
        "may", "might", "does", "did", "doing", "such", "some", "any", "all",
        "each", "both", "few", "many", "much", "most", "other",
        "about", "because", "since", "so", "as", "at", "by", "on", "in",
    }
)


def format_decision_template(result: DecisionResult) -> str:
    """Deterministic user-facing decision text. No scores or internals."""
    if result.status is DecisionStatus.CLARIFY or (
        result.status is DecisionStatus.LOW_CONFIDENCE and result.clarification
        and not result.recommendation
    ):
        return (result.clarification or "Could you clarify the options?").strip()[:2048]

    if not result.recommendation:
        if result.clarification:
            return result.clarification.strip()[:2048]
        return "I need clearer options to make a recommendation."

    lines = [f"Recommendation: {result.recommendation}"]
    if result.reasons:
        lines.append("")
        lines.append("Why:")
        for r in result.reasons:
            lines.append(f"- {r}")
    if result.tradeoffs:
        lines.append("")
        lines.append("Trade-offs:")
        for t in result.tradeoffs:
            lines.append(f"- {t}")
    lines.append("")
    lines.append(f"Confidence: {_CONF_LABEL.get(result.confidence, 'Low')}")
    if result.assumptions:
        lines.append("")
        lines.append("Assumptions:")
        for a in result.assumptions:
            lines.append(f"- {a}")
    return "\n".join(lines).strip()[:2048]


def _norm_line(text: str) -> str:
    t = re.sub(r"\s+", " ", str(text or "").strip().lower())
    t = re.sub(r"[^\w\s+#.]", "", t)
    return t.strip()


def _significant_tokens(text: str) -> Set[str]:
    out: Set[str] = set()
    for tok in _norm_line(text).replace("/", " ").split():
        if len(tok) < 3:
            continue
        if tok in _STOPWORDS:
            continue
        out.add(tok)
    return out


def _extract_recommendation_line(text: str) -> str:
    m = re.search(r"(?im)^recommendation:\s*(.+)$", str(text or ""))
    if not m:
        return ""
    return m.group(1).strip()[:120]


def _extract_confidence_line(text: str) -> str:
    m = re.search(r"(?im)^confidence:\s*(.+)$", str(text or ""))
    if not m:
        return ""
    return m.group(1).strip()


def _section_bullets(text: str, header: str) -> Optional[List[str]]:
    """Return bullets under a section header, or None if section absent."""
    pat = re.compile(
        rf"(?ims)^[ \t]*{re.escape(header)}\s*:?[ \t]*\n(.*?)(?=^[ \t]*(?:{'|'.join(re.escape(h) for h in _SECTION_HEADERS)})\s*:|\Z)"
    )
    m = pat.search(str(text or ""))
    if not m:
        return None
    body = m.group(1)
    bullets: List[str] = []
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^[-*•]\s+", "", s)
        s = re.sub(r"^\d+[.)]\s+", "", s)
        if s:
            bullets.append(s.strip()[:200])
    return bullets


def _has_section(text: str, header: str) -> bool:
    return bool(re.search(rf"(?im)^[ \t]*{re.escape(header)}\s*:", str(text or "")))


def _bullets_bounded(model_bullets: Sequence[str], authoritative: Sequence[str]) -> bool:
    """Each model bullet's significant tokens must appear in authoritative text."""
    auth_blob = " ".join(authoritative)
    auth_tokens = _significant_tokens(auth_blob)
    if not authoritative:
        return len(model_bullets) == 0
    if not model_bullets:
        return False
    for bullet in model_bullets:
        toks = _significant_tokens(bullet)
        if not toks:
            return False
        if not toks.issubset(auth_tokens):
            return False
    return True


def _assumption_sets_match(model_bullets: Sequence[str], authoritative: Sequence[str]) -> bool:
    model_set = {_norm_line(b) for b in model_bullets if _norm_line(b)}
    auth_set = {_norm_line(a) for a in authoritative if _norm_line(a)}
    return model_set == auth_set


def _options_mentioned_ok(text: str, options: Sequence[str]) -> bool:
    """Reject invented/changed option lists if an Options section is present."""
    for hdr in ("options considered", "options"):
        bullets = _section_bullets(text, hdr)
        if bullets is None:
            continue
        model_opts = {_norm_line(b) for b in bullets if _norm_line(b)}
        auth_opts = {_norm_line(o) for o in options if _norm_line(o)}
        if model_opts != auth_opts:
            return False
    m = re.search(r"(?im)^[ \t]*options(?:\s+considered)?\s*:\s*(.+)$", text)
    if m and not re.match(r"^\s*$", m.group(1) or ""):
        line = m.group(1).strip()
        if line and not line.startswith(("-", "*", "•")):
            parts = [p.strip() for p in re.split(r"[,;/]| \bor\b ", line, flags=re.I) if p.strip()]
            if parts:
                model_opts = {_norm_line(p) for p in parts}
                auth_opts = {_norm_line(o) for o in options if _norm_line(o)}
                if model_opts != auth_opts:
                    return False
    return True


def pin_ollama_formatting(
    deterministic: DecisionResult,
    model_text: str,
) -> str:
    """Validate optional Ollama polish against authoritative DecisionResult.

    On any protected-field divergence, unsafe language, or ambiguity:
    discard the entire model output and return the deterministic template.
    Never mutates DecisionResult. Never retries.
    """
    template = format_decision_template(deterministic)
    if deterministic.status is not DecisionStatus.OK or not deterministic.recommendation:
        return template
    raw = str(model_text or "").strip()
    if not raw:
        return template

    if _AUTH_LEAK.search(raw) or _ACTION_UNSAFE.search(raw):
        return template

    cleaned = re.sub(
        r"(?i)</?(decision_context|safe_context|situation|conversation_context)\b[^>]*>",
        "",
        raw,
    ).strip()
    if len(cleaned) < 20:
        return template

    if not re.search(r"(?im)^recommendation:\s*\S", cleaned):
        return template

    model_rec = _extract_recommendation_line(cleaned)
    auth_rec = deterministic.recommendation.strip()
    if not model_rec:
        return template
    mr = model_rec.strip().rstrip(".")
    ar = auth_rec.strip().rstrip(".")
    if mr.lower() != ar.lower():
        return template

    model_conf = _extract_confidence_line(cleaned)
    auth_conf = _CONF_LABEL.get(deterministic.confidence, "Low")
    if not model_conf or model_conf.strip().lower() != auth_conf.lower():
        return template

    for banned in ("facts", "constraints", "preferences"):
        if _has_section(cleaned, banned):
            return template

    if not _options_mentioned_ok(cleaned, deterministic.options_considered):
        return template

    model_assumptions = _section_bullets(cleaned, "assumptions")
    auth_assumptions = tuple(deterministic.assumptions or ())
    if auth_assumptions:
        if model_assumptions is None:
            return template
        if not _assumption_sets_match(model_assumptions, auth_assumptions):
            return template
    else:
        if model_assumptions is not None and len(model_assumptions) > 0:
            return template

    model_why = _section_bullets(cleaned, "why")
    auth_why = tuple(deterministic.reasons or ())
    if auth_why:
        if model_why is None:
            return template
        if not _bullets_bounded(model_why, auth_why):
            return template
    else:
        if model_why is not None and len(model_why) > 0:
            return template

    model_trade = _section_bullets(cleaned, "trade-offs")
    if model_trade is None:
        model_trade = _section_bullets(cleaned, "tradeoffs")
    auth_trade = tuple(deterministic.tradeoffs or ())
    if auth_trade:
        if model_trade is None:
            return template
        if not _bullets_bounded(model_trade, auth_trade):
            return template
    else:
        if model_trade is not None and len(model_trade) > 0:
            return template

    return cleaned[:2048]


def maybe_polish_with_ollama(
    result: DecisionResult,
    *,
    provider: Any = None,
) -> Tuple[str, int]:
    """Return (text, ollama_calls). Prefer template; at most one local call."""
    template = format_decision_template(result)
    if result.status is not DecisionStatus.OK or not result.recommendation:
        return template, 0
    if result.confidence in (DecisionConfidence.HIGH, DecisionConfidence.MEDIUM):
        return template, 0
    if provider is None:
        return template, 0
    name = str(getattr(provider, "name", "") or "").strip().lower()
    if name != "ollama":
        return template, 0
    try:
        from core.cost_guard.invoke import authorize_llm_provider
        from core.cost_guard.types import CostClass, CostGuardBlockedError
        from models.base_provider import (
            LLMResponse,
            ProviderTimeoutError,
            ProviderUnavailableError,
        )
        decision = authorize_llm_provider(provider, capability="v8_decide")
        if (not decision.is_allow) or decision.cost_class != CostClass.LOCAL_FREE:
            return template, 0
        prompt = (
            "Rephrase the following decision result more naturally. "
            "Do not change the recommendation, confidence, facts, or options. "
            "Keep the same section headers.\n\n"
            f"{template}"
        )
        try:
            out = provider.generate(
                prompt,
                system_prompt=(
                    "You format decision summaries only. Never invent facts. "
                    "Never claim you performed computer actions. No tools."
                ),
                tools=None,
                temperature=0.2,
                timeout=50,
                num_predict=180,
                capability="v8_decide",
            )
        except (ProviderTimeoutError, ProviderUnavailableError, CostGuardBlockedError):
            return template, 1
        except Exception:
            return template, 1
        if not isinstance(out, LLMResponse):
            return template, 1
        return pin_ollama_formatting(result, str(out.text or "")), 1
    except Exception:
        return template, 0
