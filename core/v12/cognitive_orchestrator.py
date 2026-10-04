"""V12 Cognitive Orchestrator.

The frozen V11 pipeline with V12 layers plugged into its extension points. V12.1
replaces Stage 12 (response generation) with Response Intelligence; every other stage
(Context Fusion, goals, reasoning, planning, Cost Guard, authorization, execution,
verification, V9 delivery metadata, outcome, experience) is inherited unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.v11.cognitive_orchestrator import V11CognitiveOrchestrator
from core.v12.response_intelligence import ResponseIntelligence


class V12CognitiveOrchestrator(V11CognitiveOrchestrator):

    def __init__(self, response_intelligence: Optional[ResponseIntelligence] = None):
        super().__init__()
        self.response_intelligence = response_intelligence or ResponseIntelligence()

    def _compose_response(self, facts: Dict[str, Any]) -> Dict[str, Any]:
        composed = self.response_intelligence.compose(facts)
        return {
            "text": composed.text,
            "intent": composed.intent.value,
            "source": composed.source.value,
            "corrections": list(composed.corrections),
        }


v12_cognitive_orchestrator = V12CognitiveOrchestrator()


def process_v12_cognitive_cycle(
    user_input: str,
    owner_id: str,
    session_id: str = "",
    lang: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    project_id: Optional[str] = None,
    authorized_plan_hash: str = "",
    computer_session_id: str = "",
) -> Dict[str, Any]:
    """Convenience entry point for the V12 cognitive pipeline."""
    return v12_cognitive_orchestrator.process_cognitive_cycle(
        user_input=user_input,
        owner_id=owner_id,
        session_id=session_id,
        lang=lang,
        context=context,
        project_id=project_id,
        authorized_plan_hash=authorized_plan_hash,
        computer_session_id=computer_session_id,
    )
