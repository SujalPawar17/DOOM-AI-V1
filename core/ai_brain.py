import os
from typing import Dict, List, Any
import json
import asyncio
from datetime import datetime
import requests
import platform

# Try to import optional dependencies
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    print("OpenAI not available - AI reasoning will be limited")

try:
    import wolframalpha
    WOLFRAM_AVAILABLE = True
except ImportError:
    WOLFRAM_AVAILABLE = False
    print("Wolfram Alpha not available - math computations will be limited")

try:
    from newsapi import NewsApiClient
    NEWS_API_AVAILABLE = True
except ImportError:
    NEWS_API_AVAILABLE = False
    print("News API not available - news features will be limited")

try:
    import speedtest
    SPEEDTEST_AVAILABLE = True
except ImportError:
    SPEEDTEST_AVAILABLE = False
    print("Speedtest not available - network speed tests will be limited")

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    print("psutil not available - system monitoring will be limited")

class DOOMBrain:
    def __init__(self):
        self.openai_client = None
        self.wolfram_client = None
        self.news_api = None
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
        
        self.conversation_history = []
        self.load_api_keys()
        
    def load_api_keys(self):
        """Load API keys from environment variables"""
        try:
            if OPENAI_AVAILABLE:
                openai.api_key = os.getenv('OPENAI_API_KEY')
                if openai.api_key:
                    self.openai_client = openai
                    print("✅ OpenAI configured successfully")
                else:
                    print("⚠️  OpenAI API key not found in environment variables")
                
            if WOLFRAM_AVAILABLE:
                wolfram_key = os.getenv('WOLFRAM_API_KEY')
                if wolfram_key:
                    self.wolfram_client = wolframalpha.Client(wolfram_key)
                    print("✅ Wolfram Alpha configured successfully")
                else:
                    print("⚠️  Wolfram Alpha API key not found")
                    
            if NEWS_API_AVAILABLE:
                news_key = os.getenv('NEWS_API_KEY')
                if news_key:
                    self.news_api = NewsApiClient(api_key=news_key)
                    print("✅ News API configured successfully")
                else:
                    print("⚠️  News API key not found")
                    
        except Exception as e:
            print(f"Error loading API keys: {e}")
    
    def think(self, query: str, context: str = "") -> str:
        """Advanced reasoning using OpenAI GPT"""
        if not OPENAI_AVAILABLE:
            return "I'm sorry, but I need OpenAI to be properly configured for advanced reasoning. Please install openai==0.28.1 and set your API key."
            
        if not self.openai_client:
            return "I'm sorry, but I need my AI brain to be properly configured. Please set your OPENAI_API_KEY in the .env file."
            
        try:
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"Context: {context}\nQuery: {query}"}
            ]
            
            # Add conversation history for context
            for msg in self.conversation_history[-5:]:  # Last 5 messages
                messages.append(msg)
            
            response = self.openai_client.ChatCompletion.create(
                model="gpt-3.5-turbo",  # Use 3.5-turbo for compatibility
                messages=messages,
                max_tokens=500,
                temperature=0.7
            )
            
            answer = response.choices[0].message.content
            
            # Update conversation history
            self.conversation_history.append({"role": "user", "content": query})
            self.conversation_history.append({"role": "assistant", "content": answer})
            
            return answer
            
        except Exception as e:
            return f"I encountered an error while thinking: {e}"
    
    def compute_math(self, query: str) -> str:
        """Advanced mathematical computations using Wolfram Alpha"""
        if not WOLFRAM_AVAILABLE:
            return "I need Wolfram Alpha access for complex calculations. Please install wolframalpha and set your API key."
            
        if not self.wolfram_client:
            return "I need Wolfram Alpha to be properly configured. Please set your WOLFRAM_API_KEY in the .env file."
            
        try:
            res = self.wolfram_client.query(query)
            return next(res.results).text
        except Exception as e:
            return f"Math computation error: {e}"
    
    def get_news(self, topic: str = "technology", count: int = 5) -> str:
        """Get latest news using News API"""
        if not NEWS_API_AVAILABLE:
            return "I need News API access for current events. Please install newsapi-python and set your API key."
            
        if not self.news_api:
            return "I need News API to be properly configured. Please set your NEWS_API_KEY in the .env file."
            
        try:
            headlines = self.news_api.get_top_headlines(q=topic, language='en', page_size=count)
            news_summary = f"Latest {topic} news:\n"
            for i, article in enumerate(headlines['articles'], 1):
                news_summary += f"{i}. {article['title']}\n"
            return news_summary
        except Exception as e:
            return f"News retrieval error: {e}"
    
    def system_status(self) -> Dict[str, Any]:
        """Real-time system monitoring like JARVIS"""
        if not PSUTIL_AVAILABLE:
            return {"error": "psutil not available - install with: pip install psutil"}
            
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Internet speed test
            download_speed = upload_speed = "N/A"
            if SPEEDTEST_AVAILABLE:
                try:
                    st = speedtest.Speedtest()
                    download_speed = st.download() / 1_000_000  # Convert to Mbps
                    upload_speed = st.upload() / 1_000_000
                except:
                    pass
            
            return {
                "cpu_usage": f"{cpu_percent}%",
                "memory_usage": f"{memory.percent}%",
                "disk_usage": f"{disk.percent}%",
                "download_speed": f"{download_speed:.2f} Mbps" if isinstance(download_speed, float) else download_speed,
                "upload_speed": f"{upload_speed:.2f} Mbps" if isinstance(upload_speed, float) else upload_speed,
                "platform": platform.system(),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        except Exception as e:
            return {"error": str(e)}
    
    def web_search(self, query: str) -> str:
        """Web search capability"""
        try:
            search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
            return f"I found search results for '{query}'. You can view them at: {search_url}"
        except Exception as e:
            return f"Web search error: {e}"
    
    def wikipedia_search(self, query: str) -> str:
        """Wikipedia knowledge search"""
        try:
            import wikipedia
            summary = wikipedia.summary(query, sentences=3)
            return f"Wikipedia: {summary}"
        except ImportError:
            return "Wikipedia search not available. Please install wikipedia package."
        except Exception as e:
            return f"Wikipedia search error: {e}"
    
    def creative_task(self, task: str) -> str:
        """Creative content generation"""
        if not OPENAI_AVAILABLE:
            return "I need my creative brain to be configured. Please install openai==0.28.1 and set your API key."
            
        if not self.openai_client:
            return "I need OpenAI to be properly configured. Please set your OPENAI_API_KEY in the .env file."
            
        try:
            response = self.openai_client.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a creative AI assistant. Generate creative content based on the user's request."},
                    {"role": "user", "content": task}
                ],
                max_tokens=300,
                temperature=0.9
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Creative task error: {e}"
    
    def optimize_system(self) -> str:
        """System optimization recommendations like JARVIS"""
        if not PSUTIL_AVAILABLE:
            return "System optimization not available. Please install psutil package."
            
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