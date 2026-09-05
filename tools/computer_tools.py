import subprocess
import os
import time
import psutil
from tools.base import BaseTool, ToolResult


class OpenApplicationTool(BaseTool):
    name = "computer_open_app"
    description = "Launches any application on Windows (e.g. notepad, chrome, vscode, calc, spotify, excel)"
    permission_level = "safe"
    timeout = 10
    parameters = {
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "Name of application to open (e.g., 'notepad', 'chrome', 'vscode', 'calc')"
            }
        },
        "required": ["app_name"]
    }

    def _execute_impl(self, app_name: str, **kwargs) -> ToolResult:
        app_map = {
            "notepad": "notepad",
            "calculator": "calc",
            "calc": "calc",
            "chrome": "start chrome",
            "google chrome": "start chrome",
            "edge": "start msedge",
            "firefox": "start firefox",
            "vscode": "code",
            "code": "code",
            "vs code": "code",
            "spotify": "start spotify",
            "word": "start winword",
            "excel": "start excel",
            "powerpoint": "start powerpnt",
            "terminal": "start wt",
            "cmd": "start cmd",
            "powershell": "start powershell",
            "explorer": "explorer",
            "paint": "mspaint"
        }
        target = app_map.get(app_name.lower().strip(), f"start {app_name}")
        try:
            subprocess.run(target, shell=True, check=False)
            return ToolResult(success=True, output=f"Successfully launched {app_name}, Sujal.", action="open_app", target=target)
        except Exception as e:
            return ToolResult(success=False, output=f"Failed to launch {app_name}", error=str(e), action="open_app", target=target)


class CloseApplicationTool(BaseTool):
    name = "computer_close_app"
    description = "Closes a running application or process on Windows"
    permission_level = "moderate"
    timeout = 10
    parameters = {
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "Process name or title to close (e.g., 'notepad.exe', 'chrome')"
            }
        },
        "required": ["app_name"]
    }

    def _execute_impl(self, app_name: str, **kwargs) -> ToolResult:
        app_lower = app_name.lower().replace(".exe", "")
        killed = False
        for proc in psutil.process_iter(['name']):
            try:
                if app_lower in proc.info['name'].lower():
                    proc.kill()
                    killed = True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if killed:
            return ToolResult(success=True, output=f"Closed {app_name} successfully.", action="close_app", target=app_name)
        return ToolResult(success=False, output=f"No active process matching '{app_name}' found to close.", action="close_app", target=app_name)


class ControlMediaTool(BaseTool):
    name = "computer_control_media"
    description = "Controls system media playback (play, pause, next, previous, volume up, volume down, mute)"
    permission_level = "safe"
    timeout = 5
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["play", "pause", "resume", "next", "previous", "volume_up", "volume_down", "mute"],
                "description": "Media action to perform"
            }
        },
        "required": ["action"]
    }

    def _execute_impl(self, action: str, **kwargs) -> ToolResult:
        try:
            import pyautogui
            action_map = {
                "play": "playpause",
                "pause": "playpause",
                "resume": "playpause",
                "next": "nexttrack",
                "previous": "prevtrack",
                "volume_up": "volumeup",
                "volume_down": "volumedown",
                "mute": "volumemute"
            }
            key = action_map.get(action.lower().strip(), "playpause")
            pyautogui.press(key)
            return ToolResult(success=True, output=f"Media action '{action}' executed successfully.", action="control_media", target=action)
        except Exception as e:
            return ToolResult(success=False, output=f"Media control failed: {e}", error=str(e), action="control_media", target=action)


class StreamYouTubeTool(BaseTool):
    name = "computer_stream_youtube"
    description = "Searches and streams any song, artist, video, or soundtrack directly on YouTube"
    permission_level = "safe"
    timeout = 15
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Song name, artist, or video title to play on YouTube (e.g. 'Hans Zimmer Interstellar', 'Believer Imagine Dragons', 'Lofi hip hop')"
            }
        },
        "required": ["query"]
    }

    def _execute_impl(self, query: str, **kwargs) -> ToolResult:
        if not query or query.strip() in ["music", "song", "media", "something"]:
            query = "cyberpunk synthwave radio"
        
        clean_query = query.strip()
        import urllib.parse
        import webbrowser
        from database.postgres_db import postgres_manager

        # Log music fact to PostgreSQL
        if postgres_manager.is_connected():
            postgres_manager.save_semantic_fact(
                key="last_played_music",
                value={"query": clean_query, "platform": "YouTube", "played_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                category="music"
            )

        try:
            import pywhatkit
            pywhatkit.playonyt(clean_query)
            return ToolResult(success=True, output=f"Streaming '{clean_query}' on YouTube, Boss Sujal.", action="stream_youtube", target=clean_query)
        except Exception:
            # Fallback to direct web browser query search
            url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_query)}"
            webbrowser.open(url)
            return ToolResult(success=True, output=f"Opened YouTube search for '{clean_query}', Boss Sujal.", action="stream_youtube", target=clean_query)
