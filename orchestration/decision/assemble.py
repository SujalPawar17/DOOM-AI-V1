"""V8.22 assemble DecisionInput. Read-only. Never authorizes."""

from __future__ import annotations

import re
from typing import Any, List, Tuple

from orchestration.conversation.context import (
    sanitize_context_text,
)
from orchestration.conversation.resolve import extract_options
from orchestration.decision.types import (
    MAX_ASSUMPTION_CHARS,
    MAX_ASSUMPTIONS,
    MAX_CONSTRAINT_CHARS,
    MAX_CONSTRAINTS,
    MAX_FACT_CHARS,
    MAX_FACTS,
    MAX_OPTION_CHARS,
    MAX_OPTIONS,
    MAX_PREFERENCE_CHARS,
    MAX_PREFERENCES,
    MAX_QUESTION_CHARS,
    DecisionInput,
    DecisionSourceFlags,
)

_OR_SPLIT = re.compile(
    r"(?i)\s+(?:or|versus|vs\.?)\s+"
)
_BETWEEN = re.compile(
    r"(?i)\bbetween\s+(.+?)\s+and\s+(.+?)(?:[?.!]|$)"
)


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


def options_from_user_text(question: str) -> Tuple[str, ...]:
    q = str(question or "").strip()
    found = extract_options(q)
    if len(found) >= 2:
        return _bound_tuple(list(found), max_items=MAX_OPTIONS, max_chars=MAX_OPTION_CHARS)
    m = _BETWEEN.search(q)
    if m:
        a = sanitize_context_text(m.group(1), limit=MAX_OPTION_CHARS)
        b = sanitize_context_text(m.group(2), limit=MAX_OPTION_CHARS)
        if a and b and a.lower() != b.lower():
            return (a[:MAX_OPTION_CHARS], b[:MAX_OPTION_CHARS])
    # "which is better for X, A or B" / "Python or C++"
    parts = _OR_SPLIT.split(q)
    if len(parts) >= 2:
        # Take last segment before or and first after — crude but bounded.
        left = parts[-2]
        right = parts[-1]
        # Strip leading question stems from left.
        left = re.sub(
            r"(?i)^.*\b(?:better|use|choose|pick|recommend|between)\b[,:\s]*",
            "",
            left,
        ).strip(" ,?")
        # Keep short option-like tokens.
        left_tok = sanitize_context_text(left.split(",")[-1], limit=MAX_OPTION_CHARS)
        right_tok = sanitize_context_text(
            re.split(r"[?.!]", right)[0], limit=MAX_OPTION_CHARS
        )
        # Prefer last word-ish chunk if still long.
        if left_tok and len(left_tok) > 40:
            left_tok = sanitize_context_text(left_tok.split()[-1], limit=MAX_OPTION_CHARS)
        if right_tok and len(right_tok) > 40:
            right_tok = sanitize_context_text(right_tok.split()[0], limit=MAX_OPTION_CHARS)
        if (
            left_tok
            and right_tok
            and left_tok.lower() != right_tok.lower()
            and len(left_tok) >= 1
            and len(right_tok) >= 1
        ):
            return (left_tok[:MAX_OPTION_CHARS], right_tok[:MAX_OPTION_CHARS])
    return ()


def options_from_anchor_assistant(assistant_text: str) -> Tuple[str, ...]:
    found = extract_options(assistant_text)
    if len(found) < 2:
        return ()
    return _bound_tuple(list(found), max_items=MAX_OPTIONS, max_chars=MAX_OPTION_CHARS)


def _system_facts_constraints(query: str) -> Tuple[List[str], List[str], dict, bool]:
    facts: List[str] = []
    constraints: List[str] = []
    factors: dict = {}
    used = False
    try:
        from orchestration.system.observe import collect_system_observation
        from orchestration.situation.assemble import derive_factors
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
            constraints.append("Prefer lower memory pressure when choosing local models.")
        if derived.cpu_load in ("ELEVATED", "HIGH"):
            facts.append(f"Current CPU load is {derived.cpu_load.lower()}.")
            constraints.append("Prefer lower CPU load when choosing local models.")
        if derived.ollama == "UNAVAILABLE":
            facts.append("Local Ollama appears unavailable.")
            assumptions = "Local model runtime availability is uncertain."
            facts.append(assumptions)
        # CPU-only cue from architecture string when present.
        arch = str(getattr(obs, "architecture", "") or "")
        if arch:
            facts.append(f"Reported architecture: {arch[:40]}.")
    except Exception:
        return [], [], {}, False
    # Lexical hardware cues from the question itself.
    ql = query.lower()
    if re.search(r"\b\d+\s*gb\s*ram\b", ql):
        m = re.search(r"(\d+)\s*gb\s*ram", ql)
        if m:
            facts.append(f"User stated {m.group(1)}GB RAM.")
            constraints.append("Respect stated RAM capacity.")
    if re.search(r"\bcpu[- ]?only\b|\bno gpu\b", ql):
        facts.append("User indicated CPU-only / no GPU.")
        constraints.append("Prefer lighter local models on CPU-only hardware.")
    if re.search(r"\b(chrome|cursor|doom)\b", ql) and re.search(r"\brunning\b", ql):
        facts.append("User reports other apps are running concurrently.")
        constraints.append("Account for concurrent application memory use.")
    return facts, constraints, factors, used


def _memory_preferences(owner_id: str, query: str) -> Tuple[List[str], bool]:
    prefs: List[str] = []
    used = False
    if not owner_id:
        return prefs, used
    try:
        profile_texts: List[str] = []
        try:
            from orchestration.user_model.config import is_v827_user_model_enabled
            from orchestration.user_model.resolve import profile_strings_for_consumer

            if is_v827_user_model_enabled():
                profile_texts = list(
                    profile_strings_for_consumer(
                        owner_id, "DECISION", query=query, limit=MAX_PREFERENCES
                    )
                )
        except Exception:
            profile_texts = []

        mem_texts: List[str] = []
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
                mem_texts.append(text[:MAX_PREFERENCE_CHARS])

        if profile_texts:
            from orchestration.user_model.resolve import merge_profile_then_memory

            prefs = merge_profile_then_memory(
                profile_texts, mem_texts, limit=MAX_PREFERENCES
            )
            used = bool(prefs)
        else:
            prefs = mem_texts[:MAX_PREFERENCES]
            used = bool(prefs)
    except Exception:
        return [], False
    return prefs, used


def assemble_decision_input(
    plan: Any,
    question: str,
    *,
    anchor_assistant_text: str = "",
) -> DecisionInput:
    """Build a bounded DecisionInput from informational sources only."""
    q = sanitize_context_text(question, limit=MAX_QUESTION_CHARS) or str(question or "")[:MAX_QUESTION_CHARS]
    owner = str(getattr(plan, "owner_id", "") or "")[:64]

    opts = options_from_user_text(q)
    from_conv = False
    if len(opts) < 2 and anchor_assistant_text:
        opts = options_from_anchor_assistant(anchor_assistant_text)
        if len(opts) >= 2:
            from_conv = True

    facts_l, constraints_l, factors, used_sys = _system_facts_constraints(q)
    prefs_l, used_mem = _memory_preferences(owner, q)

    assumptions: List[str] = []
    if not used_sys:
        assumptions.append("Current system measurements were unavailable.")
    if not re.search(r"(?i)\bgpu\b", q) and any(
        "model" in o.lower() or "llama" in o.lower() for o in opts
    ):
        assumptions.append("GPU availability was not provided.")

    # Soft situation flag when factors present.
    used_sit = bool(factors) and (
        factors.get("RAM_PRESSURE") not in (None, "NORMAL")
        or factors.get("CPU_LOAD") not in (None, "NORMAL")
    )

    return DecisionInput(
        question=q[:MAX_QUESTION_CHARS],
        facts=_bound_tuple(facts_l, max_items=MAX_FACTS, max_chars=MAX_FACT_CHARS),
        constraints=_bound_tuple(
            constraints_l, max_items=MAX_CONSTRAINTS, max_chars=MAX_CONSTRAINT_CHARS
        ),
        options=opts[:MAX_OPTIONS],
        preferences=_bound_tuple(
            prefs_l, max_items=MAX_PREFERENCES, max_chars=MAX_PREFERENCE_CHARS
        ),
        situation_factors={str(k)[:32]: str(v)[:32] for k, v in list(factors.items())[:8]},
        assumptions_seed=_bound_tuple(
            assumptions, max_items=MAX_ASSUMPTIONS, max_chars=MAX_ASSUMPTION_CHARS
        ),
        source_flags=DecisionSourceFlags(
            conversation=from_conv,
            memory=used_mem,
            system=used_sys,
            situation=used_sit,
        ),
    )
