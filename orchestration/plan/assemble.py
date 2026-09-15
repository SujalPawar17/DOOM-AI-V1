"""V8.23 assemble PlanInput. Read-only. Never authorizes."""

from __future__ import annotations

import re
from typing import Any, List, Tuple

from orchestration.conversation.context import sanitize_context_text
from orchestration.conversation.resolve import extract_options
from orchestration.plan.types import (
    MAX_CONSTRAINT_CHARS,
    MAX_CONSTRAINTS,
    MAX_FACT_CHARS,
    MAX_FACTS,
    MAX_GOAL_SUMMARY_CHARS,
    MAX_PREFERENCE_CHARS,
    MAX_PREFERENCES,
    MAX_QUESTION_CHARS,
    MAX_SEED_STEP_CHARS,
    MAX_SEED_STEPS,
    PlanInput,
    PlanSourceFlags,
)


def extract_recommendation_from_text(assistant_text: str) -> str:
    m = re.search(r"(?im)^recommendation:\s*(.+)$", str(assistant_text or ""))
    if not m:
        return ""
    return sanitize_context_text(m.group(1), limit=MAX_GOAL_SUMMARY_CHARS)


def extract_goal_summary(question: str, anchor_assistant_text: str = "") -> str:
    q = " ".join(str(question or "").strip().split())
    rec = extract_recommendation_from_text(anchor_assistant_text)
    if rec:
        return rec[:MAX_GOAL_SUMMARY_CHARS]
    for pat in (
        r"(?i)\bnext steps for\s+(.+?)(?:[?.!]|$)",
        r"(?i)\bplan (?:how )?to\s+(.+?)(?:[?.!]|$)",
        r"(?i)\bplan for\s+(.+?)(?:[?.!]|$)",
        r"(?i)\bmake (?:me )?a (?:plan|checklist) for\s+(.+?)(?:[?.!]|$)",
        r"(?i)\bimproving\s+(.+?)(?:[?.!]|$)",
        r"(?i)\bhow (?:should|do) i (?:proceed|start|approach)(?: with)?\s+(.+?)(?:[?.!]|$)",
    ):
        m = re.search(pat, q)
        if m:
            g = sanitize_context_text(m.group(1), limit=MAX_GOAL_SUMMARY_CHARS)
            if g and len(g) >= 8:
                return g
    if len(q) >= 20:
        g = sanitize_context_text(
            re.sub(
                r"(?i)^.*?\b(next steps|plan|break down|proceed)\b[:\s,]*",
                "",
                q,
            ),
            limit=MAX_GOAL_SUMMARY_CHARS,
        )
        if g and len(g) >= 12:
            return g
    return ""


def _bound_tuple(items: List[str], *, max_items: int, max_chars: int) -> Tuple[str, ...]:
    out: List[str] = []
    for raw in items:
        text = sanitize_context_text(raw, limit=max_chars)
        if not text:
            continue
        out.append(text[:max_chars])
        if len(out) >= max_items:
            break
    return tuple(out)


def _system_facts_constraints(query: str) -> Tuple[List[str], List[str], dict, bool]:
    facts: List[str] = []
    constraints: List[str] = []
    factors: dict = {}
    used = False
    try:
        from orchestration.situation.assemble import derive_factors
        from orchestration.system.observe import collect_system_observation

        obs = collect_system_observation(cpu_interval=0.05)
        derived = derive_factors(obs)
        factors = {
            "CPU_LOAD": derived.cpu_load,
            "RAM_PRESSURE": derived.ram_pressure,
            "DISK_PRESSURE": derived.disk_pressure,
            "OLLAMA": derived.ollama,
            "SYSTEM_HEALTH": derived.system_health,
        }
        used = True
        if derived.ram_pressure in ("ELEVATED", "HIGH"):
            facts.append(f"Current RAM pressure is {derived.ram_pressure.lower()}.")
            constraints.append("Free memory before starting heavy work.")
        if derived.cpu_load in ("ELEVATED", "HIGH"):
            facts.append(f"Current CPU load is {derived.cpu_load.lower()}.")
            constraints.append("Avoid stacking heavy tasks while CPU load is high.")
        if derived.system_health not in (None, "", "OK", "NORMAL"):
            facts.append(f"Overall system health is {str(derived.system_health).lower()}.")
        if derived.ollama == "UNAVAILABLE":
            facts.append("Local Ollama appears unavailable.")
    except Exception:
        return [], [], {}, False
    ql = query.lower()
    if re.search(r"\b(memory|ram|free memory|heavy ai)\b", ql):
        constraints.append("Account for memory before heavy AI work.")
    return facts, constraints, factors, used


def _memory_preferences(owner_id: str, query: str) -> Tuple[List[str], bool]:
    prefs: List[str] = []
    if not owner_id:
        return prefs, False
    try:
        from orchestration.conversation.personal_memory import (
            list_personal_memories,
            search_personal_memories,
        )

        hits = search_personal_memories(owner_id, query, limit=MAX_PREFERENCES)
        if not hits:
            hits = list_personal_memories(owner_id, limit=MAX_PREFERENCES)
        for hit in hits:
            text = sanitize_context_text(
                getattr(hit, "content", "") or "", limit=MAX_PREFERENCE_CHARS
            )
            if text:
                prefs.append(text[:MAX_PREFERENCE_CHARS])
            if len(prefs) >= MAX_PREFERENCES:
                break
        return prefs, bool(prefs)
    except Exception:
        return [], False


def _seed_steps_from_text(question: str, anchor: str) -> Tuple[str, ...]:
    found = extract_options(question)
    if len(found) < 1 and anchor:
        found = extract_options(anchor)
    if not found:
        return ()
    return _bound_tuple(list(found), max_items=MAX_SEED_STEPS, max_chars=MAX_SEED_STEP_CHARS)


def assemble_plan_input(
    plan: Any,
    question: str,
    *,
    anchor_assistant_text: str = "",
    anchor_user_text: str = "",
) -> PlanInput:
    q = sanitize_context_text(question, limit=MAX_QUESTION_CHARS) or str(question or "")[:MAX_QUESTION_CHARS]
    owner = str(getattr(plan, "owner_id", "") or "")[:64]
    goal = extract_goal_summary(q, anchor_assistant_text)
    if not goal and anchor_user_text:
        goal = sanitize_context_text(anchor_user_text, limit=MAX_GOAL_SUMMARY_CHARS)
    facts_l, constraints_l, factors, used_sys = _system_facts_constraints(q)
    prefs_l, used_mem = _memory_preferences(owner, q)
    seeds = _seed_steps_from_text(q, anchor_assistant_text)
    from_conv = bool(anchor_assistant_text or anchor_user_text)
    decision_seed = bool(extract_recommendation_from_text(anchor_assistant_text))
    assumptions: List[str] = []
    if not used_sys:
        assumptions.append("Current system measurements were unavailable.")
    factor_pairs = tuple(
        (str(k)[:32], str(v)[:32]) for k, v in list(factors.items())[:8]
    )
    used_sit = bool(factors) and (
        factors.get("RAM_PRESSURE") not in (None, "NORMAL")
        or factors.get("CPU_LOAD") not in (None, "NORMAL")
    )
    return PlanInput(
        question=q[:MAX_QUESTION_CHARS],
        goal_summary=(goal or "")[:MAX_GOAL_SUMMARY_CHARS],
        facts=_bound_tuple(facts_l, max_items=MAX_FACTS, max_chars=MAX_FACT_CHARS),
        constraints=_bound_tuple(
            constraints_l, max_items=MAX_CONSTRAINTS, max_chars=MAX_CONSTRAINT_CHARS
        ),
        preferences=_bound_tuple(
            prefs_l, max_items=MAX_PREFERENCES, max_chars=MAX_PREFERENCE_CHARS
        ),
        seed_steps=seeds,
        situation_factors=factor_pairs,
        source_flags=PlanSourceFlags(
            conversation=from_conv,
            memory=used_mem,
            system=used_sys,
            situation=used_sit,
            decision_seed=decision_seed,
        ),
    )
