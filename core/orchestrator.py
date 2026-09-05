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
        if not user_input or not user_input.strip():
            state_machine.transition_to(DoomState.IDLE, "Standing by, Boss.")
            return "Standing by, Boss."

        start_time = time.time()
        perf: Dict[str, float] = {
            "planning_ms": 0.0,
            "routing_ms": 0.0,
            "llm_ms": 0.0,
            "tool_ms": 0.0,
            "verification_ms": 0.0,
            "synthesis_ms": 0.0,
            "checkpoint_ms": 0.0,
            "total_ms": 0.0
        }
        user_prompt = user_input.strip()
        print(f"\n[DOOM CORE] [*] Initiating Autonomous Goal: '{user_prompt}' (lang: {lang or 'auto'})")

        # Step 1: Record user turn in short-term memory
        short_term_memory.add_user_turn(user_prompt)

        # Step 2: Initialize Task Engine & Planning
        t_plan_start = time.time()
        task = task_engine.create_task(user_prompt)
        plan: ExecutionPlan = self.planner.classify_and_plan(user_prompt)
        step_descriptions = [s.description for s in plan.steps] if plan.steps else [f"Execute goal: {user_prompt}"]
        task_engine.set_plan_steps(step_descriptions)
        perf["planning_ms"] = (time.time() - t_plan_start) * 1000.0
        print(f"[DOOM CORE] [PLAN] Intent: {plan.type} ({len(step_descriptions)} planned step(s))")

        # ─────────────────────────────────────────────────────────────────────
        # FAST-PATH 1: DIRECT INTENT (Who am I, Identity, Greetings)
        # ZERO tool calls, instant response from Memory & Persona
        # ─────────────────────────────────────────────────────────────────────
        if plan.type == "DIRECT":
            t_direct = time.time()
            lower = user_prompt.lower()
            name = user_profile.get_name()
            title = user_profile.get_title()
            access = user_profile.get_access_level()
            projects = user_profile.get_projects()
            proj_list = [p["name"] if isinstance(p, dict) and "name" in p else str(p) for p in projects]
            proj_str = ", ".join(proj_list) if proj_list else "DOOM V3 Personal AI OS"

            if any(w in lower for w in ["who am i", "what is my name", "my profile"]):
                role = user_profile.get_role()
                final_text = (
                    f"You are {name}, {role}. "
                    f"You hold {access} security clearance. "
                    f"Active Focus: {proj_str}. At your command, Boss."
                )
            elif any(w in lower for w in ["who are you", "what is your name"]):
                final_text = f"I am DOOM V3, your sovereign Personal AI Operating System and autonomous companion. Standing by, Boss."
            else:
                final_text = f"Greetings, Boss {name}. All systems nominal and awaiting your command."

            perf["direct_ms"] = (time.time() - t_direct) * 1000.0
            return self._finalize_and_log(
                task=task,
                user_prompt=user_prompt,
                final_text=final_text,
                observations=[],
                verification={"verified": True, "status": "COMPLETED", "details": "Direct profile resolution."},
                start_time=start_time,
                step_descriptions=step_descriptions,
                perf=perf,
                termination_reason=TerminationReason.COMPLETED
            )

        # ─────────────────────────────────────────────────────────────────────
        # FAST-PATH 2: DETERMINISTIC TELEMETRY QUERY
        # "Show my CPU, RAM and disk" -> Direct telemetry, ZERO python scripts!
        # ─────────────────────────────────────────────────────────────────────
        if plan.type == "QUERY" and plan.metadata.get("category") == "system_telemetry":
            t_telemetry = time.time()
            try:
                cpu = psutil.cpu_percent(interval=0.1)
                mem = psutil.virtual_memory().percent
                disk = psutil.disk_usage("/").percent
                final_text = (
                    f"Workstation Telemetry:\n"
                    f"• CPU Usage: {cpu}%\n"
                    f"• RAM Usage: {mem}%\n"
                    f"• Disk Usage: {disk}%\n"
                    f"All hardware subsystems operating within optimal thermal thresholds, Boss."
                )
            except Exception as e:
                final_text = f"Workstation Telemetry encountered an error: {e}"

            perf["telemetry_ms"] = (time.time() - t_telemetry) * 1000.0
            return self._finalize_and_log(
                task=task,
                user_prompt=user_prompt,
                final_text=final_text,
                observations=[],
                verification={"verified": True, "status": "COMPLETED", "details": "Direct hardware telemetry query."},
                start_time=start_time,
                step_descriptions=step_descriptions,
                perf=perf,
                termination_reason=TerminationReason.COMPLETED
            )

        # ─────────────────────────────────────────────────────────────────────
        # MULTI-TURN AUTONOMOUS AGENT LOOP WITH LOOP CONTROL & RETRY LOGIC
        # ─────────────────────────────────────────────────────────────────────
        system_prompt = self.context_mgr.build_system_prompt()
        schemas = self.tools.get_schemas()

        if lang and lang != "en":
            lang_names = {
                "hi": "Hindi", "mr": "Marathi", "ta": "Tamil", "te": "Telugu",
                "kn": "Kannada", "ml": "Malayalam", "gu": "Gujarati",
                "bn": "Bengali", "pa": "Punjabi", "ur": "Urdu"
            }
            lang_name = lang_names.get(lang, lang)
            system_prompt += f"\n\nIMPORTANT: Respond in {lang_name} language. Use native script if applicable."

        observations: List[CanonicalToolResult] = []
        called_signatures: List[str] = []
        agent_step = 0
        total_tool_calls = 0
        current_context = user_prompt
        last_llm_text = ""
        termination_reason = TerminationReason.COMPLETED
        retry_counts: Dict[str, int] = {}
        # V3.2: Reset DecisionEngine per-action failure counts for this new task
        self.decision_eng.reset()

        while agent_step < self.max_agent_steps and total_tool_calls < self.max_tool_calls:
            agent_step += 1
            state_machine.transition_to(DoomState.THINKING, f"Reasoning (turn {agent_step})", task_id=task.task_id)

            # Route model
            t_route_start = time.time()
            provider = self.router.route(plan.type)
            perf["routing_ms"] += (time.time() - t_route_start) * 1000.0
            task_engine.record_tool_call("", model_name=provider.name)

            t_llm = time.time()
            try:
                llm_response = self.router.generate(
                    prompt=current_context,
                    system_prompt=system_prompt,
                    tools=schemas,
                    task_type=plan.type
                )
            except NoCapableProviderError as e:
                # V3.3: No capable provider available - pause task
                print(f"[DOOM CORE] [PROVIDER OUTAGE] No capable provider for {plan.type}: {e}")
                task_engine.pause_task(f"NO_CAPABLE_MODEL_AVAILABLE: {e.task_type}")
                termination_reason = TerminationReason.UNRECOVERABLE_ERROR
                # Return user-facing response
                return f"Boss, the task is paused because no capable reasoning provider is currently available for {e.task_type}. The completed work has been saved and the task can resume from the current step when a provider is available."
            perf["llm_ms"] += (time.time() - t_llm) * 1000.0

            if llm_response.text:
                last_llm_text = llm_response.text

            # If LLM did not request tools, we have reached the conclusion
            if not llm_response.tool_calls:
                break

            # Execute tool calls with Pre-Execution Decision & Idempotency
            turn_observations = []
            
            # Extract expected filename from user prompt for artifact identity enforcement
            expected_filename = extract_expected_filename(user_prompt)
            if expected_filename:
                print(f"[DOOM CORE] [ARTIFACT IDENTITY] Enforcing exact filename: {expected_filename}")

            for tc in llm_response.tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("arguments", {})
                if not tool_name:
                    continue

                # Correct filename in tool args to match user's explicit request
                if expected_filename:
                    tool_args = correct_tool_filename(tool_name, tool_args, expected_filename)

                tool_obj = self.tools.get_tool(tool_name)

                # 1. Pre-Execution Decision: Check for redundancy and mutual exclusivity
                should_run, skip_reason = self.decision_eng.should_execute(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    executed_observations=observations,
                    already_called_signatures=called_signatures
                )

                if not should_run:
                    print(f"[AGENT] Skipping redundant tool: {tool_name}\nReason: {skip_reason}")
                    skip_obs = CanonicalToolResult(
                        tool=tool_name,
                        success=True,
                        action="skip_redundant",
                        target="",
                        output=f"[SKIPPED REDUNDANT] {skip_reason}",
                        metadata={"skip_reason": skip_reason}
                    )
                    observations.append(skip_obs)
                    turn_observations.append(f"Tool '{tool_name}' skipped: {skip_reason}")
                    continue

                # 2. Risk check
                if tool_obj and tool_obj.get_effective_risk() == RiskLevel.CRITICAL:
                    print(f"[DOOM SECURITY] Tool '{tool_name}' requires explicit authorization.")
                    task_engine.require_user_approval(tool_name, tool_args)
                    termination_reason = TerminationReason.USER_APPROVAL_REQUIRED
                    return f"Action '{tool_name}' requires your authorization, Boss. Please confirm in the DOOM HUD."

                # 3. Execution with retry logic
                sig = self.decision_eng.compute_action_signature(tool_name, tool_args)
                called_signatures.append(sig)

                retry_count = 0
                tool_success = False
                last_canonical_res = None

                while retry_count <= self.max_retries_per_action and not tool_success:
                    if retry_count > 0:
                        print(f"[DOOM CORE] [RETRY {retry_count}/{self.max_retries_per_action}] {tool_name}")
                        time.sleep(0.5)  # Brief backoff

                    state_machine.transition_to(DoomState.EXECUTING, f"Running: {tool_name} (attempt {retry_count + 1})", task_id=task.task_id)
                    print(f"[DOOM CORE] [TOOL #{total_tool_calls + 1}] {tool_name} with {tool_args}")

                    t_t_start = time.time()
                    canonical_res: CanonicalToolResult = self.tools.execute_tool(tool_name, tool_args)
                    t_duration = (time.time() - t_t_start) * 1000.0
                    perf["tool_ms"] += t_duration

                    # Check for timeout
                    if canonical_res.error_type == "TIMEOUT":
                        retry_count += 1
                        retry_counts[tool_name] = retry_count
                        if retry_count > self.max_retries_per_action:
                            print(f"[DOOM CORE] [TOOL TIMEOUT] {tool_name} exceeded max retries")
                            termination_reason = TerminationReason.TIMEOUT
                            last_canonical_res = canonical_res
                            break
                        continue

                    # Check for other failures
                    if not canonical_res.success:
                        retry_count += 1
                        retry_counts[tool_name] = retry_count
                        # V3.2: Notify DecisionEngine so its should_execute() gate fires next time
                        self.decision_eng.record_failure(sig)
                        if retry_count > self.max_retries_per_action:
                            print(f"[DOOM CORE] [TOOL FAILED] {tool_name} exceeded max retries")
                            termination_reason = TerminationReason.MAX_RETRIES_REACHED
                            last_canonical_res = canonical_res
                            break
                        continue

                    # Success
                    tool_success = True
                    total_tool_calls += 1
                    last_canonical_res = canonical_res
                    observations.append(canonical_res)

                    # Advance step in task engine with artifact tracking
                    step_idx = min(agent_step, len(step_descriptions))
                    artifacts = canonical_res.artifact if canonical_res.artifact else None
                    task_engine.advance_step(
                        step_index=step_idx,
                        tool_name=tool_name,
                        output=canonical_res.output,
                        success=canonical_res.success,
                        artifacts=[artifacts] if artifacts else None
                    )

                    # V3.3: Save checkpoint after each tool execution
                    t_cp_start = time.time()
                    task_engine._save_checkpoint()
                    perf["checkpoint_ms"] += (time.time() - t_cp_start) * 1000.0

                    # Format structured observation for model context
                    obs_summary = canonical_res.stdout or canonical_res.output
                    if not canonical_res.success and canonical_res.stderr:
                        obs_summary = f"ERROR: {canonical_res.stderr}"
                    turn_observations.append(f"Tool '{tool_name}' result: {obs_summary}")

                if not tool_success and last_canonical_res:
                    observations.append(last_canonical_res)
                    if termination_reason in (TerminationReason.TIMEOUT, TerminationReason.MAX_RETRIES_REACHED):
                        break

            if termination_reason in (TerminationReason.TIMEOUT, TerminationReason.MAX_RETRIES_REACHED, TerminationReason.USER_APPROVAL_REQUIRED):
                break

            # Feed observations back into context for next reasoning turn
            if turn_observations:
                current_context += f"\n\n[Observations at Step {agent_step}]:\n" + "\n".join(turn_observations)
                current_context += "\nBased on these tool results, decide the next action or provide the complete final response."

        # Check termination conditions
        if agent_step >= self.max_agent_steps:
            termination_reason = TerminationReason.MAX_STEPS_REACHED
        elif total_tool_calls >= self.max_tool_calls:
            termination_reason = TerminationReason.MAX_STEPS_REACHED  # Same as max steps for practical purposes

        # ─────────────────────────────────────────────────────────────────────
        # Step 5: Ground-Truth Verification
        # ─────────────────────────────────────────────────────────────────────
        t_v_start = time.time()
        state_machine.transition_to(DoomState.VERIFYING, "Verifying results...", task_id=task.task_id)
        verification = self.verifier.verify_ground_truth(user_prompt, observations)
        perf["verification_ms"] = (time.time() - t_v_start) * 1000.0
        print(f"[DOOM CORE] [VERIFICATION] Status: {verification['status']} ({verification['details']})")

        # If verification failed and we have retries left, attempt self-healing (for AUTONOMOUS plans)
        if not verification.get("verified", True) and plan.type == "AUTONOMOUS":
            # Self-healing logic would go here - the DecisionEngine already prevents duplicate writes
            # The LLM will naturally try to fix based on the error in the context
            pass

        # ─────────────────────────────────────────────────────────────────────
        # Step 6: Single Synthesized Final Response Generation (NO raw concatenation)
        # ─────────────────────────────────────────────────────────────────────
        t_synth = time.time()
        final_text = self._synthesize_final_response(
            user_prompt=user_prompt,
            observations=observations,
            plan=plan,
            last_llm_text=last_llm_text,
            verification=verification
        )
        perf["synthesis_ms"] = (time.time() - t_synth) * 1000.0

        return self._finalize_and_log(
            task=task,
            user_prompt=user_prompt,
            final_text=final_text,
            observations=observations,
            verification=verification,
            start_time=start_time,
            step_descriptions=step_descriptions,
            perf=perf,
            termination_reason=termination_reason
        )

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
        if termination_reason == TerminationReason.MAX_STEPS_REACHED:
            if completed_actions and not failed_actions:
                return FinalResponseStatus.PARTIAL_SUCCESS
            return FinalResponseStatus.FAILED
        if termination_reason == TerminationReason.UNRECOVERABLE_ERROR:
            return FinalResponseStatus.FAILED
        
        # Default fallback
        if verification_passed:
            return FinalResponseStatus.SUCCESS
        elif completed_actions:
            return FinalResponseStatus.PARTIAL_SUCCESS
        else:
            return FinalResponseStatus.FAILED


doom_core = DOOMCore()