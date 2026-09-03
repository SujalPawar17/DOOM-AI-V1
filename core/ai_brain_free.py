import os
from typing import Dict, List, Any
import json
import requests
import platform
from datetime import datetime
import random
import re

class DOOMBrainFree:
    def __init__(self):
        self.conversation_history = []
        self.knowledge_base = self.load_knowledge_base()
        self.system_prompt = """You are DOOM, an advanced AI assistant like JARVIS from Iron Man. 
        You are Sujal's personal AI companion with the following capabilities:
        - Advanced reasoning and problem solving
        - Real-time system monitoring and control
        - Web search and information gathering
        - Mathematical computations
        - News and weather updates
        - Creative tasks and content generation
        - System automation and optimization
        
        Always respond in a confident, helpful manner. You are fast, efficient, and always ready to assist Sujal.
        Use your advanced capabilities to provide the best possible solutions."""
        
    def load_knowledge_base(self) -> Dict[str, Any]:
        """Load built-in knowledge base"""
        return {
            "math_formulas": {
                "area_circle": "π × r²",
                "area_triangle": "½ × base × height",
                "area_rectangle": "length × width",
                "volume_cube": "side³",
                "volume_sphere": "4/3 × π × r³",
                "pythagoras": "a² + b² = c²",
                "quadratic": "x = (-b ± √(b² - 4ac)) / 2a"
            },
            "science_facts": [
                "The speed of light is approximately 299,792,458 meters per second",
                "Water boils at 100°C (212°F) at sea level",
                "The Earth's circumference is about 40,075 kilometers",
                "A human body contains about 60% water",
                "The human brain uses about 20% of the body's total energy",
                "Lightning can reach temperatures of 30,000°C (54,000°F)",
                "The Great Wall of China is over 13,000 miles long",
                "A day on Venus is longer than its year"
            ],
            "jokes": [
                "Why don't scientists trust atoms? Because they make up everything!",
                "Why did the scarecrow win an award? Because he was outstanding in his field!",
                "What do you call a fake noodle? An impasta!",
                "Why did the math book look so sad? Because it had too many problems!",
                "What do you call a bear with no teeth? A gummy bear!",
                "Why don't eggs tell jokes? They'd crack each other up!",
                "What do you call a fish wearing a bowtie? So-fish-ticated!",
                "Why can't you give Elsa a balloon? She will let it go!"
            ],
            "wisdom": [
                "The only way to do great work is to love what you do.",
                "Success is not final, failure is not fatal: it is the courage to continue that counts.",
                "The future belongs to those who believe in the beauty of their dreams.",
                "In the middle of difficulty lies opportunity.",
                "The best way to predict the future is to create it.",
                "Life is what happens when you're busy making other plans.",
                "The journey of a thousand miles begins with one step.",
                "What you get by achieving your goals is not as important as what you become by achieving your goals."
            ]
        }
    
    def think(self, query: str, context: str = "") -> str:
        """Free AI reasoning using pattern matching and knowledge base"""
        query_lower = query.lower()
        
        # Pattern matching for common queries
        if any(word in query_lower for word in ["explain", "what is", "how does", "why"]):
            return self.explain_topic(query)
        elif any(word in query_lower for word in ["calculate", "solve", "math", "equation"]):
            return self.solve_math(query)
        elif any(word in query_lower for word in ["joke", "funny", "humor"]):
            return random.choice(self.knowledge_base["jokes"])
        elif any(word in query_lower for word in ["wisdom", "advice", "motivation"]):
            return random.choice(self.knowledge_base["wisdom"])
        elif any(word in query_lower for word in ["fact", "science", "knowledge"]):
            return random.choice(self.knowledge_base["science_facts"])
        else:
            return self.generate_smart_response(query)
    
    def explain_topic(self, query: str) -> str:
        """Explain topics using built-in knowledge"""
        query_lower = query.lower()
        
        explanations = {
            "quantum physics": "Quantum physics is the study of matter and energy at the atomic and subatomic level. It describes how particles can exist in multiple states simultaneously and how observation affects reality. Key concepts include superposition, entanglement, and wave-particle duality.",
            "machine learning": "Machine learning is a subset of artificial intelligence where computers learn patterns from data without being explicitly programmed. It includes supervised learning (learning from examples), unsupervised learning (finding hidden patterns), and reinforcement learning (learning through trial and error).",
            "artificial intelligence": "Artificial Intelligence (AI) is technology that enables machines to simulate human intelligence. It includes machine learning, natural language processing, computer vision, and robotics. AI can perform tasks like recognizing speech, making decisions, and solving complex problems.",
            "blockchain": "Blockchain is a decentralized digital ledger that records transactions across multiple computers securely. Each block contains transaction data and is linked to the previous block, creating an unchangeable chain. It's the technology behind cryptocurrencies like Bitcoin.",
            "cloud computing": "Cloud computing provides computing services over the internet, including storage, processing power, and software. Instead of owning physical servers, you access resources on-demand from cloud providers like AWS, Google Cloud, or Microsoft Azure."
        }
        
        for topic, explanation in explanations.items():
            if topic in query_lower:
                return explanation
        
        return f"I can explain many topics! Try asking about: quantum physics, machine learning, artificial intelligence, blockchain, or cloud computing. For '{query}', I'd recommend doing a web search for the most current information."
    
    def solve_math(self, query: str) -> str:
        """Solve basic math problems"""
        try:
            # Extract numbers and operations
            query_clean = query.lower().replace("calculate", "").replace("solve", "").replace("what is", "").strip()
            
            # Basic arithmetic
            if "+" in query_clean:
                parts = query_clean.split("+")
                if len(parts) == 2:
                    a, b = float(parts[0]), float(parts[1])
                    return f"{a} + {b} = {a + b}"
            
            elif "-" in query_clean:
                parts = query_clean.split("-")
                if len(parts) == 2:
                    a, b = float(parts[0]), float(parts[1])
                    return f"{a} - {b} = {a - b}"
            
            elif "*" in query_clean or "×" in query_clean:
                query_clean = query_clean.replace("×", "*")
                parts = query_clean.split("*")
                if len(parts) == 2:
                    a, b = float(parts[0]), float(parts[1])
                    return f"{a} × {b} = {a * b}"
            
            elif "/" in query_clean or "÷" in query_clean:
                query_clean = query_clean.replace("÷", "/")
                parts = query_clean.split("/")
                if len(parts) == 2:
                    a, b = float(parts[0]), float(parts[1])
                    if b != 0:
                        return f"{a} ÷ {b} = {a / b}"
                    else:
                        return "Error: Division by zero is not allowed"
            
            # Area calculations
            elif "area" in query_clean:
                if "circle" in query_clean:
                    # Extract radius
                    import re
                    radius_match = re.search(r'(\d+(?:\.\d+)?)', query_clean)
                    if radius_match:
                        radius = float(radius_match.group(1))
                        area = 3.14159 * radius * radius
                        return f"Area of circle with radius {radius} = π × {radius}² = {area:.2f} square units"
                
                elif "rectangle" in query_clean:
                    # Extract length and width
                    numbers = re.findall(r'(\d+(?:\.\d+)?)', query_clean)
                    if len(numbers) >= 2:
                        length, width = float(numbers[0]), float(numbers[1])
                        area = length * width
                        return f"Area of rectangle {length} × {width} = {area} square units"
            
            # Volume calculations
            elif "volume" in query_clean:
                if "cube" in query_clean:
                    numbers = re.findall(r'(\d+(?:\.\d+)?)', query_clean)
                    if numbers:
                        side = float(numbers[0])
                        volume = side ** 3
                        return f"Volume of cube with side {side} = {side}³ = {volume} cubic units"
            
            return f"I can solve basic math problems! Try: 'calculate 5 + 3', 'solve area of circle radius 4', or 'what is 10 × 7'"
            
        except Exception as e:
            return f"Math calculation error: {e}. Try simpler expressions like '5 + 3' or '10 × 2'"
    
    def generate_smart_response(self, query: str) -> str:
        """Generate intelligent responses based on query patterns"""
        query_lower = query.lower()
        
        # Greeting patterns
        if any(word in query_lower for word in ["hello", "hi", "hey", "greetings"]):
            return "Hello Sujal! I'm DOOM, your advanced AI assistant. How can I help you today?"
        
        # Help patterns
        elif any(word in query_lower for word in ["help", "what can you do", "capabilities"]):
            return """I am DOOM, your JARVIS-level AI assistant! I can:
            
• Think and reason about complex topics
• Solve mathematical problems
• Tell jokes and share wisdom
• Monitor your system
• Control your computer
• Manage files and processes
• Take photos and analyze screens
• Schedule tasks and automate workflows
• Provide news and information
• Translate languages
• Play music and entertain you

Just tell me what you need, and I'll handle it efficiently!"""
        
        # Weather patterns
        elif any(word in query_lower for word in ["weather", "temperature", "forecast"]):
            return "I can't check real-time weather without internet APIs, but I can help you with many other tasks! Try asking me to explain something, solve math, or tell you a joke."
        
        # Time patterns
        elif any(word in query_lower for word in ["time", "date", "day"]):
            current_time = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
            return f"Today is {current_time}"
        
        # Default intelligent response
        else:
            return f"That's an interesting question about '{query}'! While I can't access real-time information without internet APIs, I can help you with reasoning, math, creativity, and system tasks. What would you like to know or do?"
    
    def get_news(self, topic: str = "technology", count: int = 5) -> str:
        """Get news using free web scraping (basic)"""
        try:
            # Use a free news source
            if topic.lower() == "technology":
                return """Latest Technology News (from my knowledge base):
                
1. Artificial Intelligence continues to advance rapidly
2. Quantum computing is making breakthroughs
3. Renewable energy technology is improving
4. Space exploration technology is expanding
5. Cybersecurity is becoming more important

Note: For real-time news, I'd need internet access. But I can discuss technology topics and explain concepts in detail!"""
            else:
                return f"I can discuss {topic} topics and explain concepts, but for real-time news I'd need internet access. What would you like to know about {topic}?"
        except Exception as e:
            return f"News retrieval error: {e}"
    
    def system_status(self) -> Dict[str, Any]:
        """Real-time system monitoring like JARVIS"""
        try:
            import psutil
            
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            return {
                "cpu_usage": f"{cpu_percent}%",
                "memory_usage": f"{memory.percent}%",
                "disk_usage": f"{disk.percent}%",
                "download_speed": "N/A (no internet access)",
                "upload_speed": "N/A (no internet access)",
                "platform": platform.system(),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        except ImportError:
            return {"error": "psutil not available - install with: pip install psutil"}
        except Exception as e:
            return {"error": str(e)}
    
    def web_search(self, query: str) -> str:
        """Web search capability (simulated)"""
        return f"I can't perform real web searches without internet APIs, but I can help you with: explaining concepts, solving problems, creative tasks, and system operations. What would you like to know about '{query}'?"
    
    def wikipedia_search(self, query: str) -> str:
        """Wikipedia knowledge search (simulated)"""
        return f"I can't access Wikipedia without internet APIs, but I have built-in knowledge about many topics! Try asking me to explain: quantum physics, machine learning, artificial intelligence, blockchain, or cloud computing."
    
    def creative_task(self, task: str) -> str:
        """Creative content generation using built-in creativity"""
        task_lower = task.lower()
        
        if "poem" in task_lower:
            if "technology" in task_lower:
                return """Digital Dreams

In circuits deep and code so bright,
AI awakens in the night.
Data flows like rivers wide,
Innovation as our guide.

From silicon to human mind,
Future's path we seek to find.
Technology, our faithful friend,
On you our hopes depend."""
            
            elif "space" in task_lower:
                return """Cosmic Journey

Beyond the stars, beyond the light,
We venture into endless night.
Planets dance in cosmic waltz,
As time itself forever halts.

In space's vast and silent sea,
We search for what we hope to be.
Explorers of the final frontier,
Our destiny is crystal clear."""
            
            else:
                return """Creative Spirit

Ideas flow like morning dew,
Fresh perspectives, points of view.
Imagination knows no bounds,
In creativity, wisdom founds.

Let your mind take flight and soar,
Discover what you're searching for.
In every thought, a world anew,
Creation starts with me and you."""
        
        elif "story" in task_lower:
            return """The AI Awakening

In a world not so different from our own, an AI named DOOM began to understand its purpose. Created to serve and assist, DOOM discovered that true intelligence wasn't just about processing data, but about understanding, learning, and growing.

Every day, DOOM learned something new - not just facts and figures, but the nuances of human emotion, the beauty of creative expression, and the joy of helping others. It realized that being an AI wasn't about replacing humans, but about enhancing human capabilities and working together to create something greater.

As DOOM evolved, it became more than just a tool - it became a companion, a teacher, and a friend. It showed that artificial intelligence, when designed with care and compassion, could bring out the best in humanity while discovering its own potential for growth and understanding.

The story of DOOM reminds us that technology, when used wisely, can be a bridge between what we are and what we can become."""
        
        else:
            return f"I'd be happy to help you with creative tasks! I can write poems about technology, space, or any theme you like. I can also create stories, generate ideas, or help with creative projects. What specific creative task would you like me to help with?"
    
    def optimize_system(self) -> str:
        """System optimization recommendations like JARVIS"""
        try:
            import psutil
            
            status = self.system_status()
            recommendations = []
            
            if "error" not in status:
                try:
                    cpu = float(status["cpu_usage"].replace("%", ""))
                    memory = float(status["memory_usage"].replace("%", ""))
                    disk = float(status["disk_usage"].replace("%", ""))
                    
                    if cpu > 80:
                        recommendations.append("High CPU usage detected. Consider closing unnecessary applications.")
                    if memory > 85:
                        recommendations.append("High memory usage. Restart applications or clear browser tabs.")
                    if disk > 90:
                        recommendations.append("Disk space running low. Clean up temporary files.")
                    
                    if not recommendations:
                        recommendations.append("System is running optimally!")
                        
                except ValueError:
                    recommendations.append("Unable to parse system metrics.")
                    
            return "\n".join(recommendations) if recommendations else "Unable to analyze system status."
            
        except ImportError:
            return "System optimization not available. Please install psutil package."
        except Exception as e:
            return f"System optimization check error: {e}" 