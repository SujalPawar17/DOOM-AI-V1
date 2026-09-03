#!/usr/bin/env python3
"""
DOOM V2 Architecture & Functionality Verification Test Suite
Tests: Tool Registry, Model Router, Memory 2.0, DOOM Core Orchestrator, Voice, and Acoustic Sensors.
"""

import os
import sys
from datetime import datetime

# Configure utf-8 encoding safely for Windows cmd/powershell
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

def print_test_banner():
    print("=" * 65)
    print("🤖 DOOM V2 — PERSONAL AI OS ARCHITECTURE TEST SUITE")
    print("=" * 65)
    print(f"Test initiated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

def test_package_imports():
    print("\n[*] Section 1: Testing Package Dependencies...")
    packages = ["speech_recognition", "pyttsx3", "requests", "colorama", "edge_tts", "psutil"]
    for pkg in packages:
        try:
            __import__(pkg)
            print(f"[OK] {pkg} loaded")
        except ImportError as e:
            print(f"[FAIL] {pkg} missing: {e}")
            return False
    return True

def test_memory_subsystem():
    print("\n[*] Section 2: Testing Memory 2.0 Subsystem...")
    try:
        from memory import user_profile, short_term_memory, episodic_memory, semantic_memory
        
        # Test User Profile
        boss_name = user_profile.get_name()
        if boss_name != "Sujal":
            print(f"[FAIL] User profile name expected 'Sujal', got '{boss_name}'")
            return False
        print(f"[OK] User Profile loaded: Boss = '{boss_name}' ({user_profile.get_role()})")

        # Test Short Term Memory
        short_term_memory.clear()
        short_term_memory.add_user_turn("Hello DOOM")
        short_term_memory.add_assistant_turn("Greetings Sujal.")
        if len(short_term_memory.turns) != 2:
            print("[FAIL] Short term memory turn count mismatch")
            return False
        print("[OK] Short-Term Conversation History verified")

        # Test Semantic Memory
        semantic_memory.remember_fact("project_v2_status", "operational")
        val = semantic_memory.recall_fact("project_v2_status")
        if val != "operational":
            print("[FAIL] Semantic memory recall mismatch")
            return False
        print("[OK] Semantic Memory verified")

        # Test Episodic Memory
        episodic_memory.record_episode("test_task", ["step 1"], [{"name": "test_tool", "args": {}}], "success", True)
        recent = episodic_memory.get_recent_episodes(1)
        if not recent or recent[0]["goal"] != "test_task":
            print("[FAIL] Episodic memory record mismatch")
            return False
        print("[OK] Episodic Memory verified")

        return True
    except Exception as e:
        print(f"[FAIL] Memory 2.0 test crashed: {e}")
        return False

def test_tool_registry():
    print("\n[*] Section 3: Testing Standardized Tool Registry...")
    try:
        from core.tool_registry import tool_registry
        tools = tool_registry.get_all_tools()
        print(f"[OK] Tool Registry initialized with {len(tools)} tools")
        
        # Verify tool schemas
        schemas = tool_registry.get_schemas()
        if len(schemas) != len(tools):
            print("[FAIL] Schemas count mismatch")
            return False
        print(f"[OK] JSON Function schemas generated for {len(schemas)} tools")

        # Test specific tool execution: coding_run_python
        res = tool_registry.execute_tool("coding_run_python", {"code_or_file": "print(2**10)"})
        if not res.success or "1024" not in res.output:
            print(f"[FAIL] coding_run_python test failed: {res}")
            return False
        print(f"[OK] Tool 'coding_run_python' executed successfully (Result: {res.output.strip()})")

        # Test specific tool execution: system_get_status
        res_sys = tool_registry.execute_tool("system_get_status", {})
        if not res_sys.success:
            print(f"[FAIL] system_get_status failed: {res_sys}")
            return False
        print(f"[OK] Tool 'system_get_status' executed successfully ({res_sys.output[:50]}...)")

        return True
    except Exception as e:
        print(f"[FAIL] Tool Registry test crashed: {e}")
        return False

def test_model_router():
    print("\n[*] Section 4: Testing Model Router...")
    try:
        from core.model_router import model_router
        status = model_router.get_provider_status()
        print(f"[OK] Registered Providers: {status}")
        
        provider = model_router.route("coding")
        print(f"[OK] Router selected '{provider.name}' for coding task")
        if not provider:
            return False
        return True
    except Exception as e:
        print(f"[FAIL] Model Router test crashed: {e}")
        return False

def test_doom_core_orchestrator():
    print("\n[*] Section 5: Testing DOOM Core Master Orchestrator...")
    try:
        from core.orchestrator import doom_core
        
        # Test 1: Identity goal
        resp1 = doom_core.process_request("Who am I?")
        if "Sujal" not in resp1:
            print(f"[FAIL] Expected 'Sujal' in identity response, got: {resp1}")
            return False
        print(f"[OK] Orchestrator Identity Goal passed: '{resp1}'")

        # Test 2: Direct Action (System status)
        resp2 = doom_core.process_request("System status")
        if "CPU" not in resp2 and "Memory" not in resp2:
            print(f"[FAIL] System status response mismatch: {resp2}")
            return False
        print(f"[OK] Orchestrator Direct Action Goal passed: '{resp2[:60]}...'")

        return True
    except Exception as e:
        print(f"[FAIL] DOOM Core Orchestrator test crashed: {e}")
        return False

def test_audio_and_voice():
    print("\n[*] Section 6: Testing Voice & Acoustic Modules...")
    try:
        from core.cinematic_voice import speak
        from core.sound_detector import sound_detector
        print("[OK] Cinematic British Voice Engine loaded")
        print("[OK] Acoustic Double-Clap Detector loaded")
        return True
    except Exception as e:
        print(f"[FAIL] Voice/Acoustic test crashed: {e}")
        return False

def test_postgres_database():
    print("\n[*] Section 7: Testing PostgreSQL Database Connection & Tables...")
    try:
        from database.postgres_db import postgres_manager
        stats = postgres_manager.test_connection()
        if stats.get("status") != "connected":
            print(f"[FAIL] PostgreSQL connection failed: {stats}")
            return False
        
        print(f"[OK] Connected to PostgreSQL '{stats.get('database')}' at {stats.get('host')}")
        tables = stats.get("tables", {})
        print(f"[OK] Table status: profiles={tables.get('profile_count',0)}, episodes={tables.get('episode_count',0)}, facts={tables.get('fact_count',0)}, telemetry={tables.get('telemetry_count',0)}, logs={tables.get('log_count',0)}")

        # Test tool execution: database_status
        from core.tool_registry import tool_registry
        res = tool_registry.execute_tool("database_status", {})
        if not res.success:
            print(f"[FAIL] database_status tool failed: {res}")
            return False
        print("[OK] Tool 'database_status' executed successfully")

        # Test tool execution: database_query
        res_q = tool_registry.execute_tool("database_query", {"query": "SELECT * FROM user_profiles;"})
        if not res_q.success:
            print(f"[FAIL] database_query tool failed: {res_q}")
            return False
        print(f"[OK] Tool 'database_query' executed successfully")

        return True
    except Exception as e:
        print(f"[FAIL] PostgreSQL test crashed: {e}")
        return False

def run_all_tests():
    print_test_banner()
    tests = [
        ("Package Dependencies", test_package_imports),
        ("Memory 2.0 Subsystem", test_memory_subsystem),
        ("Standardized Tool Registry", test_tool_registry),
        ("Model Router", test_model_router),
        ("DOOM Core Orchestrator", test_doom_core_orchestrator),
        ("Voice & Acoustic Sensors", test_audio_and_voice),
        ("PostgreSQL Database Subsystem", test_postgres_database)
    ]
    
    passed = 0
    total = len(tests)
    for test_name, test_func in tests:
        try:
            if test_func():
                passed += 1
                print(f"[PASSED] {test_name}")
            else:
                print(f"[FAILED] {test_name}")
        except Exception as e:
            print(f"[ERROR] {test_name} error: {e}")

    print("\n" + "=" * 65)
    print(f"[SUMMARY] {passed}/{total} Test Sections Passed")
    print("=" * 65)
    if passed == total:
        print("🎉 DOOM V2 — PERSONAL AI OS IS 100% OPERATIONAL!")
    return passed == total

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)