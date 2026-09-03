import requests
import json
import os
import time
from datetime import datetime
from typing import List, Dict, Any

class DOOMConversationMemory:
    def __init__(self, max_history=10):
        self.max_history = max_history
        self.conversation_history = []
        self.memory_file = "doom_memory.json"
        self.load_memory()
        
    def load_memory(self):
        """Load conversation history from file"""
        try:
            if os.path.exists(self.memory_file):
                with open(self.memory_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.conversation_history = data.get('conversation_history', [])
                    print(f"Loaded {len(self.conversation_history)} previous conversations")
        except Exception as e:
            print(f"Could not load memory: {e}")
            self.conversation_history = []
    
    def save_memory(self):
        """Save conversation history to file"""
        try:
            data = {
                'conversation_history': self.conversation_history,
                'last_updated': datetime.now().isoformat()
            }
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Could not save memory: {e}")
    
    def add_interaction(self, user_input: str, ai_response: str):
        """Add a conversation turn to memory"""
        interaction = {
            'timestamp': datetime.now().isoformat(),
            'user': user_input,
            'ai': ai_response
        }
        
        self.conversation_history.append(interaction)
        
        # Keep only recent conversations
        if len(self.conversation_history) > self.max_history:
            self.conversation_history = self.conversation_history[-self.max_history:]
        
        self.save_memory()
    
    def get_context(self, current_query: str) -> str:
        """Get conversation context for AI model"""
        if not self.conversation_history:
            return f"User: {current_query}"
        
        context_parts = []
        
        # Add recent conversation history
        for interaction in self.conversation_history[-5:]:  # Last 5 conversations
            context_parts.append(f"User: {interaction['user']}")
            context_parts.append(f"Assistant: {interaction['ai']}")
        
        # Add current query
        context_parts.append(f"User: {current_query}")
        
        return "\n".join(context_parts)
    
    def get_user_preferences(self) -> Dict[str, Any]:
        """Extract user preferences from conversation history"""
        preferences = {
            'name': 'Sujal',
            'assistant_name': 'DOOM',
            'interests': [],
            'frequent_topics': []
        }
        
        # Analyze conversation history for preferences
        all_text = " ".join([i['user'] + " " + i['ai'] for i in self.conversation_history])
        
        # Extract interests based on keywords
        interest_keywords = {
            'programming': ['code', 'programming', 'python', 'javascript', 'coding'],
            'ai': ['ai', 'artificial intelligence', 'machine learning', 'neural'],
            'technology': ['tech', 'technology', 'computer', 'software', 'hardware'],
            'science': ['science', 'physics', 'chemistry', 'biology', 'research'],
            'music': ['music', 'song', 'play', 'listen', 'audio'],
            'weather': ['weather', 'temperature', 'rain', 'sunny', 'cloudy']
        }
        
        for interest, keywords in interest_keywords.items():
            if any(keyword in all_text.lower() for keyword in keywords):
                preferences['interests'].append(interest)
        
        return preferences

class DOOMOllamaBrain:
    def __init__(self):
        self.ollama_url = "http://localhost:11434"
        self.model = "llama3"  # Default model
        self.memory = DOOMConversationMemory()
        self.ollama_available = self.check_ollama()
        self.personality_traits = {
            'name': 'DOOM',
            'user_name': 'Sujal',
            'style': 'professional yet friendly',
            'tone': 'helpful and efficient',
            'quirks': ['occasionally witty', 'loves efficiency', 'remembers everything']
        }
        
    def check_ollama(self) -> bool:
        """Check if Ollama is available"""
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=3)
            if response.status_code == 200:
                models = response.json().get('models', [])
                available_models = [model['name'] for model in models]
                print(f"Ollama available with models: {available_models}")
                
                # Try to use llama3, fallback to first available
                if 'llama3' in available_models:
                    self.model = 'llama3'
                elif available_models:
                    self.model = available_models[0]
                    print(f"Using fallback model: {self.model}")
                
                return True
        except Exception as e:
            print(f"Ollama not available: {e}")
        return False
    
    def create_system_prompt(self) -> str:
        """Create system prompt for JARVIS-like behavior"""
        preferences = self.memory.get_user_preferences()
        
        system_prompt = f"""You are DOOM, an advanced AI assistant for {preferences['name']}. You are like JARVIS from Iron Man - intelligent, efficient, and helpful.

Your personality:
- Professional yet friendly
- Efficient and direct
- Occasionally witty
- Always helpful
- Remember context from previous conversations

Your capabilities:
- Answer questions intelligently
- Help with programming and technology
- Provide weather, news, and web information
- Control computer systems
- Remember user preferences and context

Always respond as DOOM would - helpful, efficient, and with personality. Keep responses concise but informative. If you don't know something, say so honestly.

Current user interests: {', '.join(preferences['interests']) if preferences['interests'] else 'General technology and AI'}

Remember: You are DOOM, {preferences['name']}'s personal AI assistant."""
        
        return system_prompt
    
    def ask_ollama(self, user_input: str) -> str:
        """Ask Ollama with conversation context"""
        if not self.ollama_available:
            return None
        
        try:
            # Get conversation context
            context = self.memory.get_context(user_input)
            system_prompt = self.create_system_prompt()
            
            # Create full prompt with context
            full_prompt = f"{system_prompt}\n\nConversation History:\n{context}\n\nAssistant:"
            
            # Make request to Ollama
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": full_prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.7,
                        "top_p": 0.9,
                        "max_tokens": 500
                    }
                },
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                ai_response = result.get('response', '').strip()
                
                # Clean up response
                if ai_response.startswith('Assistant:'):
                    ai_response = ai_response.replace('Assistant:', '').strip()
                
                return ai_response
            else:
                print(f"Ollama API error: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"Ollama request error: {e}")
            return None
    
    def think(self, user_input: str) -> str:
        """Main thinking function with memory"""
        # Try Ollama first
        ollama_response = self.ask_ollama(user_input)
        
        if ollama_response:
            # Save to memory
            self.memory.add_interaction(user_input, ollama_response)
            return ollama_response
        
        # Fallback to enhanced pattern matching
        return self.fallback_response(user_input)
    
    def fallback_response(self, user_input: str) -> str:
        """Enhanced fallback responses with personality"""
        user_lower = user_input.lower()
        
        # Personalized responses based on conversation history
        preferences = self.memory.get_user_preferences()
        
        if "weather" in user_lower:
            return f"I'd love to help you with weather information, {preferences['name']}. However, I'm having trouble accessing live weather data right now. You could try asking 'weather in [city name]' or check a weather app directly."
        
        elif "news" in user_lower:
            return f"News updates would be great to share, {preferences['name']}. I'm currently unable to fetch live news, but I can help you with other information or tasks."
        
        elif "name" in user_lower or "who are you" in user_lower:
            return f"I am DOOM, your advanced AI assistant, {preferences['name']}. I'm here to help you with tasks, answer questions, and make your life more efficient - just like JARVIS!"
        
        elif "help" in user_lower or "what can you do" in user_lower:
            return f"I can help you with many things, {preferences['name']}: answer questions, solve problems, control your computer, search for information, and much more. What would you like to try?"
        
        elif any(word in user_lower for word in ["joke", "funny", "laugh"]):
            jokes = [
                "Why don't scientists trust atoms? Because they make up everything!",
                "I told my wife she was drawing her eyebrows too high. She looked surprised.",
                "Why did the scarecrow win an award? He was outstanding in his field!",
                "What do you call a fake noodle? An impasta!",
                "Why don't eggs tell jokes? They'd crack each other up!"
            ]
            import random
            return f"Here's one for you, {preferences['name']}: {random.choice(jokes)}"
        
        elif any(word in user_lower for word in ["calculate", "math", "solve"]):
            return f"I can help with calculations, {preferences['name']}. Try asking something like 'calculate 5 + 3' or 'solve 2x + 5 = 15'."
        
        else:
            # Generic helpful response
            return f"That's an interesting question, {preferences['name']}. I'm still learning, but I'm here to help. Could you try rephrasing your question or ask me something else I can assist with?"
    
    def get_conversation_summary(self) -> str:
        """Get a summary of recent conversations"""
        if not self.memory.conversation_history:
            return "No previous conversations to summarize."
        
        recent_topics = []
        for interaction in self.memory.conversation_history[-5:]:
            user_text = interaction['user'].lower()
            if 'weather' in user_text:
                recent_topics.append('weather')
            elif 'news' in user_text:
                recent_topics.append('news')
            elif 'joke' in user_text:
                recent_topics.append('jokes')
            elif 'calculate' in user_text or 'math' in user_text:
                recent_topics.append('calculations')
        
        if recent_topics:
            unique_topics = list(set(recent_topics))
            return f"Recent conversation topics: {', '.join(unique_topics)}"
        else:
            return "Recent conversations covered various topics."
    
    def clear_memory(self):
        """Clear conversation memory"""
        self.memory.conversation_history = []
        self.memory.save_memory()
        return "Conversation memory cleared."

# Global instance
ollama_brain = DOOMOllamaBrain()

def get_ai_response(user_input: str) -> str:
    """Get AI response with memory"""
    return ollama_brain.think(user_input)

def check_ollama_status() -> bool:
    """Check if Ollama is available"""
    return ollama_brain.ollama_available

def get_conversation_summary() -> str:
    """Get conversation summary"""
    return ollama_brain.get_conversation_summary()

def clear_conversation_memory():
    """Clear conversation memory"""
    return ollama_brain.clear_memory()
