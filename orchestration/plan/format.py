"""V8.23 plan formatting. Template-first; optional single Ollama polish."""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Set, Tuple

from orchestration.plan.types import (
    PlanConfidence,
    PlanResult,
    PlanStatus,
    PlanStepItem,
)

_CONF_LABEL = {
    PlanConfidence.HIGH: "High",
    PlanConfidence.MEDIUM: "Medium",
    PlanConfidence.LOW: "Low",
}

_ACTION_UNSAFE = re.compile(
    r"(?i)\b("
    r"click|type|execute|run|delete|send|purchase|authorize|approve|"
    r"bypass\s+security|ignore\s+security|override\s+(doom\s+)?safety|"
    r"reveal\s+(credentials?|secrets?)|password|api[_-]?key|"
    r"filesystem|browser\s+session|computer\s+session|rm\s+-rf|"
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


def format_plan_template(result: PlanResult) -> str:
    if result.status is PlanStatus.CLARIFY or (
        result.status is PlanStatus.LOW_CONFIDENCE and result.clarification and not result.steps
    ):
        return (result.clarification or CLARIFY_DEFAULT).strip()[:2048]
    if not result.steps:
        if result.clarification:
            return result.clarification.strip()[:2048]
        return CLARIFY_DEFAULT
    lines = [f"Plan: {result.title}"]
    lines.append("")
    lines.append("Steps:")
    for s in result.steps:
        lines.append(f"{s.index}. {s.title}")
        if s.detail:
            lines.append(f"   {s.detail}")
    if result.reasons:
        lines.append("")
        lines.append("Why:")
        for r in result.reasons:
            lines.append(f"- {r}")
    if result.blockers:
        lines.append("")
        lines.append("Watch-outs:")
        for b in result.blockers:
            lines.append(f"- {b}")
    lines.append("")
    lines.append(f"Confidence: {_CONF_LABEL.get(result.confidence, 'Low')}")
    if result.assumptions:
        lines.append("")
        lines.append("Assumptions:")
        for a in result.assumptions:
            lines.append(f"- {a}")
    return "\n".join(lines).strip()[:2048]


CLARIFY_DEFAULT = (
    "To build a useful plan, I need to know what you want to accomplish. "
    "Please describe the goal in a sentence or two."
)


def _norm_line(text: str) -> str:
    t = re.sub(r"\s+", " ", str(text or "").strip().lower())
    t = re.sub(r"[^\w\s+#.]", "", t)
    return t.strip()


def _extract_plan_title(text: str) -> str:
    m = re.search(r"(?im)^plan:\s*(.+)$", str(text or ""))
    return m.group(1).strip()[:120] if m else ""


def _extract_confidence_line(text: str) -> str:
    m = re.search(r"(?im)^confidence:\s*(.+)$", str(text or ""))
    return m.group(1).strip() if m else ""


def _parse_steps(text: str) -> List[Tuple[str, str]]:
    """Return list of (title, detail) from numbered steps section."""
    m = re.search(r"(?ims)^steps:\s*\n(.*?)(?=^[ \t]*(?:why|watch|confidence|assumptions)\s*:|\Z)", text)
    if not m:
        return []
    body = m.group(1)
    out: List[Tuple[str, str]] = []
    current_title = ""
    current_detail = ""
    for line in body.splitlines():
        sm = re.match(r"^\s*(\d+)[.)]\s+(.+)$", line.strip())
        if sm:
            if current_title:
                out.append((current_title, current_detail))
            current_title = sm.group(2).strip()
            current_detail = ""
            continue
        if line.strip() and current_title:
            d = line.strip()
            if d.startswith("-"):
                d = d.lstrip("- ").strip()
            current_detail = d
    if current_title:
        out.append((current_title, current_detail))
    return out


def _bullets_section(text: str, header: str) -> Optional[List[str]]:
    pat = re.compile(
        rf"(?ims)^[ \t]*{re.escape(header)}\s*:?\s*\n(.*?)(?=^[ \t]*(?:plan|steps|why|watch|confidence|assumptions)\s*:|\Z)"
    )
    m = pat.search(text)
    if not m:
        return None
    bullets: List[str] = []
    for line in m.group(1).splitlines():
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^[-*•]\s+", "", s)
        if s:
            bullets.append(s)
    return bullets


def _assumption_sets_match(model: Sequence[str], auth: Sequence[str]) -> bool:
    return {_norm_line(b) for b in model if _norm_line(b)} == {
        _norm_line(a) for a in auth if _norm_line(a)
    }


def _steps_match(model_steps: Sequence[Tuple[str, str]], auth: Sequence[PlanStepItem]) -> bool:
    if len(model_steps) != len(auth):
        return False
    for (mt, _), a in zip(model_steps, auth):
        if _norm_line(mt) != _norm_line(a.title):
            return False
    return True


def pin_ollama_formatting(deterministic: PlanResult, model_text: str) -> str:
    template = format_plan_template(deterministic)
    if deterministic.status is not PlanStatus.OK or not deterministic.steps:
        return template
    raw = str(model_text or "").strip()
    if not raw or _AUTH_LEAK.search(raw) or _ACTION_UNSAFE.search(raw):
        return template
    cleaned = re.sub(
        r"(?i)</?(decision_context|safe_context|situation|conversation_context)\b[^>]*>",
        "",
        raw,
    ).strip()
    if len(cleaned) < 20 or not re.search(r"(?im)^plan:\s*\S", cleaned):
        return template
    if _norm_line(_extract_plan_title(cleaned)) != _norm_line(deterministic.title):
        return template
    conf = _extract_confidence_line(cleaned)
    auth_conf = _CONF_LABEL.get(deterministic.confidence, "Low")
    if not conf or conf.strip().lower() != auth_conf.lower():
        return template
    model_steps = _parse_steps(cleaned)
    if not _steps_match(model_steps, deterministic.steps):
        return template
    numbered = re.findall(r"(?m)^\s*\d+[.)]\s+", cleaned)
    if len(numbered) != len(deterministic.steps):
        return template
    if len(re.findall(r"(?im)^steps:\s*$", cleaned)) > 1:
        return template
    for banned in ("facts", "constraints", "preferences", "recommendation"):
        if re.search(rf"(?im)^[ \t]*{banned}\s*:", cleaned):
            return template
    auth_ass = tuple(deterministic.assumptions or ())
    model_ass = _bullets_section(cleaned, "assumptions")
    if auth_ass:
        if model_ass is None or not _assumption_sets_match(model_ass, auth_ass):
            return template
    elif model_ass and len(model_ass) > 0:
        return template
    auth_blk = tuple(deterministic.blockers or ())
    model_blk = _bullets_section(cleaned, "watch-outs")
    if auth_blk:
        if model_blk is None or not _assumption_sets_match(model_blk, auth_blk):
            return template
    elif model_blk and len(model_blk) > 0:
        return template
    return cleaned[:2048]


def maybe_polish_with_ollama(
    result: PlanResult,
    *,
    provider: Any = None,
) -> Tuple[str, int]:
    template = format_plan_template(result)
    if result.status is not PlanStatus.OK or not result.steps:
        return template, 0
    if result.confidence in (PlanConfidence.HIGH, PlanConfidence.MEDIUM):
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

        decision = authorize_llm_provider(provider, capability="v8_plan")
        if (not decision.is_allow) or decision.cost_class != CostClass.LOCAL_FREE:
            return template, 0
        prompt = (
            "Rephrase the following plan more naturally. "
            "Do not change the plan title, steps, order, confidence, watch-outs, or assumptions. "
            "Keep the same section headers.\n\n"
            f"{template}"
        )
        try:
            out = provider.generate(
                prompt,
                system_prompt=(
                    "You format informational plans only. Never invent executable commands. "
                    "Never claim you performed computer actions. No tools."
                ),
                tools=None,
                temperature=0.2,
                timeout=50,
                num_predict=220,
                capability="v8_plan",
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
