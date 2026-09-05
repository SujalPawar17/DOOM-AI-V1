"""
DOOM V5.1 — Memory Writers
Typed, high-level write helpers for the most common memory operations.
All writes go through MemoryManager.store() — never bypass policy.
"""
from typing import Any, Dict, List, Optional

from memory.schemas import MemoryRecord
from memory.types import (
    MemoryType, MemorySource, ConfidenceLevel,
    VerificationStatus, PrivacyClass,
)


def write_experience(
    goal: str,
    outcome_summary: str,
    tools_used: Optional[List[str]] = None,
    task_id: Optional[str] = None,
    project_id: Optional[str] = None,
    task_verified: bool = False,
) -> Optional[MemoryRecord]:
    """
    Record a verified task execution experience.
    Only called after GroundTruthVerifier confirms task success.
    This replaces the legacy episodic_memory.record_episode() call in bridge.py.

    IMPORTANT: If task_verified=False, experience is stored as UNVERIFIED with LOW confidence.
    """
    from memory.manager import memory_manager

    # Build a concise, meaningful experience summary
    tools_str = ", ".join(tools_used[:3]) if tools_used else "no specific tools"
    content = f"Task: '{goal[:200]}' completed using {tools_str}. Outcome: {outcome_summary[:300]}"

    record = MemoryRecord(
        memory_type=MemoryType.EXPERIENCE,
        content=content,
        source=MemorySource.VERIFIED_TASK if task_verified else MemorySource.TOOL_RESULT,
        confidence=ConfidenceLevel.HIGH if task_verified else ConfidenceLevel.MEDIUM,
        verification_status=VerificationStatus.VERIFIED if task_verified else VerificationStatus.UNVERIFIED,
        importance=0.7 if task_verified else 0.4,
        task_id=task_id,
        project_id=project_id,
        tags=["experience", "task_execution"],
        metadata={
            "goal": goal[:300],
            "tools_used": tools_used or [],
            "verified": task_verified,
        },
    )
    return memory_manager.store(record)


def write_preference(
    preference_key: str,
    preference_value: str,
    user_explicit: bool = True,
    project_id: Optional[str] = None,
) -> Optional[MemoryRecord]:
    """
    Store a user preference memory.
    Handles supersession of conflicting existing preferences automatically.

    user_explicit=True → HIGH confidence (user directly stated this)
    user_explicit=False → MEDIUM confidence (inferred from conversation)
    """
    from memory.manager import memory_manager

    content = f"User preference: {preference_key} = {preference_value}"
    record = MemoryRecord(
        memory_type=MemoryType.PREFERENCE,
        content=content,
        source=MemorySource.USER_EXPLICIT if user_explicit else MemorySource.USER_CONVERSATION,
        confidence=ConfidenceLevel.HIGH if user_explicit else ConfidenceLevel.MEDIUM,
        verification_status=VerificationStatus.VERIFIED if user_explicit else VerificationStatus.UNVERIFIED,
        importance=0.8 if user_explicit else 0.5,
        project_id=project_id,
        privacy_class=PrivacyClass.PRIVATE,
        tags=["preference", preference_key.lower().replace(" ", "_")],
        metadata={"key": preference_key, "value": preference_value, "user_explicit": user_explicit},
    )
    # Check for conflicting preference and supersede if found
    return memory_manager.store_with_supersession(record, conflict_keywords=[preference_key])


def write_semantic_fact(
    key: str,
    value: str,
    category: str = "general",
    source: MemorySource = MemorySource.SYSTEM_OBSERVATION,
    project_id: Optional[str] = None,
) -> Optional[MemoryRecord]:
    """
    Store a semantic fact in the V5.1 canonical memory store.
    Note: Legacy semantic_memory.remember_fact() continues to work for backward compat.
    This function writes to the canonical memory_records table.
    """
    from memory.manager import memory_manager

    content = f"{key}: {value}"
    record = MemoryRecord(
        memory_type=MemoryType.SEMANTIC,
        content=content,
        source=source,
        confidence=ConfidenceLevel.MEDIUM,
        importance=0.5,
        project_id=project_id,
        tags=["semantic", category, key.lower().replace(" ", "_")],
        metadata={"key": key, "value": value, "category": category},
    )
    return memory_manager.store(record)


def write_project_memory(
    project_id: str,
    content: str,
    importance: float = 0.6,
    tags: Optional[List[str]] = None,
    source: MemorySource = MemorySource.USER_CONVERSATION,
) -> Optional[MemoryRecord]:
    """
    Store project-specific context memory.
    """
    from memory.manager import memory_manager

    record = MemoryRecord(
        memory_type=MemoryType.PROJECT,
        content=content,
        source=source,
        confidence=ConfidenceLevel.MEDIUM,
        importance=importance,
        project_id=project_id,
        tags=["project", project_id.lower()] + (tags or []),
        metadata={"project_id": project_id},
    )
    return memory_manager.store(record)
