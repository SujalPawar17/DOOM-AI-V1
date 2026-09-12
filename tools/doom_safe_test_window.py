"""Harmless local Windows UI for V8 computer-path validation. No network, no files."""

from __future__ import annotations

import argparse
import ctypes
import sys
from ctypes import wintypes

WINDOW_TITLE = "DOOM Safe Test Window"
BUTTON_NAME = "DOOM_TEST_BUTTON"
INPUT_NAME = "DOOM_TEST_INPUT"
STATUS_READY = "READY"
STATUS_CLICKED = "CLICKED"
CLASS_NAME = "DoomSafeTestWindow"

WM_COMMAND = 0x0111
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
BN_CLICKED = 0
IDC_TEST_BUTTON = 1001
IDC_TEST_BUTTON_DUP = 1002

WS_OVERLAPPED = 0x00000000
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_VISIBLE = 0x10000000
WS_CHILD = 0x40000000
WS_TABSTOP = 0x00010000
WS_CLIPCHILDREN = 0x02000000
WS_EX_CLIENTEDGE = 0x00000200
WS_EX_CONTROLPARENT = 0x00010000
ES_LEFT = 0x0000
ES_AUTOHSCROLL = 0x0080
COLOR_WINDOW = 5
SW_SHOW = 5
IDC_EDIT = 2004
OBJID_CLIENT = 0xFFFFFFFC
CHILDID_SELF = 0
CLSCTX_INPROC_SERVER = 0x1
COINIT_APARTMENTTHREADED = 0x2
S_OK = 0
S_FALSE = 1
RPC_E_CHANGED_MODE = -2147417850  # 0x80010106

# uiautomationcoreapi.h / uiautomation.h (Microsoft Name_Property_GUID)
NAME_PROPERTY_GUID = (0xC3A6921B, 0x4A99, 0x44F1, (0xBC, 0xA6, 0x61, 0x18, 0x70, 0x52, 0xC4, 0x31))
# oleacc.h PROPID_ACC_NAME (MSAA Name; SetHwndPropStr Name annotation)
PROPID_ACC_NAME = (0x608D3DF8, 0x8128, 0x4AA7, (0xA4, 0x28, 0xF5, 0x5E, 0x49, 0x26, 0x72, 0x91))
CLSID_ACC_PROP_SERVICES = (0xB5F8350B, 0x0548, 0x48B1, (0xA6, 0xEE, 0x88, 0xBD, 0x00, 0xB4, 0xA5, 0xE7))
IID_IACC_PROP_SERVICES = (0x6E26E776, 0x04F0, 0x495D, (0x80, 0xE4, 0x33, 0x30, 0x35, 0x2E, 0x31, 0x69))

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HANDLE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


_status_hwnd = 0
_edit_hwnd = 0
_main_hwnd = 0
_wndproc_ref = None
_acc_services = None
_ole_initialized = False


def _user32():
    return ctypes.WinDLL("user32", use_last_error=True)


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _co_initialize() -> None:
    global _ole_initialized
    # WinDLL: CoInitializeEx may return S_FALSE / RPC_E_CHANGED_MODE (not OleDLL-fail).
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx.argtypes = [wintypes.LPVOID, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    hr = int(ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED))
    if hr not in (S_OK, S_FALSE, RPC_E_CHANGED_MODE):
        raise OSError("CoInitializeEx failed: 0x%08X" % (hr & 0xFFFFFFFF))
    _ole_initialized = hr in (S_OK, S_FALSE)


def _co_uninitialize() -> None:
    global _ole_initialized
    if not _ole_initialized:
        return
    ctypes.WinDLL("ole32").CoUninitialize()
    _ole_initialized = False


def _oleacc_gen_dir():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent / ".doom_runtime" / "comtypes_gen"


def _prepare_oleacc_wrappers() -> None:
    """Load oleacc IAccPropServices via comtypes (same gen-dir convention as UIA)."""
    import comtypes.client
    import comtypes.gen as gen_pkg

    path = _oleacc_gen_dir()
    path.mkdir(parents=True, exist_ok=True)
    init_py = path / "__init__.py"
    if not init_py.exists():
        init_py.write_text("# generated comtypes wrappers (runtime cache)\n", encoding="utf-8")
    target = str(path)
    comtypes.client.gen_dir = target
    if target not in list(gen_pkg.__path__):
        gen_pkg.__path__.insert(0, target)
    comtypes.client.GetModule("oleacc.dll")


def _guid_from_parts(parts):
    from comtypes import GUID

    data4 = "".join("%02X" % int(b) for b in parts[3])
    return GUID("{%08X-%04X-%04X-%s-%s}" % (
        int(parts[0]), int(parts[1]), int(parts[2]), data4[:4], data4[4:],
    ))


def _create_acc_prop_services():
    import comtypes.client

    _prepare_oleacc_wrappers()
    from comtypes.gen.Accessibility import CAccPropServices, IAccPropServices

    return comtypes.client.CreateObject(CAccPropServices, interface=IAccPropServices)


def pin_edit_accessible_name(hwnd: int, name: str = INPUT_NAME) -> None:
    """Pin UIA/MSAA Name on an EDIT HWND. Does not set Edit value/text."""
    global _acc_services
    if int(hwnd or 0) <= 0:
        raise OSError("invalid EDIT hwnd")
    if _acc_services is None:
        _acc_services = _create_acc_prop_services()
    for parts in (NAME_PROPERTY_GUID, PROPID_ACC_NAME):
        _acc_services.SetHwndPropStr(
            int(hwnd), OBJID_CLIENT, CHILDID_SELF, _guid_from_parts(parts), str(name),
        )


def clear_edit_accessible_name(hwnd: int = 0) -> None:
    global _acc_services, _edit_hwnd
    target = int(hwnd or _edit_hwnd or 0)
    svc = _acc_services
    if svc is not None and target > 0:
        try:
            g1 = _guid_from_parts(NAME_PROPERTY_GUID)
            g2 = _guid_from_parts(PROPID_ACC_NAME)
            arr = (type(g1) * 2)(g1, g2)
            svc.ClearHwndProps(int(target), OBJID_CLIENT, CHILDID_SELF, arr, 2)
        except Exception:
            pass
    _acc_services = None


def _bind_apis(user32, kernel32):
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.UpdateWindow.argtypes = [wintypes.HWND]
    user32.UpdateWindow.restype = wintypes.BOOL
    user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.SetWindowTextW.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.PostQuitMessage.restype = None
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE


def _make_wndproc(user32):
    @WNDPROC
    def wndproc(hwnd, msg, wparam, lparam):
        global _status_hwnd
        if msg == WM_COMMAND:
            ctrl_id = int(wparam) & 0xFFFF
            notify = (int(wparam) >> 16) & 0xFFFF
            if notify == BN_CLICKED and ctrl_id in (IDC_TEST_BUTTON, IDC_TEST_BUTTON_DUP):
                if _status_hwnd:
                    user32.SetWindowTextW(_status_hwnd, STATUS_CLICKED)
                return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            clear_edit_accessible_name()
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    return wndproc


def run_window(*, duplicate_buttons: bool = False, title: str = WINDOW_TITLE, ready_event=None) -> None:
    global _status_hwnd, _wndproc_ref, _edit_hwnd, _main_hwnd
    if sys.platform != "win32":
        print("DOOM Safe Test Window requires Windows (native Win32 BUTTON/EDIT).", file=sys.stderr)
        raise SystemExit(1)

    _co_initialize()
    user32 = _user32()
    kernel32 = _kernel32()
    _bind_apis(user32, kernel32)

    hinst = kernel32.GetModuleHandleW(None)
    wndproc = _make_wndproc(user32)
    _wndproc_ref = wndproc

    wc = WNDCLASSW()
    wc.lpfnWndProc = wndproc
    wc.hInstance = hinst
    wc.hbrBackground = ctypes.c_void_p(COLOR_WINDOW + 1)
    wc.lpszClassName = CLASS_NAME
    atom = user32.RegisterClassW(ctypes.byref(wc))
    if not atom:
        err = ctypes.get_last_error()
        if err not in (1410,):  # class already exists
            print("RegisterClassW failed: %s" % err, file=sys.stderr)
            raise SystemExit(1)

    style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX | WS_CLIPCHILDREN
    hwnd = user32.CreateWindowExW(
        WS_EX_CONTROLPARENT,
        CLASS_NAME,
        str(title or WINDOW_TITLE),
        style,
        120,
        120,
        420,
        240,
        None,
        None,
        hinst,
        None,
    )
    if not hwnd:
        print("CreateWindowExW failed: %s" % ctypes.get_last_error(), file=sys.stderr)
        raise SystemExit(1)

    def child(ex, cls, text, st, x, y, w, h, ctrl_id):
        hctl = user32.CreateWindowExW(
            ex, cls, text, st, x, y, w, h, hwnd, wintypes.HMENU(ctrl_id), hinst, None,
        )
        if not hctl:
            print("CreateWindowExW child %s failed: %s" % (cls, ctypes.get_last_error()), file=sys.stderr)
            raise SystemExit(1)
        return hctl

    static_style = WS_CHILD | WS_VISIBLE
    btn_style = WS_CHILD | WS_VISIBLE | WS_TABSTOP
    edit_style = WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_LEFT | ES_AUTOHSCROLL

    child(0, "STATIC", WINDOW_TITLE, static_style, 16, 16, 380, 20, 2001)
    _status_hwnd = child(0, "STATIC", STATUS_READY, static_style, 16, 42, 380, 20, 2002)
    child(0, "BUTTON", BUTTON_NAME, btn_style, 16, 72, 260, 28, IDC_TEST_BUTTON)
    y = 108
    if duplicate_buttons:
        child(0, "BUTTON", BUTTON_NAME, btn_style, 16, 108, 260, 28, IDC_TEST_BUTTON_DUP)
        y = 144
    child(0, "STATIC", INPUT_NAME, static_style, 16, y, 380, 18, 2003)
    _edit_hwnd = child(WS_EX_CLIENTEDGE, "EDIT", "", edit_style, 16, y + 22, 260, 24, IDC_EDIT)
    try:
        pin_edit_accessible_name(int(_edit_hwnd), INPUT_NAME)
    except Exception:
        user32.DestroyWindow(hwnd)
        user32.UnregisterClassW(CLASS_NAME, hinst)
        _status_hwnd = 0
        _edit_hwnd = 0
        _main_hwnd = 0
        _co_uninitialize()
        raise
    _main_hwnd = int(hwnd)

    user32.ShowWindow(hwnd, SW_SHOW)
    user32.UpdateWindow(hwnd)
    if ready_event is not None:
        ready_event.set()

    msg = MSG()
    while True:
        ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if ret == 0 or ret == -1:
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    user32.UnregisterClassW(CLASS_NAME, hinst)
    _status_hwnd = 0
    _edit_hwnd = 0
    _main_hwnd = 0
    _co_uninitialize()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harmless DOOM computer-path test window")
    parser.add_argument(
        "--duplicate-buttons",
        action="store_true",
        help="Two identical button names for AMBIGUOUS_TARGET",
    )
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("DOOM Safe Test Window requires Windows (native Win32 BUTTON/EDIT).", file=sys.stderr)
        return 1
    run_window(duplicate_buttons=bool(args.duplicate_buttons))
    return 0


if __name__ == "__main__":
    sys.exit(main())
