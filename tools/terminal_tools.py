import subprocess
import shlex
import time
from tools.base import BaseTool, ToolResult


class ExecuteTerminalCommandTool(BaseTool):
    name = "terminal_execute"
    description = "Executes a safe PowerShell or Command Prompt command on Windows and captures stdout/stderr"
    permission_level = "moderate"
    timeout = 15
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to run (e.g., 'dir', 'python test_doom.py', 'git status')"
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 15)"
            }
        },
        "required": ["command"]
    }

    # Block destructive commands without confirmation
    FORBIDDEN_KEYWORDS = ["format ", "del /f /s /q c:", "rmdir /s /q c:", "diskpart"]

    def _execute_impl(self, command: str, timeout: int = 15, **kwargs) -> ToolResult:
        start_t = time.time()
        cmd_lower = command.lower().strip()
        for forbidden in self.FORBIDDEN_KEYWORDS:
            if forbidden in cmd_lower:
                return ToolResult(success=False, output="Command blocked by DOOM Security Layer for safety.", error="SecurityViolation", action="execute_terminal", duration_ms=(time.time() - start_t) * 1000, exit_code=-1, target=command)

        try:
            res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
            duration = (time.time() - start_t) * 1000
            out = res.stdout if res.stdout else res.stderr
            return ToolResult(
                success=(res.returncode == 0),
                output=out.strip() or f"Command executed (Return code: {res.returncode})",
                action="execute_terminal",
                artifact={"command": command, "returncode": res.returncode},
                stdout=res.stdout or "",
                stderr=res.stderr or "",
                duration_ms=duration,
                exit_code=res.returncode,
                target=command,
                data={"returncode": res.returncode, "stdout": res.stdout, "stderr": res.stderr}
            )
        except subprocess.TimeoutExpired:
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=False, output=f"Command timed out after {timeout} seconds.", error="TIMEOUT", action="execute_terminal", duration_ms=duration, exit_code=-1, target=command)
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=False, output=f"Execution error: {e}", error=str(e), action="execute_terminal", duration_ms=duration, exit_code=-1, target=command)
