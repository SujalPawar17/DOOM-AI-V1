"""V12 Cognitive Orchestrator.

The frozen V11 pipeline with V12 layers plugged into its extension points. V12.1
replaces Stage 12 (response generation) with Response Intelligence; every other stage
(Context Fusion, goals, reasoning, planning, Cost Guard, authorization, execution,
verification, V9 delivery metadata, outcome, experience) is inherited unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.v11.cognitive_orchestrator import V11CognitiveOrchestrator
from core.v12.adaptive_learning import AdaptiveLearning
from core.v12.multimodal import (
    PerceptionNormalizer, PerceptualItem, UnifiedPerceptualContext, compose_caller_context,
)
from core.v12.response_intelligence import ResponseIntelligence


class V12CognitiveOrchestrator(V11CognitiveOrchestrator):

    def __init__(self, response_intelligence: Optional[ResponseIntelligence] = None,
                 learning: Optional[AdaptiveLearning] = None,
                 perception: Optional[UnifiedPerceptualContext] = None):
        super().__init__()
        self.response_intelligence = response_intelligence or ResponseIntelligence()
        self.learning = learning or AdaptiveLearning()
        self.perception = perception or UnifiedPerceptualContext()
        self.normalizer = PerceptionNormalizer()

    def perceive(self, item: PerceptualItem) -> bool:
        """V12.4: add a normalized perceptual item to its owner+session context."""
        return self.perception.add(item)

    def process_cognitive_cycle(self, user_input: str, owner_id: str, session_id: str = "",
                                lang: Optional[str] = None, context: Optional[Dict[str, Any]] = None,
                                project_id: Optional[str] = None, authorized_plan_hash: str = "",
                                computer_session_id: str = "") -> Dict[str, Any]:
        # V12.2: learned knowledge reaches Context Fusion through the hardened caller-
        # context channel; caller-supplied keys (e.g. monitoring metadata) take precedence.
        try:
            learned = self.learning.retrieve(owner_id, session_id, user_input)
        except Exception:
            learned = {}
        try:
            perceptual = self.perception.to_context(owner_id, session_id)
        except Exception:
            perceptual = {}
        # Priority: caller/monitoring keys > perceptual context > learned knowledge.
        merged_context = compose_caller_context(context, perceptual, learned)
        result = super().process_cognitive_cycle(
            user_input=user_input, owner_id=owner_id, session_id=session_id, lang=lang,
            context=merged_context or None, project_id=project_id,
            authorized_plan_hash=authorized_plan_hash, computer_session_id=computer_session_id,
        )
        result.setdefault("stages", {})["perception"] = {
            "completed": True, "percept_keys": sorted(k for k in merged_context if k.startswith("percept_"))}
        result.setdefault("stages", {})["adaptive_learning"] = self._learn_from_cycle(
            user_input, owner_id, session_id, context, result, sorted(learned))
        return result

    def _learn_from_cycle(self, user_input: str, owner_id: str, session_id: str,
                          context: Optional[Dict[str, Any]], result: Dict[str, Any], retrieved) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"completed": True, "retrieved_keys": retrieved,
                                   "learned_from_statement": [], "experience_patterns": 0}
        try:
            # Only the user's own words are statements; monitor-generated input is not.
            if not (context or {}).get("monitoring_trigger"):
                summary["learned_from_statement"] = [
                    f"{i.kind.value}:{i.key}" for i in self.learning.observe_utterance(owner_id, session_id, user_input)]
            exp = (result.get("provenance") or {}).get("experience_integration") or {}
            if exp.get("status") == "OK":
                from orchestration.experience.store import list_experiences
                listed = list_experiences(owner_id)
                summary["experience_patterns"] = len(
                    self.learning.observe_experiences(owner_id, list(listed.experiences or ())))
        except Exception as exc:  # learning never breaks a cycle
            summary["error"] = type(exc).__name__
        return summary

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
