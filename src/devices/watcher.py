"""
Surveillance USB temps réel via fenêtre Win32 invisible (WM_DEVICECHANGE).
FIX 2 — Fix ctypes OverflowError (argtypes + restype complets, 64-bit safe).
FIX 3 — Respect de self.paused dans _window_proc.
"""
import ctypes
import ctypes.wintypes
import threading
import time
from typing import Callable


WM_DEVICECHANGE = 0x0219
DBT_DEVICEARRIVAL = 0x8000
DBT_DEVTYP_DEVICEINTERFACE = 5
DEVICE_NOTIFY_WINDOW_HANDLE = 0
GUID_DEVINTERFACE_USB_DEVICE = "{A5DCBF10-6530-11D2-901F-00C04FB951ED}"

# ── Déclaration complète argtypes/restype (64-bit safe) ───────────────────────
# Sans argtypes, ctypes convertit les int Python en c_int (32-bit) → OverflowError
# sur les handles Windows 64-bit (HWND, WPARAM, LPARAM, LRESULT, HDEVNOTIFY…).
_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32

_k32.GetModuleHandleW.restype = ctypes.c_void_p
_k32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]

_u32.RegisterClassExW.restype = ctypes.c_ushort
_u32.RegisterClassExW.argtypes = [ctypes.c_void_p]

_u32.CreateWindowExW.restype = ctypes.c_void_p
_u32.CreateWindowExW.argtypes = [
    ctypes.c_ulong,    # dwExStyle
    ctypes.c_wchar_p,  # lpClassName
    ctypes.c_wchar_p,  # lpWindowName
    ctypes.c_ulong,    # dwStyle
    ctypes.c_int,      # X
    ctypes.c_int,      # Y
    ctypes.c_int,      # nWidth
    ctypes.c_int,      # nHeight
    ctypes.c_void_p,   # hWndParent
    ctypes.c_void_p,   # hMenu
    ctypes.c_void_p,   # hInstance
    ctypes.c_void_p,   # lpParam
]

_u32.PeekMessageW.restype = ctypes.c_bool
_u32.PeekMessageW.argtypes = [
    ctypes.c_void_p,   # lpMsg
    ctypes.c_void_p,   # hWnd
    ctypes.c_uint,     # wMsgFilterMin
    ctypes.c_uint,     # wMsgFilterMax
    ctypes.c_uint,     # wRemoveMsg
]

_u32.TranslateMessage.restype = ctypes.c_bool
_u32.TranslateMessage.argtypes = [ctypes.c_void_p]

_u32.DispatchMessageW.restype = ctypes.c_void_p
_u32.DispatchMessageW.argtypes = [ctypes.c_void_p]

_u32.DefWindowProcW.restype = ctypes.c_void_p
_u32.DefWindowProcW.argtypes = [
    ctypes.c_void_p,   # hWnd (HWND)
    ctypes.c_uint,     # Msg (UINT)
    ctypes.c_void_p,   # wParam (WPARAM = UINT_PTR, 64-bit)
    ctypes.c_void_p,   # lParam (LPARAM = LONG_PTR, 64-bit)
]

_u32.RegisterDeviceNotificationW.restype = ctypes.c_void_p
_u32.RegisterDeviceNotificationW.argtypes = [
    ctypes.c_void_p,   # hRecipient
    ctypes.c_void_p,   # NotificationFilter
    ctypes.c_ulong,    # Flags
]

_u32.UnregisterDeviceNotification.restype = ctypes.c_bool
_u32.UnregisterDeviceNotification.argtypes = [ctypes.c_void_p]

_u32.DestroyWindow.restype = ctypes.c_bool
_u32.DestroyWindow.argtypes = [ctypes.c_void_p]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class DEV_BROADCAST_DEVICEINTERFACE(ctypes.Structure):
    _fields_ = [
        ("dbcc_size", ctypes.c_ulong),
        ("dbcc_devicetype", ctypes.c_ulong),
        ("dbcc_reserved", ctypes.c_ulong),
        ("dbcc_classguid", GUID),
        ("dbcc_name", ctypes.c_wchar * 256),
    ]


class DeviceWatcher:
    """Surveillance USB événementielle via WM_DEVICECHANGE."""

    def __init__(self, callback: Callable[[str], None]):
        self.callback = callback
        self.hwnd = None
        self.running = True
        self.paused = False       # FIX 3 — flag pause
        self.enabled = True
        self.hdev_notify = None

    def start(self):
        """Démarre le watcher dans un thread dédié."""
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        """Boucle de messages Win32."""
        # LRESULT = LONG_PTR = 64-bit sur Windows 64-bit → c_void_p (pas c_long)
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_void_p,   # LRESULT
            ctypes.c_void_p,   # hWnd
            ctypes.c_uint,     # Msg
            ctypes.c_void_p,   # wParam
            ctypes.c_void_p,   # lParam
        )

        class WNDCLASSEX(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("style", ctypes.c_uint),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p),
                ("hIconSm", ctypes.c_void_p),
            ]

        # Garder une référence pour éviter le GC
        self._wndproc = WNDPROC(self._window_proc)

        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.lpfnWndProc = self._wndproc
        wc.lpszClassName = "PrankGuardUSB"

        # argtypes/restype déclarés au niveau module — appels directs 64-bit safe
        wc.hInstance = _k32.GetModuleHandleW(None)

        _u32.RegisterClassExW(ctypes.byref(wc))

        self.hwnd = _u32.CreateWindowExW(
            0, "PrankGuardUSB", "USB", 0, 0, 0, 0, 0,
            None, None, wc.hInstance, None
        )

        if self.hwnd:
            self._register_notification()
            msg = ctypes.wintypes.MSG()
            while self.running:
                if _u32.PeekMessageW(ctypes.byref(msg), self.hwnd, 0, 0, 1):
                    _u32.TranslateMessage(ctypes.byref(msg))
                    _u32.DispatchMessageW(ctypes.byref(msg))
                time.sleep(0.01)

    def _register_notification(self):
        """Enregistre la notification USB via RegisterDeviceNotificationW."""
        guid_str = GUID_DEVINTERFACE_USB_DEVICE.strip("{}")
        parts = guid_str.split("-")
        guid = GUID()
        guid.Data1 = int(parts[0], 16)
        guid.Data2 = int(parts[1], 16)
        guid.Data3 = int(parts[2], 16)
        guid.Data4[0] = int(parts[3][0:2], 16)
        guid.Data4[1] = int(parts[3][2:4], 16)
        for i in range(6):
            guid.Data4[2 + i] = int(parts[4][i * 2 : i * 2 + 2], 16)

        dbdi = DEV_BROADCAST_DEVICEINTERFACE()
        dbdi.dbcc_size = ctypes.sizeof(DEV_BROADCAST_DEVICEINTERFACE)
        dbdi.dbcc_devicetype = DBT_DEVTYP_DEVICEINTERFACE
        dbdi.dbcc_classguid = guid

        self.hdev_notify = _u32.RegisterDeviceNotificationW(
            self.hwnd, ctypes.byref(dbdi), DEVICE_NOTIFY_WINDOW_HANDLE
        )

    def _window_proc(self, hwnd, msg, wparam, lparam):
        """Callback Win32 — appelé quand un device USB est connecté/déconnecté."""
        # FIX 3 — Ne pas réagir si en pause
        if msg == WM_DEVICECHANGE and wparam == DBT_DEVICEARRIVAL:
            if self.enabled and not self.paused:
                self.callback("USB")
        return _u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def stop(self):
        """Arrête le watcher proprement."""
        self.running = False
        if self.hdev_notify:
            _u32.UnregisterDeviceNotification(self.hdev_notify)
            self.hdev_notify = None
        if self.hwnd:
            _u32.DestroyWindow(self.hwnd)
            self.hwnd = None
