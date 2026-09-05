import os
import glob
import time
from typing import List, Dict, Any
from tools.base import BaseTool, ToolResult, RiskLevel
from core.path_resolver import canonical_path


class ReadFileTool(BaseTool):
    name = "filesystem_read_file"
    description = "Reads content from a text file on the local filesystem"
    permission_level = "safe"
    risk_level = RiskLevel.SAFE
    timeout = 10

    purpose = "Reads text content from a local file"
    category = "filesystem"
    when_to_use = "When user requests inspecting, reading, or viewing a file"
    do_not_use_when = "File is binary or user asked to execute the file instead"

    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Relative or absolute path to the file to read (e.g. 'Desktop/system_info.py')"
            }
        },
        "required": ["file_path"]
    }

    def _execute_impl(self, file_path: str, **kwargs) -> ToolResult:
        start_t = time.time()
        cpath = canonical_path(file_path)
        if not cpath.exists:
            return ToolResult(
                success=False,
                output=f"File not found: {cpath.relative_path}",
                action="read_file",
                error="FileNotFoundError",
                duration_ms=(time.time() - start_t) * 1000,
                target=cpath.absolute_path
            )
        try:
            with open(cpath.absolute_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(5000)
            duration = (time.time() - start_t) * 1000
            artifact = {"path": cpath.absolute_path, "relative_path": cpath.relative_path, "name": cpath.filename, "size_bytes": os.path.getsize(cpath.absolute_path), "exists": True}
            return ToolResult(
                success=True,
                output=content,
                action="read_file",
                artifact=artifact,
                stdout=content,
                stderr="",
                duration_ms=duration,
                exit_code=0,
                target=cpath.absolute_path,
                data=artifact
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output=f"Error reading file {cpath.relative_path}: {e}",
                action="read_file",
                error=str(e),
                duration_ms=(time.time() - start_t) * 1000,
                exit_code=-1,
                target=cpath.absolute_path
            )


class WriteFileTool(BaseTool):
    name = "filesystem_write_file"
    description = "Writes or overwrites text content to a file on the local filesystem"
    permission_level = "moderate"
    risk_level = RiskLevel.MEDIUM
    timeout = 10

    purpose = "Writes or overwrites arbitrary text content to a file"
    category = "filesystem"
    side_effects = ["create_file", "write_disk"]
    when_to_use = "When user explicitly asks to write generic text, json, or configuration files"
    do_not_use_when = "A Python script was already created by coding_write_script at the same target path"
    mutually_exclusive_with = ["coding_write_script"]

    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path where the file should be written (e.g. 'Desktop/system_info.py')"
            },
            "content": {
                "type": "string",
                "description": "The exact content to write to the file"
            }
        },
        "required": ["file_path", "content"]
    }

    def _execute_impl(self, file_path: str, content: str, **kwargs) -> ToolResult:
        start_t = time.time()
        try:
            cpath = canonical_path(file_path)
            os.makedirs(os.path.dirname(cpath.absolute_path), exist_ok=True)
            with open(cpath.absolute_path, "w", encoding="utf-8") as f:
                f.write(content)
            duration = (time.time() - start_t) * 1000
            size_bytes = len(content.encode("utf-8"))
            artifact = {"path": cpath.absolute_path, "relative_path": cpath.relative_path, "name": cpath.filename, "size_bytes": size_bytes, "exists": True}
            return ToolResult(
                success=True,
                output=f"Successfully written {size_bytes} bytes to {cpath.relative_path}",
                action="create_file",
                artifact=artifact,
                stdout="",
                stderr="",
                duration_ms=duration,
                exit_code=0,
                target=cpath.absolute_path,
                data=artifact
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output=f"Error writing file {file_path}: {e}",
                action="create_file",
                error=str(e),
                duration_ms=(time.time() - start_t) * 1000,
                exit_code=-1,
                target=file_path
            )


class ListDirectoryTool(BaseTool):
    name = "filesystem_list_dir"
    description = "Lists files and subdirectories in a directory"
    permission_level = "safe"
    risk_level = RiskLevel.SAFE
    timeout = 10

    purpose = "Lists files in a given directory"
    category = "filesystem"

    parameters = {
        "type": "object",
        "properties": {
            "directory_path": {
                "type": "string",
                "description": "Directory path to list (defaults to '.')"
            }
        }
    }

    def _execute_impl(self, directory_path: str = ".", **kwargs) -> ToolResult:
        start_t = time.time()
        try:
            cpath = canonical_path(directory_path)
            target_dir = cpath.absolute_path if os.path.isdir(cpath.absolute_path) else os.getcwd()
            items = os.listdir(target_dir)
            formatted = []
            for item in items[:50]:
                full_p = os.path.join(target_dir, item)
                item_type = "[DIR]" if os.path.isdir(full_p) else "[FILE]"
                formatted.append(f"{item_type} {item}")
            duration = (time.time() - start_t) * 1000
            return ToolResult(
                success=True,
                output="\n".join(formatted),
                action="list_directory",
                artifact={"path": target_dir, "name": os.path.basename(target_dir)},
                stdout="\n".join(formatted),
                stderr="",
                duration_ms=duration,
                exit_code=0,
                target=target_dir,
                data={"items": items, "directory": target_dir}
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output=f"Error listing directory: {e}",
                action="list_directory",
                error=str(e),
                duration_ms=(time.time() - start_t) * 1000,
                exit_code=-1,
                target=directory_path
            )


class SearchFilesTool(BaseTool):
    name = "filesystem_search_files"
    description = "Searches for files matching a pattern or extension in a directory recursively"
    permission_level = "safe"
    risk_level = RiskLevel.SAFE
    timeout = 15

    purpose = "Searches for files matching glob pattern"
    category = "filesystem"

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

    def _execute_impl(self, pattern: str, directory: str = ".", **kwargs) -> ToolResult:
        start_t = time.time()
        try:
            cpath = canonical_path(directory)
            target_dir = cpath.absolute_path if os.path.isdir(cpath.absolute_path) else os.getcwd()
            search_path = os.path.join(target_dir, "**", pattern)
            matches = glob.glob(search_path, recursive=True)
            duration = (time.time() - start_t) * 1000
            if matches:
                return ToolResult(
                    success=True,
                    output="\n".join(matches[:30]),
                    action="search_files",
                    artifact={"path": target_dir, "matches": matches[:30]},
                    stdout="\n".join(matches[:30]),
                    stderr="",
                    duration_ms=duration,
                    exit_code=0,
                    target=target_dir,
                    data={"matches": matches}
                )
            return ToolResult(
                success=True,
                output=f"No files matching '{pattern}' were found.",
                action="search_files",
                artifact={"path": target_dir, "matches": []},
                duration_ms=duration,
                exit_code=0,
                target=target_dir
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output=f"Error searching files: {e}",
                action="search_files",
                error=str(e),
                duration_ms=(time.time() - start_t) * 1000,
                exit_code=-1,
                target=directory
            )
