import os
import sys
import time
import subprocess
import traceback
from typing import Dict, Any, List
from tools.base import BaseTool, ToolResult, RiskLevel
from core.path_resolver import canonical_path


class WriteScriptTool(BaseTool):
    name = "coding_write_script"
    description = "Generates and saves a Python script file to disk (supports Desktop or scripts folder)"
    permission_level = "moderate"
    risk_level = RiskLevel.MEDIUM
    timeout = 10

    purpose = "Create a Python source file at target location"
    category = "coding"
    side_effects = ["create_file", "write_disk"]
    when_to_use = "When user specifically asks to create, write, or generate a Python script file"
    do_not_use_when = "A simple existing system telemetry tool can answer the request without creating code"
    mutually_exclusive_with = ["filesystem_write_file"]

    parameters = {
        "type": "object",
        "properties": {
            "file_name": {
                "type": "string",
                "description": "Script file path or name (e.g. 'Desktop/system_info.py', 'snake_game.py')"
            },
            "code": {
                "type": "string",
                "description": "Full working Python code content"
            }
        },
        "required": ["file_name", "code"]
    }

    def _execute_impl(self, file_name: str, code: str, **kwargs) -> ToolResult:
        start_t = time.time()
        try:
            raw = file_name.strip()
            if not raw.endswith(".py"):
                raw += ".py"

            # Use canonical path resolver. If bare filename, default to scripts/
            has_dir = os.sep in raw or "/" in raw or "\\" in raw
            default_dir = None if has_dir else os.path.join(os.getcwd(), "scripts")
            cpath = canonical_path(raw, default_dir=default_dir)

            os.makedirs(os.path.dirname(cpath.absolute_path), exist_ok=True)
            with open(cpath.absolute_path, "w", encoding="utf-8") as f:
                f.write(code)

            duration = (time.time() - start_t) * 1000
            file_bytes = len(code.encode("utf-8"))

            artifact = {
                "path": cpath.absolute_path,
                "relative_path": cpath.relative_path,
                "name": cpath.filename,
                "size_bytes": file_bytes,
                "exists": True,
                "lines": len(code.splitlines())
            }

            return ToolResult(
                success=True,
                output=f"Successfully generated and saved script to {cpath.relative_path}",
                action="create_file",
                artifact=artifact,
                stdout="",
                stderr="",
                duration_ms=duration,
                exit_code=0,
                target=cpath.absolute_path,
                data=artifact
            )
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(
                success=False,
                output=f"Error creating script: {e}",
                action="create_file",
                error=str(e),
                duration_ms=duration,
                exit_code=-1,
                target=file_name
            )


class RunPythonTool(BaseTool):
    name = "coding_run_python"
    description = "Executes Python code or a script file in an isolated process and captures output"
    permission_level = "moderate"
    risk_level = RiskLevel.MEDIUM
    timeout = 30

    purpose = "Executes a Python script or inline snippet and captures execution output"
    category = "coding"
    side_effects = ["run_process"]
    when_to_use = "When user requests running or testing a Python file or script"
    do_not_use_when = "Request is purely to read or display code without running it"

    parameters = {
        "type": "object",
        "properties": {
            "code_or_file": {
                "type": "string",
                "description": "Path to a .py script file (e.g. 'Desktop/system_info.py') OR Python snippet string"
            },
            "timeout": {
                "type": "integer",
                "description": "Execution timeout in seconds (default 15)"
            }
        },
        "required": ["code_or_file"]
    }

    def _execute_impl(self, code_or_file: str, timeout: int = 15, **kwargs) -> ToolResult:
        start_t = time.time()
        try:
            target = code_or_file.strip()
            cpath = canonical_path(target)

            # Check if canonical path exists or target file exists
            if cpath.exists and cpath.absolute_path.endswith(".py"):
                res = subprocess.run(
                    [sys.executable, cpath.absolute_path],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=os.path.dirname(cpath.absolute_path)
                )
                duration = (time.time() - start_t) * 1000
                stdout = res.stdout.strip() if res.stdout else ""
                stderr = res.stderr.strip() if res.stderr else ""
                out = stdout or stderr or "Script executed successfully with no output."
                
                artifact = {
                    "path": cpath.absolute_path,
                    "relative_path": cpath.relative_path,
                    "name": cpath.filename,
                    "size_bytes": os.path.getsize(cpath.absolute_path),
                    "exists": True,
                    "returncode": res.returncode
                }

                return ToolResult(
                    success=(res.returncode == 0),
                    output=out,
                    action="execute_file",
                    artifact=artifact,
                    stdout=stdout,
                    stderr=stderr,
                    duration_ms=duration,
                    exit_code=res.returncode,
                    target=cpath.absolute_path,
                    data=artifact
                )
            elif os.path.exists(target) and target.endswith(".py"):
                res = subprocess.run([sys.executable, target], capture_output=True, text=True, timeout=timeout)
                duration = (time.time() - start_t) * 1000
                stdout = res.stdout.strip() if res.stdout else ""
                stderr = res.stderr.strip() if res.stderr else ""
                out = stdout or stderr or "Script executed successfully with no output."
                
                artifact = {
                    "path": os.path.abspath(target),
                    "name": os.path.basename(target),
                    "size_bytes": os.path.getsize(target),
                    "exists": True,
                    "returncode": res.returncode
                }

                return ToolResult(
                    success=(res.returncode == 0),
                    output=out,
                    action="execute_file",
                    artifact=artifact,
                    stdout=stdout,
                    stderr=stderr,
                    duration_ms=duration,
                    exit_code=res.returncode,
                    target=os.path.abspath(target),
                    data=artifact
                )
            else:
                # Direct code execution via python -c
                res = subprocess.run([sys.executable, "-c", target], capture_output=True, text=True, timeout=timeout)
                duration = (time.time() - start_t) * 1000
                stdout = res.stdout.strip() if res.stdout else ""
                stderr = res.stderr.strip() if res.stderr else ""
                out = stdout or stderr or "Code executed successfully."
                
                return ToolResult(
                    success=(res.returncode == 0),
                    output=out,
                    action="execute_code",
                    artifact={},
                    stdout=stdout,
                    stderr=stderr,
                    duration_ms=duration,
                    exit_code=res.returncode,
                    target="inline_code",
                    data={"returncode": res.returncode, "stdout": stdout, "stderr": stderr}
                )
        except subprocess.TimeoutExpired:
            duration = (time.time() - start_t) * 1000
            return ToolResult(
                success=False,
                output=f"Execution timed out after {timeout} seconds.",
                action="execute_code",
                error="TIMEOUT",
                duration_ms=duration,
                exit_code=-1,
                target=code_or_file
            )
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(
                success=False,
                output=f"Execution failed: {e}",
                action="execute_code",
                error=str(e),
                duration_ms=duration,
                exit_code=-1,
                target=code_or_file
            )
