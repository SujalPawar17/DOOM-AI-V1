"""Read-only Win32 foreground identity. Title is advisory. No mutation APIs."""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Tuple

INVALID_IDENTITY = "INVALID_IDENTITY"
WINDOW_GONE = "WINDOW_GONE"
ACCESS_DENIED = "ACCESS_DENIED"
OK = "OK"

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MONITOR_DEFAULTTONEAREST = 2


@dataclass
class Win32Identity:
    hwnd: int = 0
    pid: int = 0
    exe_path_norm: str = ""
    publisher_norm: str = ""
    window_class: str = ""
    title_advisory: str = ""
    rect: Tuple[int, int, int, int] = (0, 0, 0, 0)
    monitor_id: int = 0
    outcome: str = WINDOW_GONE


def _user32():
    return ctypes.WinDLL("user32", use_last_error=True)


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _norm_exe(path: str) -> str:
    if not path:
        return ""
    cleaned = path.replace("/", "\\")
    try:
        cleaned = os.path.normpath(cleaned)
        cleaned = os.path.normcase(cleaned)
    except Exception:
        cleaned = path
    return cleaned[:512]


def _publisher_norm(_exe_path: str) -> str:
    """Authenticode publisher. Empty when lookup is not bounded/reliable."""
    return ""


def _title(hwnd: int, max_title: int) -> str:
    user32 = _user32()
    n = int(user32.GetWindowTextLengthW(hwnd) or 0)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(min(n + 1, max_title + 1))
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return (buf.value or "")[:max_title]


def _class_name(hwnd: int) -> str:
    user32 = _user32()
    buf = ctypes.create_unicode_buffer(257)
    n = int(user32.GetClassNameW(hwnd, buf, 257) or 0)
    if n <= 0:
        return ""
    return (buf.value or "")[:64]


def _rect(hwnd: int) -> Tuple[int, int, int, int]:
    user32 = _user32()
    rc = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rc)):
        return (0, 0, 0, 0)
    return (int(rc.left), int(rc.top), int(rc.right), int(rc.bottom))


def _monitor_index(hwnd: int) -> int:
    user32 = _user32()
    hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not hmon:
        return 0
    found = {"n": 0, "idx": 0}

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def _cb(hmonitor, _hdc, _lprect, _lparam):
        found["n"] += 1
        if int(hmonitor) == int(hmon):
            found["idx"] = found["n"]
        return True

    cb = MONITORENUMPROC(_cb)
    user32.EnumDisplayMonitors(0, 0, cb, 0)
    return int(found["idx"] or 0)


def _query_exe(pid: int) -> str:
    if pid <= 0:
        return ""
    k32 = _kernel32()
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        ok = k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        if not ok:
            return ""
        path = buf.value or ""
        try:
            long_buf = ctypes.create_unicode_buffer(32768)
            n = k32.GetLongPathNameW(path, long_buf, 32768)
            if n:
                path = long_buf.value or path
        except Exception:
            pass
        return _norm_exe(path)
    finally:
        k32.CloseHandle(handle)


def read_hwnd_win32(hwnd: int, *, max_title: int = 80) -> Win32Identity:
    """Inspect a specific HWND. Does not call GetForegroundWindow()."""
    ident = Win32Identity()
    if sys.platform != "win32":
        ident.outcome = WINDOW_GONE
        return ident
    handle = int(hwnd or 0)
    if handle <= 0:
        ident.outcome = WINDOW_GONE
        return ident
    user32 = _user32()
    try:
        if not bool(user32.IsWindow(handle)):
            ident.outcome = WINDOW_GONE
            return ident
    except Exception:
        ident.outcome = WINDOW_GONE
        return ident
    ident.hwnd = handle
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    ident.pid = int(pid.value or 0)
    ident.window_class = _class_name(handle)
    ident.title_advisory = _title(handle, max_title)
    ident.rect = _rect(handle)
    ident.monitor_id = _monitor_index(handle)
    if ident.pid <= 0:
        ident.outcome = INVALID_IDENTITY
        return ident
    ident.exe_path_norm = _query_exe(ident.pid)
    if not ident.exe_path_norm:
        ident.outcome = INVALID_IDENTITY
        return ident
    ident.publisher_norm = _publisher_norm(ident.exe_path_norm)
    ident.outcome = OK
    return ident


def read_foreground_win32(*, max_title: int = 80) -> Win32Identity:
    ident = Win32Identity()
    if sys.platform != "win32":
        ident.outcome = WINDOW_GONE
        return ident
    user32 = _user32()
    hwnd = int(user32.GetForegroundWindow() or 0)
    if hwnd == 0:
        ident.outcome = WINDOW_GONE
        return ident
    return read_hwnd_win32(hwnd, max_title=max_title)


def enumerate_visible_windows(*, max_title: int = 80, limit: int = 40) -> list:
    """Top-level visible windows with titles. No mutation. No secrets."""
    out: list = []
    if sys.platform != "win32":
        return out
    user32 = _user32()
    cap = min(max(int(limit or 40), 1), 40)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if len(out) >= cap:
            return False
        try:
            if not bool(user32.IsWindow(hwnd)) or not bool(user32.IsWindowVisible(hwnd)):
                return True
        except Exception:
            return True
        ident = read_hwnd_win32(int(hwnd), max_title=max_title)
        if ident.outcome != OK:
            return True
        if not str(ident.title_advisory or "").strip():
            return True
        out.append(ident)
        return True

    cb = WNDENUMPROC(_cb)
    try:
        user32.EnumWindows(cb, 0)
    except Exception:
        return out
    return out


def exe_basename(exe_path_norm: str) -> str:
    if not exe_path_norm:
        return ""
    return os.path.basename(exe_path_norm.replace("/", "\\"))[:80]
