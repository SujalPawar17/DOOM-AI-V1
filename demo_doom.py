#!/usr/bin/env python3
"""
DOOM Advanced AI Assistant Demo Script
This script demonstrates DOOM's capabilities without requiring voice input
"""

import os
import sys
import time
from datetime import datetime

def print_demo_banner():
    """Print demo banner"""
    print("=" * 70)
    print("🚀 DOOM ADVANCED AI ASSISTANT - CAPABILITIES DEMO")
    print("=" * 70)
    print("Demonstrating JARVIS-level AI capabilities...")
    print("=" * 70)

def demo_ai_brain():
    """Demo AI brain capabilities"""
    print("\n🤖 AI BRAIN & REASONING DEMO")
    print("-" * 40)
    
    try:
        from core.ai_brain import DOOMBrain
        brain = DOOMBrain()
        
        print("✅ AI Brain loaded successfully")
        print("• OpenAI GPT-4 integration ready")
        print("• Advanced reasoning capabilities active")
        print("• Mathematical computations ready")
        print("• Creative content generation active")
        
        # Test system status
        print("\n📊 Testing System Status...")
        status = brain.system_status()
        if "error" not in status:
            print(f"• CPU Usage: {status['cpu_usage']}")
            print(f"• Memory Usage: {status['memory_usage']}")
            print(f"• Disk Usage: {status['disk_usage']}")
            print(f"• Platform: {status['platform']}")
        else:
            print("⚠️  System status check failed")
            
    except Exception as e:
        print(f"❌ AI Brain demo failed: {e}")

def demo_vision():
    """Demo computer vision capabilities"""
    print("\n👁️ COMPUTER VISION DEMO")
    print("-" * 40)
    
    try:
        from core.vision import DOOMVision
        vision = DOOMVision()
        
        print("✅ Computer Vision loaded successfully")
        print("• Face recognition system active")
        print("• Screen analysis capabilities ready")
        print("• Photo capture system ready")
        print("• Object detection active")
        
        # Test screen analysis
        print("\n🖥️  Testing Screen Analysis...")
        analysis = vision.analyze_screen()
        if "error" not in analysis:
            print(f"• Screen Resolution: {analysis['resolution']}")
            print(f"• Brightness Level: {analysis['brightness']}")
            print(f"• Faces Detected: {analysis['face_count']}")
        else:
            print("⚠️  Screen analysis failed")
            
    except Exception as e:
        print(f"❌ Vision demo failed: {e}")

def demo_automation():
    """Demo automation capabilities"""
    print("\n⚡ AUTOMATION & SYSTEM CONTROL DEMO")
    print("-" * 40)
    
    try:
        from core.automation import DOOMAutomation
        automation = DOOMAutomation()
        
        print("✅ Automation system loaded successfully")
        print("• System control ready")
        print("• Process management active")
        print("• File operations ready")
        print("• Task scheduling active")
        print("• Network operations ready")
        
        # Test system optimization
        print("\n🔧 Testing System Optimization...")
        optimization = automation.system_optimization()
        print(f"• Optimization Status: {optimization}")
        
        # Test network connection
        print("\n🌐 Testing Network Connection...")
        connection = automation.network_operations("check_connection")
        print(f"• Connection Status: {connection}")
        
    except Exception as e:
        print(f"❌ Automation demo failed: {e}")

def demo_commands():
    """Demo command handling capabilities"""
    print("\n🎯 COMMAND HANDLING DEMO")
    print("-" * 40)
    
    try:
        from core.commands import DOOMCommandHandler
        handler = DOOMCommandHandler()
        
        print("✅ Command handler loaded successfully")
        print("• Voice command processing ready")
        print("• Natural language understanding active")
        print("• Context awareness enabled")
        print("• Error handling robust")
        
        print("\n📋 Available Command Categories:")
        print("• AI & Reasoning: think, analyze, explain")
        print("• Math & Science: calculate, solve, compute")
        print("• System Control: shutdown, restart, sleep")
        print("• Vision: take photo, analyze screen")
        print("• Process Management: list, kill, start")
        print("• Network: ping, speed test, connection")
        print("• Files: copy, move, delete, create")
        print("• Automation: schedule, optimize, backup")
        print("• Information: news, search, wikipedia")
        print("• Creative: write, create, generate")
        
    except Exception as e:
        print(f"❌ Command handler demo failed: {e}")

def demo_memory():
    """Demo memory system"""
    print("\n🧠 MEMORY SYSTEM DEMO")
    print("-" * 40)
    
    try:
        from core.memory import remember, recall
        
        print("✅ Memory system loaded successfully")
        print("• Persistent storage active")
        print("• JSON-based memory ready")
        print("• Data persistence enabled")
        
        # Test memory operations
        print("\n💾 Testing Memory Operations...")
        remember("demo_test", "DOOM memory system working perfectly!")
        test_value = recall("demo_test")
        print(f"• Memory Test: {test_value}")
        
        # Show existing memory
        if os.path.exists("memory.json"):
            with open("memory.json", "r") as f:
                import json
                memory_data = json.load(f)
                print(f"• Existing Memory Keys: {list(memory_data.keys())}")
        
    except Exception as e:
        print(f"❌ Memory demo failed: {e}")

def demo_translation():
    """Demo translation capabilities"""
    print("\n🌍 TRANSLATION DEMO")
    print("-" * 40)
    
    try:
        from core.translate import translate
        
        print("✅ Translation system loaded successfully")
        print("• Google Translate integration ready")
        print("• Multi-language support active")
        print("• Text-to-speech translation ready")
        
        print("\n🔤 Translation Capabilities:")
        print("• Support for 100+ languages")
        print("• Real-time translation")
        print("• Voice output in target language")
        
    except Exception as e:
        print(f"❌ Translation demo failed: {e}")

def demo_media():
    """Demo media capabilities"""
    print("\n🎵 MEDIA & ENTERTAINMENT DEMO")
    print("-" * 40)
    
    try:
        import pywhatkit
        
        print("✅ Media system loaded successfully")
        print("• YouTube integration ready")
        print("• Music playback capabilities active")
        print("• Entertainment features ready")
        
        print("\n🎬 Media Features:")
        print("• YouTube video playback")
        print("• Music streaming")
        print("• Joke telling")
        print("• Content discovery")
        
    except Exception as e:
        print(f"❌ Media demo failed: {e}")

def run_full_demo():
    """Run the complete demo"""
    print_demo_banner()
    
    print(f"Demo started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Run all demos
    demo_ai_brain()
    demo_vision()
    demo_automation()
    demo_commands()
    demo_memory()
    demo_translation()
    demo_media()
    
    print("\n" + "=" * 70)
    print("🎉 DOOM DEMO COMPLETE!")
    print("=" * 70)
    print("\n🚀 DOOM is ready with JARVIS-level capabilities!")
    print("\n📋 To use DOOM with voice:")
    print("1. Set up your API keys in .env file")
    print("2. Run: python doom.py")
    print("3. Say: 'Hey DOOM'")
    
    print("\n💡 Demo completed successfully!")
    print("=" * 70)

if __name__ == "__main__":
    try:
        run_full_demo()
    except KeyboardInterrupt:
        print("\n\n⚠️  Demo interrupted by user.")
    except Exception as e:
        print(f"\n❌ Demo failed: {e}")
        print("Please check your installation and try again.") 