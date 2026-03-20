"""
Surveillance USB temps réel via fenêtre Win32 invisible (WM_DEVICECHANGE).
FIX 2 — Fix ctypes OverflowError (restype c_void_p pour handles 64-bit).
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
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
            ctypes.c_void_p, ctypes.c_void_p
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

        # FIX 2 — Déclarer le restype AVANT l'appel (64-bit safe)
        ctypes.windll.kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)

        ctypes.windll.user32.RegisterClassExW(ctypes.byref(wc))

        # FIX 2 — restype + None au lieu de 0 (évite OverflowError)
        ctypes.windll.user32.CreateWindowExW.restype = ctypes.c_void_p
        self.hwnd = ctypes.windll.user32.CreateWindowExW(
            0, "PrankGuardUSB", "USB", 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None
        )

        if self.hwnd:
            self._register_notification()
            msg = ctypes.wintypes.MSG()
            while self.running:
                if ctypes.windll.user32.PeekMessageW(
                    ctypes.byref(msg), self.hwnd, 0, 0, 1
                ):
                    ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                    ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
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

        self.hdev_notify = ctypes.windll.user32.RegisterDeviceNotificationW(
            self.hwnd, ctypes.byref(dbdi), DEVICE_NOTIFY_WINDOW_HANDLE
        )

    def _window_proc(self, hwnd, msg, wparam, lparam):
        """Callback Win32 — appelé quand un device USB est connecté/déconnecté."""
        # FIX 3 — Ne pas réagir si en pause
        if msg == WM_DEVICECHANGE and wparam == DBT_DEVICEARRIVAL:
            if self.enabled and not self.paused:
                self.callback("USB")
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def stop(self):
        """Arrête le watcher proprement."""
        self.running = False
        if self.hdev_notify:
            ctypes.windll.user32.UnregisterDeviceNotification(self.hdev_notify)
