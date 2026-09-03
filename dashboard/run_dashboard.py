import os
import sys
import time
import threading
import webbrowser
import uvicorn

# Ensure project root directory is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dashboard.server import app

def open_browser():
    time.sleep(1.2)
    url = "http://localhost:8000"
    print(f"\n[DOOM HUD] [LAUNCH] Opening Holographic Control Center at: {url}")
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"[DOOM HUD] Note: Open {url} in your browser ({e})")

if __name__ == "__main__":
    print("=" * 60)
    print("[DOOM V2] HOLOGRAPHIC JARVIS HUD & WEB DASHBOARD")
    print("=" * 60)
    print("[*] Starting FastAPI & WebSocket Telemetry Server on Port 8000...")
    
    # Launch browser in background thread
    threading.Thread(target=open_browser, daemon=True).start()
    
    # Start Uvicorn Server with auto-reload enabled
    uvicorn.run("dashboard.server:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
