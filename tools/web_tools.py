import requests
import json
import urllib.parse
import time
from tools.base import BaseTool, ToolResult
from core.web_search import search_web, get_weather, get_news, get_stock_price, get_definition


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Searches the live internet via DuckDuckGo and returns synthesized factual results"
    permission_level = "safe"
    timeout = 15
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

    def _execute_impl(self, query: str, **kwargs) -> ToolResult:
        start_t = time.time()
        try:
            res = search_web(query)
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=True, output=res, action="web_search", artifact={"query": query}, stdout=res, stderr="", duration_ms=duration, exit_code=0, target=query)
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=False, output=f"Web search failed: {e}", error=str(e), action="web_search", duration_ms=duration, exit_code=-1, target=query)


class WeatherTool(BaseTool):
    name = "web_weather"
    description = "Fetches live weather and forecast for any city or current location"
    permission_level = "safe"
    timeout = 10
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

    def _execute_impl(self, city: str = "current", **kwargs) -> ToolResult:
        start_t = time.time()
        try:
            res = get_weather(city)
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=True, output=res, action="web_weather", artifact={"city": city}, stdout=res, stderr="", duration_ms=duration, exit_code=0, target=city)
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=False, output=f"Weather query failed: {e}", error=str(e), action="web_weather", duration_ms=duration, exit_code=-1, target=city)


class NewsTool(BaseTool):
    name = "web_news"
    description = "Fetches latest real-time news headlines on any topic or technology"
    permission_level = "safe"
    timeout = 15
    parameters = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Topic or category (e.g. 'AI', 'Technology', 'Space', 'World')"
            }
        }
    }

    def _execute_impl(self, topic: str = "general", **kwargs) -> ToolResult:
        start_t = time.time()
        try:
            res = get_news(topic)
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=True, output=res, action="web_news", artifact={"topic": topic}, stdout=res, stderr="", duration_ms=duration, exit_code=0, target=topic)
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=False, output=f"News query failed: {e}", error=str(e), action="web_news", duration_ms=duration, exit_code=-1, target=topic)


class StockPriceTool(BaseTool):
    name = "web_stock_price"
    description = "Fetches current stock quote and company telemetry"
    permission_level = "safe"
    timeout = 10
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

    def _execute_impl(self, symbol: str, **kwargs) -> ToolResult:
        start_t = time.time()
        try:
            res = get_stock_price(symbol)
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=True, output=res, action="web_stock_price", artifact={"symbol": symbol}, stdout=res, stderr="", duration_ms=duration, exit_code=0, target=symbol)
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(success=False, output=f"Stock query failed: {e}", error=str(e), action="web_stock_price", duration_ms=duration, exit_code=-1, target=symbol)
