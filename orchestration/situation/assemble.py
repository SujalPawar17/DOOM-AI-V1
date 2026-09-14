"""V8.20 Situation Model assembly. Read-only. Never authorizes or executes."""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from orchestration.conversation.context import (
    MAX_CONV_MSG_CHARS,
    MAX_CONV_TOTAL_CHARS,
    MAX_CONV_TURNS,
    MAX_ITEM_CHARS,
    MAX_MEMORY_ITEMS,
    MAX_MEMORY_SECTION_CHARS,
    MAX_SYSTEM_SECTION_CHARS,
    MAX_TOTAL_CONTEXT_CHARS,
    exclude_sensitive_text,
    get_conversation_turns,
    sanitize_context_text,
)
from orchestration.goal.plan_types import GoalPlan
from orchestration.situation.model import DerivedFactors, SituationModel
from orchestration.situation.relevance import (
    situation_relevant,
    situation_wants_conversation,
    situation_wants_memory,
    situation_wants_system,
)

# V8.20 injected block bound (matches V8.19 total context budget).
MAX_SITUATION_CHARS = MAX_TOTAL_CONTEXT_CHARS  # 3000


def _cpu_load(cpu_percent: float) -> str:
    if cpu_percent >= 85.0:
        return "HIGH"
    if cpu_percent >= 60.0:
        return "ELEVATED"
    return "NORMAL"


def _ram_pressure(memory_percent: float) -> str:
    # Align with V8.18: >=85 under pressure, >=95 degraded → HIGH.
    if memory_percent >= 95.0:
        return "HIGH"
    if memory_percent >= 85.0:
        return "ELEVATED"
    if memory_percent >= 70.0:
        return "ELEVATED"
    return "NORMAL"


def _disk_pressure(disks: Tuple[Any, ...]) -> str:
    if not disks:
        return "NORMAL"
    worst = 0.0
    for d in disks:
        try:
            worst = max(worst, float(getattr(d, "percent_used", 0.0) or 0.0))
            if float(getattr(d, "free_gb", 99.0) or 99.0) < 1.0:
                return "HIGH"
        except Exception:
            continue
    if worst >= 95.0:
        return "HIGH"
    if worst >= 85.0:
        return "ELEVATED"
    return "NORMAL"


def derive_factors(obs: Any) -> DerivedFactors:
    if obs is None:
        return DerivedFactors()
    try:
        cpu = float(getattr(obs, "cpu_percent", 0.0) or 0.0)
        mem = float(getattr(obs, "memory_percent", 0.0) or 0.0)
        disks = tuple(getattr(obs, "disks", ()) or ())
        ollama_raw = str(getattr(obs, "ollama_status", "unavailable") or "unavailable")
        health = str(getattr(obs, "health", "HEALTHY") or "HEALTHY")
        return DerivedFactors(
            cpu_load=_cpu_load(cpu),
            ram_pressure=_ram_pressure(mem),
            disk_pressure=_disk_pressure(disks),
            ollama="AVAILABLE" if ollama_raw == "running" else "UNAVAILABLE",
            system_health=health if health in (
                "HEALTHY", "UNDER_MEMORY_PRESSURE", "DEGRADED"
            ) else "HEALTHY",
        )
    except Exception:
        return DerivedFactors()


def _memory_items(owner_id: str, query: str) -> Tuple[str, ...]:
    if not situation_wants_memory(query):
        return ()
    try:
        from orchestration.conversation.personal_memory import (
            list_personal_memories,
            search_personal_memories,
        )
        hits = search_personal_memories(owner_id, query, limit=MAX_MEMORY_ITEMS)
        if not hits:
            hits = list_personal_memories(owner_id, limit=MAX_MEMORY_ITEMS)
    except Exception:
        return ()
    out: List[str] = []
    total = 0
    for hit in hits:
        text = sanitize_context_text(getattr(hit, "content", "") or "", limit=MAX_ITEM_CHARS)
        if not text:
            continue
        if total + len(text) > MAX_MEMORY_SECTION_CHARS:
            break
        out.append(text)
        total += len(text)
        if len(out) >= MAX_MEMORY_ITEMS:
            break
    return tuple(out)


def _system_text(query: str) -> Tuple[str, Any]:
    if not situation_wants_system(query):
        return "", None
    try:
        from orchestration.system.observe import collect_system_observation
        obs = collect_system_observation(cpu_interval=0.05)
    except Exception:
        return "", None
    disks = []
    for d in list(getattr(obs, "disks", ()) or ())[:3]:
        disks.append(
            f"{d.label} {d.free_gb:.0f}/{d.total_gb:.0f} GB free "
            f"({d.percent_used:.0f}% used)"
        )
    lines = [
        f"CPU: {obs.cpu_percent:.0f}%",
        (
            f"RAM: {obs.memory_used_gb:.1f} GB / {obs.memory_total_gb:.1f} GB "
            f"({obs.memory_percent:.0f}%)"
        ),
        "Disks: " + (", ".join(disks) if disks else "unavailable"),
        f"Ollama: {obs.ollama_status}",
        f"Dashboard: {obs.doom_dashboard_status}",
        f"Health: {obs.health}",
    ]
    body = "\n".join(lines)
    if exclude_sensitive_text(body):
        return "", None
    return body[:MAX_SYSTEM_SECTION_CHARS], obs


def _conversation_items(owner_id: str, session_id: str, query: str) -> Tuple[str, ...]:
    turns = get_conversation_turns(owner_id, session_id)
    if not situation_wants_conversation(query, has_turns=bool(turns)):
        return ()
    out: List[str] = []
    total = 0
    # Cap to MAX_CONV_TURNS pairs ≈ 2*turns messages already in store.
    for row in turns[-(MAX_CONV_TURNS * 2):]:
        role = "User" if row.get("role") == "user" else "DOOM"
        text = sanitize_context_text(row.get("text") or "", limit=MAX_CONV_MSG_CHARS)
        if not text:
            continue
        line = f"{role}: {text}"
        if total + len(line) > MAX_CONV_TOTAL_CHARS:
            break
        out.append(line)
        total += len(line)
    return tuple(out)


def _doom_state(obs: Any) -> str:
    """Bounded runtime state from observation only (no IDs/secrets)."""
    if obs is None:
        return ""
    try:
        parts = [
            f"Ollama service: {getattr(obs, 'ollama_status', 'unavailable')}",
            f"Dashboard service: {getattr(obs, 'doom_dashboard_status', 'unavailable')}",
            f"OS: {getattr(obs, 'os_name', 'unknown')}",
        ]
        body = "; ".join(parts)
        if exclude_sensitive_text(body):
            return ""
        return body[:240]
    except Exception:
        return ""


def build_situation_model(plan: Any, query: str) -> Optional[SituationModel]:
    """Construct a bounded SituationModel or None when not relevant / unavailable."""
    if type(plan) is not GoalPlan:
        return None
    q = str(query or "")[:2048]
    if not situation_relevant(q):
        return None

    owner = str(plan.owner_id or "")[:64]
    session = str(plan.session_id or "")[:64]
    req = sanitize_context_text(q, limit=500) or q[:500]

    memory = _memory_items(owner, q)
    system_text, obs = _system_text(q)
    conversation = _conversation_items(owner, session, q)
    factors = derive_factors(obs) if obs is not None else DerivedFactors()
    doom = _doom_state(obs)

    # If every optional source failed and no factors from observation, still allow
    # a minimal model with the user request when situationally relevant — but prefer
    # falling back when there is nothing useful beyond the request itself.
    if not memory and not system_text and not conversation and obs is None:
        return None

    return SituationModel(
        user_request=req,
        relevant_conversation=conversation,
        relevant_memory=memory,
        system_observation=system_text,
        doom_state=doom,
        derived_factors=factors,
        includes_memory=bool(memory),
        includes_system=bool(system_text),
        includes_conversation=bool(conversation),
    )


def assemble_situation_block(plan: Any, query: str) -> Tuple[str, str]:
    """Return (situation_block, status). Never authorizes."""
    try:
        model = build_situation_model(plan, query)
    except Exception:
        return "", "SITUATION_UNAVAILABLE"
    if model is None:
        return "", "SITUATION_SKIPPED"
    try:
        from orchestration.situation.format import format_situation_block
        block = format_situation_block(model)
    except Exception:
        return "", "SITUATION_UNAVAILABLE"
    if not block:
        return "", "SITUATION_EMPTY"
    if len(block) > MAX_SITUATION_CHARS + 400:
        # Hard fail-closed trim of injected body only via formatter bound.
        block = block[: MAX_SITUATION_CHARS + 400]
    return block, "SITUATION_OK"
