import subprocess
import shlex
from tools.base import BaseTool, ToolResult

class ExecuteTerminalCommandTool(BaseTool):
    name = "terminal_execute"
    description = "Executes a safe PowerShell or Command Prompt command on Windows and captures stdout/stderr"
    permission_level = "moderate"
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

    def execute(self, command: str, timeout: int = 15, **kwargs) -> ToolResult:
        cmd_lower = command.lower().strip()
        for forbidden in self.FORBIDDEN_KEYWORDS:
            if forbidden in cmd_lower:
                return ToolResult(success=False, output="Command blocked by DOOM Security Layer for safety.", error="SecurityViolation")

        try:
            res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
            out = res.stdout if res.stdout else res.stderr
            return ToolResult(
                success=(res.returncode == 0),
                output=out.strip() or f"Command executed (Return code: {res.returncode})",
                data={"returncode": res.returncode, "stdout": res.stdout, "stderr": res.stderr}
            )
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, output=f"Command timed out after {timeout} seconds.", error="TimeoutExpired")
        except Exception as e:
            return ToolResult(success=False, output=f"Execution error: {e}", error=str(e))
