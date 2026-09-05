import os
import psutil
import time
from datetime import datetime
from tools.base import BaseTool, ToolResult
from core.advanced_automation import get_system_info, take_screenshot, optimize_system


class SystemStatusTool(BaseTool):
    name = "system_get_status"
    description = "Retrieves live CPU usage, memory utilization, disk space, and process count"
    permission_level = "safe"
    timeout = 5
    parameters = {"type": "object", "properties": {}}

    def _execute_impl(self, **kwargs) -> ToolResult:
        start_t = time.time()
        try:
            info = get_system_info()
            duration = (time.time() - start_t) * 1000
            if "error" not in info:
                out = f"CPU Usage: {info['cpu_percent']}%, Memory: {info['memory'].percent}%, Disk: {info['disk'].percent}%, Processes: {info['processes']}"
                return ToolResult(success=True, output=out, action="get_status", artifact=info, stdout=out, stderr="", duration_ms=duration, exit_code=0, target="system", data=info)
            return ToolResult(success=False, output="Unable to retrieve system metrics", error=info.get("error"), action="get_status", duration_ms=duration, exit_code=-1, target="system")
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=False, output=f"System metrics check failed: {e}", error=str(e), action="get_status", duration_ms=duration, exit_code=-1, target="system")


class TakeScreenshotTool(BaseTool):
    name = "system_take_screenshot"
    description = "Captures a full screenshot of all displays and saves it to disk"
    permission_level = "safe"
    timeout = 10
    parameters = {"type": "object", "properties": {}}

    def _execute_impl(self, **kwargs) -> ToolResult:
        start_t = time.time()
        try:
            res = take_screenshot()
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=True, output=res, action="take_screenshot", artifact={"type": "screenshot"}, stdout=res, stderr="", duration_ms=duration, exit_code=0, target="display", data={"result": res})
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=False, output=f"Screenshot capture failed: {e}", error=str(e), action="take_screenshot", duration_ms=duration, exit_code=-1, target="display")


class OptimizeSystemTool(BaseTool):
    name = "system_optimize"
    description = "Performs memory and disk diagnostic check with optimization recommendations"
    permission_level = "safe"
    timeout = 30
    parameters = {"type": "object", "properties": {}}

    def _execute_impl(self, **kwargs) -> ToolResult:
        start_t = time.time()
        try:
            res = optimize_system()
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=True, output=res, action="optimize", artifact={"type": "optimization"}, stdout=res, stderr="", duration_ms=duration, exit_code=0, target="system", data={"result": res})
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=False, output=f"Optimization failed: {e}", error=str(e), action="optimize", duration_ms=duration, exit_code=-1, target="system")


class LockWorkstationTool(BaseTool):
    name = "system_lock_pc"
    description = "Immediately locks the Windows workstation screen for security"
    permission_level = "safe"
    timeout = 5
    parameters = {"type": "object", "properties": {}}

    def _execute_impl(self, **kwargs) -> ToolResult:
        start_t = time.time()
        try:
            os.system("rundll32.exe user32.dll,LockWorkStation")
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=True, output="Workstation locked securely, Sujal.", action="lock_workstation", artifact={}, stdout="", stderr="", duration_ms=duration, exit_code=0, target="workstation")
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=False, output=f"Failed to lock workstation: {e}", error=str(e), action="lock_workstation", duration_ms=duration, exit_code=-1, target="workstation")


class StopSpeakingTool(BaseTool):
    name = "system_stop_speaking"
    description = "Immediately stops all ongoing voice speech output (also triggered by Ctrl+Shift+S hotkey)"
    permission_level = "safe"
    timeout = 2
    parameters = {"type": "object", "properties": {}}

    def _execute_impl(self, **kwargs) -> ToolResult:
        start_t = time.time()
        try:
            from core.cinematic_voice import stop_speaking
            stop_speaking()
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=True, output="All speech stopped, Sujal.", action="stop_speaking", artifact={}, stdout="", stderr="", duration_ms=duration, exit_code=0, target="voice")
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=False, output=f"Failed to stop speech: {e}", error=str(e), action="stop_speaking", duration_ms=duration, exit_code=-1, target="voice")
