import os
import sys
import time
import subprocess
import traceback
from tools.base import BaseTool, ToolResult

class WriteScriptTool(BaseTool):
    name = "coding_write_script"
    description = "Generates and saves a Python script file to the scripts/ folder"
    permission_level = "moderate"
    parameters = {
        "type": "object",
        "properties": {
            "file_name": {
                "type": "string",
                "description": "Script file name (e.g. 'snake_game.py', 'prime_calculator.py')"
            },
            "code": {
                "type": "string",
                "description": "Full working Python code content"
            }
        },
        "required": ["file_name", "code"]
    }

    def execute(self, file_name: str, code: str, **kwargs) -> ToolResult:
        try:
            os.makedirs("scripts", exist_ok=True)
            if not file_name.endswith(".py"):
                file_name += ".py"
            target_path = os.path.join("scripts", os.path.basename(file_name))
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(code)
            return ToolResult(
                success=True,
                output=f"Successfully generated and saved script to {target_path}",
                data={"path": target_path, "lines": len(code.splitlines())}
            )
        except Exception as e:
            return ToolResult(success=False, output=f"Error creating script: {e}", error=str(e))

class RunPythonTool(BaseTool):
    name = "coding_run_python"
    description = "Executes Python code or a script file in an isolated process and captures output"
    permission_level = "moderate"
    parameters = {
        "type": "object",
        "properties": {
            "code_or_file": {
                "type": "string",
                "description": "Python snippet string OR path to a .py script file to execute"
            },
            "timeout": {
                "type": "integer",
                "description": "Execution timeout in seconds (default 15)"
            }
        },
        "required": ["code_or_file"]
    }

    def execute(self, code_or_file: str, timeout: int = 15, **kwargs) -> ToolResult:
        try:
            # Check if it's an existing file
            if os.path.exists(code_or_file) and code_or_file.endswith(".py"):
                res = subprocess.run([sys.executable, code_or_file], capture_output=True, text=True, timeout=timeout)
                out = res.stdout if res.stdout else res.stderr
                return ToolResult(
                    success=(res.returncode == 0),
                    output=out.strip() or "Script executed successfully with no output.",
                    data={"returncode": res.returncode, "stdout": res.stdout, "stderr": res.stderr}
                )
            else:
                # Direct code execution via python -c
                res = subprocess.run([sys.executable, "-c", code_or_file], capture_output=True, text=True, timeout=timeout)
                out = res.stdout if res.stdout else res.stderr
                return ToolResult(
                    success=(res.returncode == 0),
                    output=out.strip() or "Code executed successfully.",
                    data={"returncode": res.returncode, "stdout": res.stdout, "stderr": res.stderr}
                )
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, output=f"Execution timed out after {timeout} seconds.", error="TimeoutExpired")
        except Exception as e:
            return ToolResult(success=False, output=f"Execution failed: {e}", error=str(e))
