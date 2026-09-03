from typing import Dict, List, Any, Optional
from tools.base import BaseTool, ToolResult
from tools import ALL_TOOLS

class ToolRegistry:
    """Central registry for DOOM V2 tools with schema generation and invocation"""
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        for tool in ALL_TOOLS:
            self.register_tool(tool)

    def register_tool(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def get_all_tools(self) -> List[BaseTool]:
        return list(self._tools.values())

    def get_schemas(self) -> List[Dict[str, Any]]:
        """Returns JSON function calling schemas for all registered tools"""
        return [tool.to_json_schema() for tool in self._tools.values()]

    def execute_tool(self, name: str, arguments: Dict[str, Any]) -> ToolResult:
        tool = self.get_tool(name)
        if not tool:
            return ToolResult(success=False, output=f"Unknown tool: '{name}'", error="ToolNotFound")
        try:
            return tool.execute(**arguments)
        except Exception as e:
            return ToolResult(success=False, output=f"Error executing tool '{name}': {e}", error=str(e))

tool_registry = ToolRegistry()
