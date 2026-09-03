import sys
import time
import random
from colorama import init, Fore, Back, Style

# Initialize colorama for cross-platform colored output
init(autoreset=True)

class DOOMUI:
    def __init__(self):
        self.colors = {
            'jarvis_blue': Fore.CYAN,
            'jarvis_gold': Fore.YELLOW,
            'jarvis_green': Fore.GREEN,
            'jarvis_red': Fore.RED,
            'jarvis_purple': Fore.MAGENTA,
            'jarvis_white': Fore.WHITE,
            'jarvis_bright': Style.BRIGHT
        }
        
    def jarvis_header(self):
        """Display JARVIS-like header"""
        print(f"\n{self.colors['jarvis_blue']}{'='*60}")
        print(f"{self.colors['jarvis_bright']}{self.colors['jarvis_blue']}[DOOM] Advanced AI Assistant")
        print(f"{self.colors['jarvis_gold']}[TARGET] JARVIS-Level Intelligence - 100% FREE")
        print(f"{self.colors['jarvis_blue']}{'='*60}\n")
    
    def type_out(self, text, color='jarvis_white', speed=0.02):
        """JARVIS-like typing effect"""
        colored_text = f"{self.colors[color]}{text}"
        for char in colored_text:
            sys.stdout.write(char)
            sys.stdout.flush()
            time.sleep(speed)
        print()
    
    def thinking_animation(self, duration=2):
        """JARVIS-like thinking animation"""
        thinking_chars = ["|", "/", "-", "\\"]
        start_time = time.time()
        
        while time.time() - start_time < duration:
            for char in thinking_chars:
                print(f"\r{self.colors['jarvis_blue']}DOOM is thinking {char}", end="", flush=True)
                time.sleep(0.1)
        
        print(f"\r{self.colors['jarvis_green']}DOOM is ready [OK]" + " " * 20)
    
    def status_message(self, message, status="info"):
        """Display status messages with colors"""
        colors = {
            'info': self.colors['jarvis_blue'],
            'success': self.colors['jarvis_green'],
            'warning': self.colors['jarvis_gold'],
            'error': self.colors['jarvis_red'],
            'processing': self.colors['jarvis_purple']
        }
        
        icons = {
            'info': '[i]',
            'success': '[OK]',
            'warning': '[!]',
            'error': '[X]',
            'processing': '[>]'
        }
        
        color = colors.get(status, self.colors['jarvis_white'])
        icon = icons.get(status, '📋')
        
        print(f"{color}{icon} {message}")
    
    def listening_indicator(self):
        """Show JARVIS-like listening indicator"""
        print(f"{self.colors['jarvis_blue']}[MIC] Listening...")
    
    def wake_word_detected(self):
        """Show wake word detection"""
        print(f"{self.colors['jarvis_green']}[WAKE] Wake word detected!")
    
    def processing_command(self, command):
        """Show command processing"""
        print(f"{self.colors['jarvis_purple']}[PROC] Processing: {self.colors['jarvis_white']}{command}")
    
    def system_ready(self):
        """Show system ready status"""
        print(f"{self.colors['jarvis_green']}[READY] DOOM is online.")
    
    def shutdown_message(self):
        """Show shutdown message"""
        print(f"\n{self.colors['jarvis_red']}[STOP] Shutdown requested by user.")
    
    def error_message(self, error):
        """Show error message"""
        print(f"{self.colors['jarvis_red']}[ERR] Error: {error}")
    
    def success_message(self, message):
        """Show success message"""
        print(f"{self.colors['jarvis_green']}[OK] {message}")
    
    def loading_sequence(self):
        """JARVIS-like loading sequence"""
        loading_items = [
            "Initializing AI Brain...",
            "Loading Voice Recognition...",
            "Setting up Computer Vision...",
            "Preparing Automation Systems...",
            "Calibrating Sensors...",
            "System Ready!"
        ]
        
        for item in loading_items:
            self.status_message(item, "processing")
            time.sleep(0.5)
        
        print()
    
    def conversation_bubble(self, text, speaker="DOOM"):
        """Display conversation in chat-like format"""
        if speaker == "DOOM":
            print(f"\n{self.colors['jarvis_blue']}[DOOM]: {self.colors['jarvis_white']}{text}")
        else:
            print(f"\n{self.colors['jarvis_gold']}[SUJAL]: {self.colors['jarvis_white']}{text}")
    
    def matrix_effect(self, text="DOOM"):
        """Matrix-like effect for startup"""
        chars = "01"
        for i in range(20):
            line = ""
            for j in range(60):
                line += random.choice(chars)
            print(f"{self.colors['jarvis_green']}{line}")
            time.sleep(0.1)
        
        print(f"\n{self.colors['jarvis_bright']}{self.colors['jarvis_blue']}{text} ONLINE")
        time.sleep(1)
    
    def progress_bar(self, current, total, task="Loading"):
        """Show progress bar"""
        percent = (current / total) * 100
        bar_length = 30
        filled_length = int(bar_length * current // total)
        bar = '#' * filled_length + '-' * (bar_length - filled_length)
        
        print(f"\r{self.colors['jarvis_blue']}{task}: |{bar}| {percent:.1f}%", end="", flush=True)
        
        if current == total:
            print(f"\n{self.colors['jarvis_green']}[OK] {task} Complete!")

# Global UI instance
ui = DOOMUI()

def show_jarvis_startup():
    """Show JARVIS-like startup sequence"""
    ui.jarvis_header()
    ui.loading_sequence()
    ui.system_ready()

def show_listening():
    """Show listening indicator"""
    ui.listening_indicator()

def show_processing(command):
    """Show command processing"""
    ui.processing_command(command)

def show_wake_word_detected():
    """Show wake word detection"""
    ui.wake_word_detected()

def show_thinking():
    """Show thinking animation"""
    ui.thinking_animation()

def show_conversation(text, speaker="DOOM"):
    """Show conversation"""
    ui.conversation_bubble(text, speaker)
