"""Canonical path allow/deny. Denied roots take precedence. Fail closed."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

from proactive.computer.fs.types import ObjectType, PathIdentity, Status

SENSITIVE_NAMES = frozenset({
    ".env", ".netrc", "id_rsa", "id_ed25519", "id_dsa",
    "credentials.json", "secrets.json", "masterkey",
})


def split_roots(raw: str) -> List[str]:
    parts = []
    for item in (raw or "").replace(";", os.pathsep).split(os.pathsep):
        item = item.strip().strip('"')
        if item:
            parts.append(item)
    return parts


def allowed_roots() -> List[Path]:
    return _canon_roots(os.getenv("PROACTIVE_COMPUTER_FS_ALLOWED_ROOTS", "") or "")


def denied_roots() -> List[Path]:
    return _canon_roots(os.getenv("PROACTIVE_COMPUTER_FS_DENIED_ROOTS", "") or "")


def _canon_roots(raw: str) -> List[Path]:
    out: List[Path] = []
    for item in split_roots(raw):
        try:
            out.append(Path(item).resolve())
        except Exception:
            continue
    return out


def _under(child: Path, root: Path) -> bool:
    try:
        child_n = os.path.normcase(str(child))
        root_n = os.path.normcase(str(root))
        return child_n == root_n or child_n.startswith(root_n + os.sep)
    except Exception:
        return False


def is_sensitive_name(path: Path) -> bool:
    name = path.name.lower()
    if name in {n.lower() for n in SENSITIVE_NAMES}:
        return True
    if name.endswith(".pem") or name.endswith(".key"):
        return True
    return False


def canonicalize(raw: str) -> Tuple[Optional[Path], Status]:
    text = (raw or "").strip()
    if not text or "\x00" in text:
        return None, Status.PATH_INVALID
    if text.startswith("\\\\.\\") or text.lower().startswith("file:"):
        return None, Status.PATH_INVALID
    if text.startswith("\\\\") or text.startswith("//"):
        return None, Status.PATH_INVALID
    if "\\\\?\\" in text or "\\\\.\\" in text:
        return None, Status.PATH_INVALID
    try:
        requested = Path(text)
        if not requested.is_absolute():
            return None, Status.PATH_INVALID
        resolved = requested.resolve()
    except Exception:
        return None, Status.PATH_INVALID
    try:
        if requested.exists() or resolved.exists():
            if resolved.is_symlink() or requested.is_symlink():
                pass
            real = Path(os.path.realpath(str(resolved)))
            if os.path.normcase(str(real)) != os.path.normcase(str(resolved)):
                resolved = real
    except OSError:
        return None, Status.PATH_ESCAPE_BLOCKED
    return resolved, Status.SUCCESS


def scope_status(resolved: Path) -> Status:
    allowed = allowed_roots()
    if not allowed:
        return Status.PATH_NOT_ALLOWED
    denied = denied_roots()
    for root in denied:
        if _under(resolved, root):
            return Status.PATH_DENIED
    if any(_under(resolved, root) for root in allowed):
        return Status.SUCCESS
    return Status.PATH_NOT_ALLOWED


def identity_of(resolved: Path) -> PathIdentity:
    exists = resolved.exists()
    otype = ObjectType.MISSING
    size = 0
    mtime = 0
    index = ""
    if exists:
        try:
            if resolved.is_dir() and not resolved.is_symlink():
                otype = ObjectType.DIRECTORY
            elif resolved.is_file():
                otype = ObjectType.FILE
            else:
                otype = ObjectType.OTHER
        except OSError:
            otype = ObjectType.OTHER
        try:
            st = resolved.stat()
            size = int(st.st_size)
            mtime = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
            index = str(getattr(st, "st_ino", 0))
        except OSError:
            pass
    return PathIdentity(
        canonical_path=str(resolved),
        object_type=otype,
        exists=exists,
        size_bytes=size,
        mtime_ns=mtime,
        file_index=index,
    )


def escape_if_unproven(raw: str, resolved: Path) -> Optional[Status]:
    try:
        real = Path(os.path.realpath(str(resolved)))
    except OSError:
        return Status.PATH_ESCAPE_BLOCKED
    if scope_status(real) != Status.SUCCESS:
        return Status.PATH_ESCAPE_BLOCKED
    return None
