"""
DOOM V4 — Cognitive Decision Layer
Selects high-level execution strategy while strictly enforcing V3.3 security & risk boundaries.
"""

from typing import Dict, Any, List, Optional, Tuple
from core.cognition.schemas import CognitiveIntent, CognitiveDecisionType
from tools.base import RiskLevel


class CognitiveDecisionEngine:
    """
    Cognitive Decision Layer:
    Selects strategy and enforces security approval gates for high/critical risk actions.
    """

    def decide(
        self,
        intent: CognitiveIntent,
        needs_clarification: bool,
        required_capabilities: List[str],
        entities: Dict[str, Any],
        tool_candidate: Optional[str] = None
    ) -> Tuple[CognitiveDecisionType, str]:
        """
        Returns:
          - decision: CognitiveDecisionType
          - decision_basis: str
        """
        # 1. Clarification Gate
        if needs_clarification:
            return (
                CognitiveDecisionType.ASK_CLARIFICATION,
                "Request is ambiguous or target is underspecified for a potentially impactful action."
            )

        # 2. Direct Conversational Answers
        if intent == CognitiveIntent.CONVERSATION:
            return (
                CognitiveDecisionType.ANSWER_DIRECTLY,
                "Goal is conversational or identity inquiry; respond immediately without tool invocation."
            )

        # 3. Security Check: High or Critical Risk Tools require explicit approval
        if tool_candidate:
            from core.tool_registry import tool_registry
            tool_obj = tool_registry.get_tool(tool_candidate)
            if tool_obj and tool_obj.get_effective_risk() in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                return (
                    CognitiveDecisionType.REQUEST_APPROVAL,
                    f"Selected tool '{tool_candidate}' has effective risk '{tool_obj.get_effective_risk().value}' requiring user authorization."
                )

        # 4. Multi-Step & Complex Automation -> Dynamic Plan Creation
        if intent in (CognitiveIntent.MULTI_STEP, CognitiveIntent.AUTOMATION):
            return (
                CognitiveDecisionType.CREATE_PLAN,
                "Goal involves sequential multi-action steps requiring coordinated plan, execution, and verification."
            )

        # 5. Direct System Telemetry Operation
        if intent == CognitiveIntent.SYSTEM_OPERATION:
            return (
                CognitiveDecisionType.EXECUTE_TOOL,
                "Query hardware sensors directly via read-only telemetry tools."
            )

        # 6. Web Search
        if intent == CognitiveIntent.SEARCH:
            return (
                CognitiveDecisionType.SEARCH_WEB,
                "Goal requires external live web intelligence retrieval."
            )

        # 7. Single Direct Action
        if intent in (CognitiveIntent.ACTION, CognitiveIntent.CREATION, CognitiveIntent.MODIFICATION):
            return (
                CognitiveDecisionType.EXECUTE_TOOL,
                "Execute single focused operational action."
            )

        return (
            CognitiveDecisionType.ANSWER_DIRECTLY,
            "Default cognitive resolution strategy."
        )


cognitive_decision_engine = CognitiveDecisionEngine()
