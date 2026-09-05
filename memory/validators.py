"""
DOOM V5.1 — Memory Validators
Content gate: rejects secrets, invalid fields, and unworthy content.
Called by MemoryWritePolicy before any durable storage operation.
"""
import re
from typing import Any, Tuple

from memory.types import SECRET_PATTERNS, MemoryType, MemorySource, ConfidenceLevel, PrivacyClass


class MemoryValidator:
    """
    Pre-storage validation for MemoryRecord candidates.
    All validations are non-fatal to callers — errors are returned as (False, reason) tuples.
    """

    # Minimum content length (chars) required for a durable memory
    MIN_CONTENT_LENGTH: int = 3

    # Maximum content length allowed (prevents storing huge raw outputs)
    MAX_CONTENT_LENGTH: int = 4000

    # Phrases that indicate raw chain-of-thought (must not be stored)
    CHAIN_OF_THOUGHT_PATTERNS: tuple = (
        "let me think",
        "i am reasoning",
        "step 1:",
        "step 2:",
        "<thinking>",
        "</thinking>",
        "chain of thought",
    )

    def validate(self, content: str, memory_type: MemoryType,
                 source: MemorySource, confidence: ConfidenceLevel,
                 privacy_class: PrivacyClass, importance: float) -> Tuple[bool, str]:
        """
        Full validation pass. Returns (is_valid, rejection_reason).
        """
        ok, reason = self.check_secret(content)
        if not ok:
            return False, reason

        ok, reason = self.check_content_length(content)
        if not ok:
            return False, reason

        ok, reason = self.check_chain_of_thought(content)
        if not ok:
            return False, reason

        ok, reason = self.check_importance_range(importance)
        if not ok:
            return False, reason

        return True, ""

    def check_secret(self, content: str) -> Tuple[bool, str]:
        """Reject content that appears to contain credentials or secrets."""
        lower = content.lower()
        for pattern in SECRET_PATTERNS:
            if pattern in lower:
                # Additional context check: make sure it's not just mentioning the word
                # e.g. "the user prefers password managers" is flagged to be safe
                return False, f"Content rejected: appears to contain sensitive credential pattern '{pattern}'"
        # Check for common secret formats (e.g. long hexadecimal strings, JWT-like tokens)
        if re.search(r'\b[A-Za-z0-9+/]{40,}\b', content):
            return False, "Content rejected: appears to contain a long encoded token or key"
        return True, ""

    def check_content_length(self, content: str) -> Tuple[bool, str]:
        """Reject empty or oversized content."""
        if not content or len(content.strip()) < self.MIN_CONTENT_LENGTH:
            return False, f"Content too short (min {self.MIN_CONTENT_LENGTH} chars)"
        if len(content) > self.MAX_CONTENT_LENGTH:
            return False, f"Content too long (max {self.MAX_CONTENT_LENGTH} chars) — store a summary instead"
        return True, ""

    def check_chain_of_thought(self, content: str) -> Tuple[bool, str]:
        """Reject raw chain-of-thought reasoning artifacts."""
        lower = content.lower()
        for pattern in self.CHAIN_OF_THOUGHT_PATTERNS:
            if pattern in lower:
                return False, f"Content rejected: appears to be raw chain-of-thought (pattern: '{pattern}')"
        return True, ""

    def check_importance_range(self, importance: float) -> Tuple[bool, str]:
        """Validate importance is within [0.0, 1.0]."""
        if not (0.0 <= importance <= 1.0):
            return False, f"Importance {importance} out of range [0.0, 1.0]"
        return True, ""

    def is_memory_worthy(self, content: str, memory_type: MemoryType, source: MemorySource) -> bool:
        """
        Quick heuristic: is this content worth storing as a durable memory?
        Rejects temporary artifacts, trivial outputs, and noise.
        """
        if not content or len(content.strip()) < self.MIN_CONTENT_LENGTH:
            return False
        lower = content.strip().lower()

        # Reject pure system noise
        noise_patterns = [
            "task queued", "processing...", "please wait",
            "none", "null", "undefined", "n/a",
            "ok", "done.", "success.", "completed.",
        ]
        if lower in noise_patterns:
            return False

        # Raw tool outputs without meaningful content
        if memory_type == MemoryType.EXPERIENCE and len(content.strip()) < 20:
            return False

        return True


memory_validator = MemoryValidator()
