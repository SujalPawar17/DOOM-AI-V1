import json
import time
from typing import Dict, Any, List, Optional
from core.context_manager import context_manager
from core.planner import planner, ExecutionPlan
from core.model_router import model_router
from core.tool_registry import tool_registry
from core.verifier import verifier
from memory import user_profile, short_term_memory, episodic_memory

class DOOMCore:
    """
    DOOM V2 Master Orchestrator
    Implements the full 8-step AI OS lifecycle:
    User Request -> Understand -> Classify -> Plan -> Model Router -> Execute Tools -> Verify -> Respond
    """
    def __init__(self):
        self.context_mgr = context_manager
        self.planner = planner
        self.router = model_router
        self.tools = tool_registry
        self.verifier = verifier

    def process_request(self, user_input: str, lang: Optional[str] = None) -> str:
        if not user_input or not user_input.strip():
            return "Standing by, Sujal."

        start_time = time.time()
        user_prompt = user_input.strip()
        print(f"\n[DOOM CORE] [*] Processing Goal: '{user_prompt}' (lang: {lang or 'auto'})")

        # Step 1: Record user turn in short-term memory
        short_term_memory.add_user_turn(user_prompt)

        # Step 2: Intent Classification & Goal Planning
        plan: ExecutionPlan = self.planner.classify_and_plan(user_prompt)
        print(f"[DOOM CORE] [PLAN] Task Type: {plan.task_type} ({len(plan.steps)} planned step(s))")

        # Step 3: Model Routing
        provider = self.router.route(plan.task_type)
        print(f"[DOOM CORE] [ROUTER] Routed Model: {provider.name}")

        # Step 4: Context Assembly
        system_prompt = self.context_mgr.build_system_prompt()
        schemas = self.tools.get_schemas()

        # Add language instruction to system prompt if specified
        if lang and lang != "en":
            lang_names = {
                "hi": "Hindi", "mr": "Marathi", "ta": "Tamil", "te": "Telugu",
                "kn": "Kannada", "ml": "Malayalam", "gu": "Gujarati", 
                "bn": "Bengali", "pa": "Punjabi", "ur": "Urdu"
            }
            lang_name = lang_names.get(lang, lang)
            system_prompt += f"\n\nIMPORTANT: Respond in {lang_name} language. Use native script if applicable."

        # Step 5: Model Generation & Tool Invocation
        llm_response = provider.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            tools=schemas
        )

        tools_executed: List[Dict[str, Any]] = []

        # Step 6: Tool Execution Loop (if tool calls requested)
        if llm_response.tool_calls:
            for tc in llm_response.tool_calls:
                tool_name = tc["name"]
                tool_args = tc["arguments"]
                print(f"[DOOM CORE] [TOOL] Executing Tool: '{tool_name}' with args {tool_args}")
                tool_res = self.tools.execute_tool(tool_name, tool_args)
                tools_executed.append({
                    "name": tool_name,
                    "args": tool_args,
                    "result": tool_res
                })

        # Step 7: Generate final response - use tool results directly for cleaner output
        if tools_executed:
            # Build response from tool results directly (more reliable than second LLM pass)
            tool_outputs = []
            for t in tools_executed:
                if t['result'].success and t['result'].output:
                    tool_outputs.append(t['result'].output)
            if tool_outputs:
                final_text = " ".join(tool_outputs)
                # Skip verifier for tool-based responses to preserve exact tool output
            else:
                final_text = self.verifier.polish_response(llm_response.text, tools_executed)
        else:
            final_text = self.verifier.polish_response(llm_response.text, tools_executed)
        
        # Stop any ongoing speech from tool execution before responding
        from core.cinematic_voice import stop_speaking
        stop_speaking()

        # Step 8: Update Episodic & Short-Term Memory
        used_tool_names = [t["name"] for t in tools_executed]
        short_term_memory.add_assistant_turn(final_text, used_tool_names)
        
        episodic_memory.record_episode(
            goal=user_prompt,
            plan_steps=[s.description for s in plan.steps],
            tools_called=[{"name": t["name"], "args": t["args"]} for t in tools_executed],
            outcome=final_text,
            success=True
        )

        # Step 9: Audit Log to PostgreSQL Database
        try:
            latency_ms = (time.time() - start_time) * 1000.0
            from database.postgres_db import postgres_manager
            postgres_manager.log_command(
                user_command=user_prompt,
                response_text=final_text,
                tools_used=used_tool_names,
                latency_ms=latency_ms
            )
        except Exception:
            pass

        print(f"[DOOM CORE] [RESPONSE] {final_text.encode('ascii', 'replace').decode('ascii')}")
        return final_text

# Global DOOM Core instance
doom_core = DOOMCore()
