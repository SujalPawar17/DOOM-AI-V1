import os
import re
from typing import Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class CanonicalPath:
    raw_path: str
    absolute_path: str
    relative_path: str
    filename: str
    exists: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_path": self.raw_path,
            "absolute_path": self.absolute_path,
            "relative_path": self.relative_path,
            "filename": self.filename,
            "exists": self.exists
        }

class PathResolver:
    """
    Centralized Windows Path Normalizer for DOOM V3.1.
    Resolves any path reference (Desktop, home ~, relative, absolute)
    into a single canonical representation to eliminate path-mismatch duplication.
    """
    def __init__(self, workspace_root: Optional[str] = None):
        self.workspace_root = workspace_root or os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        self.user_home = os.path.expanduser("~")
        self.desktop_dir = os.path.join(self.user_home, "Desktop")

    def resolve(self, path_str: str, default_dir: Optional[str] = None) -> CanonicalPath:
        if not path_str or not isinstance(path_str, str):
            path_str = ""

        clean = path_str.strip().strip("'\"")
        # Normalize forward/back slashes
        clean = clean.replace("/", os.sep).replace("\\", os.sep)

        # Handle user home ~
        if clean.startswith("~" + os.sep) or clean == "~":
            clean = os.path.expanduser(clean)
        # Handle Desktop shortcuts: "Desktop\foo" or "desktop\foo"
        elif clean.lower().startswith("desktop" + os.sep):
            rel_part = clean[len("desktop" + os.sep):]
            clean = os.path.join(self.desktop_dir, rel_part)
        elif clean.lower() == "desktop":
            clean = self.desktop_dir
        elif not os.path.isabs(clean):
            if default_dir:
                clean = os.path.join(default_dir, clean)
            else:
                clean = os.path.join(self.workspace_root, clean)

        abs_path = os.path.abspath(clean)

        # Compute relative path cleanly
        try:
            if abs_path.startswith(self.workspace_root):
                rel_path = os.path.relpath(abs_path, self.workspace_root)
            elif abs_path.startswith(self.desktop_dir):
                rel_path = os.path.join("Desktop", os.path.relpath(abs_path, self.desktop_dir))
            elif abs_path.startswith(self.user_home):
                rel_path = os.path.relpath(abs_path, self.user_home)
            else:
                rel_path = abs_path
        except Exception:
            rel_path = abs_path

        rel_path = rel_path.replace("\\", "/")
        filename = os.path.basename(abs_path)
        exists = os.path.exists(abs_path)

        return CanonicalPath(
            raw_path=path_str,
            absolute_path=abs_path,
            relative_path=rel_path,
            filename=filename,
            exists=exists
        )

path_resolver = PathResolver()

def canonical_path(path_str: str, default_dir: Optional[str] = None) -> CanonicalPath:
    return path_resolver.resolve(path_str, default_dir=default_dir)
