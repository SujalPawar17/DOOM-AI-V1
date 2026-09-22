#!/usr/bin/env python3
"""V9 Final Acceptance Tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.voice.delivery import classify_response_category, segment_for_delivery, ResponseCategory
from core.voice import VoiceEngine, VoiceEngineConfig
from core.voice.personality import TACTICAL_VOICE_PERSONALITY


def test_delivery_categories():
    """Test all 15 delivery categories."""
    print("Testing all 15 delivery categories...")
    
    test_cases = [
        ("Normal response", "The system is ready.", "normal_response"),
        ("Question", "What is the status?", "question"),
        ("Confirmation", "Understood.", "confirmation"),
        ("Explanation", "The system works because the connection is established.", "explanation"),
        ("Instruction", "First, run the script. Then check the logs.", "instruction"),
        ("Command", "Run the script now.", "command"),
        ("Warning", "Warning: This will overwrite the file.", "warning"),
        ("Error", "Error: Connection failed.", "error"),
        ("Success", "Done. Task completed successfully.", "success"),
        ("Status", "Status: System online.", "status"),
        ("Thinking", "Processing...", "thinking"),
        ("Critical", "Critical failure detected.", "critical"),
        ("Humor", "Just kidding!", "humor"),
        ("Greeting", "Hello Sujal.", "greeting"),
        ("Farewell", "Goodbye Sujal.", "farewell"),
    ]
    
    all_passed = True
    for name, text, expected in test_cases:
        category = classify_response_category(text, context="")
        status = "PASS" if category.value == expected else "FAIL"
        if status == "FAIL":
            all_passed = False
        print(f"  {status} {name}: {category.value} (expected: {expected})")
    
    return all_passed


def test_technical_content_protection():
    """Test technical content protection."""
    print("\nTesting technical content protection...")
    
    tech_texts = [
        r"File at C:\Users\test.py",
        "Visit https://example.com/api",
        "IP 192.168.1.100",
        "Version v9.3.8",
        "Run python script.py --flag=value",
        "Set $HOME or ${PATH}",
        "Call my_function()",
        "Latency 50ms",
        "Address 0x7fff1234",
        "ID: 123e4567-e89b-12d3-a456-426614174000",
    ]
    
    all_passed = True
    for text in tech_texts:
        segments = segment_for_delivery(text)
        has_technical = any(s[0] == "technical" for s in segments)
        status = "PASS" if has_technical else "FAIL"
        if status == "FAIL":
            all_passed = False
        print(f"  {status} Technical: {text}")
    
    return all_passed


def test_delivery_with_engine():
    """Test delivery through VoiceEngine."""
    print("\nTesting delivery through VoiceEngine...")
    
    engine = VoiceEngine(config=VoiceEngineConfig(voice_profile="tactical", headless_mode=False))
    
    test_cases = [
        ("Normal", "The system is ready.", "normal"),
        ("Question", "What is the status?", "question"),
        ("Confirmation", "Understood.", "confirmation"),
        ("Explanation", "The system works because the connection is established.", "explanation"),
        ("Instruction", "First, run the script. Then check the logs.", "instruction"),
        ("Command", "Run the script now.", "command"),
        ("Warning", "Warning: This will overwrite the file.", "warning"),
        ("Error", "Error: Connection failed.", "error"),
        ("Success", "Done. Task completed successfully.", "success"),
        ("Status", "Status: System online.", "status"),
        ("Thinking", "Processing...", "thinking"),
        ("Critical", "Critical failure detected.", "critical"),
        ("Humor", "Just kidding!", "humor"),
        ("Greeting", "Hello Sujal.", "greeting"),
        ("Farewell", "Goodbye Sujal.", "farewell"),
    ]
    
    all_passed = True
    for name, text, context in test_cases:
        result = engine.speak_with_delivery(text, context=context)
        status = "PASS" if result.value == "AVAILABLE" else "FAIL"
        if status == "FAIL":
            all_passed = False
        print(f"  {status} {name}: {result.value}")
    
    engine.shutdown()
    return all_passed


def main():
    print("=" * 60)
    print("V9 FINAL ACCEPTANCE TESTS")
    print("=" * 60)
    
    results = []
    results.append(("Delivery Categories", test_delivery_categories()))
    results.append(("Technical Content Protection", test_technical_content_protection()))
    results.append(("VoiceEngine Delivery", test_delivery_with_engine()))
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_passed = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {status} {name}")
        if not passed:
            all_passed = False
    
    if all_passed:
        print("\nALL ACCEPTANCE TESTS PASSED!")
    else:
        print("\nSOME ACCEPTANCE TESTS FAILED!")
    
    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)