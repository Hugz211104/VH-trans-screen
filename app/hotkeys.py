from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_SPECIAL_KEYS = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "home": 0x24,
    "end": 0x23, "insert": 0x2D, "delete": 0x2E, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "`": 0xC0,
}


def parse_hotkey(spec: str) -> Optional[tuple[int, int]]:
    """Parse '<alt>+t' / 'ctrl+shift+f2' into (modifiers, virtual-key)."""
    mods = MOD_NOREPEAT
    vk = 0
    for raw in spec.lower().replace(" ", "").split("+"):
        part = raw.strip("<>")
        if not part:
            continue
        if part == "alt":
            mods |= MOD_ALT
        elif part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in ("win", "cmd", "super"):
            mods |= MOD_WIN
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x70 + int(part[1:]) - 1
        elif part in _SPECIAL_KEYS:
            vk = _SPECIAL_KEYS[part]
        else:
            return None
    return (mods, vk) if vk else None


class GlobalHotkeyManager(QObject):
    """Native Win32 RegisterHotKey listener (zero in-game keystroke hook latency)."""

    triggered = pyqtSignal(str)

    def __init__(self, bindings: dict[str, str]) -> None:
        super().__init__()
        self._bindings = bindings
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._ready = threading.Event()
        self.failed: list[str] = []

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="HotkeyThread", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)

    def _run(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())
        id_map: dict[int, str] = {}
        for idx, (name, spec) in enumerate(self._bindings.items(), start=1):
            parsed = parse_hotkey(spec)
            if parsed and user32.RegisterHotKey(None, idx, parsed[0], parsed[1]):
                id_map[idx] = name
            else:
                self.failed.append(spec)
        self._ready.set()

        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and int(msg.wParam) in id_map:
                    self.triggered.emit(id_map[int(msg.wParam)])
        finally:
            for idx in id_map:
                user32.UnregisterHotKey(None, idx)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive() and self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(1.0)
        self._thread = None
