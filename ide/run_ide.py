"""
DOOM Cyber IDE — Dedicated Launcher
Runs the standalone AI Development Studio on port 8500 and opens the browser.
"""

import os
import sys
import webbrowser
import threading
import time
import uvicorn
from pathlib import Path

# Ensure DOOM root is in sys.path
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

def open_browser():
    time.sleep(1.2)
    url = "http://localhost:8500"
    print(f"\n[DOOM IDE] Opening Cyber IDE Studio at: {url}")
    webbrowser.open(url)

def main():
    port = int(os.getenv("DOOM_IDE_PORT", "8500"))
    host = os.getenv("DOOM_IDE_HOST", "0.0.0.0")

    print("=" * 60)
    print(" [*] DOOM CYBER IDE -- AUTONOMOUS AI DEVELOPMENT STUDIO")
    print("     Custom Engineered for Boss Sujal")
    print(f"     Port: {port}  |  Host: {host}")
    print("=" * 60)

    # Launch browser thread
    threading.Thread(target=open_browser, daemon=True).start()

    # Start FastAPI / Uvicorn server
    uvicorn.run(
        "ide.server:app",
        host=host,
        port=port,
        reload=True,
        reload_dirs=[os.path.join(root_dir, "ide")]
    )

if __name__ == "__main__":
    main()

