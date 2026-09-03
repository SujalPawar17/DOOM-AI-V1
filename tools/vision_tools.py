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
    parameters = {
        "type": "object",
        "properties": {
            "duration_seconds": {
                "type": "integer",
                "description": "How many seconds to scan webcam for gestures (default 5)"
            }
        }
    }

    def execute(self, duration_seconds: int = 5, **kwargs) -> ToolResult:
        if not VISION_AVAILABLE or vision is None:
            return ToolResult(success=False, output="Computer vision module is unavailable.", error="ModuleMissing")
        try:
            gesture = vision.scan_webcam_gesture(duration_seconds)
            return ToolResult(success=True, output=f"Identified hand gesture: {gesture}", data={"gesture": gesture})
        except Exception as e:
            return ToolResult(success=False, output=f"Gesture scan failed: {e}", error=str(e))

class TakePhotoTool(BaseTool):
    name = "vision_take_photo"
    description = "Captures a high-resolution photo from the primary webcam and saves to disk"
    permission_level = "safe"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> ToolResult:
        if not VISION_AVAILABLE or vision is None:
            return ToolResult(success=False, output="Webcam vision is unavailable.", error="ModuleMissing")
        try:
            res = vision.take_photo()
            return ToolResult(success=True, output=res)
        except Exception as e:
            return ToolResult(success=False, output=f"Photo capture failed: {e}", error=str(e))

class AnalyzeScreenTool(BaseTool):
    name = "vision_analyze_screen"
    description = "Analyzes display resolution, color temperature, and brightness metrics"
    permission_level = "safe"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> ToolResult:
        if not VISION_AVAILABLE or vision is None:
            return ToolResult(success=False, output="Vision analysis unavailable.", error="ModuleMissing")
        try:
            analysis = vision.analyze_screen()
            if "error" not in analysis:
                out = f"Screen resolution: {analysis['resolution']}, Brightness: {analysis['brightness']}"
                return ToolResult(success=True, output=out, data=analysis)
            return ToolResult(success=False, output="Screen analysis error", error=analysis.get("error"))
        except Exception as e:
            return ToolResult(success=False, output=f"Screen analysis failed: {e}", error=str(e))
