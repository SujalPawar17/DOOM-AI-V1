"""V8.23/V8.24 plan formatting. Template-first; optional single Ollama polish."""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Tuple

from orchestration.plan.analysis import PlanAnalysis
from orchestration.plan.continuity.engine import continuity_blockers
from orchestration.plan.continuity.types import PlanContinuityState
from orchestration.plan.modes import PlanMode
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

CLARIFY_DEFAULT = (
    "To build a useful plan, I need to know what you want to accomplish. "
    "Please describe the goal in a sentence or two."
)


def format_plan_template(result: PlanResult) -> str:
    """V8.23-compatible template (no analysis sections)."""
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


def format_plan_with_analysis(
    result: PlanResult,
    analysis: Optional[PlanAnalysis] = None,
    *,
    continuity_state: Optional[PlanContinuityState] = None,
) -> str:
    if result.status is PlanStatus.CLARIFY or (
        result.status is PlanStatus.LOW_CONFIDENCE and result.clarification and not result.steps
    ):
        return (result.clarification or CLARIFY_DEFAULT).strip()[:2048]
    if not result.steps:
        if result.clarification:
            return result.clarification.strip()[:2048]
        return CLARIFY_DEFAULT

    mode = analysis.mode if analysis else PlanMode.CREATE

    if mode is PlanMode.NEXT and analysis and analysis.next_step_index:
        lines = [
            f"Next step: {analysis.next_step_index}. {analysis.next_step_title}",
        ]
        if analysis.next_rationale:
            lines.append(analysis.next_rationale)
        if analysis.dependencies:
            lines.append("")
            lines.append("Dependencies:")
            for d in analysis.dependencies[:3]:
                lines.append(
                    f"- {d.to_index} depends on {d.from_index} — {d.reason}"
                )
        lines.append("")
        lines.append(f"Confidence: {_CONF_LABEL.get(result.confidence, 'Low')}")
        return "\n".join(lines).strip()[:2048]

    if mode is PlanMode.VALIDATE and analysis:
        lines = ["Plan validation"]
        if analysis.issues:
            lines.append("")
            lines.append("Issues:")
            for iss in analysis.issues:
                lines.append(f"- {iss.message}")
        blockers = list(result.blockers or ())
        if continuity_state is not None:
            for b in continuity_blockers(continuity_state):
                if b not in blockers:
                    blockers.append(b)
        lines.append("")
        lines.append("Blockers:")
        if blockers:
            for b in blockers:
                lines.append(f"- {b}")
        else:
            lines.append("- None")
        lines.append("")
        lines.append(f"Confidence: {_CONF_LABEL.get(result.confidence, 'Low')}")
        return "\n".join(lines).strip()[:2048]

    if mode is PlanMode.DEPEND and analysis:
        lines = ["Dependencies:"]
        if analysis.dependencies:
            for d in analysis.dependencies:
                lines.append(
                    f"- {d.to_index} depends on {d.from_index} — {d.reason}"
                )
        else:
            lines.append("- None detected")
        lines.append("")
        lines.append(f"Confidence: {_CONF_LABEL.get(result.confidence, 'Low')}")
        return "\n".join(lines).strip()[:2048]

    # CREATE / REFINE full output
    lines = [f"Plan: {result.title}"]
    lines.append("")
    lines.append("Steps:")
    for s in result.steps:
        lines.append(f"{s.index}. {s.title}")
        if s.detail:
            lines.append(f"   {s.detail}")
    if analysis and analysis.priority_order:
        lines.append("")
        lines.append("Priority:")
        lines.append(" → ".join(str(i) for i in analysis.priority_order))
    if analysis and analysis.dependencies:
        lines.append("")
        lines.append("Dependencies:")
        for d in analysis.dependencies:
            lines.append(
                f"- {d.to_index} depends on {d.from_index} — {d.reason}"
            )
    blockers = result.blockers or ()
    lines.append("")
    lines.append("Blockers:")
    if blockers:
        for b in blockers:
            lines.append(f"- {b}")
    else:
        lines.append("- None")
    if analysis and analysis.next_step_index:
        lines.append("")
        lines.append(
            f"Next step: {analysis.next_step_index}. {analysis.next_step_title}"
            + (f" — {analysis.next_rationale}" if analysis.next_rationale else "")
        )
    if result.reasons:
        lines.append("")
        lines.append("Why:")
        for r in result.reasons:
            lines.append(f"- {r}")
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


def _extract_plan_title(text: str) -> str:
    m = re.search(r"(?im)^plan:\s*(.+)$", str(text or ""))
    return m.group(1).strip()[:120] if m else ""


def _extract_confidence_line(text: str) -> str:
    m = re.search(r"(?im)^confidence:\s*(.+)$", str(text or ""))
    return m.group(1).strip() if m else ""


def _extract_priority_line(text: str) -> str:
    m = re.search(r"(?ims)^priority:\s*\n?\s*([0-9\s→\->,]+)", str(text or ""))
    if not m:
        return ""
    return re.sub(r"[^\d]+", " ", m.group(1)).strip()


def _extract_next_step_line(text: str) -> str:
    m = re.search(r"(?im)^next step:\s*(.+)$", str(text or ""))
    return m.group(1).strip() if m else ""


def _parse_steps(text: str) -> List[Tuple[str, str]]:
    m = re.search(
        r"(?ims)^steps:\s*\n(.*?)(?=^[ \t]*(?:why|watch|priority|dependencies|"
        r"blockers|next step|confidence|assumptions)\s*:|\Z)",
        text,
    )
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
        rf"(?ims)^[ \t]*{re.escape(header)}\s*:?\s*\n(.*?)(?=^[ \t]*(?:plan|steps|why|watch|"
        rf"priority|dependencies|blockers|next step|confidence|assumptions)\s*:|\Z)"
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


def _priority_match(text: str, analysis: PlanAnalysis) -> bool:
    if not analysis.priority_order:
        return True
    got = _extract_priority_line(text)
    if not got:
        return False
    nums = [int(x) for x in got.split() if x.isdigit()]
    return tuple(nums) == tuple(analysis.priority_order)


def _deps_match(text: str, analysis: PlanAnalysis) -> bool:
    auth = analysis.dependencies or ()
    if not auth:
        # Model must not invent a Dependencies section with edges
        sec = _bullets_section(text, "dependencies")
        if sec is None:
            return True
        meaningful = [s for s in sec if not re.search(r"(?i)^none", s)]
        return len(meaningful) == 0
    model_lines = _bullets_section(text, "dependencies")
    if model_lines is None:
        return False
    auth_set = {(d.from_index, d.to_index) for d in auth}
    model_set = set()
    for line in model_lines:
        m = re.search(r"(\d+)\s+depends on\s+(\d+)", line, re.I)
        if m:
            model_set.add((int(m.group(2)), int(m.group(1))))  # from, to
    return model_set == auth_set


def _next_match(text: str, analysis: PlanAnalysis) -> bool:
    if not analysis.next_step_index:
        return True
    line = _extract_next_step_line(text)
    if not line:
        return False
    m = re.match(r"(\d+)\.\s*(.+?)(?:\s*—|\s+-|\Z)", line)
    if not m:
        return False
    if int(m.group(1)) != analysis.next_step_index:
        return False
    title = m.group(2).strip()
    # Title may include rationale after em-dash already stripped
    return _norm_line(title).startswith(_norm_line(analysis.next_step_title)[:40]) or _norm_line(
        analysis.next_step_title
    ) in _norm_line(title)


def pin_ollama_formatting(
    deterministic: PlanResult,
    model_text: str,
    analysis: Optional[PlanAnalysis] = None,
) -> str:
    template = (
        format_plan_with_analysis(deterministic, analysis)
        if analysis is not None
        else format_plan_template(deterministic)
    )
    if deterministic.status is not PlanStatus.OK or not deterministic.steps:
        return template
    # Mode-specific templates without Plan: header
    if analysis and analysis.mode in (PlanMode.NEXT, PlanMode.VALIDATE, PlanMode.DEPEND):
        raw = str(model_text or "").strip()
        if not raw or _AUTH_LEAK.search(raw) or _ACTION_UNSAFE.search(raw):
            return template
        if analysis.mode is PlanMode.NEXT and not _next_match(raw, analysis):
            return template
        if analysis.mode is PlanMode.DEPEND and not _deps_match(raw, analysis):
            return template
        conf = _extract_confidence_line(raw)
        auth_conf = _CONF_LABEL.get(deterministic.confidence, "Low")
        if conf and conf.strip().lower() != auth_conf.lower():
            return template
        return raw[:2048]

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
    # Blockers section (V8.24) or Watch-outs (V8.23)
    auth_blk = tuple(deterministic.blockers or ())
    model_blk = _bullets_section(cleaned, "blockers")
    if model_blk is None:
        model_blk = _bullets_section(cleaned, "watch-outs")
    if auth_blk:
        # Allow "- None" absence vs list — require same normalized set excluding None
        auth_set = {_norm_line(b) for b in auth_blk if _norm_line(b)}
        if model_blk is None:
            return template
        model_set = {_norm_line(b) for b in model_blk if _norm_line(b) and _norm_line(b) != "none"}
        if model_set != auth_set:
            return template
    if analysis is not None:
        if not _priority_match(cleaned, analysis):
            return template
        if not _deps_match(cleaned, analysis):
            return template
        if not _next_match(cleaned, analysis):
            return template
    return cleaned[:2048]


def maybe_polish_with_ollama(
    result: PlanResult,
    *,
    analysis: Optional[PlanAnalysis] = None,
    provider: Any = None,
    continuity_state: Optional[PlanContinuityState] = None,
) -> Tuple[str, int]:
    template = (
        format_plan_with_analysis(
            result, analysis, continuity_state=continuity_state
        )
        if analysis is not None
        else format_plan_template(result)
    )
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
            "Do not change the plan title, steps, order, confidence, blockers, "
            "priority, dependencies, next step, or assumptions. "
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
                num_predict=280,
                capability="v8_plan",
            )
        except (ProviderTimeoutError, ProviderUnavailableError, CostGuardBlockedError):
            return template, 1
        except Exception:
            return template, 1
        if not isinstance(out, LLMResponse):
            return template, 1
        return pin_ollama_formatting(result, str(out.text or ""), analysis=analysis), 1
    except Exception:
        return template, 0
