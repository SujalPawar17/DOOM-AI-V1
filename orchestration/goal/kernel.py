"""V8.1 classification kernel. Zero execution authority."""

from __future__ import annotations

import time
import uuid

from orchestration.goal.catalog import lookup
from orchestration.goal.hashing import goal_hash
from orchestration.goal.normalizer import normalize_intent
from orchestration.goal.types import (
    GOAL_SCHEMA_VERSION,
    AvailabilityStatus,
    CapabilityClass,
    GoalClassificationResult,
    GoalSpec,
    INTENT_TO_CAPABILITY,
    IntentClass,
    Provenance,
    sanitize_context,
    truncate_intent,
)
from proactive.config import OWNER_ID, is_v8_enabled


def process_goal(raw_intent: str, context=None) -> GoalClassificationResult:
    """Classify user text into a GoalSpec and availability. Never executes."""
    ctx = sanitize_context(context)
    text = truncate_intent(raw_intent)
    intent = normalize_intent(text)
    cap = INTENT_TO_CAPABILITY[intent]
    owner = str(ctx.get("owner_id") or OWNER_ID)[:64]
    session = str(ctx.get("session_id") or "")[:64]
    computer_session = str(ctx.get("computer_session_id") or "")[:64]
    gid = str(uuid.uuid4())
    ts = int(time.time() * 1000)
    digest = goal_hash({
        "capability_class": cap.value,
        "computer_session_id": computer_session,
        "normalized_intent": intent.value,
        "owner_id": owner,
        "provenance": Provenance.USER_TEXT.value,
        "raw_intent": text,
        "schema_version": GOAL_SCHEMA_VERSION,
        "session_id": session,
    })
    spec = GoalSpec(
        goal_id=gid,
        schema_version=GOAL_SCHEMA_VERSION,
        owner_id=owner,
        session_id=session,
        computer_session_id=computer_session,
        raw_intent=text,
        normalized_intent=intent,
        capability_class=cap,
        provenance=Provenance.USER_TEXT,
        requested_unix_ms=ts,
        goal_hash=digest,
    )
    record = lookup(cap)
    if not is_v8_enabled():
        return GoalClassificationResult(
            status=AvailabilityStatus.V8_DISABLED,
            reason_code="V8_DISABLED",
            goal=spec,
            capability=record,
            execution_permitted=False,
        )
    if intent is IntentClass.UNKNOWN:
        return GoalClassificationResult(
            status=AvailabilityStatus.UNKNOWN,
            reason_code="UNKNOWN_INTENT",
            goal=spec,
            capability=record,
            execution_permitted=False,
        )
    if intent is IntentClass.AMBIGUOUS:
        return GoalClassificationResult(
            status=AvailabilityStatus.AMBIGUOUS,
            reason_code="AMBIGUOUS_INTENT",
            goal=spec,
            capability=record,
            execution_permitted=False,
        )
    if cap is CapabilityClass.NONE or not record.exists:
        return GoalClassificationResult(
            status=AvailabilityStatus.CAPABILITY_UNAVAILABLE,
            reason_code="UNKNOWN_CAPABILITY",
            goal=spec,
            capability=record,
            execution_permitted=False,
        )
    if not record.available:
        return GoalClassificationResult(
            status=AvailabilityStatus.CAPABILITY_UNAVAILABLE,
            reason_code="CAPABILITY_UNAVAILABLE",
            goal=spec,
            capability=record,
            execution_permitted=False,
        )
    return GoalClassificationResult(
        status=AvailabilityStatus.CAPABILITY_AVAILABLE,
        reason_code="CAPABILITY_AVAILABLE",
        goal=spec,
        capability=record,
        execution_permitted=False,
    )
