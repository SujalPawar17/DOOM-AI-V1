from tools.base import BaseTool, ToolResult
from tools.computer_tools import OpenApplicationTool, CloseApplicationTool, ControlMediaTool, StreamYouTubeTool
from tools.filesystem_tools import ReadFileTool, WriteFileTool, ListDirectoryTool, SearchFilesTool
from tools.terminal_tools import ExecuteTerminalCommandTool
from tools.web_tools import WebSearchTool, WeatherTool, NewsTool, StockPriceTool
from tools.coding_tools import WriteScriptTool, RunPythonTool
from tools.system_tools import SystemStatusTool, TakeScreenshotTool, OptimizeSystemTool, LockWorkstationTool, StopSpeakingTool
from tools.vision_tools import ScanGestureTool, TakePhotoTool, AnalyzeScreenTool
from tools.database_tools import DatabaseQueryTool, DatabaseTelemetryTool, DatabaseStatusTool
from tools.workstation_modes import CodeModeTool, DailyBriefingTool, StandupReportTool, LockdownTool, ScreenVisionTool
from tools.developer_tools import ProjectScaffolderTool, APITesterTool

ALL_TOOLS = [
    OpenApplicationTool(),
    CloseApplicationTool(),
    ControlMediaTool(),
    StreamYouTubeTool(),
    ReadFileTool(),
    WriteFileTool(),
    ListDirectoryTool(),
    SearchFilesTool(),
    ExecuteTerminalCommandTool(),
    WebSearchTool(),
    WeatherTool(),
    NewsTool(),
    StockPriceTool(),
    WriteScriptTool(),
    RunPythonTool(),
    SystemStatusTool(),
    TakeScreenshotTool(),
    OptimizeSystemTool(),
    LockWorkstationTool(),
    StopSpeakingTool(),
    ScanGestureTool(),
    TakePhotoTool(),
    AnalyzeScreenTool(),
    DatabaseQueryTool(),
    DatabaseTelemetryTool(),
    DatabaseStatusTool(),
    CodeModeTool(),
    DailyBriefingTool(),
    StandupReportTool(),
    LockdownTool(),
    ScreenVisionTool(),
    ProjectScaffolderTool(),
    APITesterTool()
]

__all__ = [
    "BaseTool", "ToolResult", "ALL_TOOLS",
    "OpenApplicationTool", "CloseApplicationTool", "ControlMediaTool", "StreamYouTubeTool",
    "ReadFileTool", "WriteFileTool", "ListDirectoryTool", "SearchFilesTool",
    "ExecuteTerminalCommandTool",
    "WebSearchTool", "WeatherTool", "NewsTool", "StockPriceTool",
    "WriteScriptTool", "RunPythonTool",
    "SystemStatusTool", "TakeScreenshotTool", "OptimizeSystemTool", "LockWorkstationTool", "StopSpeakingTool",
    "ScanGestureTool", "TakePhotoTool", "AnalyzeScreenTool",
    "DatabaseQueryTool", "DatabaseTelemetryTool", "DatabaseStatusTool",
    "CodeModeTool", "DailyBriefingTool", "StandupReportTool", "LockdownTool", "ScreenVisionTool",
    "ProjectScaffolderTool", "APITesterTool"
]
