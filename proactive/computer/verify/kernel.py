"""V7.6 verification kernel. Structured state is authoritative. Fail closed."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Tuple

from core.cost_guard import ResourceRequest, ResourceType, cost_guard
from proactive.computer.actions.types import ApprovalState
from proactive.computer.fs.kernel import execute_fs_action
from proactive.computer.fs.types import FsActionRequest, FsActionType
from proactive.computer.observe import capture_observation
from proactive.computer.policy import (
    VERIFY_SCHEMA_VERSION,
    verification_allowed,
    verify_max_attempts,
    verify_timeout_ms,
)
from proactive.computer.verify.types import (
    BROWSER_ONLY,
    FS_ONLY,
    MAX_TEXT_NEEDLE,
    VerificationEvidence,
    VerificationRequest,
    VerificationResult,
    VerificationSpec,
    VerificationStatus,
    VerificationType,
    expected_map,
)
from proactive.computer.verify.visual import visual_evidence
from proactive.config import OWNER_ID
from proactive.store import proactive_store

ALLOWED_CAPS = frozenset({"computer", "browser", "filesystem"})
ALLOWED_MODES = frozenset({"structured", "visual"})


def _result(
    request: VerificationRequest,
    status: VerificationStatus,
    *,
    after: str = "",
    failure: str = "",
    duration: int = 0,
    evidence: Optional[VerificationEvidence] = None,
    tel: Optional[Dict[str, Any]] = None,
) -> VerificationResult:
    spec = request.spec
    return VerificationResult(
        verification_id=str(spec.verification_id or ""),
        action_id=str(request.action_id or ""),
        verification_type=str(spec.verification_type or ""),
        status=status,
        before_hash=str(request.before_hash or ""),
        after_hash=after,
        failure_code=failure or (status.value if status != VerificationStatus.VERIFIED else ""),
        duration_ms=duration,
        evidence=evidence or VerificationEvidence(),
        telemetry=tel or {},
    )


def _emergency_stop(owner_id: str, computer_session_id: str = "") -> bool:
    owner = str(owner_id or OWNER_ID)[:64]
    sid = str(computer_session_id or "")[:64]
    if sid:
        row = proactive_store.get_computer_session(sid, owner)
        if row and (row.get("emergency_stop") or str(row.get("status") or "") == "STOPPED"):
            return True
    observing = proactive_store.get_observing_computer_session(owner)
    if observing and observing.get("emergency_stop"):
        return True
    return False


def _cost_ok(capability: str) -> bool:
    if capability == "computer":
        d = cost_guard.authorize(ResourceRequest(
            resource_type=ResourceType.VISION, provider="local_uia", capability="computer_verify",
        ))
        return bool(d.is_allow)
    if capability == "browser":
        d = cost_guard.authorize(ResourceRequest(
            resource_type=ResourceType.HTTP,
            provider="local_browser",
            capability="computer_verify",
            host="127.0.0.1",
            endpoint="http://127.0.0.1/",
        ))
        return bool(d.is_allow)
    d = cost_guard.authorize(ResourceRequest(
        resource_type=ResourceType.OTHER, provider="local_filesystem", capability="computer_verify",
    ))
    return bool(d.is_allow)


def _parse_type(raw: str) -> Optional[VerificationType]:
    try:
        return VerificationType(str(raw or "").strip())
    except ValueError:
        return None


def _validate(spec: VerificationSpec) -> Optional[str]:
    if not str(spec.verification_id or "").strip():
        return "MISSING_VERIFICATION_ID"
    if not str(spec.owner_id or "").strip():
        return "MISSING_OWNER"
    if spec.capability not in ALLOWED_CAPS:
        return "UNSUPPORTED_CAPABILITY"
    vtype = _parse_type(spec.verification_type)
    if vtype is None:
        return "UNSUPPORTED_TYPE"
    if spec.evidence_mode not in ALLOWED_MODES:
        return "INVALID_EVIDENCE_MODE"
    if int(spec.timeout_ms) < 50 or int(spec.timeout_ms) > verify_timeout_ms():
        return "INVALID_TIMEOUT"
    if int(spec.max_attempts) < 1 or int(spec.max_attempts) > verify_max_attempts():
        return "INVALID_ATTEMPTS"
    if vtype in BROWSER_ONLY and spec.capability != "browser":
        return "CAPABILITY_MISMATCH"
    if vtype in FS_ONLY and spec.capability != "filesystem":
        return "CAPABILITY_MISMATCH"
    exp = expected_map(spec.expected)
    if vtype == VerificationType.OBSERVATION_HASH_MATCH and not exp.get("expected_hash"):
        return "MISSING_EXPECTED_HASH"
    if spec.capability == "filesystem" and not exp.get("path"):
        return "MISSING_PATH"
    if spec.capability == "browser" and not exp.get("session_id"):
        return "MISSING_SESSION"
    if vtype == VerificationType.URL_MATCH and not exp.get("expected_url"):
        return "MISSING_EXPECTED_URL"
    if vtype == VerificationType.ORIGIN_MATCH and not exp.get("expected_origin"):
        return "MISSING_EXPECTED_ORIGIN"
    if vtype == VerificationType.DIRECTORY_ENTRY_MATCH and not exp.get("expected_name"):
        return "MISSING_EXPECTED_NAME"
    if vtype in (VerificationType.TEXT_PRESENT, VerificationType.TEXT_ABSENT):
        needle = exp.get("text") or ""
        if not needle:
            return "MISSING_TEXT"
        if len(needle) > MAX_TEXT_NEEDLE:
            return "TEXT_LIMIT"
    if vtype == VerificationType.TARGET_IDENTITY_MATCH:
        if spec.capability == "computer" and not (
            (exp.get("automation_id") or exp.get("runtime_id")) and exp.get("control_type")
        ):
            return "MISSING_IDENTITY"
        if spec.capability == "browser" and not (
            exp.get("element_id") or exp.get("test_id") or (exp.get("role") and exp.get("name"))
        ):
            return "MISSING_IDENTITY"
    return None


def _evidence(source: str, **fields: str) -> VerificationEvidence:
    pairs = tuple((k, str(v)[:128]) for k, v in fields.items() if v is not None)
    return VerificationEvidence(source=source, fields=pairs, visual_available=False)


def _eval_fs(vtype: VerificationType, exp: Dict[str, str], fs_fn: Callable) -> Tuple[VerificationStatus, str, str, VerificationEvidence]:
    req = FsActionRequest(
        action_id="verify-observe",
        action_type=FsActionType.LIST_DIRECTORY if vtype == VerificationType.DIRECTORY_ENTRY_MATCH else FsActionType.OBSERVE_PATH,
        path=exp.get("path", ""),
        owner_id=exp.get("owner_id", "") or OWNER_ID,
    )
    out = fs_fn(req)
    st = getattr(out.status, "value", str(out.status))
    after = str(out.after_observation_hash or out.before_observation_hash or "")
    ident = dict(out.identity or {})
    exists = bool(ident.get("exists"))
    otype = str(ident.get("object_type") or "")
    ev = _evidence(
        "filesystem",
        exists=str(exists).lower(),
        object_type=otype,
        size_bytes=str(ident.get("size_bytes") or ""),
        listing_count=str(len(out.listing or [])),
    )
    if st == "EMERGENCY_STOP_ACTIVE":
        return VerificationStatus.EMERGENCY_STOP_ACTIVE, "EMERGENCY_STOP_ACTIVE", after, ev
    if st in ("POLICY_BLOCKED", "PATH_NOT_ALLOWED", "PATH_DENIED", "PATH_INVALID", "PATH_ESCAPE_BLOCKED"):
        return VerificationStatus.VERIFICATION_BLOCKED, st, after, ev
    if st not in ("SUCCESS", "PATH_NOT_FOUND"):
        return VerificationStatus.VERIFICATION_FAILED, st, after, ev

    if vtype == VerificationType.OBSERVATION_HASH_MATCH:
        live = after
        ok = live and live == exp.get("expected_hash")
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "HASH_MISMATCH", after, ev)
    if vtype == VerificationType.TARGET_EXISTS:
        ok = exists
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "TARGET_MISSING", after, ev)
    if vtype == VerificationType.TARGET_ABSENT:
        ok = not exists
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "TARGET_STILL_PRESENT", after, ev)
    if vtype == VerificationType.FILE_METADATA_MATCH:
        et = (exp.get("expected_type") or "").strip()
        if et and otype != et:
            return VerificationStatus.NOT_VERIFIED, "TYPE_MISMATCH", after, ev
        if exp.get("expected_exists"):
            want = exp.get("expected_exists", "").lower() == "true"
            if exists != want:
                return VerificationStatus.NOT_VERIFIED, "EXISTS_MISMATCH", after, ev
        if exp.get("expected_size_bytes"):
            try:
                if int(ident.get("size_bytes") or 0) != int(exp.get("expected_size_bytes") or -1):
                    return VerificationStatus.NOT_VERIFIED, "SIZE_MISMATCH", after, ev
            except ValueError:
                return VerificationStatus.INVALID_VERIFICATION_SPEC, "INVALID_SIZE", after, ev
        return VerificationStatus.VERIFIED, "", after, ev
    if vtype == VerificationType.DIRECTORY_ENTRY_MATCH:
        if not exists or otype != "directory":
            return VerificationStatus.NOT_VERIFIED, "NOT_DIRECTORY", after, ev
        name = (exp.get("expected_name") or "")[:128]
        names = [str(c.get("name") or "") for c in (out.listing or [])]
        ok = name in names
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "ENTRY_MISSING", after, ev)
    if vtype == VerificationType.TEXT_PRESENT:
        needle = (exp.get("text") or "")[:MAX_TEXT_NEEDLE]
        blob = " ".join(str(c.get("name") or "")[:128] for c in (out.listing or [])[:50])
        blob = (ident.get("canonical_path") or "")[:256] + " " + blob
        ok = needle.lower() in blob.lower()
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "TEXT_MISSING", after, ev)
    if vtype == VerificationType.TEXT_ABSENT:
        needle = (exp.get("text") or "")[:MAX_TEXT_NEEDLE]
        blob = " ".join(str(c.get("name") or "")[:128] for c in (out.listing or [])[:50])
        ok = needle.lower() not in blob.lower()
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "TEXT_PRESENT", after, ev)
    if vtype == VerificationType.TARGET_IDENTITY_MATCH:
        et = exp.get("expected_type") or ""
        ok = exists and (not et or otype == et)
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "IDENTITY_MISMATCH", after, ev)
    if vtype == VerificationType.TARGET_STATE_MATCH:
        et = exp.get("expected_type") or ""
        ok = (not et or otype == et) and (exp.get("expected_exists", "true").lower() != "true" or exists)
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "STATE_MISMATCH", after, ev)
    return VerificationStatus.INVALID_VERIFICATION_SPEC, "UNSUPPORTED_TYPE", after, ev


def _eval_browser(vtype: VerificationType, exp: Dict[str, str], browser_fn: Callable, owner: str, session: str) -> Tuple[VerificationStatus, str, str, VerificationEvidence]:
    packed = browser_fn(exp.get("session_id") or session, owner)
    if isinstance(packed, dict):
        st = str(packed.get("status") or "")
        obs = packed.get("observation")
        err = str(packed.get("error") or st)
    else:
        st, obs, err = "SUCCESS", packed, ""
    if st == "EMERGENCY_STOP_ACTIVE":
        return VerificationStatus.EMERGENCY_STOP_ACTIVE, st, "", _evidence("browser")
    if st in ("POLICY_BLOCKED",):
        return VerificationStatus.VERIFICATION_BLOCKED, err or st, "", _evidence("browser")
    if obs is None:
        return VerificationStatus.VERIFICATION_FAILED, err or st or "NO_OBSERVATION", "", _evidence("browser")
    after = str(getattr(obs, "observation_hash", "") or "")
    url = str(getattr(obs, "url", "") or "")
    origin = str(getattr(obs, "origin", "") or "")
    elements = list(getattr(obs, "elements", None) or [])
    ev = _evidence("browser", url=url[:256], origin=origin[:256], nodes=str(len(elements)))

    def _find():
        eid = exp.get("element_id") or ""
        tid = exp.get("test_id") or ""
        role = exp.get("role") or ""
        name = exp.get("name") or ""
        hits = []
        for el in elements:
            if eid and str(getattr(el, "element_id", "") or "") != eid:
                continue
            if tid and str(getattr(el, "test_id", "") or "") != tid:
                continue
            if role and str(getattr(el, "role", "") or "") != role:
                continue
            if name and str(getattr(el, "name", "") or "") != name:
                continue
            if eid or tid or (role and name):
                hits.append(el)
        return hits

    if vtype == VerificationType.OBSERVATION_HASH_MATCH:
        ok = after and after == exp.get("expected_hash")
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "HASH_MISMATCH", after, ev)
    if vtype == VerificationType.URL_MATCH:
        ok = url == exp.get("expected_url")
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "URL_MISMATCH", after, ev)
    if vtype == VerificationType.ORIGIN_MATCH:
        ok = origin == exp.get("expected_origin")
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "ORIGIN_MISMATCH", after, ev)
    hits = _find()
    if vtype == VerificationType.TARGET_EXISTS:
        ok = bool(hits)
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "TARGET_MISSING", after, ev)
    if vtype == VerificationType.TARGET_ABSENT:
        ok = not hits
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "TARGET_STILL_PRESENT", after, ev)
    if vtype == VerificationType.TARGET_IDENTITY_MATCH:
        ok = len(hits) == 1
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "IDENTITY_MISMATCH", after, ev)
    if vtype == VerificationType.TARGET_STATE_MATCH:
        if len(hits) != 1:
            return VerificationStatus.NOT_VERIFIED, "IDENTITY_MISMATCH", after, ev
        el = hits[0]
        if bool(getattr(el, "is_password", False)):
            return VerificationStatus.VERIFICATION_BLOCKED, "PASSWORD_FIELD", after, ev
        if exp.get("expected_disabled"):
            want = exp.get("expected_disabled", "").lower() == "true"
            if bool(getattr(el, "disabled", False)) != want:
                return VerificationStatus.NOT_VERIFIED, "STATE_MISMATCH", after, ev
        if exp.get("expected_value"):
            live_val = str(getattr(el, "value", "") or "")
            if live_val != exp.get("expected_value"):
                return VerificationStatus.NOT_VERIFIED, "VALUE_MISMATCH", after, ev
        return VerificationStatus.VERIFIED, "", after, ev
    names = [str(getattr(el, "name", "") or "")[:80] for el in elements[:40] if not bool(getattr(el, "is_password", False))]
    blob = " ".join(names)
    if vtype == VerificationType.TEXT_PRESENT:
        needle = (exp.get("text") or "")[:MAX_TEXT_NEEDLE]
        ok = needle.lower() in blob.lower()
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "TEXT_MISSING", after, ev)
    if vtype == VerificationType.TEXT_ABSENT:
        needle = (exp.get("text") or "")[:MAX_TEXT_NEEDLE]
        ok = needle.lower() not in blob.lower()
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "TEXT_PRESENT", after, ev)
    return VerificationStatus.INVALID_VERIFICATION_SPEC, "UNSUPPORTED_TYPE", after, ev


def _eval_computer(vtype: VerificationType, exp: Dict[str, str], computer_fn: Callable, owner: str) -> Tuple[VerificationStatus, str, str, VerificationEvidence]:
    obs = computer_fn(owner)
    after = str(getattr(obs, "observation_hash", "") or "")
    auto = str(getattr(obs, "uia_automation_id", "") or "")
    runtime = str(getattr(obs, "uia_runtime_id", "") or "")
    ctype = str(getattr(obs, "uia_control_type", "") or "")
    outcome = str(getattr(obs, "outcome_code", "") or "")
    ev = _evidence("computer", control_type=ctype, outcome=outcome[:64])
    exists = bool(getattr(obs, "hwnd", 0)) and outcome not in ("WINDOW_GONE", "UIA_UNAVAILABLE", "INVALID_IDENTITY")
    if vtype == VerificationType.OBSERVATION_HASH_MATCH:
        ok = after and after == exp.get("expected_hash")
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "HASH_MISMATCH", after, ev)
    if vtype == VerificationType.TARGET_EXISTS:
        return (VerificationStatus.VERIFIED if exists else VerificationStatus.NOT_VERIFIED, "" if exists else "TARGET_MISSING", after, ev)
    if vtype == VerificationType.TARGET_ABSENT:
        return (VerificationStatus.VERIFIED if not exists else VerificationStatus.NOT_VERIFIED, "" if not exists else "TARGET_STILL_PRESENT", after, ev)
    if vtype == VerificationType.TARGET_IDENTITY_MATCH:
        ok = True
        if exp.get("automation_id") and auto != exp.get("automation_id"):
            ok = False
        if exp.get("runtime_id") and runtime != exp.get("runtime_id"):
            ok = False
        if exp.get("control_type") and ctype != exp.get("control_type"):
            ok = False
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "IDENTITY_MISMATCH", after, ev)
    if vtype == VerificationType.TARGET_STATE_MATCH:
        want = exp.get("expected_outcome") or ""
        ok = (not want or outcome == want) and exists
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "STATE_MISMATCH", after, ev)
    title = str(getattr(obs, "title_advisory", "") or "")[:80]
    if vtype == VerificationType.TEXT_PRESENT:
        needle = (exp.get("text") or "")[:MAX_TEXT_NEEDLE]
        ok = needle.lower() in title.lower()
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "TEXT_MISSING", after, ev)
    if vtype == VerificationType.TEXT_ABSENT:
        needle = (exp.get("text") or "")[:MAX_TEXT_NEEDLE]
        ok = needle.lower() not in title.lower()
        return (VerificationStatus.VERIFIED if ok else VerificationStatus.NOT_VERIFIED, "" if ok else "TEXT_PRESENT", after, ev)
    return VerificationStatus.INVALID_VERIFICATION_SPEC, "UNSUPPORTED_TYPE", after, ev


def execute_verification(
    request: VerificationRequest,
    *,
    fs_fn: Callable = execute_fs_action,
    computer_fn: Callable = capture_observation,
    browser_fn: Optional[Callable] = None,
    visual_fn: Callable = visual_evidence,
    clock: Callable[[], float] = time.monotonic,
    emergency_stop_fn: Optional[Callable[[str, str], bool]] = None,
    cost_ok_fn: Optional[Callable[[str], bool]] = None,
) -> VerificationResult:
    from proactive.computer.browser.kernel import observe_browser_session

    spec = request.spec
    tel: Dict[str, Any] = {
        "schema_version": VERIFY_SCHEMA_VERSION,
        "capability": spec.capability,
        "verification_type": spec.verification_type,
        "visual_used": False,
    }
    invalid = _validate(spec)
    if invalid:
        return _result(request, VerificationStatus.INVALID_VERIFICATION_SPEC, failure=invalid, tel=tel)
    if str(request.approved_spec_hash or "") and str(request.approved_spec_hash) != spec.spec_hash():
        return _result(request, VerificationStatus.APPROVAL_INVALIDATED, failure="APPROVAL_INVALIDATED", tel=tel)
    if spec.evidence_mode == "visual":
        vis = visual_fn(request) or {}
        tel["visual_used"] = False
        return _result(
            request,
            VerificationStatus.VERIFICATION_UNAVAILABLE,
            failure=str(vis.get("failure_code") or "VISUAL_RUNTIME_UNAVAILABLE"),
            tel=tel,
            evidence=VerificationEvidence(source="visual", visual_available=False),
        )
    if not verification_allowed():
        return _result(request, VerificationStatus.VERIFICATION_BLOCKED, failure="VERIFICATION_DISABLED", tel=tel)
    cost_fn = cost_ok_fn or _cost_ok
    if not cost_fn(spec.capability):
        return _result(request, VerificationStatus.VERIFICATION_BLOCKED, failure="COST_GUARD_BLOCKED", tel=tel)

    owner = str(spec.owner_id or OWNER_ID)[:64]
    stop_fn = emergency_stop_fn or _emergency_stop
    bfn = browser_fn or observe_browser_session
    vtype = _parse_type(spec.verification_type)
    assert vtype is not None
    exp = expected_map(spec.expected)
    start = float(clock())
    deadline = start + (int(spec.timeout_ms) / 1000.0)
    attempts = int(spec.max_attempts)
    last = (VerificationStatus.NOT_VERIFIED, "NO_ATTEMPT", "", _evidence("none"))

    for _ in range(attempts):
        now = float(clock())
        if now >= deadline:
            return _result(
                request, VerificationStatus.VERIFICATION_TIMEOUT, after=last[2],
                failure="VERIFICATION_TIMEOUT", duration=int((now - start) * 1000),
                evidence=last[3], tel=tel,
            )
        if stop_fn(owner, request.computer_session_id):
            return _result(request, VerificationStatus.EMERGENCY_STOP_ACTIVE, after=last[2], evidence=last[3], tel=tel)
        if spec.capability == "filesystem":
            last = _eval_fs(vtype, exp, fs_fn)
        elif spec.capability == "browser":
            last = _eval_browser(vtype, exp, bfn, owner, exp.get("session_id", ""))
        else:
            last = _eval_computer(vtype, exp, computer_fn, owner)
        if last[0] == VerificationStatus.VERIFIED:
            return _result(
                request, VerificationStatus.VERIFIED, after=last[2],
                duration=int((float(clock()) - start) * 1000), evidence=last[3], tel=tel,
            )
        if last[0] not in (VerificationStatus.NOT_VERIFIED,):
            return _result(
                request, last[0], after=last[2], failure=last[1],
                duration=int((float(clock()) - start) * 1000), evidence=last[3], tel=tel,
            )
    return _result(
        request, last[0], after=last[2], failure=last[1],
        duration=int((float(clock()) - start) * 1000), evidence=last[3], tel=tel,
    )
