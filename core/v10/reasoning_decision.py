"""V10.4 Reasoning + Decision Integration layer.

Integrates V10.1 FusedContext and V10.3 understood goal with V8 bounded reasoning
and decision engines to produce deterministic reasoning and decision outputs.
"""

from __future__ import annotations

from typing import Dict, Any, List, Optional, Tuple

from core.v10.context_fusion import FusedContext
from core.v10.goal_understanding import GoalUnderstandingResult, GoalSpec
from core.cognition.schemas import CognitiveIntent, CognitiveDecisionType
from core.cognition import reasoning_engine, cognitive_decision_engine

# Mapping from V8 goal types IntentClass to V8 cognition CognitiveIntent
GOAL_INTENT_TO_COGNITIVE_INTENT = {
    # Goal types IntentClass -> CognitiveIntent
    "COMPUTER": CognitiveIntent.ACTION,  # Computer actions are treated as generic actions
    "BROWSER": CognitiveIntent.ACTION,   # Browser actions are generic actions
    "FILESYSTEM": CognitiveIntent.ACTION, # Filesystem actions are generic actions
    "MEMORY_READ": CognitiveIntent.QUERY, # Memory read is a query
    "MEMORY_SAVE": CognitiveIntent.ACTION, # Direct match
    "SYSTEM_STATUS": CognitiveIntent.SYSTEM_OPERATION, # System status query
    "WORLD_ACTION": CognitiveIntent.ACTION, # World action is generic action
    "CONVERSATION": CognitiveIntent.CONVERSATION, # Direct match
    "DECISION": CognitiveIntent.QUERY,    # Decision intent is treated as query for reasoning
    "PLAN": CognitiveIntent.CREATION,     # Plan intent is treated as creation of a plan
    "UNKNOWN": CognitiveIntent.UNKNOWN,
    "AMBIGUOUS": CognitiveIntent.UNKNOWN, # Ambiguous treated as unknown for reasoning
}

# Reverse mapping for convenience (not used yet)
COGNITIVE_INTENT_TO_GOAL_INTENT = {v: k for k, v in GOAL_INTENT_TO_COGNITIVE_INTENT.items()}


def _map_goal_intent_to_cognitive(intent_str: str) -> CognitiveIntent:
    """Map a V8 goal types IntentClass string to a CognitiveIntent."""
    # Normalize the string to upper case for mapping lookup
    intent_upper = intent_str.upper()
    if intent_upper in GOAL_INTENT_TO_COGNITIVE_INTENT:
        return GOAL_INTENT_TO_COGNITIVE_INTENT[intent_upper]
    # Fallback to UNKNOWN
    return CognitiveIntent.UNKNOWN


def _extract_entities_from_fused_context(fused: FusedContext) -> Dict[str, Any]:
    """Extract relevant entities from FusedContext for reasoning input.
    
    This is a simplified extraction; in a full implementation, we would
    parse the various context sections (user_model, memory, etc.) to
    pull out structured entities like target_file, target_app, etc.
    """
    entities: Dict[str, Any] = {}
    
    # Extract from memory context if available
    memory_dict = {}
    for key, value in fused.context.items():
        if key.startswith("personal_memory_") or key.startswith("general_memory_"):
            # Store memory content under a generic key; in practice we might
            # want to structure this better, but for reasoning input we
            # just need a dict of relevant memory facts.
            memory_dict[key] = value
    
    if memory_dict:
        entities["relevant_memory_facts"] = memory_dict
    
    # Extract from user model context if available
    user_model_dict = {}
    for key, value in fused.context.items():
        if key.startswith("user_model_"):
            user_model_dict[key] = value
    
    if user_model_dict:
        entities["user_model_entries"] = user_model_dict
    
    # Extract goal state if available
    if "active_goal_id" in fused.context:
        entities["active_goal_id"] = fused.context["active_goal_id"]
    if "active_goal_intent" in fused.context:
        entities["active_goal_intent"] = fused.context["active_goal_intent"]
    if "active_goal_capability" in fused.context:
        entities["active_goal_capability"] = fused.context["active_goal_capability"]
    
    # Extract plan context if available
    if "plan_id" in fused.context:
        entities["plan_id"] = fused.context["plan_id"]
    
    return entities


def _extract_constraints_from_fused_context(fused: FusedContext) -> List[str]:
    """Extract constraints from FusedContext for reasoning input.
    
    Constraints are limitations or conditions that must be respected
    during reasoning (e.g., read_only, no_code_generation, target_location).
    """
    constraints: List[str] = []
    
    # Extract from context sections that imply constraints
    if "request_length" in fused.context:
        # If request is very long, we might need to truncate or summarize
        # but for now we don't add a specific constraint
        pass
    
    # Check for location constraints in memory or user model
    # This is simplified; in practice we would parse memory entries
    # for location-based constraints.
    if "conversation_history" in fused.context:
        # If we have conversation history, we might want to consider
        # it as context but not necessarily a constraint
        pass
    
    # If we have an active goal, we might inherit its constraints
    if "active_goal_intent" in fused.context:
        intent_val = fused.context["active_goal_intent"]
        if intent_val and isinstance(intent_val, str):
            # If active goal is a query, we might constrain to read-only
            if intent_val.upper() in ["QUERY", "SYSTEM_STATUS"]:
                constraints.append("read_only")
    
    return constraints


def _extract_required_capabilities_from_fused_context(fused: FusedContext) -> List[str]:
    """Extract required capabilities from FusedContext for reasoning input.
    
    This maps the understood goal's capability class to a list of strings
    that match what the V8 reasoning engine expects (e.g., "filesystem", 
    "coding", "general").
    """
    # We will get the capability class from the understood goal later
    # For now, we return a default list; this will be overridden
    # by the caller using the understood goal's capability.
    return ["general"]


def _build_relevant_memory_from_fused_context(fused: FusedContext) -> Dict[str, Any]:
    """Build a relevant memory dictionary from FusedContext's memory sections.
    
    The V8 reasoning engine expects a dictionary of relevant memory facts.
    We pull all personal_memory_* and general_memory_* entries from the
    FusedContext and treat them as relevant memory facts.
    """
    relevant_memory: Dict[str, Any] = {}
    
    for key, value in fused.context.items():
        if key.startswith("personal_memory_") or key.startswith("general_memory_"):
            # Use the context key as the memory fact key, and the value as the fact content
            relevant_memory[key] = value
    
    return relevant_memory


class ReasoningResult:
    """Structured result from the V10.4 reasoning integration.
    
    Contains the reasoning summary, assumptions, unresolved questions,
    and provenance information.
    """
    
    def __init__(
        self,
        reasoning_summary: str,
        assumptions: List[str],
        unresolved_questions: List[str],
        provenance: Optional[Dict[str, Any]] = None
    ):
        self.reasoning_summary = reasoning_summary
        self.assumptions = assumptions
        self.unresolved_questions = unresolved_questions
        self.provenance = provenance or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "reasoning_summary": self.reasoning_summary,
            "assumptions": self.assumptions,
            "unresolved_questions": self.unresolved_questions,
            "provenance": self.provenance,
        }
    
    def __repr__(self) -> str:
        return f"ReasoningResult(summary='{self.reasoning_summary[:50]}...', assumptions={len(self.assumptions)}, unresolved={len(self.unresolved_questions)})"


class DecisionResult:
    """Structured result from the V10.4 decision integration.
    
    Contains the decision type, decision basis, and provenance.
    """
    
    def __init__(
        self,
        decision_type: CognitiveDecisionType,
        decision_basis: str,
        provenance: Optional[Dict[str, Any]] = None
    ):
        self.decision_type = decision_type
        self.decision_basis = decision_basis
        self.provenance = provenance or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "decision_type": self.decision_type.value,
            "decision_basis": self.decision_basis,
            "provenance": self.provenance,
        }
    
    def __repr__(self) -> str:
        return f"DecisionResult(type='{self.decision_type.value}', basis='{self.decision_basis[:50]}...')"


class ReasoningDecisionIntegration:
    """V10.4 Reasoning + Decision Integration layer.
    
    Integrates V10.1 FusedContext and V10.3 understood goal with V8 bounded
    reasoning and decision engines.
    """
    
    def __init__(self):
        # No state needed; we use the global V8 engines directly
        pass
    
    def integrate(
        self,
        fused_context: FusedContext,
        goal_understanding: GoalUnderstandingResult,
        owner_id: str,
        session_id: str = ""
    ) -> Tuple[ReasoningResult, DecisionResult]:
        """Perform reasoning and decision integration.
        
        Args:
            fused_context: The FusedContext from V10.1 Context Fusion.
            goal_understanding: The GoalUnderstandingResult from V10.3.
            owner_id: The owner ID for scoping.
            session_id: The session ID (optional).
            
        Returns:
            A tuple of (ReasoningResult, DecisionResult).
        """
        # Step 1: Extract the user request from the fused context
        user_request = fused_context.context.get("request_raw", "")
        if not user_request:
            # Fallback to empty string if no request found
            user_request = ""
        
        # Step 2: Run V8 understanding engine to get detailed linguistic analysis
        # We use the understanding engine to get entities, constraints, required_capabilities, etc.
        # Note: The understanding engine also returns intent, but we will override it
        # with the intent from the goal understanding.
        from core.cognition import understanding_engine
        
        (intent_from_understanding, 
         normalized_goal_from_understanding, 
         entities_from_understanding, 
         constraints_from_understanding, 
         required_capabilities_from_understanding, 
         needs_clarification_from_understanding, 
         clarification_prompt_from_understanding, 
         confidence_from_understanding, 
         task_type_from_understanding) = understanding_engine.understand(
            user_request=user_request,
            context=None  # The understanding engine doesn't use context, but we pass None for clarity
        )
        
        # Step 3: Get the understood goal from V10.3 goal understanding
        understood_goal: GoalSpec = goal_understanding.understood_goal
        if understood_goal is None:
            # If no understood goal, we fall back to the understanding engine's intent
            intent_cognitive = intent_from_understanding
            normalized_goal = normalized_goal_from_understanding
            entities = entities_from_understanding
            constraints = constraints_from_understanding
            required_capabilities = required_capabilities_from_understanding
            needs_clarification = needs_clarification_from_understanding
        else:
            # Override intent with the one from the understood goal (mapped to CognitiveIntent)
            intent_cognitive = _map_goal_intent_to_cognitive(understood_goal.normalized_intent.value)
            # Use the normalized goal from the understood goal's raw_intent (or we could use the one from understanding)
            # We'll use the understood goal's raw_intent as the normalized goal for reasoning
            normalized_goal = understood_goal.raw_intent
            # For entities, we start with the understanding engine's output and then
            # add or override with goal-specific entities
            entities = entities_from_understanding.copy()
            # Add goal-specific entities
            entities.update({
                "goal_id": understood_goal.goal_id,
                "goal_capability": understood_goal.capability_class.value,
                "goal_provenance": understood_goal.provenance.value,
                "goal_requested_unix_ms": understood_goal.requested_unix_ms,
                "goal_hash": understood_goal.goal_hash,
            })
            # For constraints, we start with understanding engine's constraints and add goal-based constraints
            constraints = constraints_from_understanding.copy()
            # Add constraints based on goal capability (e.g., if goal is SYSTEM_STATUS, add read_only)
            cap_val = understood_goal.capability_class.value
            if cap_val == "system_read":
                constraints.append("read_only")
            elif cap_val == "memory_read":
                constraints.append("read_only")  # Memory read is read-only
            # For required_capabilities, we derive from the goal's capability class
            # We map the V8 goal CapabilityClass to a list of strings that the reasoning engine expects
            required_capabilities = self._map_goal_capability_to_required_capabilities(understood_goal.capability_class)
            # For needs_clarification, we use the understanding engine's output but
            # we might override if the goal is very clear (e.g., a specific system command)
            needs_clarification = needs_clarification_from_understanding
            # If the goal is a clear action with no ambiguity, we might reduce need for clarification
            # but we keep the understanding engine's judgment for safety.
        
        # Step 4: Enhance entities, constraints, required_capabilities with FusedContext data
        # Extract additional entities from fused context
        fused_entities = _extract_entities_from_fused_context(fused_context)
        entities.update(fused_entities)
        
        # Extract additional constraints from fused context
        fused_constraints = _extract_constraints_from_fused_context(fused_context)
        constraints.extend(fused_constraints)
        # Remove duplicates while preserving order
        seen = set()
        unique_constraints = []
        for c in constraints:
            if c not in seen:
                seen.add(c)
                unique_constraints.append(c)
        constraints = unique_constraints
        
        # Enhance required_capabilities with FusedContext data (e.g., if user model indicates certain capabilities)
        # For now, we keep the required_capabilities as derived from the goal
        # but we could add more based on user model or memory.
        # We'll leave it as is for simplicity.
        
        # Step 5: Build relevant memory from fused context
        relevant_memory = _build_relevant_memory_from_fused_context(fused_context)
        
        # Step 6: Call V8 reasoning engine
        reasoning_summary, assumptions, unresolved_questions = reasoning_engine.reason(
            intent=intent_cognitive,
            normalized_goal=normalized_goal,
            entities=entities,
            constraints=constraints,
            required_capabilities=required_capabilities,
            relevant_memory=relevant_memory
        )
        
        # Step 7: Build reasoning result with provenance
        reasoning_provenance = {
            "fused_context_keys": list(fused_context.context.keys()),
            "goal_understood": understood_goal is not None,
            "owner_id": owner_id,
            "session_id": session_id,
            "reasoning_engine_version": "V8",
        }
        reasoning_result = ReasoningResult(
            reasoning_summary=reasoning_summary,
            assumptions=assumptions,
            unresolved_questions=unresolved_questions,
            provenance=reasoning_provenance
        )
        
        # Step 8: Prepare input to V8 decision engine
        # The decision engine needs:
        #   intent: CognitiveIntent
        #   needs_clarification: bool
        #   required_capabilities: List[str]
        #   entities: Dict[str, Any]
        #   tool_candidate: Optional[str]
        #
        # We already have most of these from above.
        # For tool_candidate, we can try to infer from the intent and entities,
        # but we set it to None for now as it's optional and used for high-risk tool approval.
        tool_candidate: Optional[str] = None
        
        # Step 9: Call V8 decision engine
        decision_type, decision_basis = cognitive_decision_engine.decide(
            intent=intent_cognitive,
            needs_clarification=needs_clarification,
            required_capabilities=required_capabilities,
            entities=entities,
            tool_candidate=tool_candidate
        )
        
        # Step 10: Build decision result with provenance
        decision_provenance = {
            "reasoning_summary_length": len(reasoning_summary),
            "assumptions_count": len(assumptions),
            "unresolved_questions_count": len(unresolved_questions),
            "owner_id": owner_id,
            "session_id": session_id,
            "decision_engine_version": "V8",
        }
        decision_result = DecisionResult(
            decision_type=decision_type,
            decision_basis=decision_basis,
            provenance=decision_provenance
        )
        
        return reasoning_result, decision_result
    
    def _map_goal_capability_to_required_capabilities(self, capability_class) -> List[str]:
        """Map a V8 goal types CapabilityClass to a list of required capability strings.
        
        The V8 reasoning engine expects a list of strings like ["filesystem", "coding", "general"]
        that correspond to the capabilities needed for the reasoning task.
        
        We map the V8 goal CapabilityClass to one or more of these strings.
        """
        # Map from goal capability class to a list of strings
        capability_mapping = {
            "computer": ["general"],  # Generic capability
            "browser": ["general"],   # Generic capability
            "filesystem": ["filesystem"],
            "memory_read": ["general"],  # Memory read is a query capability
            "memory_write": ["general"],  # Memory write is a generic capability
            "system_read": ["general"],  # System telemetry is a generic query
            "world_act": ["general"],    # World action is generic
            "conversation": ["general"], # Conversation is generic
            "none": [],                  # No specific capabilities
            "sequence": ["general"],     # Sequence is generic
            "verification": ["general"], # Verification is generic
            "world_act": ["general"],    # World action is generic
            "memory_read": ["general"],  # Duplicate for clarity
        }
        
        cap_val = capability_class.value if hasattr(capability_class, 'value') else str(capability_class)
        return capability_mapping.get(cap_val, ["general"])


# Global instance for convenience
reasoning_decision_integration = ReasoningDecisionIntegration()


def integrate_reasoning_decision(
    fused_context: FusedContext,
    goal_understanding: GoalUnderstandingResult,
    owner_id: str,
    session_id: str = ""
) -> Tuple[ReasoningResult, DecisionResult]:
    """Convenience function for V10.4 reasoning + decision integration."""
    return reasoning_decision_integration.integrate(
        fused_context=fused_context,
        goal_understanding=goal_understanding,
        owner_id=owner_id,
        session_id=session_id
    )
