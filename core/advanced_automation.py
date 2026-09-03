import subprocess
import os
import time
import psutil
import schedule
import threading
from datetime import datetime
import json

try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False
    print("PyAutoGUI not available - advanced automation features will be limited")

class DOOMAdvancedAutomation:
    def __init__(self):
        self.scheduler_running = False
        self.automation_log = []
        self.custom_skills = self.load_custom_skills()
        
    def load_custom_skills(self):
        """Load custom automation skills"""
        skills_file = "doom_skills.json"
        try:
            if os.path.exists(skills_file):
                with open(skills_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Could not load custom skills: {e}")
        return {}
    
    def save_custom_skills(self):
        """Save custom automation skills"""
        skills_file = "doom_skills.json"
        try:
            with open(skills_file, 'w') as f:
                json.dump(self.custom_skills, f, indent=2)
        except Exception as e:
            print(f"Could not save custom skills: {e}")
    
    def add_custom_skill(self, name: str, command: str, description: str = ""):
        """Add a custom automation skill"""
        self.custom_skills[name] = {
            'command': command,
            'description': description,
            'created': datetime.now().isoformat()
        }
        self.save_custom_skills()
        return f"Custom skill '{name}' added successfully."
    
    def execute_custom_skill(self, skill_name: str) -> str:
        """Execute a custom automation skill"""
        if skill_name not in self.custom_skills:
            return f"Custom skill '{skill_name}' not found."
        
        try:
            command = self.custom_skills[skill_name]['command']
            result = subprocess.run(command, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                return f"Executed skill '{skill_name}': {result.stdout}"
            else:
                return f"Error executing skill '{skill_name}': {result.stderr}"
                
        except Exception as e:
            return f"Error executing skill '{skill_name}': {str(e)}"
    
    def open_application(self, app_name: str) -> str:
        """Open applications intelligently"""
        app_commands = {
            'chrome': 'start chrome',
            'firefox': 'start firefox',
            'edge': 'start msedge',
            'notepad': 'notepad',
            'calculator': 'calc',
            'word': 'start winword',
            'excel': 'start excel',
            'powerpoint': 'start powerpnt',
            'outlook': 'start outlook',
            'spotify': 'start spotify',
            'discord': 'start discord',
            'steam': 'start steam',
            'vscode': 'code',
            'pycharm': 'pycharm64',
            'photoshop': 'start photoshop',
            'blender': 'start blender'
        }
        
        app_lower = app_name.lower()
        
        # Try exact match first
        if app_lower in app_commands:
            try:
                subprocess.run(app_commands[app_lower], shell=True)
                return f"Opening {app_name}..."
            except Exception as e:
                return f"Could not open {app_name}: {str(e)}"
        
        # Try generic system start
        try:
            subprocess.run(f"start {app_lower}", shell=True)
            return f"Launching {app_name}, Sujal..."
        except Exception:
            return f"Application '{app_name}' not found. Try: Chrome, Firefox, Notepad, Calculator, Word, Excel, etc."
    
    def control_media(self, action: str) -> str:
        """Control media playback"""
        if not PYAUTOGUI_AVAILABLE:
            return "Media control requires PyAutoGUI. Install it with: pip install pyautogui"
        
        try:
            pyautogui.FAILSAFE = True
            
            if action.lower() in ['play', 'pause']:
                pyautogui.press('space')
                return f"Media {action}ed."
            elif action.lower() == 'next':
                pyautogui.press('nexttrack')
                return "Skipped to next track."
            elif action.lower() == 'previous':
                pyautogui.press('prevtrack')
                return "Skipped to previous track."
            elif action.lower() == 'volume up':
                pyautogui.press('volumeup')
                return "Volume increased."
            elif action.lower() == 'volume down':
                pyautogui.press('volumedown')
                return "Volume decreased."
            elif action.lower() == 'mute':
                pyautogui.press('volumemute')
                return "Volume muted."
            else:
                return f"Unknown media action: {action}. Try: play, pause, next, previous, volume up, volume down, mute"
                
        except Exception as e:
            return f"Media control error: {str(e)}"
    
    def take_screenshot(self, filename: str = None) -> str:
        """Take a screenshot"""
        if not PYAUTOGUI_AVAILABLE:
            return "Screenshot requires PyAutoGUI. Install it with: pip install pyautogui"
        
        try:
            if not filename:
                filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            
            screenshot = pyautogui.screenshot()
            screenshot.save(filename)
            return f"Screenshot saved as {filename}"
            
        except Exception as e:
            return f"Screenshot error: {str(e)}"
    
    def click_at_position(self, x: int, y: int) -> str:
        """Click at specific screen position"""
        if not PYAUTOGUI_AVAILABLE:
            return "Screen control requires PyAutoGUI. Install it with: pip install pyautogui"
        
        try:
            pyautogui.click(x, y)
            return f"Clicked at position ({x}, {y})"
        except Exception as e:
            return f"Click error: {str(e)}"
    
    def type_text(self, text: str) -> str:
        """Type text at current cursor position"""
        if not PYAUTOGUI_AVAILABLE:
            return "Text input requires PyAutoGUI. Install it with: pip install pyautogui"
        
        try:
            pyautogui.typewrite(text)
            return f"Typed: {text}"
        except Exception as e:
            return f"Text input error: {str(e)}"
    
    def press_key(self, key: str) -> str:
        """Press a specific key"""
        if not PYAUTOGUI_AVAILABLE:
            return "Key control requires PyAutoGUI. Install it with: pip install pyautogui"
        
        try:
            pyautogui.press(key)
            return f"Pressed key: {key}"
        except Exception as e:
            return f"Key press error: {str(e)}"
    
    def get_system_info(self) -> dict:
        """Get comprehensive system information"""
        try:
            info = {
                'cpu_percent': psutil.cpu_percent(interval=1),
                'memory': psutil.virtual_memory(),
                'disk': psutil.disk_usage('/'),
                'battery': psutil.sensors_battery() if hasattr(psutil, 'sensors_battery') else None,
                'boot_time': datetime.fromtimestamp(psutil.boot_time()),
                'platform': os.name,
                'processes': len(psutil.pids())
            }
            return info
        except Exception as e:
            return {'error': str(e)}
    
    def monitor_system(self, duration: int = 60) -> str:
        """Monitor system for specified duration"""
        try:
            start_time = time.time()
            max_cpu = 0
            max_memory = 0
            
            while time.time() - start_time < duration:
                cpu = psutil.cpu_percent()
                memory = psutil.virtual_memory().percent
                
                max_cpu = max(max_cpu, cpu)
                max_memory = max(max_memory, memory)
                
                time.sleep(5)  # Check every 5 seconds
            
            return f"System monitoring complete. Max CPU: {max_cpu}%, Max Memory: {max_memory}%"
            
        except Exception as e:
            return f"Monitoring error: {str(e)}"
    
    def optimize_system(self) -> str:
        """Provide system optimization recommendations"""
        try:
            info = self.get_system_info()
            recommendations = []
            
            if info.get('cpu_percent', 0) > 80:
                recommendations.append("High CPU usage detected. Consider closing unnecessary applications.")
            
            if info.get('memory', {}).get('percent', 0) > 85:
                recommendations.append("High memory usage. Consider restarting applications or the system.")
            
            disk_usage = info.get('disk', {}).get('percent', 0)
            if disk_usage > 90:
                recommendations.append("Disk space is low. Consider cleaning up files.")
            
            if not recommendations:
                recommendations.append("System appears to be running optimally.")
            
            return "System optimization recommendations: " + "; ".join(recommendations)
            
        except Exception as e:
            return f"Optimization analysis error: {str(e)}"
    
    def schedule_task(self, task_name: str, time_str: str, command: str) -> str:
        """Schedule a task"""
        try:
            def run_task():
                subprocess.run(command, shell=True)
                self.automation_log.append(f"Executed scheduled task: {task_name}")
            
            schedule.every().day.at(time_str).do(run_task)
            return f"Task '{task_name}' scheduled for {time_str}"
            
        except Exception as e:
            return f"Scheduling error: {str(e)}"
    
    def run_scheduler(self):
        """Run the task scheduler in background"""
        if self.scheduler_running:
            return
        
        self.scheduler_running = True
        
        def scheduler_worker():
            while self.scheduler_running:
                schedule.run_pending()
                time.sleep(1)
        
        scheduler_thread = threading.Thread(target=scheduler_worker, daemon=True)
        scheduler_thread.start()
    
    def stop_scheduler(self):
        """Stop the task scheduler"""
        self.scheduler_running = False
        schedule.clear()
    
    def get_automation_log(self) -> list:
        """Get automation activity log"""
        return self.automation_log
    
    def clear_log(self):
        """Clear automation log"""
        self.automation_log = []
        return "Automation log cleared."

# Global instance
advanced_automation = DOOMAdvancedAutomation()

def open_app(app_name: str) -> str:
    """Open application"""
    return advanced_automation.open_application(app_name)

def control_media(action: str) -> str:
    """Control media playback"""
    return advanced_automation.control_media(action)

def take_screenshot(filename: str = None) -> str:
    """Take screenshot"""
    return advanced_automation.take_screenshot(filename)

def get_system_info() -> dict:
    """Get system information"""
    return advanced_automation.get_system_info()

def monitor_system(duration: int = 60) -> str:
    """Monitor system"""
    return advanced_automation.monitor_system(duration)

def optimize_system() -> str:
    """Optimize system"""
    return advanced_automation.optimize_system()

def add_custom_skill(name: str, command: str, description: str = "") -> str:
    """Add custom skill"""
    return advanced_automation.add_custom_skill(name, command, description)

def execute_custom_skill(skill_name: str) -> str:
    """Execute custom skill"""
    return advanced_automation.execute_custom_skill(skill_name)
