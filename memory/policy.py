"""
DOOM V5.1 — Memory Write Policy
Authoritative policy gate for all memory write decisions.
Determines: type, source, confidence, privacy, worthiness, conflict detection.
No memory is stored without passing through this policy.
"""
from typing import Any, Dict, List, Optional, Tuple

from memory.types import (
    MemoryType, MemoryStatus, MemorySource,
    ConfidenceLevel, VerificationStatus, PrivacyClass,
)
from memory.validators import memory_validator


class PolicyDecision:
    """Result of a policy evaluation."""
    __slots__ = ("approved", "rejection_reason", "memory_type", "source",
                 "confidence", "privacy_class", "verification_status",
                 "importance", "tags")

    def __init__(
        self,
        approved: bool,
        rejection_reason: str = "",
        memory_type: MemoryType = MemoryType.SEMANTIC,
        source: MemorySource = MemorySource.DERIVED_CONTEXT,
        confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM,
        privacy_class: PrivacyClass = PrivacyClass.NORMAL,
        verification_status: VerificationStatus = VerificationStatus.UNVERIFIED,
        importance: float = 0.5,
        tags: Optional[List[str]] = None,
    ):
        self.approved = approved
        self.rejection_reason = rejection_reason
        self.memory_type = memory_type
        self.source = source
        self.confidence = confidence
        self.privacy_class = privacy_class
        self.verification_status = verification_status
        self.importance = importance
        self.tags = tags or []


class MemoryWritePolicy:
    """
    Before storing memory, this policy evaluates:
    1. Is this memory-worthy?
    2. What type is it?
    3. What is its source/provenance?
    4. Is it verified?
    5. What confidence should it receive?
    6. Is it sensitive/private?
    7. Should it be rejected outright?

    Rules enforced:
    - Secrets/credentials → ALWAYS REJECTED
    - Raw chain-of-thought → REJECTED
    - LLM inference without evidence → UNVERIFIED + LOW confidence (never HIGH)
    - USER_EXPLICIT source → HIGH confidence allowed
    - VERIFIED_TASK source → VERIFIED status, HIGH confidence
    - Noise content → REJECTED
    """

    def evaluate(
        self,
        content: str,
        memory_type: MemoryType,
        source: MemorySource,
        *,
        task_verified: bool = False,
        user_explicit: bool = False,
        importance: float = 0.5,
        privacy_class: Optional[PrivacyClass] = None,
        extra_tags: Optional[List[str]] = None,
    ) -> PolicyDecision:
        """
        Full policy evaluation. Returns a PolicyDecision.
        """
        # --- Step 1: Secret check (hardest gate) ---
        valid, reason = memory_validator.check_secret(content)
        if not valid:
            return PolicyDecision(False, reason)

        # --- Step 2: Content length/quality gate ---
        valid, reason = memory_validator.check_content_length(content)
        if not valid:
            return PolicyDecision(False, reason)

        # --- Step 3: Chain-of-thought gate ---
        valid, reason = memory_validator.check_chain_of_thought(content)
        if not valid:
            return PolicyDecision(False, reason)

        # --- Step 4: Importance range ---
        valid, reason = memory_validator.check_importance_range(importance)
        if not valid:
            return PolicyDecision(False, reason)

        # --- Step 5: Worthiness heuristic ---
        if not memory_validator.is_memory_worthy(content, memory_type, source):
            return PolicyDecision(False, "Content not memory-worthy (noise or trivial output)")

        # --- Step 6: Determine confidence from source + evidence ---
        confidence = self._determine_confidence(source, task_verified, user_explicit)

        # --- Step 7: Determine verification status ---
        verification_status = self._determine_verification_status(source, task_verified, user_explicit)

        # --- Step 8: Determine privacy class ---
        effective_privacy = privacy_class or self._infer_privacy_class(content, memory_type)

        # --- Step 9: Build tags ---
        tags = list(extra_tags or [])
        tags.append(source.value.lower())
        tags.append(memory_type.value.lower())

        return PolicyDecision(
            approved=True,
            memory_type=memory_type,
            source=source,
            confidence=confidence,
            privacy_class=effective_privacy,
            verification_status=verification_status,
            importance=importance,
            tags=tags,
        )

    def _determine_confidence(
        self, source: MemorySource, task_verified: bool, user_explicit: bool
    ) -> ConfidenceLevel:
        """
        Confidence assignment rules:
        - USER_EXPLICIT → HIGH (user directly told DOOM)
        - VERIFIED_TASK → HIGH (empirically confirmed by GroundTruthVerifier)
        - TOOL_RESULT → MEDIUM (tool ran but no ground-truth verification)
        - SYSTEM_OBSERVATION → MEDIUM
        - USER_CONVERSATION → MEDIUM (inferred, not directly stated)
        - IMPORTED_DATA → MEDIUM
        - DERIVED_CONTEXT → LOW (model inference without evidence)

        CRITICAL: LLM model belief without verification can never become HIGH confidence.
        """
        if user_explicit or source == MemorySource.USER_EXPLICIT:
            return ConfidenceLevel.HIGH
        if task_verified or source == MemorySource.VERIFIED_TASK:
            return ConfidenceLevel.HIGH
        if source in (MemorySource.TOOL_RESULT, MemorySource.SYSTEM_OBSERVATION,
                      MemorySource.USER_CONVERSATION, MemorySource.IMPORTED_DATA):
            return ConfidenceLevel.MEDIUM
        # DERIVED_CONTEXT or unknown
        return ConfidenceLevel.LOW

    def _determine_verification_status(
        self, source: MemorySource, task_verified: bool, user_explicit: bool
    ) -> VerificationStatus:
        """
        Verification rules:
        - VERIFIED_TASK + task_verified=True → VERIFIED
        - USER_EXPLICIT → VERIFIED (user is the ground truth for their own preferences)
        - Everything else → UNVERIFIED
        """
        if task_verified and source == MemorySource.VERIFIED_TASK:
            return VerificationStatus.VERIFIED
        if user_explicit or source == MemorySource.USER_EXPLICIT:
            return VerificationStatus.VERIFIED
        return VerificationStatus.UNVERIFIED

    def _infer_privacy_class(self, content: str, memory_type: MemoryType) -> PrivacyClass:
        """Infer privacy class from content and type heuristics."""
        lower = content.lower()
        # Preferences about personal behavior → PRIVATE
        if memory_type == MemoryType.PREFERENCE:
            return PrivacyClass.PRIVATE
        # Health, financial, relationship keywords → SENSITIVE
        sensitive_keywords = ("health", "medical", "salary", "income", "bank",
                               "relationship", "personal", "ssn", "id number")
        if any(kw in lower for kw in sensitive_keywords):
            return PrivacyClass.SENSITIVE
        return PrivacyClass.NORMAL


memory_write_policy = MemoryWritePolicy()
