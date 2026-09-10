"""System-wide hot keys via Carbon's ``RegisterEventHotKey``.

This is the same mechanism the OS uses for menu shortcuts, so it works without
Accessibility / Input-Monitoring permission. A Cocoa app already pumps the
Carbon event loop, so the handler fires on the main thread and it is safe to
touch AppKit from the callback.
"""

from __future__ import annotations

import ctypes
import ctypes.util

# Carbon HIToolbox modifier bits (not the same as NSEvent's).
CMD = 0x0100
SHIFT = 0x0200
OPTION = 0x0800
CONTROL = 0x1000

_PATH = ctypes.util.find_library("Carbon") or \
    "/System/Library/Frameworks/Carbon.framework/Carbon"
_carbon = ctypes.CDLL(_PATH)

_EVENT_CLASS_KEYBOARD = 0x6B657962   # 'keyb'
_EVENT_HOTKEY_PRESSED = 6


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_void_p)

_carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
_carbon.InstallEventHandler.argtypes = [
    ctypes.c_void_p, _HANDLER, ctypes.c_uint32,
    ctypes.POINTER(_EventTypeSpec), ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
]
_carbon.InstallEventHandler.restype = ctypes.c_int32
_carbon.RegisterEventHotKey.argtypes = [
    ctypes.c_uint32, ctypes.c_uint32, _EventHotKeyID,
    ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p),
]
_carbon.RegisterEventHotKey.restype = ctypes.c_int32
_carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
_carbon.UnregisterEventHotKey.restype = ctypes.c_int32


class GlobalHotKey:
    """Register one system-wide hot key. Keep a reference for its lifetime."""

    _shared_handler_installed = False

    def __init__(self, keycode: int, modifiers: int, callback):
        self._callback = callback
        self.ok = False
        self._ref = ctypes.c_void_p()

        # One Carbon event handler for the whole process.
        self._thunk = _HANDLER(self._dispatch)
        spec = _EventTypeSpec(_EVENT_CLASS_KEYBOARD, _EVENT_HOTKEY_PRESSED)
        handler_ref = ctypes.c_void_p()
        _carbon.InstallEventHandler(
            _carbon.GetApplicationEventTarget(), self._thunk, 1,
            ctypes.byref(spec), None, ctypes.byref(handler_ref),
        )
        self._handler_ref = handler_ref

        hotkey_id = _EventHotKeyID(0x50524459, 1)   # 'PRDY'
        status = _carbon.RegisterEventHotKey(
            keycode, modifiers, hotkey_id,
            _carbon.GetApplicationEventTarget(), 0, ctypes.byref(self._ref),
        )
        self.ok = status == 0

    def _dispatch(self, next_handler, event, user_data):
        try:
            self._callback()
        except Exception as exc:  # noqa: BLE001
            print(f"[hotkey] {exc}")
        return 0  # noErr — swallow the key

    def unregister(self):
        if self._ref:
            _carbon.UnregisterEventHotKey(self._ref)
            self._ref = ctypes.c_void_p()
