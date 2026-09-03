#!/usr/bin/env python3
"""
DOOM Installation Script
Installs dependencies, configures environment, and validates installation.
"""

import subprocess
import sys
import os
import shutil

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

def print_banner():
    print("=" * 60)
    print("DOOM ADVANCED AI ASSISTANT - SETUP & INSTALLATION")
    print("=" * 60)

def check_python_version():
    print("\nChecking Python version...")
    if sys.version_info < (3, 8):
        print(f"[FAIL] Python 3.8+ required. Current version: {sys.version}")
        return False
    print(f"[OK] Python {sys.version.split()[0]} detected.")
    return True

def install_dependencies():
    print("\nInstalling project dependencies from core/requirements.txt...")
    req_file = os.path.join("core", "requirements.txt")
    if not os.path.exists(req_file):
        print(f"[FAIL] {req_file} not found!")
        return False
    
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_file])
        print("[OK] Dependencies installed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[FAIL] Failed to install dependencies: {e}")
        return False

def setup_environment():
    print("\nConfiguring environment file (.env)...")
    if not os.path.exists(".env"):
        if os.path.exists("config_example.txt"):
            shutil.copy("config_example.txt", ".env")
            print("[OK] Created .env from config_example.txt")
        else:
            with open(".env", "w") as f:
                f.write("# DOOM Environment Configuration\nOPENAI_API_KEY=\nWOLFRAM_API_KEY=\nNEWS_API_KEY=\n")
            print("[OK] Created default .env file")
    else:
        print("[OK] .env already exists.")
    return True

def test_imports():
    print("\nVerifying core modules...")
    try:
        import speech_recognition
        import pyttsx3
        import requests
        from core.cinematic_voice import get_voice_instance
        from core.ui_effects import ui
        print("[OK] Core modules imported successfully.")
        return True
    except ImportError as e:
        print(f"[NOTE] Module verification note: {e}")
        return False

def main():
    print_banner()
    if not check_python_version():
        sys.exit(1)
    
    if not install_dependencies():
        print("\n[NOTE] Some dependencies could not be installed automatically. You can retry with: pip install -r core/requirements.txt")
    
    setup_environment()
    test_imports()
    
    print("\n" + "=" * 60)
    print("DOOM INSTALLATION & CONFIGURATION COMPLETE!")
    print("=" * 60)
    print("\nHow to run DOOM:")
    print("  1. (Optional) Install Ollama (https://ollama.ai) for local LLM reasoning: 'ollama pull llama3'")
    print("  2. Test DOOM without voice: python test_doom.py")
    print("  3. Run full voice assistant: python doom.py")
    print("=" * 60)

if __name__ == "__main__":
    main()
