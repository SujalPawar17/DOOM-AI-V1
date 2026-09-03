import os
import psutil
from datetime import datetime
from tools.base import BaseTool, ToolResult
from core.advanced_automation import get_system_info, take_screenshot, optimize_system

class SystemStatusTool(BaseTool):
    name = "system_get_status"
    description = "Retrieves live CPU usage, memory utilization, disk space, and process count"
    permission_level = "safe"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> ToolResult:
        try:
            info = get_system_info()
            if "error" not in info:
                out = f"CPU Usage: {info['cpu_percent']}%, Memory: {info['memory'].percent}%, Disk: {info['disk'].percent}%, Processes: {info['processes']}"
                return ToolResult(success=True, output=out, data=info)
            return ToolResult(success=False, output="Unable to retrieve system metrics", error=info.get("error"))
        except Exception as e:
            return ToolResult(success=False, output=f"System metrics check failed: {e}", error=str(e))

class TakeScreenshotTool(BaseTool):
    name = "system_take_screenshot"
    description = "Captures a full screenshot of all displays and saves it to disk"
    permission_level = "safe"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> ToolResult:
        try:
            res = take_screenshot()
            return ToolResult(success=True, output=res)
        except Exception as e:
            return ToolResult(success=False, output=f"Screenshot capture failed: {e}", error=str(e))

class OptimizeSystemTool(BaseTool):
    name = "system_optimize"
    description = "Performs memory and disk diagnostic check with optimization recommendations"
    permission_level = "safe"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> ToolResult:
        try:
            res = optimize_system()
            return ToolResult(success=True, output=res)
        except Exception as e:
            return ToolResult(success=False, output=f"Optimization failed: {e}", error=str(e))

class LockWorkstationTool(BaseTool):
    name = "system_lock_pc"
    description = "Immediately locks the Windows workstation screen for security"
    permission_level = "safe"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> ToolResult:
        try:
            os.system("rundll32.exe user32.dll,LockWorkStation")
            return ToolResult(success=True, output="Workstation locked securely, Sujal.")
        except Exception as e:
            return ToolResult(success=False, output=f"Failed to lock workstation: {e}", error=str(e))


class StopSpeakingTool(BaseTool):
    name = "system_stop_speaking"
    description = "Immediately stops all ongoing voice speech output (also triggered by Ctrl+Shift+S hotkey)"
    permission_level = "safe"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> ToolResult:
        try:
            from core.cinematic_voice import stop_speaking
            stop_speaking()
            return ToolResult(success=True, output="All speech stopped, Sujal.")
        except Exception as e:
            return ToolResult(success=False, output=f"Failed to stop speech: {e}", error=str(e))
