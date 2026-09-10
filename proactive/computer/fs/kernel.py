"""V7.4 bounded filesystem kernel. Local pathlib/os only. No shell."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.cost_guard import ResourceRequest, ResourceType, cost_guard
from proactive.computer.actions.types import ApprovalState
from proactive.computer.fs.observe import observe_path
from proactive.computer.fs.paths import (
    canonicalize,
    escape_if_unproven,
    identity_of,
    is_sensitive_name,
    scope_status,
)
from proactive.computer.fs.types import (
    FsActionRequest,
    FsActionResult,
    FsActionType,
    ObjectType,
    Status,
)
from proactive.computer.policy import (
    CAPABILITY_FILESYSTEM,
    FS_SCHEMA_VERSION,
    RISK_FS_DELETE,
    RISK_FS_MUTATE,
    RISK_FS_READ,
    filesystem_allowed,
    fs_max_read_bytes,
    fs_max_write_bytes,
)
from proactive.config import OWNER_ID
from proactive.store import proactive_store


_MUTATIONS = frozenset({
    FsActionType.CREATE_DIRECTORY,
    FsActionType.WRITE_FILE,
    FsActionType.COPY_FILE,
    FsActionType.MOVE_FILE,
    FsActionType.DELETE_FILE,
})


def _cost_ok() -> bool:
    d = cost_guard.authorize(ResourceRequest(
        resource_type=ResourceType.OTHER,
        provider="local_filesystem",
        capability="filesystem",
    ))
    return bool(d.is_allow)


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


def _risk(action: FsActionType) -> str:
    if action == FsActionType.DELETE_FILE:
        return RISK_FS_DELETE
    if action in _MUTATIONS:
        return RISK_FS_MUTATE
    return RISK_FS_READ


def _approval(request: FsActionRequest) -> Optional[Status]:
    risk = _risk(request.action_type)
    if risk in ("MEDIUM", "HIGH", "CRITICAL"):
        if request.approval_state == ApprovalState.DENIED:
            return Status.APPROVAL_DENIED
        if request.approval_state != ApprovalState.APPROVED:
            return Status.APPROVAL_REQUIRED
    return None


def _result(request: FsActionRequest, status: Status, **kwargs) -> FsActionResult:
    return FsActionResult(
        action_id=str(request.action_id or ""),
        action_type=request.action_type,
        status=status,
        canonical_path=kwargs.get("path", ""),
        dest_canonical_path=kwargs.get("dest", ""),
        precondition_status=kwargs.get("precondition", ""),
        execution_status=kwargs.get("execution", ""),
        error_code=kwargs.get("error") or (status.value if status != Status.SUCCESS else ""),
        before_observation_hash=kwargs.get("before", ""),
        after_observation_hash=kwargs.get("after", ""),
        identity=kwargs.get("identity") or {},
        listing=kwargs.get("listing") or [],
        content=kwargs.get("content") or b"",
        truncated=bool(kwargs.get("truncated", False)),
        telemetry=kwargs.get("telemetry") or {},
    )


def _resolve_scoped(raw: str) -> Tuple[Optional[Path], Status]:
    resolved, st = canonicalize(raw)
    if st != Status.SUCCESS or resolved is None:
        return None, st
    sc = scope_status(resolved)
    if sc != Status.SUCCESS:
        return resolved, sc
    esc = escape_if_unproven(raw, resolved)
    if esc is not None:
        return resolved, esc
    return resolved, Status.SUCCESS


def execute_fs_action(request: FsActionRequest) -> FsActionResult:
    tel: Dict[str, Any] = {
        "schema_version": FS_SCHEMA_VERSION,
        "capability_id": CAPABILITY_FILESYSTEM,
        "risk_class": _risk(request.action_type),
        "content_logged": False,
    }
    if not filesystem_allowed():
        return _result(request, Status.POLICY_BLOCKED, precondition="not_checked", execution="not_started", telemetry=tel)
    if not _cost_ok():
        return _result(request, Status.POLICY_BLOCKED, precondition="not_checked", execution="not_started", error="COST_GUARD_BLOCKED", telemetry=tel)
    owner = str(request.owner_id or OWNER_ID)[:64]
    mutating = request.action_type in _MUTATIONS
    if mutating and _emergency_stop(owner, request.computer_session_id):
        return _result(request, Status.EMERGENCY_STOP_ACTIVE, precondition="not_checked", execution="not_started", telemetry=tel)
    appr = _approval(request)
    if appr is not None:
        return _result(request, appr, precondition="not_checked", execution="not_started", telemetry=tel)

    src, st = _resolve_scoped(request.path)
    if st != Status.SUCCESS:
        return _result(request, st, precondition="failed", execution="not_started", telemetry=tel)
    assert src is not None

    dest = None
    if request.action_type in (FsActionType.COPY_FILE, FsActionType.MOVE_FILE):
        dest, dst = _resolve_scoped(request.dest_path)
        if dst != Status.SUCCESS:
            return _result(request, dst, precondition="failed", execution="not_started", path=str(src), telemetry=tel)

    if is_sensitive_name(src) and request.action_type in (
        FsActionType.READ_FILE, FsActionType.WRITE_FILE, FsActionType.COPY_FILE,
        FsActionType.MOVE_FILE, FsActionType.DELETE_FILE,
    ):
        return _result(request, Status.SENSITIVE_FILE_BLOCKED, precondition="failed", execution="not_started", path=str(src), telemetry=tel)
    if dest is not None and is_sensitive_name(dest):
        return _result(request, Status.SENSITIVE_FILE_BLOCKED, precondition="failed", execution="not_started", path=str(src), dest=str(dest), telemetry=tel)

    before_obs = observe_path(src)
    before = before_obs.observation_hash
    ident = identity_of(src)

    if mutating:
        expected = str(request.precondition_observation_hash or "")
        if expected and expected != before:
            return _result(
                request, Status.PRECONDITION_FAILED,
                precondition="failed", execution="not_started",
                error="STALE_OBSERVATION_HASH",
                path=str(src), before=before, identity=ident.as_public(), telemetry=tel,
            )
        if request.expected_exists is not None and bool(ident.exists) != bool(request.expected_exists):
            return _result(
                request, Status.PRECONDITION_FAILED,
                precondition="failed", execution="not_started",
                error="EXISTENCE_MISMATCH",
                path=str(src), before=before, identity=ident.as_public(), telemetry=tel,
            )
        if request.expected_type is not None and ident.exists and ident.object_type != request.expected_type:
            return _result(
                request, Status.PRECONDITION_FAILED,
                precondition="failed", execution="not_started",
                error="IDENTITY_MISMATCH",
                path=str(src), before=before, identity=ident.as_public(), telemetry=tel,
            )
        if _emergency_stop(owner, request.computer_session_id):
            return _result(request, Status.EMERGENCY_STOP_ACTIVE, precondition="ok", execution="not_started", path=str(src), before=before, telemetry=tel)

    try:
        if request.action_type == FsActionType.OBSERVE_PATH:
            listing = [{"name": c.name, "object_type": c.object_type, "size_bytes": c.size_bytes} for c in before_obs.children]
            return _result(
                request, Status.SUCCESS, precondition="ok", execution="ok",
                path=str(src), before=before, after=before,
                identity=ident.as_public(), listing=listing, truncated=before_obs.truncated, telemetry=tel,
            )
        if request.action_type == FsActionType.LIST_DIRECTORY:
            if not ident.exists:
                return _result(request, Status.PATH_NOT_FOUND, precondition="failed", execution="not_started", path=str(src), before=before, telemetry=tel)
            if ident.object_type != ObjectType.DIRECTORY:
                return _result(request, Status.PRECONDITION_FAILED, precondition="failed", execution="not_started", error="NOT_DIRECTORY", path=str(src), before=before, telemetry=tel)
            listing = [{"name": c.name, "object_type": c.object_type, "size_bytes": c.size_bytes} for c in before_obs.children]
            return _result(
                request, Status.SUCCESS, precondition="ok", execution="ok",
                path=str(src), before=before, after=before,
                identity=ident.as_public(), listing=listing, truncated=before_obs.truncated, telemetry=tel,
            )
        if request.action_type == FsActionType.READ_FILE:
            if not ident.exists:
                return _result(request, Status.PATH_NOT_FOUND, precondition="failed", execution="not_started", path=str(src), before=before, telemetry=tel)
            if ident.object_type != ObjectType.FILE:
                return _result(request, Status.PRECONDITION_FAILED, precondition="failed", execution="not_started", error="NOT_FILE", path=str(src), before=before, telemetry=tel)
            limit = int(fs_max_read_bytes())
            if ident.size_bytes > limit:
                return _result(request, Status.FILE_TOO_LARGE, precondition="ok", execution="not_started", path=str(src), before=before, identity=ident.as_public(), telemetry=tel)
            data = src.read_bytes()
            if len(data) > limit:
                return _result(request, Status.FILE_TOO_LARGE, precondition="ok", execution="not_started", path=str(src), before=before, telemetry=tel)
            return _result(
                request, Status.SUCCESS, precondition="ok", execution="ok",
                path=str(src), before=before, after=before,
                identity=ident.as_public(), content=data, telemetry=tel,
            )
        if request.action_type == FsActionType.CREATE_DIRECTORY:
            if ident.exists:
                return _result(request, Status.PRECONDITION_FAILED, precondition="failed", execution="not_started", error="ALREADY_EXISTS", path=str(src), before=before, telemetry=tel)
            if not src.parent.exists() or not src.parent.is_dir():
                return _result(request, Status.PATH_NOT_FOUND, precondition="failed", execution="not_started", error="PARENT_MISSING", path=str(src), before=before, telemetry=tel)
            src.mkdir()
        elif request.action_type == FsActionType.WRITE_FILE:
            payload = request.content or b""
            if len(payload) > int(fs_max_write_bytes()):
                return _result(request, Status.FILE_TOO_LARGE, precondition="ok", execution="not_started", path=str(src), before=before, telemetry=tel)
            if ident.exists and ident.object_type == ObjectType.DIRECTORY:
                return _result(request, Status.PRECONDITION_FAILED, precondition="failed", execution="not_started", error="NOT_FILE", path=str(src), before=before, telemetry=tel)
            if not src.parent.exists() or not src.parent.is_dir():
                return _result(request, Status.PATH_NOT_FOUND, precondition="failed", execution="not_started", error="PARENT_MISSING", path=str(src), before=before, telemetry=tel)
            tmp = src.with_name(src.name + ".doomtmp")
            try:
                tmp.write_bytes(payload)
                os.replace(str(tmp), str(src))
            except Exception:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
                return _result(request, Status.EXECUTION_FAILED, precondition="ok", execution="failed", path=str(src), before=before, telemetry=tel)
        elif request.action_type == FsActionType.COPY_FILE:
            assert dest is not None
            if not ident.exists or ident.object_type != ObjectType.FILE:
                return _result(request, Status.PATH_NOT_FOUND if not ident.exists else Status.PRECONDITION_FAILED, precondition="failed", execution="not_started", path=str(src), dest=str(dest), before=before, telemetry=tel)
            if dest.exists():
                return _result(request, Status.PRECONDITION_FAILED, precondition="failed", execution="not_started", error="DEST_EXISTS", path=str(src), dest=str(dest), before=before, telemetry=tel)
            if not dest.parent.exists() or not dest.parent.is_dir():
                return _result(request, Status.PATH_NOT_FOUND, precondition="failed", execution="not_started", error="DEST_PARENT_MISSING", path=str(src), dest=str(dest), before=before, telemetry=tel)
            shutil.copyfile(str(src), str(dest))
        elif request.action_type == FsActionType.MOVE_FILE:
            assert dest is not None
            if not ident.exists or ident.object_type != ObjectType.FILE:
                return _result(request, Status.PATH_NOT_FOUND if not ident.exists else Status.PRECONDITION_FAILED, precondition="failed", execution="not_started", path=str(src), dest=str(dest), before=before, telemetry=tel)
            if dest.exists():
                return _result(request, Status.PRECONDITION_FAILED, precondition="failed", execution="not_started", error="DEST_EXISTS", path=str(src), dest=str(dest), before=before, telemetry=tel)
            if not dest.parent.exists() or not dest.parent.is_dir():
                return _result(request, Status.PATH_NOT_FOUND, precondition="failed", execution="not_started", error="DEST_PARENT_MISSING", path=str(src), dest=str(dest), before=before, telemetry=tel)
            os.replace(str(src), str(dest))
        elif request.action_type == FsActionType.DELETE_FILE:
            if not ident.exists:
                return _result(request, Status.PATH_NOT_FOUND, precondition="failed", execution="not_started", path=str(src), before=before, telemetry=tel)
            if ident.object_type == ObjectType.DIRECTORY:
                return _result(request, Status.DIRECTORY_DELETE_BLOCKED, precondition="ok", execution="not_started", path=str(src), before=before, telemetry=tel)
            if ident.object_type != ObjectType.FILE:
                return _result(request, Status.UNSUPPORTED_ACTION, precondition="ok", execution="not_started", path=str(src), before=before, telemetry=tel)
            src.unlink()
        else:
            return _result(request, Status.UNSUPPORTED_ACTION, precondition="ok", execution="not_started", path=str(src), telemetry=tel)
    except Exception:
        return _result(request, Status.EXECUTION_FAILED, precondition="ok", execution="failed", path=str(src), before=before, telemetry=tel)

    after_target = dest if request.action_type in (FsActionType.COPY_FILE, FsActionType.MOVE_FILE) and dest is not None else src
    after_obs = observe_path(after_target)
    return _result(
        request, Status.SUCCESS, precondition="ok", execution="ok",
        path=str(src), dest=str(dest) if dest is not None else "",
        before=before, after=after_obs.observation_hash,
        identity=identity_of(after_target).as_public(), telemetry=tel,
    )


def execute_fs_action_id() -> str:
    return str(uuid.uuid4())
