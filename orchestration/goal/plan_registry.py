"""V8.2 explicit capability/action/parameter allowlist. Data only; not V7 kernels."""

from __future__ import annotations

from typing import Dict, FrozenSet, Tuple

MAX_STEPS = 16
MAX_DEPENDENCY_DEPTH = 4
MIN_TIMEOUT_MS = 1
MAX_TIMEOUT_MS = 30000
MAX_IDEMPOTENT_RETRIES = 2
MAX_PARAM_CHARS = 512
PLAN_SCHEMA_VERSION = "v82.1"

CAP_COMPUTER = "computer"
CAP_BROWSER = "browser"
CAP_FILESYSTEM = "filesystem"
CAP_SEQUENCE = "sequence"
CAP_VERIFICATION = "verification"
CAP_WORLD_ACT = "world_act"
CAP_MEMORY_READ = "memory_read"
CAP_CONVERSATION = "conversation"

ALLOWED_CAPABILITIES = frozenset({
    CAP_COMPUTER, CAP_BROWSER, CAP_FILESYSTEM, CAP_SEQUENCE,
    CAP_VERIFICATION, CAP_WORLD_ACT, CAP_MEMORY_READ, CAP_CONVERSATION,
})

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

ALLOWED_ACTIONS: Dict[str, FrozenSet[str]] = {
    CAP_COMPUTER: frozenset({"OBSERVE", "CLICK", "TYPE"}),
    CAP_BROWSER: frozenset({"NAVIGATE", "CLICK", "TYPE", "BACK", "FORWARD", "REFRESH"}),
    CAP_FILESYSTEM: frozenset({
        "OBSERVE_PATH", "LIST_DIRECTORY", "READ_FILE",
        "CREATE_DIRECTORY", "WRITE_FILE", "COPY_FILE", "MOVE_FILE", "DELETE_FILE",
    }),
    CAP_SEQUENCE: frozenset({"DECLARE"}),
    CAP_VERIFICATION: VERIFY_ACTIONS,
    CAP_WORLD_ACT: frozenset({"CALENDAR_HOLD", "INTERNAL_NOTE"}),
    CAP_MEMORY_READ: frozenset({"RETRIEVE"}),
    CAP_CONVERSATION: frozenset({"RESPOND"}),
}

COMPUTER_PARAMS: Dict[str, FrozenSet[str]] = {
    "OBSERVE": frozenset({"session_id"}),
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

VERIFY_PARAMS = frozenset({
    "domain", "path", "session_id", "expected_hash", "expected_url", "expected_origin",
    "expected_type", "expected_exists", "expected_name", "expected_size_bytes",
    "expected_outcome", "expected_disabled", "expected_value",
    "automation_id", "runtime_id", "control_type", "name",
    "role", "element_id", "test_id", "text",
})

WORLD_PARAMS: Dict[str, FrozenSet[str]] = {
    "CALENDAR_HOLD": frozenset({"title"}),
    "INTERNAL_NOTE": frozenset({"note"}),
}

MEMORY_PARAMS = frozenset({"query"})
CONVERSATION_PARAMS = frozenset()
SEQUENCE_PARAMS = frozenset({"label"})

FORBIDDEN_PARAM_KEYS = frozenset({
    "callable", "function", "code", "command", "shell", "import", "module",
    "eval", "exec", "subprocess", "tool_name", "all_tools", "python",
    "arbitrary_target", "callback", "lambda", "class_name",
    "hwnd", "pid", "exe_path", "exe_path_norm", "window_handle",
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
    (CAP_WORLD_ACT, "CALENDAR_HOLD"),
    (CAP_WORLD_ACT, "INTERNAL_NOTE"),
    (CAP_SEQUENCE, "DECLARE"),
})

IDEMPOTENT_RETRY: FrozenSet[Tuple[str, str]] = frozenset({
    (CAP_COMPUTER, "OBSERVE"),
    (CAP_BROWSER, "BACK"),
    (CAP_BROWSER, "FORWARD"),
    (CAP_BROWSER, "REFRESH"),
    (CAP_FILESYSTEM, "OBSERVE_PATH"),
    (CAP_FILESYSTEM, "LIST_DIRECTORY"),
    (CAP_FILESYSTEM, "READ_FILE"),
    (CAP_MEMORY_READ, "RETRIEVE"),
}) | {(CAP_VERIFICATION, a) for a in VERIFY_ACTIONS}

RISK_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def allowed_params(capability: str, action: str) -> FrozenSet[str]:
    if capability == CAP_COMPUTER:
        return COMPUTER_PARAMS.get(action, frozenset())
    if capability == CAP_BROWSER:
        return BROWSER_PARAMS.get(action, frozenset())
    if capability == CAP_FILESYSTEM:
        return FS_PARAMS.get(action, frozenset())
    if capability == CAP_VERIFICATION:
        return VERIFY_PARAMS
    if capability == CAP_WORLD_ACT:
        return WORLD_PARAMS.get(action, frozenset())
    if capability == CAP_MEMORY_READ:
        return MEMORY_PARAMS
    if capability == CAP_CONVERSATION:
        return CONVERSATION_PARAMS
    if capability == CAP_SEQUENCE:
        return SEQUENCE_PARAMS
    return frozenset()


def policy_risk(capability: str, action: str) -> str:
    if capability == CAP_VERIFICATION:
        return "LOW"
    if capability == CAP_COMPUTER:
        if action == "OBSERVE":
            return "LOW"
        return "MEDIUM"
    if capability == CAP_BROWSER:
        return "MEDIUM"
    if capability == CAP_FILESYSTEM:
        if action == "DELETE_FILE":
            return "HIGH"
        if (capability, action) in MUTATION_ACTIONS:
            return "MEDIUM"
        return "LOW"
    if capability == CAP_WORLD_ACT:
        return "MEDIUM"
    if capability == CAP_SEQUENCE:
        return "MEDIUM"
    if capability == CAP_MEMORY_READ:
        return "LOW"
    if capability == CAP_CONVERSATION:
        return "LOW"
    return "HIGH"


def aggregate_risk(risks: Tuple[str, ...]) -> str:
    strongest = "LOW"
    best = RISK_RANK[strongest]
    for r in risks:
        n = RISK_RANK.get(r, 4)
        if n > best:
            best = n
            strongest = r if r in RISK_RANK else "HIGH"
    return strongest
