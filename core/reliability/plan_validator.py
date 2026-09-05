"""
DOOM V4.2 — Cognitive Plan Validator
Pre-execution gate validating that generated plans are structurally sound,
acyclic, within step bounds, and only reference legitimate registered tools.
"""

from typing import List, Dict, Any, Tuple, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.cognition.schemas import CognitiveStep


class PlanValidator:
    """
    Validates CognitiveStep lists before CognitiveBridge begins execution.
    Prevents execution of cyclic, malformed, or unauthorized plans.
    """

    MAX_PLAN_STEPS: int = 15

    def validate_plan(self, plan: List[Any]) -> Tuple[bool, List[str]]:
        """
        Validates the plan structure.
        Returns: (is_valid: bool, errors: List[str])
        """
        errors = []

        if not plan:
            return True, []  # Empty plan handled as direct answer

        if len(plan) > self.MAX_PLAN_STEPS:
            errors.append(f"Plan exceeds maximum step limit: {len(plan)} > {self.MAX_PLAN_STEPS}")

        # 1. Unique step IDs
        seen_ids = set()
        for step in plan:
            if step.step_id in seen_ids:
                errors.append(f"Duplicate step ID detected: {step.step_id}")
            seen_ids.add(step.step_id)

        # 2. Dependency existence and Cycle Detection (DAG validation)
        dep_graph: Dict[Any, List[Any]] = {}
        for step in plan:
            dep_graph[step.step_id] = []
            for dep in step.dependencies:
                if dep not in seen_ids:
                    errors.append(f"Step {step.step_id} references nonexistent dependency {dep}")
                elif dep == step.step_id:
                    errors.append(f"Step {step.step_id} has self-referential dependency")
                else:
                    dep_graph[step.step_id].append(dep)

        # Cycle check using DFS
        visited: Dict[Any, int] = {}  # 0: unvisited, 1: visiting, 2: visited
        def has_cycle(node: Any) -> bool:
            visited[node] = 1
            for neighbor in dep_graph.get(node, []):
                if visited.get(neighbor, 0) == 1:
                    return True
                if visited.get(neighbor, 0) == 0 and has_cycle(neighbor):
                    return True
            visited[node] = 2
            return False

        for step in plan:
            if visited.get(step.step_id, 0) == 0:
                if has_cycle(step.step_id):
                    errors.append(f"Cyclic dependency detected in plan involving step {step.step_id}")
                    break

        # 3. Tool existence validation
        from core.tool_registry import tool_registry
        known_tools = {t.name for t in tool_registry.get_all_tools()}
        # Special internal tools
        known_tools.add("verifier")
        known_tools.add("skip_redundant")

        for step in plan:
            tool_obj = tool_registry.get_tool(step.tool_name) if step.tool_name else None
            if step.tool_name and step.tool_name not in known_tools and tool_obj is None:
                errors.append(f"Step {step.step_id} references unknown tool '{step.tool_name}'")

            if not step.action:
                errors.append(f"Step {step.step_id} is missing an action descriptor")

        return len(errors) == 0, errors


# Global singleton instance
plan_validator = PlanValidator()
