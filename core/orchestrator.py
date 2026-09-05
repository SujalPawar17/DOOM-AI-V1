import json
import time
import re
import os
import psutil
from typing import Dict, Any, List, Optional
from core.context_manager import context_manager
from core.planner import planner, ExecutionPlan, PlanStep
from core.model_router import model_router, NoCapableProviderError
from core.tool_registry import tool_registry
from core.verifier import verifier
from core.state_machine import state_machine, DoomState
from core.task_engine import task_engine
from core.decision_engine import decision_engine
from core.path_resolver import canonical_path
from tools.base import RiskLevel, CanonicalToolResult, ToolResult, TerminationReason, FinalResponseStatus, MAX_AGENT_STEPS, MAX_TOOL_CALLS, MAX_RETRIES_PER_ACTION
from memory import user_profile, short_term_memory, episodic_memory
from core.cognition import cognitive_engine, CognitiveEngine


def extract_expected_filename(user_prompt: str) -> Optional[str]:
    """Extract the exact filename requested by the user from the prompt."""
    # Pattern: "called <filename>", "named <filename>", "create <filename>", "file <filename>"
    patterns = [
        r'\bcalled\s+([a-zA-Z0-9_\-\.\\\/]+\.py)\b',
        r'\bnamed\s+([a-zA-Z0-9_\-\.\\\/]+\.py)\b',
        r'\bfile\s+([a-zA-Z0-9_\-\.\\\/]+\.py)\b',
        r'create\s+(?:a\s+)?(?:python\s+)?(?:file\s+)?(?:on\s+my\s+desktop\s+)?(?:called\s+)?([a-zA-Z0-9_\-\.\\\/]+\.py)',
        r'([a-zA-Z0-9_\-\.\\\/]+\.py)(?=\s+that|\s+which|\s+to|\s*,|\s*$)',
    ]
    for pat in patterns:
        match = re.search(pat, user_prompt, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def correct_tool_filename(tool_name: str, tool_args: Dict[str, Any], expected_filename: str) -> Dict[str, Any]:
    """Correct the filename in tool args to match the user's explicit request."""
    if not expected_filename:
        return tool_args
    
    # Normalize expected filename (ensure .py extension)
    if not expected_filename.endswith('.py'):
        expected_filename += '.py'
    
    # For coding_write_script and filesystem_write_file, correct the file_name/file_path
    corrected_args = tool_args.copy()
    if tool_name in ["coding_write_script", "filesystem_write_file"]:
        for key in ["file_name", "file_path"]:
            if key in corrected_args:
                provided = corrected_args[key]
                # Extract just the filename from the provided path
                provided_filename = os.path.basename(provided)
                # If the provided filename differs significantly from expected (e.g., missing underscores)
                # Correct it by replacing the filename portion while preserving the directory
                if provided_filename.lower() != expected_filename.lower():
                    # Check if it's a simple case normalization issue (underscores removed, etc.)
                    provided_no_underscore = provided_filename.replace('_', '')
                    expected_no_underscore = expected_filename.replace('_', '')
                    if provided_no_underscore.lower() == expected_no_underscore.lower():
                        # Same name but underscores handled differently - preserve user's exact case/underscores
                        dir_part = os.path.dirname(provided)
                        corrected_args[key] = os.path.join(dir_part, expected_filename) if dir_part else expected_filename
    return corrected_args


class DOOMCore:
    """
    DOOM V3.3 Master Autonomous Agent Orchestrator — Reliability, Truth & Resume Engine.
    Features:
      - Intent Classification (DIRECT, QUERY, ACTION, MULTI_STEP, AUTONOMOUS)
      - Deterministic Telemetry/Identity dispatch (0 unnecessary tool calls)
      - Pre-Execution Decision & Idempotency Engine (rejects redundant tools)
      - Canonical Observation Format (structured, normalized tool results)
      - Ground-Truth Verification (syntax, process exit code == 0, file on disk)
      - Truth-First Synthesis (NEVER claims completion without verified ground-truth)
      - Capability-Preserving Failover (raises NoCapableProviderError on outage)
      - Task Checkpointing (persisted after every tool execution)
      - Partial Success / PAUSED states (distinguishes planned/started/blocked/complete)
      - Latency Profiling (planning, routing, llm, tool, verification, synthesis, checkpoint, total)
      - Hard Tool Timeouts with structured timeout observations
      - Autonomous Loop Control (MAX_AGENT_STEPS, MAX_TOOL_CALLS, MAX_RETRIES_PER_ACTION)
      - Explicit Termination Reasons (COMPLETED, FAILED, TIMEOUT, USER_APPROVAL_REQUIRED, MAX_STEPS_REACHED, MAX_RETRIES_REACHED, UNRECOVERABLE_ERROR)
      - Controlled Self-Healing (write->execute->observe->diagnose->repair->execute->verify)
    """
    def __init__(self):
        self.context_mgr = context_manager
        self.planner = planner
        self.router = model_router
        self.tools = tool_registry
        self.verifier = verifier
        self.decision_eng = decision_engine
        self.cognition = cognitive_engine
        self.max_agent_steps = MAX_AGENT_STEPS
        self.max_tool_calls = MAX_TOOL_CALLS
        self.max_retries_per_action = MAX_RETRIES_PER_ACTION

    def process_request(self, user_input: str, lang: Optional[str] = None) -> str:
        """
        DOOM V4.1 Master Production Entry Point — Integrated Cognitive Core.
        Delegates understanding, reasoning, decision, dynamic planning, execution,
        observation, evaluation, reflection, and adaptive replanning to CognitiveEngine.
        """
        if not user_input or not user_input.strip():
            state_machine.transition_to(DoomState.IDLE, "Standing by, Boss.")
            return "Standing by, Boss."

        start_time = time.time()
        user_prompt = user_input.strip()
        print(f"\n[DOOM CORE] [*] Initiating Autonomous Goal: '{user_prompt}' (lang: {lang or 'auto'})")

        # Step 1: Record user turn in short-term memory
        short_term_memory.add_user_turn(user_prompt)

        # Step 2: Invoke V4 Cognitive Core Lifecycle
        t_cog_start = time.time()
        try:
            cognitive_state = self.cognition.process(user_prompt, context={"lang": lang})
        except Exception as cog_err:
            print(f"[DOOM CORE] [COGNITIVE ERROR] {cog_err}")
            state_machine.transition_to(DoomState.ERROR, str(cog_err))
            return f"I encountered an anomaly in the cognitive core, Boss: {cog_err}"

        cog_ms = (time.time() - t_cog_start) * 1000.0
        total_duration_ms = (time.time() - start_time) * 1000.0

        # Telemetry & Performance Profiling
        print(
            f"[PERF] Total: {total_duration_ms:.1f}ms | "
            f"Cognition: {cognitive_state.telemetry.total_cognitive_ms:.1f}ms | "
            f"Understand: {cognitive_state.telemetry.understanding_ms:.1f}ms | "
            f"Reason: {cognitive_state.telemetry.reasoning_ms:.1f}ms | "
            f"Decide: {cognitive_state.telemetry.decision_ms:.1f}ms | "
            f"Plan: {cognitive_state.telemetry.planning_ms:.1f}ms | "
            f"Exec: {cognitive_state.telemetry.execution_ms:.1f}ms | "
            f"Verify: {cognitive_state.telemetry.verification_ms:.1f}ms"
        )

        final_text = cognitive_state.final_response
        obs_canonical = [
            CanonicalToolResult(
                tool=o.tool,
                success=o.success,
                stdout=o.stdout,
                stderr=o.stderr,
                output=o.output,
                exit_code=o.exit_code,
                action=o.action,
                artifact=o.artifacts[0] if o.artifacts else {}
            )
            for o in cognitive_state.observations
        ]

        # Step 3: Polish spoken response for TTS
        spoken_text = self.verifier.polish_response(final_text, obs_canonical)

        # Non-blocking voice playback in background thread
        try:
            import threading
            from core.cinematic_voice import stop_speaking, speak
            stop_speaking()
            threading.Thread(target=speak, args=(spoken_text,), daemon=True).start()
        except Exception as ve:
            print(f"[VOICE] Deferred voice output: {ve}")

        # PostgreSQL Audit Log
        try:
            from database.postgres_db import postgres_manager
            if postgres_manager.is_connected():
                used_tool_names = [o.tool for o in obs_canonical if o.action != "skip_redundant"]
                postgres_manager.log_command(
                    user_command=user_prompt,
                    response_text=spoken_text,
                    tools_used=used_tool_names,
                    latency_ms=total_duration_ms
                )
        except Exception:
            pass

        print(f"[DOOM CORE] [FINAL RESPONSE] {spoken_text}")
        return spoken_text

    def _synthesize_final_response(
        self,
        user_prompt: str,
        observations: List[CanonicalToolResult],
        plan: ExecutionPlan,
        last_llm_text: str,
        verification: Dict[str, Any]
    ) -> str:
        """
        V3.3 Truth-First Final Response Synthesis.
        DOOM MUST NEVER claim completion without verified ground-truth.
        Uses EXACT artifact paths from structured observations.
        Explicitly represents PARTIAL_SUCCESS, BLOCKED, and PAUSED states.
        Never concatenates raw tool execution strings.
        """
        # If no tools were called, return LLM response or fallback
        if not observations:
            return last_llm_text.strip() or "Standing by, Boss. No action was required."

        # Filter out skipped observations
        real_obs = [o for o in observations if o.action != "skip_redundant"]

        # Build structured action inventory from observations
        written_files = []      # Files successfully created
        executed_runs = []      # Execution attempts
        failed_actions = []     # Failed actions with reasons

        for o in real_obs:
            if o.success and o.action == "create_file":
                # Use EXACT canonical path from artifact — never reconstruct from LLM text
                fpath = o.artifact.get("relative_path") or o.artifact.get("path") if o.artifact else None
                if fpath:
                    written_files.append(fpath)
            elif o.action in ["execute_file", "execute_code"]:
                executed_runs.append(o)
            elif not o.success:
                failed_actions.append(o)

        # Semantic Deduplication of file mentions (preserve order, use exact canonical paths)
        unique_files = list(dict.fromkeys(written_files))

        # ── CASE 1: File created AND executed with output ────────────────────
        # Only claim "Done" if BOTH the write AND execution actually succeeded
        if unique_files and executed_runs:
            latest_run = executed_runs[-1]
            stdout_clean = latest_run.stdout.strip() if latest_run.stdout else ""
            file_ref = unique_files[0]  # EXACT artifact path

            if latest_run.success and stdout_clean:
                return (
                    f"Done. I created {file_ref}, executed it, and verified the result:\n\n"
                    f"{stdout_clean}\n\n"
                    f"The file passed verification."
                )
            elif latest_run.success and not stdout_clean:
                return f"Done. I created {file_ref} and ran it successfully (no output produced)."
            elif not latest_run.success:
                err_msg = (latest_run.stderr or latest_run.output or "unknown error").strip()
                return (
                    f"I created {file_ref}, but execution failed:\n\n"
                    f"{err_msg}\n\n"
                    f"The file exists on disk but could not be verified as executed."
                )

        # ── CASE 2: File created but NOT executed ────────────────────────────
        # V3.3 CRITICAL: Do NOT say "Done" or "Successfully created and verified"
        # when execution step was blocked or never attempted
        if unique_files and not executed_runs:
            file_ref = unique_files[0]
            verif_status = verification.get("status", "UNKNOWN")
            if verif_status == "COMPLETED":
                # File created and only creation was required
                return f"Done. I created and verified {file_ref} on disk."
            else:
                # PARTIAL: File created but execution/verification incomplete
                return (
                    f"Partially completed. I created {file_ref} on disk, "
                    f"but could not complete execution — no capable provider was available to run it. "
                    f"The task is paused and can be resumed when a provider is available."
                )

        # ── CASE 3: Execution without file creation (e.g. code snippet) ──────
        if executed_runs and not unique_files:
            latest_run = executed_runs[-1]
            stdout_clean = latest_run.stdout.strip() if latest_run.stdout else ""
            if latest_run.success and stdout_clean:
                return f"Done. Executed successfully:\n\n{stdout_clean}"
            elif not latest_run.success:
                err_msg = (latest_run.stderr or latest_run.output or "unknown error").strip()
                return f"Execution failed: {err_msg}"

        # ── CASE 4: Failed actions only ───────────────────────────────────────
        if failed_actions and not unique_files and not executed_runs:
            reasons = [f.stderr or f.output or f.error_type or "unknown" for f in failed_actions[:2]]
            return f"The task could not be completed. Reason: {'; '.join(reasons)}"

        # ── CASE 5: Mixed success/failure ─────────────────────────────────────
        if real_obs:
            succeeded = [o for o in real_obs if o.success]
            failed = [o for o in real_obs if not o.success]
            if succeeded and failed:
                return (
                    f"Partially completed. {len(succeeded)} action(s) succeeded, "
                    f"{len(failed)} could not be completed. "
                    f"The task checkpoint has been saved."
                )
            if succeeded:
                # No file or execution context - use clean output
                primary_outputs = [o.stdout or o.output for o in succeeded if o.output and not o.output.startswith("Successfully")]
                if primary_outputs:
                    return primary_outputs[-1].strip()

        # ── CASE 6: LLM conversational response (no tool-based side effects) ─
        if last_llm_text and not real_obs:
            return last_llm_text.strip()

        # ── CASE 7: Safe ambiguous fallback — never falsely claim COMPLETED ──
        verif_status = verification.get("status", "UNKNOWN")
        if verif_status == "COMPLETED":
            return "Goal verified and completed, Boss."
        elif verif_status == "PARTIAL_SUCCESS":
            return "Partially completed. Some steps succeeded but not all. The task checkpoint has been saved."
        else:
            return "The task could not be fully completed. The checkpoint has been saved for resumption."

    def _finalize_and_log(
        self,
        task: Any,
        user_prompt: str,
        final_text: str,
        observations: List[CanonicalToolResult],
        verification: Dict[str, Any],
        start_time: float,
        step_descriptions: List[str],
        perf: Dict[str, float],
        termination_reason: TerminationReason = TerminationReason.COMPLETED
    ) -> str:
        """Completes task, updates Memory 2.0, logs telemetry & PostgreSQL.
        Uses verification gates to determine true completion status."""
        spoken_text = self.verifier.polish_response(final_text, observations)
        duration_ms = (time.time() - start_time) * 1000.0
        perf["total_ms"] = duration_ms

        # Print detailed latency profile in Activity diagnostics
        print(
            f"[PERF] Plan: {perf.get('planning_ms', 0):.1f}ms | "
            f"Route: {perf.get('routing_ms', 0):.1f}ms | "
            f"LLM: {perf.get('llm_ms', 0):.1f}ms | "
            f"Tools: {perf.get('tool_ms', 0):.1f}ms | "
            f"Verify: {perf.get('verification_ms', 0):.1f}ms | "
            f"Synth: {perf.get('synthesis_ms', 0):.1f}ms | "
            f"Checkpoint: {perf.get('checkpoint_ms', 0):.1f}ms | "
            f"Total: {duration_ms:.1f}ms | "
            f"Termination: {termination_reason.value}"
        )

        # Non-blocking voice playback in background thread - with graceful TTS failure handling
        try:
            import threading
            from core.cinematic_voice import stop_speaking, speak
            stop_speaking()
            threading.Thread(target=speak, args=(spoken_text,), daemon=True).start()
        except Exception as ve:
            print(f"[VOICE] Deferred voice output: {ve}")

        # V3.3: Determine final response status based on verification gates
        final_response_status = self._determine_final_response_status(
            observations, verification, termination_reason
        )

        # Complete task in Task Engine with appropriate status
        try:
            used_tool_names = [o.tool for o in observations if o.action != "skip_redundant"]
            
            if final_response_status == FinalResponseStatus.SUCCESS:
                task_engine.complete_task(spoken_text, final_response_status, termination_reason.value)
            elif final_response_status == FinalResponseStatus.PARTIAL_SUCCESS:
                task_engine.complete_task_partial(spoken_text, termination_reason.value)
            elif final_response_status == FinalResponseStatus.BLOCKED:
                task_engine.pause_task(termination_reason.value)
            else:
                task_engine.fail_task(spoken_text)
        except Exception as te_err:
            print(f"[TASK ENGINE NOTICE] {te_err}")

        # Update Memory 2.0
        try:
            short_term_memory.add_assistant_turn(spoken_text, used_tool_names)
            episodic_memory.record_episode(
                goal=user_prompt,
                plan_steps=step_descriptions,
                tools_called=[{"name": o.tool, "action": o.action, "success": o.success} for o in observations],
                outcome=spoken_text,
                success=verification.get("verified", True)
            )
        except Exception:
            pass

        # PostgreSQL Audit Log
        try:
            from database.postgres_db import postgres_manager
            if postgres_manager.is_connected():
                postgres_manager.log_command(
                    user_command=user_prompt,
                    response_text=spoken_text,
                    tools_used=used_tool_names,
                    latency_ms=duration_ms
                )
        except Exception:
            pass

        try:
            print(f"[DOOM CORE] [FINAL RESPONSE] {spoken_text}")
        except Exception:
            pass
        return spoken_text

    def _determine_final_response_status(
        self,
        observations: List[CanonicalToolResult],
        verification: Dict[str, Any],
        termination_reason: TerminationReason
    ) -> FinalResponseStatus:
        """V3.3: Verification gates - determines true completion status from ground truth."""
        from tools.base import FinalResponseStatus
        
        # Check if all required steps completed successfully
        real_obs = [o for o in observations if o.action != "skip_redundant"]
        
        # Count expected vs completed steps
        expected_actions = set()
        completed_actions = set()
        failed_actions = set()
        
        for o in real_obs:
            if o.action in ("create_file", "execute_file", "execute_code"):
                expected_actions.add(o.action)
                if o.success:
                    completed_actions.add(o.action)
                else:
                    failed_actions.add(o.action)
        
        # Verification gate: all required verifications must pass
        verification_passed = verification.get("verified", False)
        verification_status = verification.get("status", "FAILED")
        
        # Termination reason gates
        if termination_reason == TerminationReason.COMPLETED:
            if verification_passed and verification_status == "COMPLETED" and not failed_actions:
                return FinalResponseStatus.SUCCESS
            elif verification_status == "PARTIAL_SUCCESS" or (completed_actions and failed_actions):
                return FinalResponseStatus.PARTIAL_SUCCESS
            elif failed_actions and not completed_actions:
                return FinalResponseStatus.FAILED
        
        if termination_reason == TerminationReason.TIMEOUT:
            return FinalResponseStatus.FAILED
        if termination_reason == TerminationReason.MAX_RETRIES_REACHED:
            return FinalResponseStatus.FAILED
        if termination_reason == TerminationReason.USER_APPROVAL_REQUIRED:
            return FinalResponseStatus.BLOCKED
        if termination_reason == TerminationReason.PARTIAL_COMPLETION:
            return FinalResponseStatus.PARTIAL_SUCCESS
        if termination_reason == TerminationReason.MAX_STEPS_REACHED:
            if completed_actions and not failed_actions:
                return FinalResponseStatus.PARTIAL_SUCCESS
            return FinalResponseStatus.FAILED
        if termination_reason == TerminationReason.UNRECOVERABLE_ERROR:
            return FinalResponseStatus.FAILED
        
        # Default fallback
        if verification_passed:
            return FinalResponseStatus.SUCCESS
        elif completed_actions or verification_status in ("PARTIAL_SUCCESS", "PARTIAL"):
            return FinalResponseStatus.PARTIAL_SUCCESS
        else:
            return FinalResponseStatus.FAILED


doom_core = DOOMCore()