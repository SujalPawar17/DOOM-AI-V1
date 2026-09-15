"""V8.22 deterministic decision scoring. No LLM. No embeddings."""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from orchestration.decision.types import (
    MAX_ASSUMPTION_CHARS,
    MAX_ASSUMPTIONS,
    MAX_REASON_CHARS,
    MAX_REASONS,
    MAX_TRADEOFF_CHARS,
    MAX_TRADEOFFS,
    DecisionConfidence,
    DecisionInput,
    DecisionResult,
    DecisionStatus,
)

CLARIFY_OPTIONS = (
    "Which options should I compare? Please name at least two alternatives."
)
CLARIFY_AMBIGUOUS = (
    "That choice is ambiguous. Please name the options more clearly."
)
CLARIFY_CONFLICT = (
    "I have conflicting information for that choice. "
    "Which constraint should take priority?"
)

# Score margin thresholds (integer points).
MARGIN_HIGH = 4
MARGIN_MEDIUM = 2


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _option_tokens(option: str) -> List[str]:
    return [t for t in _norm(option).replace("/", " ").split() if len(t) >= 2]


def _heavy_model(option: str) -> bool:
    o = _norm(option)
    return any(
        k in o
        for k in ("llama3", "llama 3", "70b", "large model", "bigger model")
    )


def _light_model(option: str) -> bool:
    o = _norm(option)
    return any(
        k in o
        for k in (
            "smaller",
            "small model",
            "tiny",
            "light",
            "faster-whisper",
            "mini",
        )
    )


def score_decision(inp: DecisionInput) -> DecisionResult:
    """Rank options deterministically. Never authorizes actions."""
    options = tuple(o for o in inp.options if o)
    if len(options) < 2:
        return DecisionResult(
            status=DecisionStatus.CLARIFY,
            clarification=CLARIFY_OPTIONS,
            confidence=DecisionConfidence.LOW,
            options_considered=options,
            assumptions=inp.assumptions_seed[:MAX_ASSUMPTIONS],
        )

    scores: Dict[str, int] = {o: 0 for o in options}
    reasons_by: Dict[str, List[str]] = {o: [] for o in options}
    global_assumptions: List[str] = list(inp.assumptions_seed)
    conflicts = 0

    ram = str((inp.situation_factors or {}).get("RAM_PRESSURE") or "NORMAL")
    cpu = str((inp.situation_factors or {}).get("CPU_LOAD") or "NORMAL")
    prefs_blob = " ".join(_norm(p) for p in inp.preferences)
    constraints_blob = " ".join(_norm(c) for c in inp.constraints)
    facts_blob = " ".join(_norm(f) for f in inp.facts)
    q = _norm(inp.question)

    pressure = ram in ("ELEVATED", "HIGH") or cpu in ("ELEVATED", "HIGH")
    cpu_only = "cpu-only" in facts_blob or "cpu only" in facts_blob or "no gpu" in facts_blob

    # Explicit negative constraints from user text ("not C++", "avoid Rust").
    negatives = _explicit_negatives(q)

    for opt in options:
        toks = _option_tokens(opt)
        onorm = _norm(opt)
        # Preference match (never overrides hard constraints below).
        if prefs_blob and any(t in prefs_blob for t in toks if len(t) >= 3):
            scores[opt] += 3
            reasons_by[opt].append("Matches your stated preference.")
        # Explicit positive constraint / question favor ("must use Python").
        if any(t in constraints_blob for t in toks if len(t) >= 3):
            scores[opt] += 2
            reasons_by[opt].append("Aligns with an explicit constraint.")
        if _explicit_favor(q, onorm, toks):
            scores[opt] += 3
            reasons_by[opt].append("Matches an explicit choice constraint in your question.")
        if any(n in onorm or n in toks for n in negatives):
            scores[opt] -= 5
            reasons_by[opt].append("Conflicts with an explicit negative constraint.")
        # Soft project cue: this repository / product is Python-oriented.
        if "doom" in q and "python" in toks:
            scores[opt] += 2
            reasons_by[opt].append(
                "DOOM's current codebase orientation favors Python."
            )
        # System pressure vs heavy models (hard-ish constraint).
        if pressure or cpu_only:
            if _heavy_model(opt):
                scores[opt] -= 4
                reasons_by[opt].append(
                    "Current resource pressure makes a heavier local model less suitable."
                )
            if _light_model(opt) or (
                not _heavy_model(opt)
                and any(k in onorm for k in ("small", "tiny", "light", "faster"))
            ):
                scores[opt] += 4
                reasons_by[opt].append(
                    "A lighter option better fits current resource constraints."
                )
        # Lexical "faster" / "latency" in question favors lighter.
        if re_search_latency(q) and (_light_model(opt) or "small" in onorm):
            scores[opt] += 2
            reasons_by[opt].append("You indicated latency / speed matters.")

    # Preference vs hard constraint conflict detection.
    for opt in options:
        if prefs_blob and any(t in prefs_blob for t in _option_tokens(opt) if len(t) >= 3):
            if (pressure or cpu_only) and _heavy_model(opt):
                conflicts += 1
                global_assumptions.append(
                    "Stated preference favors a heavier option that conflicts with resource pressure."
                )

    ranked = sorted(options, key=lambda o: (-scores[o], o.lower()))
    best = ranked[0]
    second = ranked[1]
    margin = scores[best] - scores[second]

    tradeoffs: List[str] = []
    for opt in ranked[:3]:
        if opt == best:
            continue
        tradeoffs.append(f"{opt}: alternative with different trade-offs.")
    if _heavy_model(best) and any(_light_model(o) or "small" in _norm(o) for o in options):
        tradeoffs.append("Heavier models may give stronger answers but use more resources.")
    if any(_light_model(o) or "small" in _norm(o) for o in options) and _heavy_model(
        next((o for o in options if o != best), "")
    ):
        tradeoffs.append("Lighter models are faster under pressure but may be less capable.")

    # Near tie / conflict → clarify or low.
    if conflicts and margin < MARGIN_HIGH:
        return DecisionResult(
            status=DecisionStatus.CLARIFY,
            clarification=CLARIFY_CONFLICT,
            confidence=DecisionConfidence.LOW,
            options_considered=options,
            assumptions=tuple(global_assumptions[:MAX_ASSUMPTIONS]),
        )

    if margin < MARGIN_MEDIUM:
        # Near tie: do not force a winner.
        return DecisionResult(
            status=DecisionStatus.LOW_CONFIDENCE,
            recommendation="",
            reasons=("The available options are too evenly matched with current facts.",),
            tradeoffs=tuple(t[:MAX_TRADEOFF_CHARS] for t in tradeoffs[:MAX_TRADEOFFS]),
            confidence=DecisionConfidence.LOW,
            assumptions=tuple(
                (global_assumptions + ["More constraints would improve confidence."])[
                    :MAX_ASSUMPTIONS
                ]
            ),
            clarification=CLARIFY_AMBIGUOUS,
            options_considered=options,
        )

    reasons = list(reasons_by[best][:MAX_REASONS])
    if not reasons:
        reasons.append("Best alignment with the stated options and available facts.")
    if inp.facts:
        # Attach one supporting fact without inventing.
        reasons.append(inp.facts[0][:MAX_REASON_CHARS])

    if margin >= MARGIN_HIGH and (prefs_blob or pressure or cpu_only or inp.constraints):
        conf = DecisionConfidence.HIGH
    elif margin >= MARGIN_MEDIUM:
        conf = DecisionConfidence.MEDIUM
    else:
        conf = DecisionConfidence.LOW

    return DecisionResult(
        status=DecisionStatus.OK,
        recommendation=best[:120],
        reasons=tuple(r[:MAX_REASON_CHARS] for r in reasons[:MAX_REASONS]),
        tradeoffs=tuple(t[:MAX_TRADEOFF_CHARS] for t in tradeoffs[:MAX_TRADEOFFS]),
        confidence=conf,
        assumptions=tuple(
            a[:MAX_ASSUMPTION_CHARS] for a in global_assumptions[:MAX_ASSUMPTIONS]
        ),
        options_considered=options,
    )


def re_search_latency(q: str) -> bool:
    return any(k in q for k in ("latency", "timeout", "slow", "faster", "speed"))


def _explicit_favor(q: str, onorm: str, toks: List[str]) -> bool:
    for t in toks:
        if len(t) < 3:
            continue
        if any(
            p in q
            for p in (
                f"must use {t}",
                f"must choose {t}",
                f"prefer {t}",
                f"preferred {t}",
                f"need {t}",
                f"requires {t}",
            )
        ):
            return True
    return False


def _explicit_negatives(q: str) -> List[str]:
    out: List[str] = []
    for m in re.finditer(r"\b(?:not|avoid|no)\s+([a-z0-9+#.]{2,20})\b", q):
        out.append(m.group(1).lower())
    return out
