import json
from typing import Dict, Any, List, Optional
from tools.base import BaseTool, ToolResult
from database.postgres_db import postgres_manager


class DatabaseQueryTool(BaseTool):
    name = "database_query"
    description = "Executes a read-only SQL query against the PostgreSQL 'Doom' database and returns structured results"
    permission_level = "sensitive"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "SQL SELECT query to execute (e.g., 'SELECT * FROM user_profiles;', 'SELECT * FROM episodic_memory ORDER BY recorded_at DESC LIMIT 5;')"
            }
        },
        "required": ["query"]
    }

    def execute(self, query: str, **kwargs) -> ToolResult:
        try:
            q_clean = query.strip()
            # Safety check: restrict to SELECT statements for safety
            if not q_clean.upper().startswith("SELECT") and not q_clean.upper().startswith("SHOW") and not q_clean.upper().startswith("EXPLAIN"):
                return ToolResult(
                    success=False,
                    output="Permission denied: Only SELECT/read queries are allowed through this tool.",
                    error="NonReadOnlyQuery"
                )

            results = postgres_manager.execute_query(q_clean, readonly=True)
            if results and isinstance(results, list) and "error" in results[0]:
                return ToolResult(
                    success=False,
                    output=f"Database query error: {results[0]['error']}",
                    error=results[0]["error"]
                )

            row_count = len(results)
            formatted = json.dumps(results[:10], indent=2, default=str)
            summary = f"Query executed successfully. Retrieved {row_count} row(s):\n{formatted}"
            if row_count > 10:
                summary += f"\n... ({row_count - 10} additional rows truncated)"

            return ToolResult(
                success=True,
                output=summary,
                data={"row_count": row_count, "rows": results}
            )
        except Exception as e:
            return ToolResult(success=False, output=f"Database execution failed: {e}", error=str(e))


class DatabaseTelemetryTool(BaseTool):
    name = "database_get_telemetry"
    description = "Retrieves recent system telemetry metrics and workstation health history from PostgreSQL"
    permission_level = "safe"
    parameters = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Number of recent telemetry records to retrieve (default 10)"
            }
        }
    }

    def execute(self, limit: int = 10, **kwargs) -> ToolResult:
        try:
            results = postgres_manager.execute_query(
                "SELECT id, cpu_percent, ram_percent, disk_percent, recorded_at FROM system_telemetry ORDER BY recorded_at DESC LIMIT %s;",
                (limit,)
            )
            if results and isinstance(results, list) and "error" in results[0]:
                return ToolResult(success=False, output=f"Telemetry retrieval error: {results[0]['error']}", error=results[0]["error"])

            if not results:
                return ToolResult(success=True, output="No historical telemetry records logged in PostgreSQL yet.", data=[])

            lines = [f"📊 Workstation Telemetry Logs (Latest {len(results)} snapshots):"]
            for r in results:
                ts = str(r.get('recorded_at', ''))[:19]
                lines.append(f"- [{ts}] CPU: {r.get('cpu_percent')}% | RAM: {r.get('ram_percent')}% | Disk: {r.get('disk_percent')}%")

            return ToolResult(
                success=True,
                output="\n".join(lines),
                data=results
            )
        except Exception as e:
            return ToolResult(success=False, output=f"Failed to fetch telemetry: {e}", error=str(e))


class DatabaseStatusTool(BaseTool):
    name = "database_status"
    description = "Checks the health, connection status, and row statistics of the PostgreSQL 'Doom' database"
    permission_level = "safe"
    parameters = {
        "type": "object",
        "properties": {}
    }

    def execute(self, **kwargs) -> ToolResult:
        try:
            stats = postgres_manager.test_connection()
            if stats.get("status") == "connected":
                tables = stats.get("tables", {})
                msg = (
                    f"PostgreSQL Database '{stats.get('database')}' is ONLINE on {stats.get('host')}.\n"
                    f"- User Profiles: {tables.get('profile_count', 0)}\n"
                    f"- Action Episodes: {tables.get('episode_count', 0)}\n"
                    f"- Semantic Facts: {tables.get('fact_count', 0)}\n"
                    f"- Telemetry Logs: {tables.get('telemetry_count', 0)}\n"
                    f"- Command Audit Logs: {tables.get('log_count', 0)}"
                )
                return ToolResult(success=True, output=msg, data=stats)
            else:
                return ToolResult(
                    success=False,
                    output=f"PostgreSQL Database is unreachable ({stats.get('status')})",
                    error="DatabaseOffline"
                )
        except Exception as e:
            return ToolResult(success=False, output=f"Error checking database status: {e}", error=str(e))
