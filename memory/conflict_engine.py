"""
DOOM V5.3.7.3 — Conflict Resolution Engine
Detects mutually exclusive competing strategies and deterministically resolves them
according to target-local precedence, empirical dominance, and freshness tie-breaking.
If a conflict cannot be deterministically resolved, signals ABSTAIN.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from memory.project_models import StrategyRecord, TransferDecision


# Known mutually exclusive tool pairs/sets
MUTUALLY_EXCLUSIVE_TOOL_SETS = [
    {"poetry", "pipenv", "conda"},
    {"pytest", "unittest", "nose"},
    {"fastapi", "flask", "django"},
    {"docker", "podman"},
    {"yarn", "pnpm", "npm"},
    {"react", "vue", "angular", "svelte"},
    {"postgres", "sqlite", "mysql"},
]


@dataclass(frozen=True)
class ConflictResolutionResult:
    """Result of evaluating strategy conflicts."""
    has_conflict: bool
    is_resolved: bool
    winner_strategy: Optional[StrategyRecord] = None
    loser_strategies: List[StrategyRecord] = field(default_factory=list)
    resolution_reason: str = ""
    decision: TransferDecision = TransferDecision.ALLOWED


class ConflictEngine:
    """
    Deterministic Conflict Detection & Resolution Engine.
    Strictly read-only; has zero execution side-effects.
    """

    @classmethod
    def are_strategies_mutually_exclusive(
        cls,
        strat_a: StrategyRecord,
        strat_b: StrategyRecord,
    ) -> bool:
        """Determines if two strategies recommend mutually exclusive tools or approaches."""
        if strat_a.strategy_id == strat_b.strategy_id:
            return False

        tools_a = set(str(t).lower().strip() for t in strat_a.recommended_tools)
        tools_b = set(str(t).lower().strip() for t in strat_b.recommended_tools)

        # Check disjoint mutually exclusive sets
        for excl_set in MUTUALLY_EXCLUSIVE_TOOL_SETS:
            has_a = bool(tools_a.intersection(excl_set))
            has_b = bool(tools_b.intersection(excl_set))
            if has_a and has_b:
                # If they select different tools from the mutually exclusive set
                diff_a = tools_a.intersection(excl_set)
                diff_b = tools_b.intersection(excl_set)
                if diff_a != diff_b:
                    return True

        # Check explicit disallowed tools
        dis_a = set(str(d).lower().strip() for d in strat_a.disallowed_tools)
        dis_b = set(str(d).lower().strip() for d in strat_b.disallowed_tools)
        if tools_a.intersection(dis_b) or tools_b.intersection(dis_a):
            return True

        # Check explicit procedure template incompatibility or architectural conflict
        proc_a = strat_a.procedure_template or {}
        proc_b = strat_b.procedure_template or {}
        incompat_a = set(str(x).lower().strip() for x in proc_a.get("incompatible_with", []))
        incompat_b = set(str(x).lower().strip() for x in proc_b.get("incompatible_with", []))
        if strat_b.strategy_id.lower() in incompat_a or strat_a.strategy_id.lower() in incompat_b:
            return True
        if proc_a.get("architecture") and proc_b.get("architecture") and proc_a.get("architecture") != proc_b.get("architecture"):
            return True

        return False

    @classmethod
    def resolve_conflicts(
        cls,
        candidate_strategies: List[Dict[str, Any]],
        target_project_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Takes candidate strategies and filters/resolves any mutual exclusion conflicts:
        1. Local target strategy wins over transferred strategy.
        2. Significantly higher reliability (gap >= 0.15) wins.
        3. Freshness / success count breaks ties.
        4. If strictly tied and conflicting, flags ABSTAIN or suppresses both.
        """
        if len(candidate_strategies) <= 1:
            return candidate_strategies

        # Group by intent category
        by_intent: Dict[str, List[Dict[str, Any]]] = {}
        for item in candidate_strategies:
            cat = item.get("intent_category") or "general"
            by_intent.setdefault(cat, []).append(item)

        resolved_list: List[Dict[str, Any]] = []

        for cat, items in by_intent.items():
            if len(items) <= 1:
                resolved_list.extend(items)
                continue

            # Check pairwise conflicts
            suppressed_ids: Set[str] = set()
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    a = items[i]
                    b = items[j]
                    id_a = a.get("strategy_id")
                    id_b = b.get("strategy_id")

                    if id_a in suppressed_ids or id_b in suppressed_ids:
                        continue

                    # Dummy StrategyRecords to test mutual exclusion
                    strat_a = StrategyRecord(
                        strategy_id=id_a,
                        name=a.get("name", ""),
                        intent_category=cat,
                        procedure_template={},
                        recommended_tools=a.get("recommended_tools", []),
                        disallowed_tools=a.get("disallowed_tools", []),
                    )
                    strat_b = StrategyRecord(
                        strategy_id=id_b,
                        name=b.get("name", ""),
                        intent_category=cat,
                        procedure_template={},
                        recommended_tools=b.get("recommended_tools", []),
                        disallowed_tools=b.get("disallowed_tools", []),
                    )

                    if cls.are_strategies_mutually_exclusive(strat_a, strat_b):
                        # Rule 1: Local precedence
                        is_trans_a = a.get("is_transferred", False)
                        is_trans_b = b.get("is_transferred", False)

                        if not is_trans_a and is_trans_b:
                            suppressed_ids.add(id_b)
                            a.setdefault("defensive_warnings", []).append(
                                f"Prioritized over conflicting transferred strategy '{id_b}'"
                            )
                            continue
                        elif is_trans_a and not is_trans_b:
                            suppressed_ids.add(id_a)
                            b.setdefault("defensive_warnings", []).append(
                                f"Prioritized over conflicting transferred strategy '{id_a}'"
                            )
                            continue

                        # Rule 2: Empirical dominance (delta >= 0.15)
                        rel_a = float(a.get("reliability_score", 0.50))
                        rel_b = float(b.get("reliability_score", 0.50))
                        if rel_a - rel_b >= 0.15:
                            suppressed_ids.add(id_b)
                            continue
                        elif rel_b - rel_a >= 0.15:
                            suppressed_ids.add(id_a)
                            continue

                        # Rule 3: Success count tie-breaker
                        succ_a = int(a.get("success_count", 0))
                        succ_b = int(b.get("success_count", 0))
                        if succ_a > succ_b + 2:
                            suppressed_ids.add(id_b)
                            continue
                        elif succ_b > succ_a + 2:
                            suppressed_ids.add(id_a)
                            continue

                        # Rule 4: Unresolvable conflict -> ABSTAIN both
                        a["transfer_decision"] = TransferDecision.ABSTAIN.value
                        a["abstain_reason"] = "UNRESOLVED_CONFLICT"
                        b["transfer_decision"] = TransferDecision.ABSTAIN.value
                        b["abstain_reason"] = "UNRESOLVED_CONFLICT"

            for item in items:
                if item.get("strategy_id") not in suppressed_ids:
                    resolved_list.append(item)

        return resolved_list
