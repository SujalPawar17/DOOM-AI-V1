import time
from tools.base import BaseTool, ToolResult

try:
    from core.vision import vision
    VISION_AVAILABLE = True
except Exception:
    VISION_AVAILABLE = False


class ScanGestureTool(BaseTool):
    name = "vision_scan_gesture"
    description = "Activates webcam to scan and identify user hand gestures (Open Palm = Mute/Stop, Peace = Activate, Fist = Confirm)"
    permission_level = "safe"
    timeout = 15
    parameters = {
        "type": "object",
        "properties": {
            "duration_seconds": {
                "type": "integer",
                "description": "How many seconds to scan webcam for gestures (default 5)"
            }
        }
    }

    def _execute_impl(self, duration_seconds: int = 5, **kwargs) -> ToolResult:
        start_t = time.time()
        if not VISION_AVAILABLE or vision is None:
            return ToolResult(success=False, output="Computer vision module is unavailable.", error="ModuleMissing", action="scan_gesture", duration_ms=(time.time() - start_t) * 1000, exit_code=-1, target="webcam")
        try:
            gesture = vision.scan_webcam_gesture(duration_seconds)
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=True, output=f"Identified hand gesture: {gesture}", action="scan_gesture", artifact={"gesture": gesture}, stdout=str(gesture), stderr="", duration_ms=duration, exit_code=0, target="webcam", data={"gesture": gesture})
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=False, output=f"Gesture scan failed: {e}", error=str(e), action="scan_gesture", duration_ms=duration, exit_code=-1, target="webcam")


class TakePhotoTool(BaseTool):
    name = "vision_take_photo"
    description = "Captures a high-resolution photo from the primary webcam and saves to disk"
    permission_level = "safe"
    timeout = 10
    parameters = {"type": "object", "properties": {}}

    def _execute_impl(self, **kwargs) -> ToolResult:
        start_t = time.time()
        if not VISION_AVAILABLE or vision is None:
            return ToolResult(success=False, output="Webcam vision is unavailable.", error="ModuleMissing", action="take_photo", duration_ms=(time.time() - start_t) * 1000, exit_code=-1, target="webcam")
        try:
            res = vision.take_photo()
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=True, output=res, action="take_photo", artifact={"type": "photo"}, stdout=res, stderr="", duration_ms=duration, exit_code=0, target="webcam", data={"result": res})
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=False, output=f"Photo capture failed: {e}", error=str(e), action="take_photo", duration_ms=duration, exit_code=-1, target="webcam")


class AnalyzeScreenTool(BaseTool):
    name = "vision_analyze_screen"
    description = "Analyzes display resolution, color temperature, and brightness metrics"
    permission_level = "safe"
    timeout = 5
    parameters = {"type": "object", "properties": {}}

    def _execute_impl(self, **kwargs) -> ToolResult:
        start_t = time.time()
        if not VISION_AVAILABLE or vision is None:
            return ToolResult(success=False, output="Vision analysis unavailable.", error="ModuleMissing", action="analyze_screen", duration_ms=(time.time() - start_t) * 1000, exit_code=-1, target="display")
        try:
            analysis = vision.analyze_screen()
            duration = (time.time() - start_t) * 1000
            if "error" not in analysis:
                out = f"Screen resolution: {analysis['resolution']}, Brightness: {analysis['brightness']}"
                return ToolResult(success=True, output=out, action="analyze_screen", artifact=analysis, stdout=out, stderr="", duration_ms=duration, exit_code=0, target="display", data=analysis)
            return ToolResult(success=False, output="Screen analysis error", error=analysis.get("error"), action="analyze_screen", duration_ms=duration, exit_code=-1, target="display")
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=False, output=f"Screen analysis failed: {e}", error=str(e), action="analyze_screen", duration_ms=duration, exit_code=-1, target="display")
