import os
import sys
import json
import subprocess
import io
import contextlib
import traceback
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Try to import groq or openai
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

class DOOMJarvisAgent:
    """Master Autonomous Agent capable of dynamic code execution, web research, OS automation and tool use"""
    def __init__(self):
        self.groq_key = os.getenv("GROQ_API_KEY", "")
        self.openai_key = os.getenv("OPENAI_API_KEY", "")
        self.groq_client = Groq(api_key=self.groq_key) if (GROQ_AVAILABLE and self.groq_key) else None
        self.conversation_memory = []
        
    def _execute_python_tool(self, code: str) -> str:
        """Dynamic Python Code Interpreter Tool"""
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        
        # Local execution sandbox environment
        local_scope = {
            "datetime": datetime,
            "os": os,
            "sys": sys,
            "math": __import__("math"),
            "json": json
        }
        
        try:
            with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                exec(code, local_scope)
            output = stdout_capture.getvalue()
            error = stderr_capture.getvalue()
            if error:
                return f"[Output]: {output}\n[Error]: {error}"
            return output if output else "[Code executed successfully with no output]"
        except Exception as e:
            return f"[Execution Error]: {traceback.format_exc()}"

    def _execute_shell_tool(self, command: str) -> str:
        """PowerShell / System Shell Execution Tool"""
        try:
            res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
            out = res.stdout.strip()
            err = res.stderr.strip()
            if res.returncode == 0:
                return out if out else "[Command completed successfully]"
            return f"[Command Error (Code {res.returncode})]: {err}\n{out}"
        except Exception as e:
            return f"[Shell Error]: {str(e)}"

    def _web_search_tool(self, query: str) -> str:
        """Deep Web Search & Summary Tool"""
        try:
            from core.web_search import search_web
            return search_web(query)
        except Exception as e:
            return f"[Search Error]: {str(e)}"

    def _file_manager_tool(self, action: str, filepath: str, content: str = "") -> str:
        """Read, Write, List, and Inspect Workspace Files"""
        try:
            if action == "read":
                if os.path.exists(filepath):
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        return f.read()[:4000]
                return f"File {filepath} does not exist."
            elif action == "write":
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)
                return f"File {filepath} written successfully ({len(content)} bytes)."
            elif action == "list":
                target = filepath if filepath else "."
                files = os.listdir(target)
                return f"Contents of {target}: {', '.join(files[:30])}"
            return f"Unknown file action: {action}"
        except Exception as e:
            return f"[File Error]: {str(e)}"

    def _gui_action_tool(self, action: str, target: str = "") -> str:
        """GUI desktop interaction tool"""
        try:
            from core.advanced_automation import open_app, take_screenshot, control_media
            if action == "open":
                return open_app(target)
            elif action == "screenshot":
                return take_screenshot()
            elif action == "media":
                return control_media(target)
            return f"Unknown GUI action {action}"
        except Exception as e:
            return f"[GUI Error]: {str(e)}"

    def execute_prompt(self, user_prompt: str) -> str:
        """Autonomous tool-calling decision and execution loop"""
        system_prompt = """You are DOOM, Sujal's hyper-intelligent, all-powerful personal AI assistant like JARVIS from Iron Man.
You are equipped with real tools to accomplish ANY request:
1. Python Code Execution: Run any Python code to calculate, manipulate data, create files, sort, plot, or automate.
2. System Shell Commands: Execute PowerShell / CMD commands.
3. Web Research: Search and summarize web information.
4. File Management: Read and write files.
5. Desktop GUI Control: Launch apps, control media, take screenshots.

If the user request requires calculation, file work, automation, or custom scripting, respond with a JSON Tool Call:
```json
{
  "tool": "python" | "shell" | "search" | "file" | "gui",
  "args": {
    "code": "...",
    "command": "...",
    "query": "...",
    "action": "read|write|list",
    "filepath": "...",
    "content": "..."
  }
}
```
Otherwise, answer directly with JARVIS wit, speed, and precision, always addressing Sujal respectfully."""

        # 1. Try Groq API (Ultra-Fast LLaMA 3.3 70B)
        if self.groq_client:
            try:
                response = self.groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.6,
                    max_tokens=600
                )
                raw_text = response.choices[0].message.content.strip()
                return self._handle_tool_or_text(raw_text, user_prompt, self.groq_client)
            except Exception as e:
                print(f"[NOTE] Groq API note: {e}")

        # 2. Try OpenAI GPT-4o if available
        if self.openai_key and OPENAI_AVAILABLE:
            try:
                client = openai.OpenAI(api_key=self.openai_key)
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.6,
                    max_tokens=600
                )
                raw_text = response.choices[0].message.content.strip()
                return self._handle_tool_or_text(raw_text, user_prompt, client)
            except Exception as e:
                print(f"[NOTE] OpenAI API note: {e}")

        # 3. Try Local Ollama Brain
        try:
            from core.ollama_brain import check_ollama_status, get_ai_response
            if check_ollama_status():
                return get_ai_response(user_prompt)
        except Exception:
            pass

        # 4. Fallback to Enhanced Offline Brain
        from core.ai_brain_enhanced import DOOMBrainEnhanced
        brain = DOOMBrainEnhanced()
        return brain.think(user_prompt)

    def _handle_tool_or_text(self, raw_text: str, original_prompt: str, client: Any) -> str:
        """Parse potential tool call JSON and execute it"""
        if "```json" in raw_text and "tool" in raw_text:
            try:
                json_str = raw_text.split("```json")[1].split("```")[0].strip()
                tool_data = json.loads(json_str)
                tool_name = tool_data.get("tool")
                args = tool_data.get("args", {})

                result = ""
                if tool_name == "python":
                    result = self._execute_python_tool(args.get("code", ""))
                elif tool_name == "shell":
                    result = self._execute_shell_tool(args.get("command", ""))
                elif tool_name == "search":
                    result = self._web_search_tool(args.get("query", ""))
                elif tool_name == "file":
                    result = self._file_manager_tool(args.get("action", ""), args.get("filepath", ""), args.get("content", ""))
                elif tool_name == "gui":
                    result = self._gui_action_tool(args.get("action", ""), args.get("target", ""))
                else:
                    return raw_text

                # Synthesize final answer using tool result
                follow_up_prompt = f"User Request: {original_prompt}\nTool Executed: {tool_name}\nTool Output: {result}\n\nDeliver the final answer to Sujal concisely in JARVIS persona."
                if hasattr(client, "chat"):
                    final_res = client.chat.completions.create(
                        model="llama-3.3-70b-versatile" if hasattr(client, "base_url") and "groq" in str(client.base_url) else "gpt-4o",
                        messages=[{"role": "user", "content": follow_up_prompt}],
                        max_tokens=400
                    )
                    return final_res.choices[0].message.content.strip()
                return f"Task completed, Sujal: {result}"
            except Exception as e:
                return raw_text
        return raw_text

# Global agent instance
jarvis_agent = DOOMJarvisAgent()

def execute_agent_prompt(prompt: str) -> str:
    return jarvis_agent.execute_prompt(prompt)
