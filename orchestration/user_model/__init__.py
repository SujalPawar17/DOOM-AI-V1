"""V8.27 User Model / Personal Cognitive Profile package.

Phase 1 foundation: types, policy, store, flag. Lazy exports.
Importing this package must not connect to Postgres or load proactive.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "Category",
    "Confidence",
    "Provenance",
    "ProfileStatus",
    "ProfileEntry",
    "ProfileResult",
    "ProfileResultStatus",
    "SCHEMA_VERSION",
    "MAX_PROFILE_ENTRIES",
    "MAX_ENTRIES_PER_CATEGORY",
    "MAX_PROFILE_VALUE_LENGTH",
    "MAX_PROFILE_KEY_LENGTH",
    "MAX_CONSUMER_RESULTS",
    "MAX_TRANSPARENCY_RESULTS",
    "is_v827_user_model_enabled",
    "is_sensitive_profile_content",
    "validate_entry_fields",
    "upsert_profile_entry",
    "get_profile_entry",
    "list_profile_entries",
    "forget_profile_entry",
    "forget_profile_category",
    "clear_profile",
    "ensure_user_profile_schema",
    "use_test_user_model_store",
    "reset_user_model_for_tests",
    "detect_profile_intent",
    "handle_user_model_request",
    "should_route_user_model",
    "ProfileIntent",
    "reset_profile_confirms_for_tests",
    "project_memory_to_profile",
    "project_after_memory_save",
    "map_memory_to_profile",
    "get_relevant_user_profile",
    "profile_strings_for_consumer",
    "format_profile_for_consumer",
    "try_direct_profile_fact_answer",
]

_LAZY = {
    "Category": ("orchestration.user_model.types", "Category"),
    "Confidence": ("orchestration.user_model.types", "Confidence"),
    "Provenance": ("orchestration.user_model.types", "Provenance"),
    "ProfileStatus": ("orchestration.user_model.types", "ProfileStatus"),
    "ProfileEntry": ("orchestration.user_model.types", "ProfileEntry"),
    "ProfileResult": ("orchestration.user_model.types", "ProfileResult"),
    "ProfileResultStatus": ("orchestration.user_model.types", "ProfileResultStatus"),
    "SCHEMA_VERSION": ("orchestration.user_model.types", "SCHEMA_VERSION"),
    "MAX_PROFILE_ENTRIES": ("orchestration.user_model.types", "MAX_PROFILE_ENTRIES"),
    "MAX_ENTRIES_PER_CATEGORY": ("orchestration.user_model.types", "MAX_ENTRIES_PER_CATEGORY"),
    "MAX_PROFILE_VALUE_LENGTH": ("orchestration.user_model.types", "MAX_PROFILE_VALUE_LENGTH"),
    "MAX_PROFILE_KEY_LENGTH": ("orchestration.user_model.types", "MAX_PROFILE_KEY_LENGTH"),
    "MAX_CONSUMER_RESULTS": ("orchestration.user_model.types", "MAX_CONSUMER_RESULTS"),
    "MAX_TRANSPARENCY_RESULTS": ("orchestration.user_model.types", "MAX_TRANSPARENCY_RESULTS"),
    "is_v827_user_model_enabled": ("orchestration.user_model.config", "is_v827_user_model_enabled"),
    "is_sensitive_profile_content": ("orchestration.user_model.policy", "is_sensitive_profile_content"),
    "validate_entry_fields": ("orchestration.user_model.policy", "validate_entry_fields"),
    "upsert_profile_entry": ("orchestration.user_model.store", "upsert_profile_entry"),
    "get_profile_entry": ("orchestration.user_model.store", "get_profile_entry"),
    "list_profile_entries": ("orchestration.user_model.store", "list_profile_entries"),
    "forget_profile_entry": ("orchestration.user_model.store", "forget_profile_entry"),
    "forget_profile_category": ("orchestration.user_model.store", "forget_profile_category"),
    "clear_profile": ("orchestration.user_model.store", "clear_profile"),
    "ensure_user_profile_schema": ("orchestration.user_model.store", "ensure_user_profile_schema"),
    "use_test_user_model_store": ("orchestration.user_model.store", "use_test_user_model_store"),
    "reset_user_model_for_tests": ("orchestration.user_model.store", "reset_user_model_for_tests"),
    "detect_profile_intent": ("orchestration.user_model.intent", "detect_profile_intent"),
    "handle_user_model_request": ("orchestration.user_model.intent", "handle_user_model_request"),
    "should_route_user_model": ("orchestration.user_model.intent", "should_route_user_model"),
    "ProfileIntent": ("orchestration.user_model.intent", "ProfileIntent"),
    "reset_profile_confirms_for_tests": (
        "orchestration.user_model.confirm",
        "reset_profile_confirms_for_tests",
    ),
    "project_memory_to_profile": (
        "orchestration.user_model.projection",
        "project_memory_to_profile",
    ),
    "project_after_memory_save": (
        "orchestration.user_model.projection",
        "project_after_memory_save",
    ),
    "map_memory_to_profile": (
        "orchestration.user_model.projection",
        "map_memory_to_profile",
    ),
    "get_relevant_user_profile": (
        "orchestration.user_model.resolve",
        "get_relevant_user_profile",
    ),
    "profile_strings_for_consumer": (
        "orchestration.user_model.resolve",
        "profile_strings_for_consumer",
    ),
    "format_profile_for_consumer": (
        "orchestration.user_model.resolve",
        "format_profile_for_consumer",
    ),
    "try_direct_profile_fact_answer": (
        "orchestration.user_model.resolve",
        "try_direct_profile_fact_answer",
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
