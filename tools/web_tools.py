import requests
import json
import urllib.parse
from tools.base import BaseTool, ToolResult
from core.web_search import search_web, get_weather, get_news, get_stock_price, get_definition

class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Searches the live internet via DuckDuckGo and returns synthesized factual results"
    permission_level = "safe"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query or topic to look up on the web"
            }
        },
        "required": ["query"]
    }

    def execute(self, query: str, **kwargs) -> ToolResult:
        try:
            res = search_web(query)
            return ToolResult(success=True, output=res)
        except Exception as e:
            return ToolResult(success=False, output=f"Web search failed: {e}", error=str(e))

class WeatherTool(BaseTool):
    name = "web_weather"
    description = "Fetches live weather and forecast for any city or current location"
    permission_level = "safe"
    parameters = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "City name (e.g. 'Mumbai', 'London', 'New York')"
            }
        },
        "required": ["city"]
    }

    def execute(self, city: str = "current", **kwargs) -> ToolResult:
        try:
            res = get_weather(city)
            return ToolResult(success=True, output=res)
        except Exception as e:
            return ToolResult(success=False, output=f"Weather query failed: {e}", error=str(e))

class NewsTool(BaseTool):
    name = "web_news"
    description = "Fetches latest real-time news headlines on any topic or technology"
    permission_level = "safe"
    parameters = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Topic or category (e.g. 'AI', 'Technology', 'Space', 'World')"
            }
        }
    }

    def execute(self, topic: str = "general", **kwargs) -> ToolResult:
        try:
            res = get_news(topic)
            return ToolResult(success=True, output=res)
        except Exception as e:
            return ToolResult(success=False, output=f"News query failed: {e}", error=str(e))

class StockPriceTool(BaseTool):
    name = "web_stock_price"
    description = "Fetches current stock quote and company telemetry"
    permission_level = "safe"
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol (e.g. 'AAPL', 'TSLA', 'GOOGL', 'NVDA')"
            }
        },
        "required": ["symbol"]
    }

    def execute(self, symbol: str, **kwargs) -> ToolResult:
        try:
            res = get_stock_price(symbol)
            return ToolResult(success=True, output=res)
        except Exception as e:
            return ToolResult(success=False, output=f"Stock query failed: {e}", error=str(e))
