"""V8.28 Goal Experience / Outcome Memory package.

Phase 1 foundation: types, policy, store, flag. Lazy exports.
Importing this package must not connect to Postgres or load proactive.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "Outcome",
    "ExperienceStatus",
    "GoalExperience",
    "ExperienceResult",
    "ExperienceResultStatus",
    "SCHEMA_VERSION",
    "MAX_EXPERIENCES_PER_OWNER",
    "MAX_CONTENT_CHARS",
    "MAX_STEPS",
    "MAX_LIST_RESULTS",
    "is_v828_goal_experience_enabled",
    "is_sensitive_experience_content",
    "validate_experience_fields",
    "create_experience",
    "get_experience",
    "list_experiences",
    "update_experience",
    "transition_experience",
    "forget_experience",
    "ensure_goal_experience_schema",
    "use_test_experience_store",
    "reset_experience_for_tests",
    "new_experience_id",
    "capture_goal_experience_from_terminal_state",
    "maybe_capture_after_registry_result",
    "get_relevant_goal_experiences",
    "experience_strings_for_consumer",
    "format_experience_for_consumer",
    "merge_profile_experience_memory",
    "MAX_CONSUMER_EXPERIENCES",
    "detect_experience_intent",
    "should_route_experience",
    "handle_experience_request",
    "ExperienceIntent",
    "reset_experience_confirms_for_tests",
]

_LAZY = {
    "Outcome": ("orchestration.experience.types", "Outcome"),
    "ExperienceStatus": ("orchestration.experience.types", "ExperienceStatus"),
    "GoalExperience": ("orchestration.experience.types", "GoalExperience"),
    "ExperienceResult": ("orchestration.experience.types", "ExperienceResult"),
    "ExperienceResultStatus": (
        "orchestration.experience.types",
        "ExperienceResultStatus",
    ),
    "SCHEMA_VERSION": ("orchestration.experience.types", "SCHEMA_VERSION"),
    "MAX_EXPERIENCES_PER_OWNER": (
        "orchestration.experience.types",
        "MAX_EXPERIENCES_PER_OWNER",
    ),
    "MAX_CONTENT_CHARS": ("orchestration.experience.types", "MAX_CONTENT_CHARS"),
    "MAX_STEPS": ("orchestration.experience.types", "MAX_STEPS"),
    "MAX_LIST_RESULTS": ("orchestration.experience.types", "MAX_LIST_RESULTS"),
    "is_v828_goal_experience_enabled": (
        "orchestration.experience.config",
        "is_v828_goal_experience_enabled",
    ),
    "is_sensitive_experience_content": (
        "orchestration.experience.policy",
        "is_sensitive_experience_content",
    ),
    "validate_experience_fields": (
        "orchestration.experience.policy",
        "validate_experience_fields",
    ),
    "create_experience": ("orchestration.experience.store", "create_experience"),
    "get_experience": ("orchestration.experience.store", "get_experience"),
    "list_experiences": ("orchestration.experience.store", "list_experiences"),
    "update_experience": ("orchestration.experience.store", "update_experience"),
    "transition_experience": (
        "orchestration.experience.store",
        "transition_experience",
    ),
    "forget_experience": ("orchestration.experience.store", "forget_experience"),
    "ensure_goal_experience_schema": (
        "orchestration.experience.store",
        "ensure_goal_experience_schema",
    ),
    "use_test_experience_store": (
        "orchestration.experience.store",
        "use_test_experience_store",
    ),
    "reset_experience_for_tests": (
        "orchestration.experience.store",
        "reset_experience_for_tests",
    ),
    "new_experience_id": ("orchestration.experience.store", "new_experience_id"),
    "capture_goal_experience_from_terminal_state": (
        "orchestration.experience.capture",
        "capture_goal_experience_from_terminal_state",
    ),
    "maybe_capture_after_registry_result": (
        "orchestration.experience.capture",
        "maybe_capture_after_registry_result",
    ),
    "get_relevant_goal_experiences": (
        "orchestration.experience.resolve",
        "get_relevant_goal_experiences",
    ),
    "experience_strings_for_consumer": (
        "orchestration.experience.resolve",
        "experience_strings_for_consumer",
    ),
    "format_experience_for_consumer": (
        "orchestration.experience.resolve",
        "format_experience_for_consumer",
    ),
    "merge_profile_experience_memory": (
        "orchestration.experience.resolve",
        "merge_profile_experience_memory",
    ),
    "MAX_CONSUMER_EXPERIENCES": (
        "orchestration.experience.resolve",
        "MAX_CONSUMER_EXPERIENCES",
    ),
    "detect_experience_intent": (
        "orchestration.experience.intent",
        "detect_experience_intent",
    ),
    "should_route_experience": (
        "orchestration.experience.intent",
        "should_route_experience",
    ),
    "handle_experience_request": (
        "orchestration.experience.intent",
        "handle_experience_request",
    ),
    "ExperienceIntent": (
        "orchestration.experience.intent",
        "ExperienceIntent",
    ),
    "reset_experience_confirms_for_tests": (
        "orchestration.experience.confirm",
        "reset_experience_confirms_for_tests",
    ),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr = target
    import importlib

    mod = importlib.import_module(mod_name)
    value = getattr(mod, attr)
    globals()[name] = value
    return value
