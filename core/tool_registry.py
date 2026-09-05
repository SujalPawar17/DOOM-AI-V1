from typing import Dict, List, Any, Optional
from tools.base import BaseTool, ToolResult, CanonicalToolResult, MAX_AGENT_STEPS, MAX_TOOL_CALLS, MAX_RETRIES_PER_ACTION
from tools import ALL_TOOLS


class ToolRegistry:
    """Central registry for DOOM V3.2 tools with schema generation and invocation"""
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

    def execute_tool(self, name: str, arguments: Dict[str, Any]) -> CanonicalToolResult:
        tool = self.get_tool(name)
        if not tool:
            return CanonicalToolResult(
                tool=name,
                success=False,
                action="error",
                output=f"Unknown tool: '{name}'",
                error_type="TOOL_NOT_FOUND",
                duration_ms=0.0,
                target=""
            )
        try:
            result = tool.execute(**arguments)
            return result.to_canonical(name, result.duration_ms)
        except Exception as e:
            return CanonicalToolResult(
                tool=name,
                success=False,
                action="error",
                output=f"Error executing tool '{name}': {e}",
                error_type="EXCEPTION",
                duration_ms=0.0,
                target=str(arguments)
            )

    def get_tools_catalog(self) -> List[Dict[str, Any]]:
        """Returns structured metadata catalogue for System / Tools UI."""
        catalog = []
        for t in self._tools.values():
            category = t.name.split("_")[0] if "_" in t.name else "general"
            risk = t.get_effective_risk().value if hasattr(t, "get_effective_risk") else "SAFE"
            catalog.append({
                "name": t.name,
                "description": t.description,
                "category": category,
                "risk_level": risk,
                "timeout": getattr(t, "timeout", 30)
            })
        return sorted(catalog, key=lambda x: (x["category"], x["name"]))


tool_registry = ToolRegistry()
