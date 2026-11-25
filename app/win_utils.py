from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Optional

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

WDA_EXCLUDEFROMCAPTURE = 0x00000011
DWMWA_CLOAKED = 14
SW_RESTORE = 9
ERROR_ALREADY_EXISTS = 183


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


# Explicit 64-bit safe Win32 signatures (prevents 32-bit int overflow on high HWNDs)
GetWindowLongPtrW = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
SetWindowLongPtrW = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
GetWindowLongPtrW.restype = ctypes.c_ssize_t
SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
SetWindowLongPtrW.restype = ctypes.c_ssize_t

user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL

dwmapi.DwmGetWindowAttribute.argtypes = [
    wintypes.HWND,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
]
dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def acquire_single_instance(
    mutex_name: str = "Local\\VHGameTranslator_SingleInstance_v2",
    window_title: str = "VH Translator",
) -> Optional[int]:
    """Acquire named Win32 mutex safely via use_last_error; focus existing window if already open."""
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, mutex_name)
    err = ctypes.get_last_error()
    if not handle:
        return 1
    if err == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        hwnd = user32.FindWindowW(None, window_title)
        if hwnd:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
        return None
    return int(handle)


def _is_window_cloaked(hwnd: int) -> bool:
    cloaked = ctypes.c_int(0)
    res = dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd),
        wintypes.DWORD(DWMWA_CLOAKED),
        ctypes.byref(cloaked),
        ctypes.sizeof(cloaked),
    )
    return res == 0 and cloaked.value != 0


def list_visible_windows(exclude_hwnds: Optional[set[int]] = None) -> list[tuple[int, str]]:
    """Return list of (hwnd, title) for visible top-level application windows."""
    excluded = exclude_hwnds or set()
    results: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        if not hwnd or int(hwnd) in excluded:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        if _is_window_cloaked(int(hwnd)):
            return True
        ex_style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if ex_style & WS_EX_TOOLWINDOW:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        rect = RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            if (rect.right - rect.left) < 50 or (rect.bottom - rect.top) < 50:
                return True
        results.append((int(hwnd), title))
        return True

    user32.EnumWindows(enum_proc, 0)
    results.sort(key=lambda item: item[1].lower())
    return results


def find_window_by_title(title: str) -> Optional[int]:
    if not title:
        return None
    windows = list_visible_windows()
    for hwnd, wtitle in windows:
        if wtitle == title:
            return hwnd
    lower = title.lower()
    for hwnd, wtitle in windows:
        if lower in wtitle.lower():
            return hwnd
    return None


def is_window_valid(hwnd: int) -> bool:
    if not hwnd:
        return False
    h = wintypes.HWND(hwnd)
    return bool(user32.IsWindow(h) and user32.IsWindowVisible(h))


def is_window_minimized(hwnd: int) -> bool:
    return bool(hwnd and user32.IsIconic(wintypes.HWND(hwnd)))


def get_foreground_window() -> int:
    fg = user32.GetForegroundWindow()
    return int(fg) if fg else 0


def get_window_client_rect_screen(hwnd: int) -> Optional[tuple[int, int, int, int]]:
    """Return physical screen (x, y, width, height) of the window's client area."""
    if not is_window_valid(hwnd) or is_window_minimized(hwnd):
        return None
    h = wintypes.HWND(hwnd)
    rect = RECT()
    if not user32.GetClientRect(h, ctypes.byref(rect)):
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    pt = POINT(rect.left, rect.top)
    if not user32.ClientToScreen(h, ctypes.byref(pt)):
        return None
    return (int(pt.x), int(pt.y), int(width), int(height))


def make_overlay_clickthrough(hwnd: int) -> None:
    """Make a window click-through, non-activating, and hidden from Alt+Tab."""
    if not hwnd:
        return
    h = wintypes.HWND(hwnd)
    ex_style = GetWindowLongPtrW(h, GWL_EXSTYLE)
    ex_style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    SetWindowLongPtrW(h, GWL_EXSTYLE, ex_style)


def exclude_from_capture(hwnd: int) -> bool:
    """Exclude window from screen capture (Windows 10 2004+), preventing OCR feedback loop."""
    if not hwnd:
        return False
    return bool(user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), WDA_EXCLUDEFROMCAPTURE))
