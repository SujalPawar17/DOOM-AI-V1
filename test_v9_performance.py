#!/usr/bin/env python3
"""V9 Performance Test - Measure synthesis latency."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.voice import VoiceEngine, VoiceEngineConfig
from core.voice.personality import TACTICAL_VOICE_PERSONALITY


def measure_first_init():
    """Measure first engine initialization time."""
    print("Measuring first engine initialization...")
    start = time.perf_counter()
    engine = VoiceEngine(config=VoiceEngineConfig(voice_profile="tactical", headless_mode=True))
    elapsed = time.perf_counter() - start
    print(f"  First init: {elapsed*1000:.1f}ms")
    return engine, elapsed


def measure_subsequent_init():
    """Measure subsequent engine initialization time."""
    print("Measuring subsequent engine initialization...")
    start = time.perf_counter()
    engine = VoiceEngine(config=VoiceEngineConfig(voice_profile="tactical", headless_mode=True))
    elapsed = time.perf_counter() - start
    print(f"  Subsequent init: {elapsed*1000:.1f}ms")
    return engine, elapsed


def measure_synthesis_latency(engine, iterations=10):
    """Measure speech synthesis latency."""
    print(f"Measuring synthesis latency ({iterations} iterations)...")
    
    texts = [
        "System online.",
        "Task completed successfully.",
        "Warning: This action will overwrite the existing configuration.",
        "Error: Connection failed. I can retry the operation.",
        "Understood. Executing command now.",
        "Let me analyze that request.",
        "Critical failure detected. Immediate action required.",
        "Good day, Sujal. Systems are operational.",
        "The file at C:\\Users\\Sujal\\Documents\\test.py has been created.",
        "Processing parameters... one moment please.",
    ]
    
    latencies = []
    for i, text in enumerate(texts * (iterations // len(texts) + 1)):
        if i >= iterations:
            break
        start = time.perf_counter()
        result = engine.speak_with_delivery(text, context="normal", priority=1)
        elapsed = time.perf_counter() - start
        latencies.append(elapsed * 1000)
    
    avg_latency = sum(latencies) / len(latencies)
    min_latency = min(latencies)
    max_latency = max(latencies)
    
    print(f"  Average: {avg_latency:.1f}ms")
    print(f"  Min: {min_latency:.1f}ms")
    print(f"  Max: {max_latency:.1f}ms")
    
    return latencies


def measure_delivery_classification_speed(iterations=1000):
    """Measure response classification speed."""
    from core.voice.delivery import classify_response_category
    
    texts = [
        "What is this?",
        "Run the script",
        "Understood",
        "Done",
        "Warning: dangerous",
        "Error: failed",
        "Critical failure",
        "First, do this",
        "Because the system works",
        "Status: online",
        "Just kidding",
        "Hello there",
        "Goodbye",
    ]
    
    print(f"Measuring classification speed ({iterations} iterations)...")
    start = time.perf_counter()
    for i in range(iterations):
        classify_response_category(texts[i % len(texts)])
    elapsed = time.perf_counter() - start
    per_call = (elapsed / iterations) * 1_000_000  # microseconds
    print(f"  Per classification: {per_call:.2f}µs")
    return per_call


def measure_segmentation_speed(iterations=1000):
    """Measure text segmentation speed."""
    from core.voice.delivery import segment_for_delivery
    
    texts = [
        "This is a normal sentence.",
        "File at C:\\Users\\test.py and also /home/user/file.txt",
        "Visit https://example.com/api for more info.",
        "Version 1.2.3 and v2.0.0-beta available.",
        "Set $HOME or ${PATH} or %TEMP%.",
        "Call my_function() or obj.method().",
        "Run python script.py --flag=value.",
        "Latency is 50ms and memory 512mb.",
        "Address 0x7fff1234 and 0xDEADBEEF.",
        "ID: 123e4567-e89b-12d3-a456-426614174000.",
    ]
    
    print(f"Measuring segmentation speed ({iterations} iterations)...")
    start = time.perf_counter()
    for i in range(iterations):
        segment_for_delivery(texts[i % len(texts)])
    elapsed = time.perf_counter() - start
    per_call = (elapsed / iterations) * 1_000_000
    print(f"  Per segmentation: {per_call:.2f}µs")
    return per_call


def main():
    print("=" * 60)
    print("V9 VOICE PERSONALITY - PERFORMANCE TEST")
    print("=" * 60)
    
    # Test classification speed
    measure_delivery_classification_speed()
    print()
    
    # Test segmentation speed
    measure_segmentation_speed()
    print()
    
    # Test engine initialization
    engine1, init1 = measure_first_init()
    print()
    
    engine2, init2 = measure_subsequent_init()
    print()
    
    # Test synthesis latency (headless mode - no actual audio)
    measure_synthesis_latency(engine1, iterations=20)
    print()
    
    # Shutdown
    engine1.shutdown()
    engine2.shutdown()
    
    print("=" * 60)
    print("PERFORMANCE TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()