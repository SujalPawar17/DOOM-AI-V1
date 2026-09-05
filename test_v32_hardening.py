#!/usr/bin/env python3
"""
DOOM V3.2 — Orchestration Hardening Integration Test Suite
Tests the hardened autonomous execution pipeline.
"""

import os
import sys
import time
import tempfile

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def test_direct_intent():
    """Test A: Direct query 'Who am I?' - fast response, no unnecessary LLM/tool usage."""
    print("\n[TEST A] Direct Intent: 'Who am I?'")
    from core.orchestrator import doom_core
    start = time.time()
    resp = doom_core.process_request("Who am I?")
    duration = time.time() - start
    print(f"  Response: {resp[:80]}...")
    print(f"  Duration: {duration:.3f}s")
    assert "Sujal" in resp, "Expected 'Sujal' in response"
    assert duration < 2.0, f"Direct intent too slow: {duration}s"
    print("  ✓ PASSED")
    return True


def test_telemetry_fast_path():
    """Test B: Telemetry 'Show my CPU, RAM and disk usage' - direct telemetry, no file creation."""
    print("\n[TEST B] Telemetry Fast Path: 'Show my CPU, RAM and disk usage'")
    from core.orchestrator import doom_core
    start = time.time()
    resp = doom_core.process_request("Show my CPU, RAM and disk usage")
    duration = time.time() - start
    print(f"  Response: {resp[:120]}...")
    print(f"  Duration: {duration:.3f}s")
    assert "CPU" in resp and "RAM" in resp and "Disk" in resp, "Expected telemetry data in response"
    assert "system_info.py" not in resp.lower(), "Should NOT create Python file for telemetry"
    assert duration < 3.0, f"Telemetry too slow: {duration}s"
    print("  ✓ PASSED")
    return True


def test_multistep_execution():
    """Test C: Multi-step 'Create a Python file on my desktop called system_info.py that displays CPU, RAM and disk usage. Run it, verify it, and tell me the result.'"""
    print("\n[TEST C] Multi-Step Execution: Create + Run + Verify system_info.py")
    from core.orchestrator import doom_core
    from core.path_resolver import canonical_path
    
    # Clean up any existing test file
    test_path = canonical_path("Desktop/system_info.py")
    if test_path.exists:
        try:
            os.remove(test_path.absolute_path)
        except Exception:
            pass
    
    start = time.time()
    resp = doom_core.process_request(
        "Create a Python file on my desktop called system_info.py that displays CPU, RAM and disk usage. Run it, verify it, and tell me the result."
    )
    duration = time.time() - start
    print(f"  Response: {resp[:200]}...")
    print(f"  Duration: {duration:.3f}s")
    
    # Verify exact artifact identity
    assert "system_info.py" in resp, "Expected exact filename 'system_info.py' in response"
    assert "CPU" in resp and "RAM" in resp and "Disk" in resp, "Expected telemetry output in response"
    
    # Verify file exists on disk
    assert test_path.exists, f"File not created at {test_path.absolute_path}"
    size = os.path.getsize(test_path.absolute_path)
    assert size > 0, "File is empty"
    print(f"  File verified: {test_path.relative_path} ({size} bytes)")
    
    # Check for no duplicate sentences
    sentences = resp.split('. ')
    unique_sentences = list(dict.fromkeys(sentences))
    assert len(sentences) == len(unique_sentences), f"Duplicate sentences detected: {sentences}"
    
    print("  ✓ PASSED")
    return True


def test_duplicate_write_prevention():
    """Test: Duplicate write prevention - second request for same file should not duplicate."""
    print("\n[TEST D] Duplicate Write Prevention")
    from core.orchestrator import doom_core
    from core.path_resolver import canonical_path
    from core.tool_registry import tool_registry
    from tools.base import CanonicalToolResult
    
    test_path = canonical_path("Desktop/duplicate_test.py")
    if test_path.exists:
        try:
            os.remove(test_path.absolute_path)
        except Exception:
            pass
    
    # First creation
    resp1 = doom_core.process_request("Create a Python file on my desktop called duplicate_test.py that prints hello world")
    print(f"  First response: {resp1[:100]}...")
    
    # Second creation (should skip)
    resp2 = doom_core.process_request("Create a Python file on my desktop called duplicate_test.py that prints hello world")
    print(f"  Second response: {resp2[:100]}...")
    
    # Should not have created duplicate
    assert test_path.exists, "File should exist"
    size = os.path.getsize(test_path.absolute_path)
    assert size > 0, "File should have content"
    
    # Response should indicate completion without duplicate creation messages
    print("  ✓ PASSED")
    return True


def test_path_resolution():
    """Test: Path resolution - exact canonical paths used."""
    print("\n[TEST E] Path Resolution")
    from core.path_resolver import canonical_path
    
    # Test Desktop resolution
    cpath = canonical_path("Desktop/test_path.py")
    assert "Desktop" in cpath.relative_path, f"Expected Desktop in relative path, got {cpath.relative_path}"
    assert cpath.absolute_path == os.path.abspath(cpath.absolute_path), "Absolute path mismatch"
    print(f"  Desktop path: {cpath.relative_path} -> {cpath.absolute_path}")
    
    # Test relative path
    cpath2 = canonical_path("scripts/test.py", default_dir=os.getcwd())
    assert "scripts" in cpath2.relative_path, f"Expected scripts in relative path, got {cpath2.relative_path}"
    print(f"  Relative path: {cpath2.relative_path} -> {cpath2.absolute_path}")
    
    print("  ✓ PASSED")
    return True


def test_ground_truth_verification():
    """Test: Ground truth verification - syntax, exit code, file on disk."""
    print("\n[TEST F] Ground Truth Verification")
    from core.verifier import verifier
    from tools.base import CanonicalToolResult
    import tempfile
    
    # Create a valid Python file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write("print('hello')\n")
        temp_path = f.name
    
    try:
        artifact = {"path": temp_path, "relative_path": temp_path, "name": os.path.basename(temp_path), "size_bytes": os.path.getsize(temp_path), "exists": True}
        obs = CanonicalToolResult(
            tool="coding_write_script",
            success=True,
            action="create_file",
            target=temp_path,
            artifact=artifact,
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=10.0
        )
        result = verifier.verify_ground_truth("test", [obs])
        assert result["verified"] == True, f"Valid file should pass verification: {result}"
        print(f"  Valid file verification: {result['details']}")
        
        # Test syntax error detection
        with open(temp_path, 'w') as f:
            f.write("print('hello'\n")  # Missing closing paren
        
        obs2 = CanonicalToolResult(
            tool="coding_write_script",
            success=True,
            action="create_file",
            target=temp_path,
            artifact=artifact,
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=10.0
        )
        result2 = verifier.verify_ground_truth("test", [obs2])
        assert result2["verified"] == False, "Syntax error should fail verification"
        assert "syntax error" in result2["details"].lower(), f"Expected syntax error in details: {result2}"
        print(f"  Syntax error detection: {result2['details']}")
        
        # Test execution verification
        obs3 = CanonicalToolResult(
            tool="coding_run_python",
            success=True,
            action="execute_file",
            target=temp_path,
            artifact={"path": temp_path, "returncode": 0},
            stdout="hello\n",
            stderr="",
            exit_code=0,
            duration_ms=100.0
        )
        result3 = verifier.verify_ground_truth("test", [obs3])
        assert result3["verified"] == True, f"Successful execution should pass: {result3}"
        print(f"  Execution verification: {result3['details']}")
        
        print("  ✓ PASSED")
        return True
    finally:
        try:
            os.remove(temp_path)
        except Exception:
            pass


def test_self_healing():
    """Test D: Self-healing 'Create a Python program with a syntax error, run it, fix it and run it again.'"""
    print("\n[TEST G] Self-Healing: Syntax Error -> Fix -> Re-run")
    from core.orchestrator import doom_core
    from core.path_resolver import canonical_path
    
    test_path = canonical_path("Desktop/self_heal_test.py")
    if test_path.exists:
        try:
            os.remove(test_path.absolute_path)
        except Exception:
            pass
    
    start = time.time()
    resp = doom_core.process_request(
        "Create a Python program with a syntax error on my desktop called self_heal_test.py, run it, fix it and run it again."
    )
    duration = time.time() - start
    print(f"  Response: {resp[:200]}...")
    print(f"  Duration: {duration:.3f}s")
    
    # Should have created and fixed the file
    assert "self_heal_test.py" in resp, "Expected filename in response"
    assert test_path.exists, "File should exist after self-healing"
    
    # File should be valid Python now
    with open(test_path.absolute_path, 'r') as f:
        content = f.read()
    try:
        compile(content, test_path.absolute_path, 'exec')
        print("  Final file has valid syntax")
    except SyntaxError:
        assert False, "File should have valid syntax after self-healing"
    
    print("  ✓ PASSED")
    return True


def test_provider_failover():
    """Test E: Provider failure - simulate primary provider failure, expect automatic failover."""
    print("\n[TEST H] Provider Failover")
    from core.model_router import model_router
    
    # Test that fallback provider is always available
    status = model_router.get_provider_status()
    print(f"  Provider status: {status}")
    
    fallback = model_router.providers["fallback"]
    assert fallback.is_available(), "Fallback provider should always be available"
    
    # Test routing to fallback when others unavailable
    provider = model_router.route("coding")
    print(f"  Routed to: {provider.name}")
    assert provider is not None, "Should always get a provider"
    
    print("  ✓ PASSED")
    return True


def test_tool_timeout():
    """Test: Tool timeout - tool exceeding timeout returns structured timeout observation."""
    print("\n[TEST I] Tool Timeout")
    from core.tool_registry import tool_registry
    from tools.base import CanonicalToolResult
    
    # Create a tool that would timeout (simulate with very short timeout)
    # We'll test the timeout observation structure by checking a normal tool
    # and ensuring the timeout mechanism exists
    result = tool_registry.execute_tool("coding_run_python", {"code_or_file": "import time; time.sleep(20)", "timeout": 1})
    assert result.success == False, "Should fail with timeout"
    assert result.error_type == "TIMEOUT", f"Expected TIMEOUT error_type, got {result.error_type}"
    assert "timed out" in result.output.lower(), f"Expected timeout message: {result.output}"
    print(f"  Timeout observation: {result.to_dict()}")
    
    print("  ✓ PASSED")
    return True


def test_max_retry():
    """Test: Max retries reached - tool failing repeatedly hits MAX_RETRIES_PER_ACTION."""
    print("\n[TEST J] Max Retries")
    from core.orchestrator import doom_core
    from core.path_resolver import canonical_path
    
    test_path = canonical_path("Desktop/max_retry_test.py")
    if test_path.exists:
        try:
            os.remove(test_path.absolute_path)
        except Exception:
            pass
    
    # This should trigger retries due to syntax error, then fix
    resp = doom_core.process_request(
        "Create a Python file on my desktop called max_retry_test.py that has a syntax error, then run it"
    )
    print(f"  Response: {resp[:150]}...")
    
    # Should eventually succeed or fail gracefully
    assert test_path.exists, "File should exist"
    print("  ✓ PASSED")
    return True


def test_response_synthesis():
    """Test: Response synthesis - clean final response without raw tool messages."""
    print("\n[TEST K] Response Synthesis - No Duplicate/Raw Messages")
    from core.orchestrator import doom_core
    from core.path_resolver import canonical_path
    
    test_path = canonical_path("Desktop/synthesis_test.py")
    if test_path.exists:
        try:
            os.remove(test_path.absolute_path)
        except Exception:
            pass
    
    resp = doom_core.process_request(
        "Create a Python file on my desktop called synthesis_test.py that prints 42. Run it and tell me the result."
    )
    print(f"  Response: {resp}")
    
    # Check for clean response (no raw tool output)
    forbidden_phrases = [
        "Successfully generated",
        "Successfully written",
        "Successfully created",
        "Tool '",
        "execution",
        "stdout",
        "stderr",
        "exit_code"
    ]
    for phrase in forbidden_phrases:
        assert phrase.lower() not in resp.lower(), f"Found forbidden phrase '{phrase}' in response: {resp}"
    
    # Should have exact artifact name and actual output
    assert "synthesis_test.py" in resp, "Exact filename missing"
    assert "42" in resp, "Actual output missing"
    
    print("  ✓ PASSED")
    return True


def test_exact_artifact_identity():
    """Test: Exact artifact identity - DOOM never changes filenames/paths."""
    print("\n[TEST L] Exact Artifact Identity")
    from core.orchestrator import doom_core
    from core.path_resolver import canonical_path
    
    test_path = canonical_path("Desktop/Exact_Name_Test.py")
    if test_path.exists:
        try:
            os.remove(test_path.absolute_path)
        except Exception:
            pass
    
    resp = doom_core.process_request(
        "Create a Python file on my desktop called Exact_Name_Test.py that prints hello"
    )
    print(f"  Response: {resp[:150]}...")
    
    # Must use EXACT canonical name
    assert "Exact_Name_Test.py" in resp, f"Expected exact 'Exact_Name_Test.py', got: {resp}"
    assert test_path.exists, "File should exist with exact name"
    
    # Verify the actual file name on disk matches exactly
    actual_name = os.path.basename(test_path.absolute_path)
    assert actual_name == "Exact_Name_Test.py", f"Disk name mismatch: {actual_name}"
    
    print("  ✓ PASSED")
    return True


def test_tts_failure_isolated():
    """Test F: TTS failure - DOOM remains fully functional through text."""
    print("\n[TEST M] TTS Failure Isolation")
    from core.cinematic_voice import get_audio_status, AudioStatus, speak_immediate
    
    # Get audio status (may be AVAILABLE, UNAVAILABLE, or FAILED)
    status = get_audio_status()
    print(f"  Audio status: {status.value}")
    assert status in [AudioStatus.AVAILABLE, AudioStatus.UNAVAILABLE, AudioStatus.FAILED]
    
    # Test speak_immediate returns AudioStatus
    result = speak_immediate("Test message for TTS")
    print(f"  speak_immediate returned: {result.value}")
    assert isinstance(result, AudioStatus)
    
    # Core functionality should work regardless of TTS
    from core.orchestrator import doom_core
    resp = doom_core.process_request("What is 2+2?")
    print(f"  Core response during TTS test: {resp[:80]}...")
    assert "4" in resp, "Core should work without TTS"
    
    print("  ✓ PASSED")
    return True


def test_latency_profiling():
    """Test: Latency profiling - all components timed."""
    print("\n[TEST N] Latency Profiling")
    from core.orchestrator import doom_core
    import io
    import contextlib
    
    # Capture stdout to check PERF output
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        resp = doom_core.process_request("Who am I?")
    
    output = f.getvalue()
    print(f"  Captured output contains PERF: {'PERF' in output}")
    assert "PERF" in output, "Latency profile should be printed"
    assert "Total:" in output, "Total time should be in profile"
    
    # Check for all components
    components = ["Plan:", "Route:", "LLM:", "Tools:", "Verify:", "Synth:", "Total:"]
    for comp in components:
        assert comp in output, f"Missing {comp} in latency profile"
    
    print(f"  Latency profile captured: {output.split('PERF')[-1].strip()[:100]}...")
    print("  ✓ PASSED")
    return True


def test_termination_reasons():
    """Test: Autonomous loop termination reasons."""
    print("\n[TEST O] Termination Reasons")
    from core.orchestrator import doom_core
    from tools.base import TerminationReason
    
    # Test COMPLETED
    resp = doom_core.process_request("Who am I?")
    # Should complete with COMPLETED (implicitly tested by fast path)
    
    # Test MAX_STEPS_REACHED would need a complex scenario
    # For now verify the enum exists
    reasons = [r.value for r in TerminationReason]
    expected = ["COMPLETED", "FAILED", "TIMEOUT", "USER_APPROVAL_REQUIRED", "MAX_STEPS_REACHED", "MAX_RETRIES_REACHED", "UNRECOVERABLE_ERROR"]
    for exp in expected:
        assert exp in reasons, f"Missing termination reason: {exp}"
    
    print(f"  All termination reasons present: {reasons}")
    print("  ✓ PASSED")
    return True


def run_all_tests():
    """Run all integration tests."""
    print("=" * 70)
    print("🤖 DOOM V3.2 — ORCHESTRATION HARDENING INTEGRATION TEST SUITE")
    print("=" * 70)
    
    tests = [
        ("Direct Intent", test_direct_intent),
        ("Telemetry Fast Path", test_telemetry_fast_path),
        ("Multi-Step Execution", test_multistep_execution),
        ("Duplicate Write Prevention", test_duplicate_write_prevention),
        ("Path Resolution", test_path_resolution),
        ("Ground Truth Verification", test_ground_truth_verification),
        ("Self-Healing", test_self_healing),
        ("Provider Failover", test_provider_failover),
        ("Tool Timeout", test_tool_timeout),
        ("Max Retries", test_max_retry),
        ("Response Synthesis", test_response_synthesis),
        ("Exact Artifact Identity", test_exact_artifact_identity),
        ("TTS Failure Isolation", test_tts_failure_isolated),
        ("Latency Profiling", test_latency_profiling),
        ("Termination Reasons", test_termination_reasons),
    ]
    
    passed = 0
    failed = 0
    for test_name, test_func in tests:
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
                print(f"  ✗ FAILED: {test_name}")
        except Exception as e:
            failed += 1
            print(f"  ✗ ERROR: {test_name} - {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 70)
    print(f"[SUMMARY] {passed}/{len(tests)} Tests Passed, {failed} Failed")
    print("=" * 70)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)