#!/usr/bin/env python3
"""
DOOM Auto-Start Setup
Enables or disables DOOM running silently on Windows startup and starts it immediately.
"""

import os
import sys
import subprocess

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

STARTUP_DIR = os.path.join(os.getenv("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
TARGET_VBS = os.path.join(STARTUP_DIR, "DOOM_Background_Service.vbs")
WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
BG_SCRIPT = os.path.join(WORKSPACE_DIR, "doom_background.pyw")
PYTHONW_EXE = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")

if not os.path.exists(PYTHONW_EXE):
    PYTHONW_EXE = sys.executable

def start_background_process():
    """Start DOOM in the background right now"""
    cmd = f'start "" "{PYTHONW_EXE}" "{BG_SCRIPT}"'
    subprocess.Popen(cmd, shell=True, cwd=WORKSPACE_DIR)
    print("[OK] DOOM background listener is now ACTIVE and running!")

def enable_autostart():
    """Create a silent VBS launcher in Windows Startup folder and start process"""
    if not os.path.exists(STARTUP_DIR):
        print(f"[ERROR] Windows Startup directory not found at: {STARTUP_DIR}")
        return False

    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "{WORKSPACE_DIR}"
WshShell.Run """{PYTHONW_EXE}"" ""{BG_SCRIPT}""", 0, False
'''

    try:
        with open(TARGET_VBS, "w", encoding="utf-8") as f:
            f.write(vbs_content)
        print("=" * 60)
        print("[SUCCESS] DOOM WINDOWS AUTO-START CONFIGURED!")
        print("=" * 60)
        print(f"[OK] Startup launcher created in Windows Startup folder.")
        start_background_process()
        print("\nDOOM is now running in the background and will auto-start on every boot.")
        print("Say 'Hey DOOM' or double-clap 👏 to talk to DOOM anytime!")
        print("=" * 60)
        return True
    except Exception as e:
        print(f"[FAIL] Could not write startup shortcut: {e}")
        return False

def disable_autostart():
    """Remove the startup launcher and stop running processes"""
    if os.path.exists(TARGET_VBS):
        try:
            os.remove(TARGET_VBS)
            print("[OK] Removed startup shortcut.")
        except Exception as e:
            print(f"[FAIL] Could not remove startup file: {e}")
    
    # Kill background pythonw process
    subprocess.run('taskkill /f /fi "WINDOWTITLE eq DOOM*" >nul 2>&1', shell=True)
    subprocess.run('wmic process where "commandline like \'%doom_background%\'" delete >nul 2>&1', shell=True)
    print("[OK] DOOM background service stopped.")

if __name__ == "__main__":
    # Auto-boot is DISABLED per user request.
    # To re-enable manually in the future, run: python setup_autostart.py enable
    if len(sys.argv) > 1 and sys.argv[1].lower() in ["enable", "on", "start"]:
        enable_autostart()
    else:
        disable_autostart()
