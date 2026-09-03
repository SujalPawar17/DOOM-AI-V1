import os
import shutil
import subprocess
import platform
import psutil
import schedule
import time
import json
import requests
import pyautogui
from datetime import datetime, timedelta
from typing import Dict, List, Any
import threading

class DOOMAutomation:
    def __init__(self):
        self.system = platform.system()
        self.automation_tasks = {}
        self.load_automation_config()
        
    def load_automation_config(self):
        """Load automation configuration"""
        try:
            if os.path.exists("automation_config.json"):
                with open("automation_config.json", "r") as f:
                    self.automation_tasks = json.load(f)
        except Exception as e:
            print(f"Error loading automation config: {e}")
    
    def save_automation_config(self):
        """Save automation configuration"""
        try:
            with open("automation_config.json", "w") as f:
                json.dump(self.automation_tasks, f, indent=2)
        except Exception as e:
            print(f"Error saving automation config: {e}")
    
    def system_control(self, command: str) -> str:
        """Control system operations like JARVIS"""
        try:
            if self.system == "Windows":
                if command == "shutdown":
                    os.system("shutdown /s /t 60")
                    return "System will shutdown in 60 seconds. Say 'cancel shutdown' to abort."
                elif command == "restart":
                    os.system("shutdown /r /t 60")
                    return "System will restart in 60 seconds. Say 'cancel restart' to abort."
                elif command == "cancel shutdown" or command == "cancel restart":
                    os.system("shutdown /a")
                    return "Shutdown/restart cancelled."
                elif command == "sleep":
                    os.system("powercfg /hibernate off")
                    os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
                    return "Putting system to sleep."
                elif command == "lock":
                    os.system("rundll32.exe user32.dll,LockWorkStation")
                    return "System locked."
            else:  # Linux/Mac
                if command == "shutdown":
                    os.system("shutdown -h 1")
                    return "System will shutdown in 1 minute."
                elif command == "restart":
                    os.system("shutdown -r 1")
                    return "System will restart in 1 minute."
                elif command == "sleep":
                    os.system("systemctl suspend")
                    return "Putting system to sleep."
            
            return f"System command '{command}' executed successfully."
        except Exception as e:
            return f"System control error: {e}"
    
    def file_operations(self, operation: str, source: str, destination: str = None) -> str:
        """Advanced file operations like JARVIS"""
        try:
            if operation == "copy":
                if os.path.isfile(source):
                    shutil.copy2(source, destination)
                    return f"File copied from {source} to {destination}"
                elif os.path.isdir(source):
                    shutil.copytree(source, destination)
                    return f"Directory copied from {source} to {destination}"
                else:
                    return f"Source {source} not found"
            
            elif operation == "move":
                shutil.move(source, destination)
                return f"Moved {source} to {destination}"
            
            elif operation == "delete":
                if os.path.isfile(source):
                    os.remove(source)
                    return f"File {source} deleted"
                elif os.path.isdir(source):
                    shutil.rmtree(source)
                    return f"Directory {source} deleted"
                else:
                    return f"Source {source} not found"
            
            elif operation == "create":
                if source.endswith('/') or os.path.splitext(source)[1] == '':
                    os.makedirs(source, exist_ok=True)
                    return f"Directory {source} created"
                else:
                    with open(source, 'w') as f:
                        f.write("")
                    return f"File {source} created"
            
            else:
                return f"Unknown operation: {operation}"
                
        except Exception as e:
            return f"File operation error: {e}"
    
    def process_management(self, action: str, process_name: str = None) -> str:
        """Manage system processes like JARVIS"""
        try:
            if action == "list":
                processes = []
                for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
                    try:
                        processes.append(proc.info)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                
                # Sort by CPU usage
                processes.sort(key=lambda x: x['cpu_percent'] or 0, reverse=True)
                
                result = "Top processes by CPU usage:\n"
                for i, proc in enumerate(processes[:10]):
                    result += f"{i+1}. {proc['name']} (PID: {proc['pid']}) - CPU: {proc['cpu_percent']:.1f}%, RAM: {proc['memory_percent']:.1f}%\n"
                
                return result
            
            elif action == "kill" and process_name:
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        if process_name.lower() in proc.info['name'].lower():
                            proc.kill()
                            return f"Process {proc.info['name']} (PID: {proc.info['pid']}) terminated"
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                
                return f"Process {process_name} not found"
            
            elif action == "start" and process_name:
                if self.system == "Windows":
                    subprocess.Popen(process_name, shell=True)
                else:
                    subprocess.Popen(process_name.split(), shell=True)
                return f"Started process: {process_name}"
            
            else:
                return f"Unknown action: {action}"
                
        except Exception as e:
            return f"Process management error: {e}"
    
    def network_operations(self, operation: str, target: str = None) -> str:
        """Network operations like JARVIS"""
        try:
            if operation == "ping":
                if not target:
                    target = "8.8.8.8"  # Google DNS
                
                if self.system == "Windows":
                    result = subprocess.run(['ping', '-n', '4', target], capture_output=True, text=True)
                else:
                    result = subprocess.run(['ping', '-c', '4', target], capture_output=True, text=True)
                
                return f"Ping results for {target}:\n{result.stdout}"
            
            elif operation == "speed_test":
                try:
                    import speedtest
                    st = speedtest.Speedtest()
                    st.get_best_server()
                    
                    download_speed = st.download() / 1_000_000  # Convert to Mbps
                    upload_speed = st.upload() / 1_000_000
                    
                    return f"Speed test results:\nDownload: {download_speed:.2f} Mbps\nUpload: {upload_speed:.2f} Mbps"
                except ImportError:
                    return "Speedtest library not available"
            
            elif operation == "check_connection":
                try:
                    response = requests.get("http://www.google.com", timeout=5)
                    return f"Internet connection: Active (Status: {response.status_code})"
                except:
                    return "Internet connection: Inactive"
            
            else:
                return f"Unknown network operation: {operation}"
                
        except Exception as e:
            return f"Network operation error: {e}"
    
    def schedule_task(self, task_name: str, schedule_time: str, command: str) -> str:
        """Schedule automated tasks like JARVIS"""
        try:
            def run_scheduled_task():
                print(f"Executing scheduled task: {task_name}")
                # Execute the command
                if command.startswith("speak:"):
                    from core.cinematic_voice import speak, stop_speaking
                    stop_speaking()
                    speak(command[6:])
                elif command.startswith("system:"):
                    self.system_control(command[7:])
                elif command.startswith("file:"):
                    parts = command[5:].split("|")
                    if len(parts) >= 3:
                        self.file_operations(parts[0], parts[1], parts[2])
            
            # Parse schedule time (format: "HH:MM" or "every X minutes")
            if schedule_time.startswith("every"):
                minutes = int(schedule_time.split()[1])
                schedule.every(minutes).minutes.do(run_scheduled_task)
                schedule_type = f"every {minutes} minutes"
            else:
                schedule.every().day.at(schedule_time).do(run_scheduled_task)
                schedule_type = f"daily at {schedule_time}"
            
            # Store task info
            self.automation_tasks[task_name] = {
                "schedule": schedule_type,
                "command": command,
                "created": datetime.now().isoformat()
            }
            
            self.save_automation_config()
            
            return f"Task '{task_name}' scheduled for {schedule_type}"
            
        except Exception as e:
            return f"Task scheduling error: {e}"
    
    def run_scheduler(self):
        """Run the task scheduler in background"""
        def scheduler_loop():
            while True:
                schedule.run_pending()
                time.sleep(1)
        
        scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
        scheduler_thread.start()
        return "Task scheduler started in background"
    
    def list_scheduled_tasks(self) -> str:
        """List all scheduled tasks"""
        if not self.automation_tasks:
            return "No scheduled tasks found"
        
        result = "Scheduled tasks:\n"
        for task_name, task_info in self.automation_tasks.items():
            result += f"- {task_name}: {task_info['schedule']} -> {task_info['command']}\n"
        
        return result
    
    def cancel_task(self, task_name: str) -> str:
        """Cancel a scheduled task"""
        if task_name in self.automation_tasks:
            del self.automation_tasks[task_name]
            self.save_automation_config()
            return f"Task '{task_name}' cancelled"
        else:
            return f"Task '{task_name}' not found"
    
    def system_optimization(self) -> str:
        """System optimization like JARVIS"""
        try:
            optimizations = []
            
            # Check disk space
            disk = psutil.disk_usage('/')
            if disk.percent > 90:
                optimizations.append("Critical: Disk space below 10%")
            elif disk.percent > 80:
                optimizations.append("Warning: Disk space below 20%")
            
            # Check memory
            memory = psutil.virtual_memory()
            if memory.percent > 90:
                optimizations.append("Critical: Memory usage above 90%")
            elif memory.percent > 80:
                optimizations.append("Warning: Memory usage above 80%")
            
            # Check CPU
            cpu_percent = psutil.cpu_percent(interval=1)
            if cpu_percent > 90:
                optimizations.append("Critical: CPU usage above 90%")
            elif cpu_percent > 80:
                optimizations.append("Warning: CPU usage above 80%")
            
            if not optimizations:
                optimizations.append("System is running optimally!")
            
            return "\n".join(optimizations)
            
        except Exception as e:
            return f"System optimization check error: {e}"
    
    def backup_system(self, backup_path: str = None) -> str:
        """Create system backup like JARVIS"""
        try:
            if not backup_path:
                backup_path = f"doom_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            # Create backup directory
            os.makedirs(backup_path, exist_ok=True)
            
            # Backup important files
            important_files = [
                "memory.json",
                "automation_config.json"
            ]
            
            for file_path in important_files:
                if os.path.exists(file_path):
                    shutil.copy2(file_path, backup_path)
            
            # Backup core directory
            if os.path.exists("core"):
                shutil.copytree("core", os.path.join(backup_path, "core"), dirs_exist_ok=True)
            
            return f"System backup created at: {backup_path}"
            
        except Exception as e:
            return f"Backup error: {e}" 