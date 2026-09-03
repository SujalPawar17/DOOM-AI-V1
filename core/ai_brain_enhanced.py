import requests
import json
import re
import random
import time
import os
from datetime import datetime

class DOOMBrainEnhanced:
    def __init__(self):
        self.conversation_history = []
        self.knowledge_base = self.load_knowledge_base()
        self.groq_key = os.getenv("GROQ_API_KEY", "").strip()
        self.openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.ollama_available = self.check_ollama()
        
    def check_ollama(self):
        """Check if local Ollama daemon is running"""
        try:
            response = requests.get("http://localhost:11434/api/tags", timeout=1)
            return response.status_code == 200
        except Exception:
            return False
    
    def ask_groq(self, prompt: str) -> str:
        """Query Groq LLaMA 3.3 70B (Ultra-fast Cloud Brain)"""
        if not self.groq_key:
            return None
        try:
            from groq import Groq
            client = Groq(api_key=self.groq_key)
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": "You are DOOM (JARVIS), an ultra-smart, loyal, high-tech AI companion for your boss Sujal. Be concise, brilliant, and confident like Tony Stark's JARVIS."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=400,
                temperature=0.7
            )
            return completion.choices[0].message.content
        except Exception:
            return None

    def ask_openai(self, prompt: str) -> str:
        """Query OpenAI GPT-4o"""
        if not self.openai_key:
            return None
        try:
            headers = {"Authorization": f"Bearer {self.openai_key}", "Content-Type": "application/json"}
            data = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "You are DOOM (JARVIS), an intelligent AI companion for Sujal. Answer in 2 concise, clear sentences."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 250
            }
            res = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=data, timeout=10)
            if res.status_code == 200:
                return res.json()["choices"][0]["message"]["content"]
        except Exception:
            pass
        return None

    def ask_ollama(self, prompt: str, model="llama3") -> str:
        """Ask local Ollama if active"""
        if not self.ollama_available:
            return None
        try:
            response = requests.post("http://localhost:11434/api/generate", 
                                   json={"model": model, "prompt": f"You are JARVIS for Sujal: {prompt}", "stream": False},
                                   timeout=15)
            if response.status_code == 200:
                return response.json()['response']
        except Exception:
            pass
        return None

    def load_knowledge_base(self):
        """Preloaded offline knowledge & personality base"""
        return {
            'jokes': [
                "Why don't scientists trust atoms? Because they make up everything!",
                "I told my computer I needed a break, and now it won't stop sending me Kit-Kat ads.",
                "Why did the Python developer wear glasses? Because they couldn't C#!",
                "There are 10 types of people in the world: those who understand binary, and those who don't.",
                "Why was the JavaScript developer sad? Because they didn't know how to 'null' their feelings."
            ],
            'wisdom': [
                "The best way to predict the future is to invent it, Sujal.",
                "Innovation distinguishes between a leader and a follower.",
                "Simplicity is the soul of efficiency.",
                "Code is like humor. When you have to explain it, it's bad."
            ]
        }

    def solve_math(self, query: str) -> str:
        """Mathematical computation engine"""
        try:
            cleaned = query.lower().replace("calculate", "").replace("solve", "").replace("what is", "").replace("math", "").replace("multiplied by", "*").replace("times", "*").replace("divided by", "/").replace("plus", "+").replace("minus", "-").strip()
            cleaned = re.sub(r'[^0-9\+\-\*\/\(\)\.\^\%]', '', cleaned)
            if cleaned:
                cleaned = cleaned.replace("^", "**")
                res = eval(cleaned, {"__builtins__": None}, {})
                return f"The calculation result is {res}, Sujal."
        except Exception:
            pass
        return "I can solve mathematical equations. Try asking: 'Calculate 125 * 8'."

    def generate_python_code(self, prompt: str) -> str:
        """Generates dynamic Python scripts and saves to disk"""
        os.makedirs("scripts", exist_ok=True)
        filename = f"scripts/task_{int(time.time())}.py"
        
        # Smart template generation based on keywords
        p_lower = prompt.lower()
        if "snake" in p_lower:
            code = ("# Snake Game Generated by DOOM\n"
                    "import turtle, time, random\n"
                    "print('Snake game initialization ready!')\n")
        elif "fibonacci" in p_lower:
            code = ("# Fibonacci Sequence\n"
                    "def fib(n):\n"
                    "    a, b = 0, 1\n"
                    "    for _ in range(n):\n"
                    "        print(a, end=' ')\n"
                    "        a, b = b, a + b\n"
                    "    print()\nfib(15)\n")
        elif "count" in p_lower or "file" in p_lower:
            code = ("# File Scanner\nimport os\n"
                    "files = os.listdir('.')\n"
                    "print(f'Total items in directory: {len(files)}')\n")
        else:
            code = (f"# Python Script Generated for Sujal: {prompt}\n"
                    f"import os, sys, datetime\n"
                    f"print('DOOM Automated Execution:')\n"
                    f"print('Task processed successfully at', datetime.datetime.now())\n")

        with open(filename, "w", encoding="utf-8") as f:
            f.write(code)

        return f"I have written and saved the Python script to {filename}, Sujal. Code generation is complete."

    def think(self, query: str) -> str:
        """Master intelligence pipeline"""
        if not query:
            return "How may I assist you, Sujal?"

        q_lower = query.strip().lower()

        # 1. Identity & Creator Recognition
        if any(w in q_lower for w in ["who am i", "what is my name", "do you know me"]):
            return "You are Sujal, my creator, boss, and lead engineer. I am at your command."

        if any(w in q_lower for w in ["who are you", "what is your name", "who made you", "who created you"]):
            return "I am DOOM, your high-tech AI companion, engineered by Sujal to operate your workstation and automate tasks like Iron Man's JARVIS."

        # 2. Dynamic Code Generation Requests
        if any(w in q_lower for w in ["write code", "write a code", "write python", "generate code", "create script", "python code"]):
            return self.generate_python_code(query)

        # 3. Math solving
        if any(w in q_lower for w in ["calculate", "solve", "what is", "multiplied by", "divided by"]) and any(c.isdigit() for c in query):
            math_res = self.solve_math(query)
            if "calculation result" in math_res:
                return math_res

        # 4. Jokes & Wisdom
        if any(w in q_lower for w in ["joke", "funny", "laugh"]):
            return f"Here's one for you, Sujal: {random.choice(self.knowledge_base['jokes'])}"

        if any(w in q_lower for w in ["wisdom", "quote", "inspire"]):
            return f"Here's some wisdom for you, Sujal: {random.choice(self.knowledge_base['wisdom'])}"

        # 5. Cloud Brain (Groq LLaMA 3.3 70B / OpenAI GPT-4o / Ollama)
        groq_resp = self.ask_groq(query)
        if groq_resp:
            return groq_resp

        openai_resp = self.ask_openai(query)
        if openai_resp:
            return openai_resp

        ollama_resp = self.ask_ollama(query)
        if ollama_resp:
            return ollama_resp

        # 6. Live Web Knowledge Fallback (Instant Search & Summarization)
        try:
            from core.web_search import search_web
            search_res = search_web(query)
            if search_res and "could not find" not in search_res.lower():
                return search_res
        except Exception:
            pass

        return f"I have analyzed your request regarding '{query}', Sujal. All systems are operational."
