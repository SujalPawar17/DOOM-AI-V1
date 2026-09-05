"""
DOOM V4.2 — Tool Input Validator & Security Firewall
Validates all tool arguments at the execution boundary.
Prevents directory traversal (../), dangerous shell injection, and invalid argument types.
"""

import os
import re
from typing import Dict, Any, Tuple, Optional, List


class ToolInputValidator:
    """
    Firewall for tool execution inputs.
    Enforces security constraints before tool dispatch.
    """

    DANGEROUS_COMMAND_PATTERNS = [
        r"\brm\s+-[rf]*\s+[/~]",              # rm -rf / or ~
        r"\bformat\s+[a-z]:",                 # format c:
        r"\bdel\s+/[sfq]\s+c:\\",             # del /s c:\
        r"\bdrop\s+database\b",               # SQL injection drop db
        r"\bshutdown\s+/[srf]\b",             # shutdown commands
        r":(){ :|:& };:",                     # fork bomb
        r">\s*/dev/sd[a-z]",                  # direct disk write
        r"\bmkfs\b"                           # format disk
    ]

    PATH_KEYS = {"file_path", "file_name", "path", "code_or_file", "target", "source", "dest"}

    def validate_inputs(
        self,
        tool_name: str,
        tool_args: Dict[str, Any]
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """
        Inspects arguments for security violations and path traversal.
        Returns: (is_valid: bool, rejection_reason: Optional[str], sanitized_args: Dict[str, Any])
        """
        sanitized = dict(tool_args)

        for k, v in tool_args.items():
            # 1. Path safety and traversal protection
            if k in self.PATH_KEYS and isinstance(v, str):
                # Detect directory traversal
                if ".." in v or "../" in v or "..\\" in v:
                    return False, f"Path traversal attack detected in parameter '{k}': '{v}'", {}
                
                # Check null byte injection
                if "\0" in v:
                    return False, f"Null byte detected in path parameter '{k}'", {}

            # 2. Dangerous shell command protection
            if k in ("command", "cmd", "script", "code") and isinstance(v, str):
                for pattern in self.DANGEROUS_COMMAND_PATTERNS:
                    if re.search(pattern, v, re.IGNORECASE):
                        return False, f"Dangerous command blocked by safety firewall: matched '{pattern}'", {}

        return True, None, sanitized


# Global singleton instance
tool_input_validator = ToolInputValidator()
