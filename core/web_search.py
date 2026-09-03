import requests
import json
import re
from urllib.parse import quote
import time

class DOOMWebSearch:
    def __init__(self):
        self.search_cache = {}
        self.cache_duration = 300  # 5 minutes
        
    def search_duckduckgo(self, query, max_results=3):
        """Search DuckDuckGo for information"""
        try:
            # Use DuckDuckGo instant answer API
            url = f"https://api.duckduckgo.com/?q={quote(query)}&format=json&no_html=1&skip_disambig=1"
            
            response = requests.get(url, timeout=10)
            data = response.json()
            
            results = []
            
            # Get abstract (summary)
            if data.get('Abstract'):
                results.append({
                    'type': 'summary',
                    'content': data['Abstract'],
                    'source': data.get('AbstractURL', 'DuckDuckGo')
                })
            
            # Get definition
            if data.get('Definition'):
                results.append({
                    'type': 'definition',
                    'content': data['Definition'],
                    'source': data.get('DefinitionURL', 'DuckDuckGo')
                })
            
            # Get related topics
            if data.get('RelatedTopics'):
                for topic in data['RelatedTopics'][:max_results]:
                    if isinstance(topic, dict) and topic.get('Text'):
                        results.append({
                            'type': 'related',
                            'content': topic['Text'],
                            'source': topic.get('FirstURL', 'DuckDuckGo')
                        })
            
            return results[:max_results]
            
        except Exception as e:
            print(f"Search error: {e}")
            return []
    
    def get_weather(self, location="current"):
        """Get weather information"""
        try:
            if location == "current":
                # Try to get location from IP
                location = self.get_location_from_ip()
            
            # Use a more reliable weather search
            query = f"weather {location} today"
            results = self.search_duckduckgo(query)
            
            if results:
                weather_info = results[0]['content']
                return f"Weather in {location}: {weather_info}"
            else:
                # Fallback response
                return f"I couldn't find current weather information for {location}. Please try a specific city name like 'weather in Mumbai' or 'weather in Delhi'."
                
        except Exception as e:
            return f"Sorry, I couldn't get weather information right now. Please try again later or specify a city name."
    
    def get_news(self, topic="general", max_news=3):
        """Get news headlines"""
        try:
            query = f"news {topic}"
            results = self.search_duckduckgo(query)
            
            if results:
                news_items = []
                for result in results[:max_news]:
                    news_items.append(result['content'])
                
                return f"Here are the latest news about {topic}:\n" + "\n".join(news_items)
            else:
                return f"I couldn't find recent news about {topic}. Try a different topic."
                
        except Exception as e:
            return f"Sorry, I couldn't get news right now: {str(e)}"
    
    def get_location_from_ip(self):
        """Get location from IP address"""
        try:
            response = requests.get("http://ip-api.com/json", timeout=5)
            data = response.json()
            return f"{data.get('city', 'Unknown')}, {data.get('country', 'Unknown')}"
        except:
            return "Unknown location"
    
    def search_wikipedia(self, topic):
        """Search Wikipedia for information"""
        try:
            query = f"wikipedia {topic}"
            results = self.search_duckduckgo(query)
            
            if results:
                return f"Wikipedia information about {topic}: {results[0]['content']}"
            else:
                return f"I couldn't find Wikipedia information about {topic}."
                
        except Exception as e:
            return f"Sorry, I couldn't access Wikipedia right now: {str(e)}"
    
    def get_stock_price(self, symbol):
        """Get stock price information"""
        try:
            query = f"stock price {symbol}"
            results = self.search_duckduckgo(query)
            
            if results:
                return f"Stock information for {symbol}: {results[0]['content']}"
            else:
                return f"I couldn't find stock information for {symbol}."
                
        except Exception as e:
            return f"Sorry, I couldn't get stock information right now: {str(e)}"
    
    def get_definition(self, word):
        """Get definition of a word"""
        try:
            query = f"definition {word}"
            results = self.search_duckduckgo(query)
            
            if results:
                return f"Definition of {word}: {results[0]['content']}"
            else:
                return f"I couldn't find a definition for {word}."
                
        except Exception as e:
            return f"Sorry, I couldn't get the definition right now: {str(e)}"
    
    def get_conversion(self, amount, from_unit, to_unit):
        """Get unit conversion"""
        try:
            query = f"convert {amount} {from_unit} to {to_unit}"
            results = self.search_duckduckgo(query)
            
            if results:
                return f"Conversion: {results[0]['content']}"
            else:
                return f"I couldn't convert {amount} {from_unit} to {to_unit}."
                
        except Exception as e:
            return f"Sorry, I couldn't perform the conversion right now: {str(e)}"
    
    def get_calculator(self, expression):
        """Get calculation result"""
        try:
            query = f"calculator {expression}"
            results = self.search_duckduckgo(query)
            
            if results:
                return f"Calculation result: {results[0]['content']}"
            else:
                return f"I couldn't calculate {expression}."
                
        except Exception as e:
            return f"Sorry, I couldn't perform the calculation right now: {str(e)}"
    
    def search_general(self, query):
        """General search function"""
        try:
            results = self.search_duckduckgo(query)
            
            if results:
                response = f"Here's what I found about {query}:\n"
                for i, result in enumerate(results, 1):
                    response += f"{i}. {result['content']}\n"
                return response
            else:
                # Provide helpful fallback responses
                if "weather" in query.lower():
                    return f"I couldn't get current weather data. Please try asking 'weather in [city name]' like 'weather in Mumbai' or 'weather in Delhi'."
                elif "news" in query.lower():
                    return f"I couldn't get current news. Please try asking 'news about [topic]' like 'news about technology' or 'news about AI'."
                else:
                    return f"I couldn't find information about {query}. Please try rephrasing your question or ask me something else I can help with."
                
        except Exception as e:
            return f"Sorry, I couldn't search for that right now. Please try again later or ask me something else I can help with."
    
    def cache_result(self, query, results):
        """Cache search results"""
        self.search_cache[query] = {
            'results': results,
            'timestamp': time.time()
        }
    
    def get_cached_result(self, query):
        """Get cached search results"""
        if query in self.search_cache:
            cached = self.search_cache[query]
            if time.time() - cached['timestamp'] < self.cache_duration:
                return cached['results']
        return None

# Global search instance
web_search = DOOMWebSearch()

def search_web(query):
    """Main web search function"""
    return web_search.search_general(query)

def get_weather(location="current"):
    """Get weather information"""
    return web_search.get_weather(location)

def get_news(topic="general"):
    """Get news headlines"""
    return web_search.get_news(topic)

def get_definition(word):
    """Get definition of a word"""
    return web_search.get_definition(word)

def get_stock_price(symbol):
    """Get stock price"""
    return web_search.get_stock_price(symbol)
