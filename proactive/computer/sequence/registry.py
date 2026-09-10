"""Explicit V7 capability/action registry. No dynamic import."""

from __future__ import annotations

from typing import Dict, FrozenSet, Tuple

CAP_COMPUTER = "computer"
CAP_BROWSER = "browser"
CAP_FILESYSTEM = "filesystem"
CAP_VERIFY = "verify"

VERIFY_ACTIONS = frozenset({
    "OBSERVATION_HASH_MATCH",
    "TARGET_EXISTS",
    "TARGET_ABSENT",
    "TARGET_IDENTITY_MATCH",
    "TARGET_STATE_MATCH",
    "URL_MATCH",
    "ORIGIN_MATCH",
    "FILE_METADATA_MATCH",
    "DIRECTORY_ENTRY_MATCH",
    "TEXT_PRESENT",
    "TEXT_ABSENT",
})

ALLOWED: Dict[str, FrozenSet[str]] = {
    CAP_COMPUTER: frozenset({"CLICK", "TYPE"}),
    CAP_BROWSER: frozenset({"NAVIGATE", "CLICK", "TYPE", "BACK", "FORWARD", "REFRESH"}),
    CAP_FILESYSTEM: frozenset({
        "OBSERVE_PATH",
        "LIST_DIRECTORY",
        "READ_FILE",
        "CREATE_DIRECTORY",
        "WRITE_FILE",
        "COPY_FILE",
        "MOVE_FILE",
        "DELETE_FILE",
    }),
    CAP_VERIFY: VERIFY_ACTIONS,
}

COMPUTER_PARAMS: Dict[str, FrozenSet[str]] = {
    "CLICK": frozenset({
        "automation_id", "runtime_id", "control_type", "name",
        "session_id", "precondition_observation_hash",
    }),
    "TYPE": frozenset({
        "automation_id", "runtime_id", "control_type", "name",
        "session_id", "precondition_observation_hash", "text", "sensitive",
    }),
}

BROWSER_PARAMS: Dict[str, FrozenSet[str]] = {
    "NAVIGATE": frozenset({"session_id", "url", "precondition_observation_hash"}),
    "CLICK": frozenset({
        "session_id", "role", "name", "element_id", "test_id",
        "precondition_observation_hash",
    }),
    "TYPE": frozenset({
        "session_id", "role", "name", "element_id", "test_id",
        "precondition_observation_hash", "text", "sensitive",
    }),
    "BACK": frozenset({"session_id", "precondition_observation_hash"}),
    "FORWARD": frozenset({"session_id", "precondition_observation_hash"}),
    "REFRESH": frozenset({"session_id", "precondition_observation_hash"}),
}

FS_PARAMS: Dict[str, FrozenSet[str]] = {
    "OBSERVE_PATH": frozenset({"path", "precondition_observation_hash", "expected_exists", "expected_type"}),
    "LIST_DIRECTORY": frozenset({"path", "precondition_observation_hash", "expected_exists", "expected_type"}),
    "READ_FILE": frozenset({"path", "precondition_observation_hash", "expected_exists", "expected_type"}),
    "CREATE_DIRECTORY": frozenset({"path", "precondition_observation_hash", "expected_exists", "expected_type"}),
    "WRITE_FILE": frozenset({
        "path", "content", "precondition_observation_hash", "expected_exists", "expected_type",
    }),
    "COPY_FILE": frozenset({
        "path", "dest_path", "precondition_observation_hash", "expected_exists", "expected_type",
    }),
    "MOVE_FILE": frozenset({
        "path", "dest_path", "precondition_observation_hash", "expected_exists", "expected_type",
    }),
    "DELETE_FILE": frozenset({
        "path", "precondition_observation_hash", "expected_exists", "expected_type",
    }),
}

VERIFY_PARAMS: FrozenSet[str] = frozenset({
    "domain", "path", "session_id", "expected_hash", "expected_url", "expected_origin",
    "expected_type", "expected_exists", "expected_name", "expected_size_bytes",
    "expected_outcome", "expected_disabled", "expected_value",
    "automation_id", "runtime_id", "control_type", "name",
    "role", "element_id", "test_id", "text",
    "timeout_ms", "max_attempts",
})

MUTATION_ACTIONS: FrozenSet[Tuple[str, str]] = frozenset({
    (CAP_COMPUTER, "CLICK"),
    (CAP_COMPUTER, "TYPE"),
    (CAP_BROWSER, "NAVIGATE"),
    (CAP_BROWSER, "CLICK"),
    (CAP_BROWSER, "TYPE"),
    (CAP_FILESYSTEM, "CREATE_DIRECTORY"),
    (CAP_FILESYSTEM, "WRITE_FILE"),
    (CAP_FILESYSTEM, "COPY_FILE"),
    (CAP_FILESYSTEM, "MOVE_FILE"),
    (CAP_FILESYSTEM, "DELETE_FILE"),
})

IDEMPOTENT_RETRY: FrozenSet[Tuple[str, str]] = frozenset({
    (CAP_BROWSER, "BACK"),
    (CAP_BROWSER, "FORWARD"),
    (CAP_BROWSER, "REFRESH"),
    (CAP_FILESYSTEM, "OBSERVE_PATH"),
    (CAP_FILESYSTEM, "LIST_DIRECTORY"),
    (CAP_FILESYSTEM, "READ_FILE"),
} | {(CAP_VERIFY, a) for a in VERIFY_ACTIONS})

RISK_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def allowed_params(capability: str, action: str) -> FrozenSet[str]:
    if capability == CAP_COMPUTER:
        return COMPUTER_PARAMS.get(action, frozenset())
    if capability == CAP_BROWSER:
        return BROWSER_PARAMS.get(action, frozenset())
    if capability == CAP_FILESYSTEM:
        return FS_PARAMS.get(action, frozenset())
    if capability == CAP_VERIFY:
        return VERIFY_PARAMS
    return frozenset()


def step_risk(capability: str, action: str) -> str:
    from proactive.computer.policy import (
        RISK_BROWSER_CLICK,
        RISK_BROWSER_HISTORY,
        RISK_BROWSER_NAVIGATE,
        RISK_BROWSER_TYPE,
        RISK_CLICK,
        RISK_FS_DELETE,
        RISK_FS_MUTATE,
        RISK_FS_READ,
        RISK_TYPE,
        RISK_VERIFY,
    )
    if capability == CAP_VERIFY:
        return RISK_VERIFY
    if capability == CAP_COMPUTER:
        return RISK_CLICK if action == "CLICK" else RISK_TYPE
    if capability == CAP_BROWSER:
        if action == "NAVIGATE":
            return RISK_BROWSER_NAVIGATE
        if action == "CLICK":
            return RISK_BROWSER_CLICK
        if action == "TYPE":
            return RISK_BROWSER_TYPE
        return RISK_BROWSER_HISTORY
    if action == "DELETE_FILE":
        return RISK_FS_DELETE
    if (capability, action) in MUTATION_ACTIONS:
        return RISK_FS_MUTATE
    return RISK_FS_READ


def aggregate_risk(pairs: Tuple[Tuple[str, str], ...]) -> str:
    strongest = "LOW"
    best = RISK_RANK[strongest]
    for cap, act in pairs:
        r = step_risk(cap, act)
        n = RISK_RANK.get(r, 2)
        if n > best:
            best = n
            strongest = r
    return strongest
