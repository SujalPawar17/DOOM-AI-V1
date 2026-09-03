import os
import glob
from typing import List
from tools.base import BaseTool, ToolResult

class ReadFileTool(BaseTool):
    name = "filesystem_read_file"
    description = "Reads content from a text file on the local filesystem"
    permission_level = "safe"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Relative or absolute path to the file to read"
            }
        },
        "required": ["file_path"]
    }

    def execute(self, file_path: str, **kwargs) -> ToolResult:
        if not os.path.exists(file_path):
            return ToolResult(success=False, output=f"File not found: {file_path}", error="FileNotFoundError")
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(5000)
            return ToolResult(success=True, output=content, data={"path": file_path, "length": len(content)})
        except Exception as e:
            return ToolResult(success=False, output=f"Error reading file {file_path}", error=str(e))

class WriteFileTool(BaseTool):
    name = "filesystem_write_file"
    description = "Writes or overwrites text content to a file on the local filesystem"
    permission_level = "moderate"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path where the file should be written"
            },
            "content": {
                "type": "string",
                "description": "The exact content to write to the file"
            }
        },
        "required": ["file_path", "content"]
    }

    def execute(self, file_path: str, content: str, **kwargs) -> ToolResult:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return ToolResult(success=True, output=f"Successfully written {len(content)} bytes to {file_path}")
        except Exception as e:
            return ToolResult(success=False, output=f"Error writing file {file_path}", error=str(e))

class ListDirectoryTool(BaseTool):
    name = "filesystem_list_dir"
    description = "Lists files and subdirectories in a directory"
    permission_level = "safe"
    parameters = {
        "type": "object",
        "properties": {
            "directory_path": {
                "type": "string",
                "description": "Directory path to list (defaults to current working directory '.')"
            }
        }
    }

    def execute(self, directory_path: str = ".", **kwargs) -> ToolResult:
        try:
            items = os.listdir(directory_path)
            formatted = []
            for item in items[:50]:
                full_p = os.path.join(directory_path, item)
                item_type = "[DIR]" if os.path.isdir(full_p) else "[FILE]"
                formatted.append(f"{item_type} {item}")
            return ToolResult(success=True, output="\n".join(formatted), data={"items": items})
        except Exception as e:
            return ToolResult(success=False, output=f"Error listing directory: {e}", error=str(e))

class SearchFilesTool(BaseTool):
    name = "filesystem_search_files"
    description = "Searches for files matching a pattern or extension in a directory recursively"
    permission_level = "safe"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob search pattern (e.g. '*.py', '*test*', '*.json')"
            },
            "directory": {
                "type": "string",
                "description": "Root directory to search in (defaults to '.')"
            }
        },
        "required": ["pattern"]
    }

    def execute(self, pattern: str, directory: str = ".", **kwargs) -> ToolResult:
        try:
            search_path = os.path.join(directory, "**", pattern)
            matches = glob.glob(search_path, recursive=True)
            if matches:
                return ToolResult(success=True, output="\n".join(matches[:30]), data={"matches": matches})
            return ToolResult(success=True, output=f"No files matching '{pattern}' were found.")
        except Exception as e:
            return ToolResult(success=False, output=f"Error searching files: {e}", error=str(e))
