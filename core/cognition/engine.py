"""
DOOM V4 — Cognitive Core Master Engine
Orchestrates the 9-stage cognitive loop:
UNDERSTAND -> REASON -> DECIDE -> PLAN -> ACT -> OBSERVE -> EVALUATE -> REFLECT -> REPLAN
Integrates with V3.3 TaskEngine, StateMachine, ModelRouter, and GroundTruthVerifier.
"""

import time
from typing import Dict, Any, List, Optional, Callable
from core.cognition.schemas import (
    CognitiveState, CognitiveIntent, CognitiveDecisionType, CognitiveStep,
    CognitiveObservation, CognitiveReflection, EvaluationOutcome, CognitiveTelemetry
)
from core.cognition.understanding import understanding_engine
from core.cognition.reasoning import reasoning_engine
from core.cognition.decision import cognitive_decision_engine
from core.cognition.planner import cognitive_planner
from core.cognition.observation import observation_engine
from core.cognition.reflection import reflection_engine
from core.cognition.replanning import cognitive_replanner
from core.state_machine import state_machine, DoomState
from core.task_engine import task_engine, TaskStatus, StepStatus
from core.verifier import verifier
from core.model_router import model_router, NoCapableProviderError
from tools.base import CanonicalToolResult, RiskLevel


MAX_COGNITIVE_ITERATIONS = 5


class CognitiveEngine:
    """
    Master Cognitive Engine for DOOM V4:
    Executes the goal-oriented cognitive lifecycle with strict ground-truth verification and bounded iterations.
    """

    def __init__(self):
        self._broadcaster: Optional[Callable[[Dict[str, Any]], None]] = None

    def set_broadcaster(self, broadcaster: Callable[[Dict[str, Any]], None]) -> None:
        self._broadcaster = broadcaster
        from core.cognition.bridge import cognitive_bridge
        cognitive_bridge.set_broadcaster(broadcaster)

    def _broadcast(self, event_type: str, **payload) -> None:
        if self._broadcaster:
            try:
                self._broadcaster({"type": "cognitive_event", "event": event_type, **payload})
            except Exception:
                pass

    def retrieve_relevant_memory(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        V5.1: Delegates to MemoryRetriever for structured, ranked, privacy-filtered retrieval.
        V5.3.7.1: Dynamically resolves project context instead of hardcoding 'doom'.
        Falls back to legacy keyword lookup if V5.1 retriever fails (graceful degradation).
        Returns a plain dict for backward compat with existing callers.
        The full MemoryContext is stored in state.memory_context.
        """
        try:
            from memory.project_context import resolve_project_context
            from memory.retrieval import memory_retriever
            proj_ctx = resolve_project_context(explicit_project_id=project_id, context=context, strict=False)
            ctx = memory_retriever.retrieve(query=goal, project_id=proj_ctx.project_id)
            if ctx.has_memories():
                # Return a summary dict for backward compat (reasoning_engine still receives dict)
                return {"memory_context_summary": ctx.context_summary, "memory_count": ctx.memory_count}
        except Exception:
            pass

        # Legacy fallback (keyword-based, V4.2 behavior preserved)
        from memory import semantic_memory, user_profile
        relevant = {}
        lower = goal.lower()
        name = user_profile.get_name()
        if "who" in lower or "name" in lower or "me" in lower or "profile" in lower:
            relevant["user_name"] = name
            relevant["user_role"] = user_profile.get_role()
        for key in ["assistant", "voice", "operating_system", "capabilities"]:
            val = semantic_memory.recall_fact(key)
            if val and (key in lower or "system" in lower or "doom" in lower or "status" in lower):
                relevant[key] = val
        return relevant

    def process(
        self,
        user_request: str,
        context: Optional[Dict[str, Any]] = None,
        project_id: Optional[str] = None,
    ) -> CognitiveState:
        """
        Public Master Entry Point for the V4 Cognitive Lifecycle.
        V5.3.7.1: Resolves dynamic project context and propagates it throughout execution.
        """
        t0 = time.time()
        from memory.project_context import resolve_project_context
        proj_ctx = resolve_project_context(explicit_project_id=project_id, context=context)
        state = CognitiveState(
            user_request=user_request,
            project_id=proj_ctx.project_id,
            project_context=proj_ctx,
        )
        self._broadcast("COGNITION_STARTED", request=user_request, project_id=proj_ctx.project_id)

        # ---------------------------------------------------------------------
        # 1. MEMORY CONTEXT RETRIEVAL (V5.1 / V5.3.7.1)
        # ---------------------------------------------------------------------
        t_mem = time.time()
        try:
            from memory.retrieval import memory_retriever
            self._broadcast("MEMORY_RETRIEVAL_STARTED", query=user_request[:60], project_id=proj_ctx.project_id)
            mem_ctx = memory_retriever.retrieve(query=user_request, project_id=proj_ctx.project_id)
            state.memory_context = mem_ctx
            state.telemetry.memory_retrieval_ms = (time.time() - t_mem) * 1000.0
            # Backward compat: populate state.relevant_memory as summary dict
            if mem_ctx.has_memories():
                state.relevant_memory = {
                    "memory_context_summary": mem_ctx.context_summary,
                    "memory_count": mem_ctx.memory_count,
                }
                self._broadcast("MEMORY_RETRIEVAL_COMPLETED",
                               count=mem_ctx.memory_count,
                               latency_ms=mem_ctx.retrieval_latency_ms)
            else:
                # Legacy fallback for profile/system facts
                state.relevant_memory = self.retrieve_relevant_memory(
                    user_request, context=context, project_id=proj_ctx.project_id
                )
        except Exception as mem_err:
            # Memory retrieval failure must never prevent cognition
            state.telemetry.memory_retrieval_ms = (time.time() - t_mem) * 1000.0
            state.relevant_memory = self.retrieve_relevant_memory(
                user_request, context=context, project_id=proj_ctx.project_id
            )


        # ---------------------------------------------------------------------
        # 2. UNDERSTAND
        # ---------------------------------------------------------------------
        t_und = time.time()
        (
            state.intent,
            state.normalized_goal,
            state.entities,
            state.constraints,
            state.required_capabilities,
            state.needs_clarification,
            state.clarification_prompt,
            state.confidence,
            state.task_type
        ) = understanding_engine.understand(user_request, context)
        state.telemetry.understanding_ms = (time.time() - t_und) * 1000.0
        self._broadcast("UNDERSTANDING_COMPLETE", intent=state.intent.value, goal=state.normalized_goal)

        # Early return if clarification is required
        if state.needs_clarification:
            state.decision = CognitiveDecisionType.ASK_CLARIFICATION
            state.final_response = state.clarification_prompt or "Could you clarify what you want me to do, Boss?"
            state.final_response_status = "blocked"
            state.is_terminal = True
            state.termination_reason = "CLARIFICATION_REQUIRED"
            elapsed = (time.time() - t0) * 1000.0
            state.telemetry.total_cognitive_ms = max(elapsed, 0.05)
            self._broadcast("COGNITION_COMPLETED", status=state.final_response_status)
            return state

        # ---------------------------------------------------------------------
        # 3. REASON
        # ---------------------------------------------------------------------
        t_reas = time.time()
        (
            state.reasoning_summary,
            state.assumptions,
            unresolved
        ) = reasoning_engine.reason(
            state.intent,
            state.normalized_goal,
            state.entities,
            state.constraints,
            state.required_capabilities,
            state.relevant_memory
        )
        state.telemetry.reasoning_ms = (time.time() - t_reas) * 1000.0
        self._broadcast("REASONING_COMPLETE", summary=state.reasoning_summary)

        # ---------------------------------------------------------------------
        # 4. DECIDE
        # ---------------------------------------------------------------------
        t_dec = time.time()
        (
            state.decision,
            state.decision_basis
        ) = cognitive_decision_engine.decide(
            state.intent,
            state.needs_clarification,
            state.required_capabilities,
            state.entities
        )
        state.telemetry.decision_ms = (time.time() - t_dec) * 1000.0
        self._broadcast("DECISION_MADE", decision=state.decision.value, basis=state.decision_basis)

        # Fast-Path / Direct Decision Resolution
        if state.decision == CognitiveDecisionType.ANSWER_DIRECTLY:
            lower = user_request.lower()
            if state.intent == CognitiveIntent.CONVERSATION or any(w in lower for w in ["who am i", "who are you", "what is my name", "my profile"]):
                from memory import user_profile
                if any(w in lower for w in ["who are you", "what is your name"]):
                    state.final_response = "I am DOOM V4, your sovereign Personal AI Operating System and autonomous companion. Standing by, Boss."
                else:
                    name = user_profile.get_name()
                    role = user_profile.get_role()
                    access = user_profile.get_access_level()
                    state.final_response = f"You are {name}, {role}. You hold {access} security clearance. Active Focus: DOOM V4 Personal AI OS. At your command, Boss."
                state.final_response_status = "success"
                state.is_terminal = True
                elapsed = (time.time() - t0) * 1000.0
                state.telemetry.total_cognitive_ms = max(elapsed, 0.05)
                self._broadcast("COGNITION_COMPLETED", status=state.final_response_status)
                return state

            # Simple arithmetic query (e.g. "What is 2 + 2?")
            import re
            math_match = re.search(r'(\d+(?:\.\d+)?)\s*([\+\-\*\/])\s*(\d+(?:\.\d+)?)', user_request)
            if math_match:
                a_val = float(math_match.group(1))
                op = math_match.group(2)
                b_val = float(math_match.group(3))
                res = a_val + b_val if op == '+' else a_val - b_val if op == '-' else a_val * b_val if op == '*' else a_val / b_val if b_val != 0 else 0
                res_str = str(int(res)) if res.is_integer() else f"{res:.2f}"
                a_str = str(int(a_val)) if a_val.is_integer() else str(a_val)
                b_str = str(int(b_val)) if b_val.is_integer() else str(b_val)
                state.final_response = f"{a_str} {op} {b_str} = {res_str}"
                state.final_response_status = "success"
                state.is_terminal = True
                elapsed = (time.time() - t0) * 1000.0
                state.telemetry.total_cognitive_ms = max(elapsed, 0.05)
                self._broadcast("COGNITION_COMPLETED", status=state.final_response_status)
                return state

            state.final_response = "Goal processed directly, Boss."
            state.final_response_status = "success"
            state.is_terminal = True
            elapsed = (time.time() - t0) * 1000.0
            state.telemetry.total_cognitive_ms = max(elapsed, 0.05)
            self._broadcast("COGNITION_COMPLETED", status=state.final_response_status)
            return state

        if state.decision == CognitiveDecisionType.REQUEST_APPROVAL:
            state.final_response = "Action requires your authorization, Boss. Please confirm in the DOOM HUD."
            state.final_response_status = "blocked"
            state.is_terminal = True
            state.termination_reason = "USER_APPROVAL_REQUIRED"
            state_machine.transition_to(DoomState.WAITING_FOR_APPROVAL, "Authorization required")
            elapsed = (time.time() - t0) * 1000.0
            state.telemetry.total_cognitive_ms = max(elapsed, 0.05)
            self._broadcast("COGNITION_COMPLETED", status=state.final_response_status)
            return state

        # ---------------------------------------------------------------------
        # 5. PLAN & EMPIRICAL GUIDANCE (V5.3.7.1)
        # ---------------------------------------------------------------------
        t_plan = time.time()
        strategies: List[Dict[str, Any]] = []
        failure_warnings: List[Dict[str, Any]] = []
        fenced_guidance = None

        try:
            from memory.retrieval import memory_retriever
            from memory.fencing import memory_context_fencer
            # Retrieve active strategies for project (STRICTLY READ-ONLY)
            strategies = memory_retriever.retrieve_strategies(
                project_id=proj_ctx.project_id,
                query=state.normalized_goal,
                max_results=3,
                evaluate_transfers=True,
            )
            # Retrieve negative warnings for project (STRICTLY READ-ONLY)
            failure_warnings = memory_retriever.retrieve_negative_experiences(
                project_id=proj_ctx.project_id,
                max_results=3,
            )
            if strategies or failure_warnings:
                fenced_guidance = memory_context_fencer.fence_empirical_guidance(
                    strategies=strategies,
                    failure_warnings=failure_warnings,
                )
                state.empirical_guidance = fenced_guidance
        except Exception:
            pass

        state.current_plan = cognitive_planner.plan(
            state.intent,
            state.normalized_goal,
            state.entities,
            state.required_capabilities,
            empirical_guidance=fenced_guidance,
            strategies=strategies,
            failure_warnings=failure_warnings,
            project_id=proj_ctx.project_id,
        )
        state.telemetry.planning_ms = (time.time() - t_plan) * 1000.0
        self._broadcast("PLAN_CREATED", step_count=len(state.current_plan))

        # ---------------------------------------------------------------------
        # 6. COGNITIVE -> V3.3 BRIDGE EXECUTION
        # Delegates execution to CognitiveBridge (TaskEngine, StateMachine,
        # ToolRegistry, RiskEngine, CheckpointManager, and Verifier)
        # ---------------------------------------------------------------------
        from core.cognition.bridge import cognitive_bridge
        merged_context = dict(context or {})
        merged_context["project_id"] = proj_ctx.project_id
        merged_context["project_context"] = proj_ctx
        state = cognitive_bridge.execute_plan(state, merged_context)
        elapsed = (time.time() - t0) * 1000.0
        state.telemetry.total_cognitive_ms = max(elapsed, 0.05)

        return state


cognitive_engine = CognitiveEngine()
