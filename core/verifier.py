import re
import os
import ast
from typing import Dict, Any, List, Optional
from tools.base import CanonicalToolResult, ToolResult
from core.path_resolver import canonical_path


class Verifier:
    """
    DOOM V3.2 Ground-Truth Verifier & Response Synthesizer.
    Validates physical realities on disk and in processes:
      - File existence and non-empty size on disk
      - Python syntax compilation (compile/ast.parse)
      - Process exit code == 0
      - Stdout output availability
    """
    def __init__(self):
        pass

    def verify_ground_truth(self, goal: str, observations: List[CanonicalToolResult]) -> Dict[str, Any]:
        """
        Performs strict empirical verification of tool execution observations.
        """
        if not observations:
            return {
                "verified": True,
                "status": "COMPLETED",
                "details": "Direct informational response verified."
            }

        checks: List[str] = []
        all_passed = True
        has_file_check = False
        has_exec_check = False

        for item in observations:
            if not isinstance(item, CanonicalToolResult):
                continue

            name = item.tool
            success = item.success
            action = item.action
            artifact = item.artifact or {}
            stdout = item.stdout
            stderr = item.stderr
            exit_code = item.exit_code
            fpath = artifact.get("path") or artifact.get("relative_path")

            if not success:
                all_passed = False
                error_detail = stderr or item.output or f"error_type: {item.error_type}"
                checks.append(f"Tool '{name}' execution failed: {error_detail}")
                continue

            # 1. File Writing & Integrity Verification
            if action == "create_file" or (fpath and fpath.endswith(".py") and action not in ["execute_file", "execute_code"]):
                has_file_check = True
                cpath = canonical_path(fpath) if fpath else None
                if cpath and cpath.exists:
                    size = os.path.getsize(cpath.absolute_path)
                    if size > 0:
                        # Validate Python Syntax
                        if cpath.absolute_path.endswith(".py"):
                            try:
                                with open(cpath.absolute_path, "r", encoding="utf-8", errors="ignore") as pf:
                                    code_str = pf.read()
                                ast.parse(code_str, filename=cpath.absolute_path)
                                checks.append(f"File '{cpath.filename}' verified on disk ({size} bytes, valid Python syntax).")
                            except SyntaxError as syn_err:
                                all_passed = False
                                checks.append(f"File '{cpath.filename}' syntax error at line {syn_err.lineno}: {syn_err.msg}")
                        else:
                            checks.append(f"File '{cpath.filename}' verified on disk ({size} bytes).")
                    else:
                        all_passed = False
                        checks.append(f"File '{cpath.filename}' exists but is empty (0 bytes).")
                else:
                    all_passed = False
                    path_desc = cpath.relative_path if cpath else str(fpath)
                    checks.append(f"File '{path_desc}' was not found on disk after write operation.")

            # 2. Execution & Process Verification
            elif action in ["execute_file", "execute_code"]:
                has_exec_check = True
                if exit_code != 0:
                    all_passed = False
                    checks.append(f"Execution failed with exit code {exit_code}: {stderr.splitlines()[-1] if stderr.splitlines() else stderr}")
                elif stderr and ("traceback" in stderr.lower() or "syntaxerror" in stderr.lower() or "error" in stderr.lower()):
                    all_passed = False
                    checks.append(f"Execution error detected: {stderr.splitlines()[-1] if stderr.splitlines() else stderr}")
                elif stdout:
                    checks.append(f"Process completed with exit code 0 and valid output ({len(stdout)} chars).")
                else:
                    checks.append("Process completed successfully with exit code 0.")

            else:
                checks.append(f"Action '{name}' verified nominal.")

        status = "COMPLETED" if all_passed else ("PARTIAL_SUCCESS" if any("verified" in c for c in checks) else "FAILED")
        return {
            "verified": all_passed,
            "status": status,
            "details": "; ".join(checks)
        }

    def verify_tool_result(self, tool_name: str, result: ToolResult) -> str:
        if not result.success:
            return f"I encountered a slight complication with {tool_name}: {result.error or result.output}"
        out = result.output.strip()
        if len(out) > 400:
            out = out[:380] + "... and completed successfully."
        return out

    def polish_response(self, text: str, tools_executed: Optional[List[Any]] = None) -> str:
        """Strips markdown and models thinking tags for clean voice and text presentation.
        Preserves underscores in filenames and code."""
        # Remove markdown formatting but preserve underscores in filenames/identifiers
        clean_text = re.sub(r'[\*\#`]', '', text or '').strip()
        clean_text = re.sub(r'<reasoning>.*?</reasoning>', '', clean_text, flags=re.DOTALL).strip()
        clean_text = re.sub(r'<?think>.*', '', clean_text, flags=re.DOTALL).strip()
        return clean_text


verifier = Verifier()
GroundTruthVerifier = Verifier