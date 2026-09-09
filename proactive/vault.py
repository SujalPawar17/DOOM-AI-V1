"""Local DPAPI vault for connector secrets. PostgreSQL stores secret_ref only.

Windows: CryptProtectData / CryptUnprotectData on a JSON blob.
Non-Windows: refuse (no plaintext vault in production).
Never log token values.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Dict, Optional

from proactive.config import connector_vault_path

_ALLOWED_TYPES = frozenset({"github_pat", "oauth_google"})
_ALLOWED_KEYS = frozenset({
    "type", "token", "refresh", "expiry", "client_id", "client_secret", "calendar_id",
})


class VaultError(Exception):
    pass


def _dpapi_protect(plain: bytes) -> bytes:
    if sys.platform != "win32":
        raise VaultError("DPAPI vault requires Windows")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(plain, len(plain))
    blob_in = DATA_BLOB(len(plain), buf)
    blob_out = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)
    )
    if not ok:
        raise VaultError("CryptProtectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(cipher: bytes) -> bytes:
    if sys.platform != "win32":
        raise VaultError("DPAPI vault requires Windows")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(cipher, len(cipher))
    blob_in = DATA_BLOB(len(cipher), buf)
    blob_out = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)
    )
    if not ok:
        raise VaultError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _sanitize_entry(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if k not in _ALLOWED_KEYS:
            continue
        if k == "type":
            t = str(v or "")
            if t not in _ALLOWED_TYPES:
                raise VaultError("unsupported secret type")
            out["type"] = t
        elif k == "expiry":
            try:
                out["expiry"] = float(v)
            except (TypeError, ValueError):
                continue
        else:
            s = str(v or "")
            if s:
                out[k] = s
    if "type" not in out or "token" not in out:
        raise VaultError("secret requires type and token")
    return out


def _load_map(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    with open(path, "rb") as fh:
        cipher = fh.read()
    if not cipher:
        return {}
    plain = _dpapi_unprotect(cipher)
    data = json.loads(plain.decode("utf-8"))
    if not isinstance(data, dict):
        raise VaultError("corrupt vault")
    return data


def _save_map(path: str, data: Dict[str, Any]) -> None:
    blob = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    cipher = _dpapi_protect(blob)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="doom_vault_", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(cipher)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def put_secret(secret_ref: str, entry: Dict[str, Any], path: Optional[str] = None) -> None:
    ref = str(secret_ref or "").strip()[:64]
    if not ref:
        raise VaultError("secret_ref required")
    vault = path or connector_vault_path()
    data = _load_map(vault) if os.path.isfile(vault) else {}
    data[ref] = _sanitize_entry(entry)
    _save_map(vault, data)


def get_secret(secret_ref: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    ref = str(secret_ref or "").strip()[:64]
    if not ref:
        return None
    vault = path or connector_vault_path()
    try:
        data = _load_map(vault)
    except VaultError:
        return None
    raw = data.get(ref)
    if not isinstance(raw, dict):
        return None
    try:
        return _sanitize_entry(raw)
    except VaultError:
        return None


def delete_secret(secret_ref: str, path: Optional[str] = None) -> bool:
    ref = str(secret_ref or "").strip()[:64]
    if not ref:
        return False
    vault = path or connector_vault_path()
    if not os.path.isfile(vault):
        return False
    data = _load_map(vault)
    if ref not in data:
        return False
    del data[ref]
    _save_map(vault, data)
    return True


def import_github_pat_from_env(secret_ref: str, path: Optional[str] = None) -> bool:
    """One-shot bootstrap: copy DOOM_GITHUB_PAT into the vault if missing. Never logs the PAT."""
    if get_secret(secret_ref, path=path):
        return True
    pat = (os.getenv("DOOM_GITHUB_PAT") or "").strip()
    if not pat:
        return False
    put_secret(secret_ref, {"type": "github_pat", "token": pat}, path=path)
    return True
