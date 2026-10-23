"""Intégration Windows native via ctypes : verrouillage, DPAPI, autostart, instance unique,
et un thread "SystemWatcher" (fenêtre cachée) qui reçoit en temps réel :
  - les branchements de périphériques (toutes classes d'interfaces, sans polling WMI),
  - le verrouillage / déverrouillage de session,
  - la mise en veille / reprise,
  - les changements d'affichage.
"""
from __future__ import annotations

import ctypes
import re
import sys
import threading
import time
import uuid
import winreg
from ctypes import wintypes
from dataclasses import dataclass, field, replace
from typing import Callable

from .log import get

log = get("winsys")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
cfgmgr32 = ctypes.WinDLL("cfgmgr32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON),
    ]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    def __str__(self) -> str:
        d4 = bytes(self.Data4)
        return (f"{self.Data1:08x}-{self.Data2:04x}-{self.Data3:04x}-"
                f"{d4[:2].hex()}-{d4[2:].hex()}")


class DEV_BROADCAST_HDR(ctypes.Structure):
    _fields_ = [("dbch_size", wintypes.DWORD), ("dbch_devicetype", wintypes.DWORD),
                ("dbch_reserved", wintypes.DWORD)]


class DEV_BROADCAST_DEVICEINTERFACE_W(ctypes.Structure):
    _fields_ = [("dbcc_size", wintypes.DWORD), ("dbcc_devicetype", wintypes.DWORD),
                ("dbcc_reserved", wintypes.DWORD), ("dbcc_classguid", GUID),
                ("dbcc_name", wintypes.WCHAR * 1)]


class DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.ULONG)]


def _guid(text: str) -> GUID:
    g = GUID()
    ctypes.memmove(ctypes.byref(g), uuid.UUID(text).bytes_le, 16)
    return g


# Nom annoncé par le matériel lui-même (« Razer Naga V2 HyperSpeed ») plutôt que le pilote générique
DEVPKEY_BUS_REPORTED_DESC = DEVPROPKEY(_guid("540b947e-8b40-45bc-a8a2-6a0b894cbda2"), 4)


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.RegisterDeviceNotificationW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD]
user32.RegisterDeviceNotificationW.restype = wintypes.HANDLE
user32.UnregisterDeviceNotification.argtypes = [wintypes.HANDLE]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.LockWorkStation.restype = wintypes.BOOL
user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.OpenInputDesktop.restype = wintypes.HANDLE
user32.CloseDesktop.argtypes = [wintypes.HANDLE]
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                wintypes.UINT]
cfgmgr32.CM_Locate_DevNodeW.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.LPCWSTR, wintypes.ULONG]
cfgmgr32.CM_Get_Parent.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.ULONG]
cfgmgr32.CM_Get_Device_IDW.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.ULONG, wintypes.ULONG]
cfgmgr32.CM_Get_DevNode_PropertyW.argtypes = [wintypes.DWORD, ctypes.POINTER(DEVPROPKEY), ctypes.POINTER(wintypes.ULONG),
                                              ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.ULONG]
cfgmgr32.CM_Request_Device_EjectW.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.c_int), wintypes.LPWSTR,
                                              wintypes.ULONG, wintypes.ULONG]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
wtsapi32.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
wtsapi32.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
wtsapi32.WTSQuerySessionInformationW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
                                                 ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]
crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
                                     wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPVOID, ctypes.POINTER(DATA_BLOB),
                                       wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]

WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_DEVICECHANGE = 0x0219
WM_DISPLAYCHANGE = 0x007E
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_QUIT = 0x0012
DBT_DEVICEARRIVAL = 0x8000
DBT_DEVICEREMOVECOMPLETE = 0x8004
DBT_DEVTYP_DEVICEINTERFACE = 5
DEVICE_NOTIFY_WINDOW_HANDLE = 0
DEVICE_NOTIFY_ALL_INTERFACE_CLASSES = 4
WTS_SESSION_LOCK = 7
WTS_SESSION_UNLOCK = 8
PBT_APMSUSPEND = 0x4
PBT_APMRESUMEAUTOMATIC = 0x12
ERROR_ALREADY_EXISTS = 183

# ── Primitives ───────────────────────────────────────────────────────────────


def lock_workstation() -> bool:
    return bool(user32.LockWorkStation())


def _wts_session_flags() -> int | None:
    """SessionFlags de WTSINFOEXW (niveau 1) : 0 = verrouillée, 1 = déverrouillée, -1 = inconnu."""
    WTS_CURRENT_SESSION, WTSSessionInfoEx = 0xFFFFFFFF, 25
    buf, size = ctypes.c_void_p(), wintypes.DWORD()
    if not wtsapi32.WTSQuerySessionInformationW(None, WTS_CURRENT_SESSION, WTSSessionInfoEx,
                                                ctypes.byref(buf), ctypes.byref(size)):
        return None
    try:
        if size.value < 20:
            return None
        # Level (DWORD) + alignement 8 de l'union, puis SessionId, SessionState, SessionFlags
        level, _pad, _sid, _state, flags = (ctypes.c_int32 * 5).from_address(buf.value)
        return flags if level == 1 else None
    finally:
        wtsapi32.WTSFreeMemory(buf)


def is_session_locked() -> bool:
    """État officiel de la session (WTS) ; repli sur l'accessibilité du bureau d'entrée.

    OpenInputDesktop seul redevient accessible avant la fin du déverrouillage (Windows Hello) :
    il annonçait un faux « déverrouillé » pendant que l'écran de verrouillage était encore affiché.
    """
    flags = _wts_session_flags()
    if flags in (0, 1):
        return flags == 0
    DESKTOP_SWITCHDESKTOP = 0x0100
    h = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not h:
        return True
    user32.CloseDesktop(h)
    return False


def dpapi_protect(data: bytes) -> bytes:
    return _dpapi(crypt32.CryptProtectData, data)


def dpapi_unprotect(data: bytes) -> bytes:
    return _dpapi(crypt32.CryptUnprotectData, data)


def _dpapi(fn, data: bytes) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not fn(ctypes.byref(blob_in), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise OSError(ctypes.get_last_error(), "DPAPI a échoué")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


_mutex_handle = None


def acquire_single_instance(name: str = "Local\\PrankGuard.v3") -> bool:
    """True si on est la seule instance (le handle reste ouvert jusqu'à la fin du process)."""
    global _mutex_handle
    _mutex_handle = kernel32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    exe = sys.executable
    pyw = exe[:-len("python.exe")] + "pythonw.exe" if exe.lower().endswith("python.exe") else exe
    return f'"{pyw}" -m prankguard --background'


def set_autostart(enabled: bool, name: str = "PrankGuard") -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass


def get_autostart(name: str = "PrankGuard") -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, name)
            return True
    except OSError:
        return False


# ── Classification des périphériques ─────────────────────────────────────────

_CLASS_BY_GUID = {
    "884b96c3-56ef-11d1-bc8c-00a0c91e6bf6": "keyboard",
    "378de44c-56ef-11d1-bc8c-00a0c91e6bf6": "mouse",
    "53f56307-b6bf-11d0-94f2-00a0c91efb8b": "storage",
    "53f5630d-b6bf-11d0-94f2-00a0c91efb8b": "storage",
    "6ac27878-a6fa-4155-ba85-f98f491d4f33": "phone",
    "e6f07b5f-ee97-4a90-b076-33f57bf4eaa7": "display",
    "cac88484-7515-4c03-82e6-71a87abac361": "network",
    "6994ad04-93ef-11d0-a3cc-00a0c9223196": "audio",
    "e5323777-f976-4f5b-9b55-b94699c46e44": "camera",
    "28d78fad-5a12-11d1-ae5b-0000f803a8c2": "printer",
    "86e0d1e0-8089-11d0-9ce4-08003e301f73": "serial",
    "4d1e55b2-f16f-11cf-88cb-001111000030": "hid",
    "a5dcbf10-6530-11d2-901f-00c04fb951ed": "usb",
}
# Ordre de spécificité : on garde la catégorie la plus parlante d'une rafale d'interfaces.
_PRIORITY = ["keyboard", "mouse", "storage", "phone", "display", "network", "camera", "audio",
             "printer", "serial", "bluetooth", "hid", "usb", "other"]
_IGNORED_BUSES = {"SWD", "ROOT", "HTREE", "ACPI", "UMB", "SW", "MMDEVAPI", "WPDBUSENUMROOT"}
_VIDPID = re.compile(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", re.I)


@dataclass
class DeviceArrival:
    category: str
    name: str
    key: str               # identifiant stable (VID:PID, ou chemin d'instance)
    instance: str
    ts: float = field(default_factory=time.time)


def _instance_from_path(path: str) -> str:
    # \\?\USB#VID_046D&PID_C52B&MI_00#7&2a4f&0&0000#{guid} -> USB\VID_046D&PID_C52B&MI_00\7&2a4f&0&0000
    p = path[4:] if path.startswith("\\\\?\\") else path
    parts = p.split("#")
    if len(parts) >= 3 and parts[-1].startswith("{"):
        parts = parts[:-1]
    return "\\".join(parts)


def _registry_name(instance: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, "SYSTEM\\CurrentControlSet\\Enum\\" + instance) as k:
            for val in ("FriendlyName", "DeviceDesc"):
                try:
                    v, _ = winreg.QueryValueEx(k, val)
                    if v:
                        return str(v).split(";")[-1].strip()
                except OSError:
                    pass
    except OSError:
        pass
    return ""


def classify(class_guid: str, path: str) -> DeviceArrival | None:
    instance = _instance_from_path(path)
    bus = instance.split("\\", 1)[0].upper()
    if not instance or bus in _IGNORED_BUSES or bus.startswith("{"):
        return None
    cat = _CLASS_BY_GUID.get(class_guid.lower(), "other")
    if bus in ("BTHENUM", "BTHLE", "BTHLEDEVICE", "BTH") and cat in ("hid", "other", "usb"):
        cat = "bluetooth"
    m = _VIDPID.search(instance)
    key = f"{m.group(1).upper()}:{m.group(2).upper()}" if m else instance.upper()
    return DeviceArrival(category=cat, name=_registry_name(instance), key=key, instance=instance)


def merge_burst(items: list[DeviceArrival]) -> DeviceArrival:
    """Un branchement physique déclenche plusieurs interfaces : on les fusionne en un seul événement."""
    best = min(items, key=lambda d: _PRIORITY.index(d.category) if d.category in _PRIORITY else 99)
    name = best.name or next((d.name for d in items if d.name), "") or best.category.capitalize()
    key = next((d.key for d in items if ":" in d.key and len(d.key) == 9), best.key)
    return DeviceArrival(category=best.category, name=name, key=key, instance=best.instance, ts=items[0].ts)


# ── SystemWatcher ────────────────────────────────────────────────────────────


class SystemWatcher:
    """Fenêtre Win32 cachée dans son propre thread ; callbacks appelés depuis ce thread."""

    BURST_WINDOW = 0.8

    def __init__(self,
                 on_device: Callable[[DeviceArrival], None] | None = None,
                 on_session: Callable[[str], None] | None = None,
                 on_power: Callable[[str], None] | None = None,
                 on_display: Callable[[], None] | None = None):
        self.on_device = on_device
        # appelé dès la première interface d'un branchement, avant la fusion de la rafale (0,8 s) :
        # sert à bloquer le clavier avant qu'une clé « BadUSB » ait le temps de taper
        self.on_device_early: Callable[[DeviceArrival], None] | None = None
        self.on_session = on_session
        self.on_power = on_power
        self.on_display = on_display
        self.on_endsession: Callable[[], None] | None = None
        self._hwnd = None
        self._hdev = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._burst: list[DeviceArrival] = []
        self._burst_lock = threading.Lock()
        self._burst_timer: threading.Timer | None = None
        self._wndproc = WNDPROC(self._proc)   # référence conservée (sinon GC -> crash)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="SystemWatcher", daemon=True)
        self._thread.start()
        self._ready.wait(3)

    def stop(self) -> None:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(2)

    def _run(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        cls = f"PrankGuardWatcher{id(self)}"
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = cls
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            log.error("RegisterClassExW a échoué (%s)", ctypes.get_last_error())
            self._ready.set()
            return
        # Fenêtre top-level jamais affichée (une fenêtre message-only ne reçoit pas WM_POWERBROADCAST)
        self._hwnd = user32.CreateWindowExW(0, cls, "PrankGuard watcher", 0, 0, 0, 0, 0, None, None, hinst, None)
        if not self._hwnd:
            log.error("CreateWindowExW a échoué (%s)", ctypes.get_last_error())
            self._ready.set()
            return

        flt = DEV_BROADCAST_DEVICEINTERFACE_W()
        flt.dbcc_size = ctypes.sizeof(DEV_BROADCAST_DEVICEINTERFACE_W)
        flt.dbcc_devicetype = DBT_DEVTYP_DEVICEINTERFACE
        self._hdev = user32.RegisterDeviceNotificationW(
            self._hwnd, ctypes.byref(flt), DEVICE_NOTIFY_WINDOW_HANDLE | DEVICE_NOTIFY_ALL_INTERFACE_CLASSES)
        if not self._hdev:
            log.warning("RegisterDeviceNotification a échoué (%s)", ctypes.get_last_error())
        if not wtsapi32.WTSRegisterSessionNotification(self._hwnd, 0):
            log.warning("WTSRegisterSessionNotification a échoué (%s)", ctypes.get_last_error())
        self._ready.set()
        log.info("SystemWatcher actif (périphériques, session, veille)")

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_DEVICECHANGE and wparam == DBT_DEVICEARRIVAL and lparam:
                self._handle_arrival(lparam)
            elif msg == WM_WTSSESSION_CHANGE and self.on_session:
                if wparam == WTS_SESSION_LOCK:
                    self.on_session("lock")
                elif wparam == WTS_SESSION_UNLOCK:
                    self.on_session("unlock")
            elif msg == WM_POWERBROADCAST and self.on_power:
                if wparam == PBT_APMSUSPEND:
                    self.on_power("suspend")
                elif wparam == PBT_APMRESUMEAUTOMATIC:
                    self.on_power("resume")
            elif msg == WM_DISPLAYCHANGE and self.on_display:
                self.on_display()
            elif msg == WM_QUERYENDSESSION:
                # arrêt / déconnexion de Windows : on ne bloque jamais, on se prépare à quitter
                if self.on_endsession:
                    self.on_endsession()
                return 1
            elif msg == WM_ENDSESSION and wparam and self.on_endsession:
                self.on_endsession()
                return 0
            elif msg == WM_CLOSE:
                if self._hdev:
                    user32.UnregisterDeviceNotification(self._hdev)
                wtsapi32.WTSUnRegisterSessionNotification(hwnd)
                user32.DestroyWindow(hwnd)
                return 0
            elif msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            log.exception("Erreur dans la boucle de messages")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _handle_arrival(self, lparam) -> None:
        hdr = ctypes.cast(lparam, ctypes.POINTER(DEV_BROADCAST_HDR)).contents
        if hdr.dbch_devicetype != DBT_DEVTYP_DEVICEINTERFACE or not self.on_device:
            return
        dbi = ctypes.cast(lparam, ctypes.POINTER(DEV_BROADCAST_DEVICEINTERFACE_W)).contents
        name_off = DEV_BROADCAST_DEVICEINTERFACE_W.dbcc_name.offset
        path = ctypes.wstring_at(lparam + name_off)
        dev = classify(str(dbi.dbcc_classguid), path)
        if dev is None:
            return
        with self._burst_lock:
            first = not self._burst
            self._burst.append(dev)
            if self._burst_timer:
                self._burst_timer.cancel()
            self._burst_timer = threading.Timer(self.BURST_WINDOW, self._flush_burst)
            self._burst_timer.daemon = True
            self._burst_timer.start()
        if first and self.on_device_early:
            try:
                self.on_device_early(dev)
            except Exception:
                log.exception("Callback périphérique (précoce) en erreur")

    def _flush_burst(self) -> None:
        with self._burst_lock:
            items, self._burst = self._burst, []
        if items and self.on_device:
            try:
                dev = merge_burst(items)
                name = product_name(dev.instance)
                self.on_device(replace(dev, name=name) if name else dev)
            except Exception:
                log.exception("Callback périphérique en erreur")


# ── Blocage clavier & éjection ───────────────────────────────────────────────


class KeyboardBlocker:
    """Avale les frappes clavier tant qu'il est actif (hook bas niveau, sans droits admin).

    Neutralise les clés « BadUSB » qui tapent des commandes dès le branchement, le temps que
    le propriétaire réponde à la souris. Les relâchements de touches passent (pas de touche
    « coincée ») et Ctrl+Alt+Suppr reste toujours géré par Windows.
    """

    MAX_SECONDS = 60.0     # filet : le clavier n'est jamais bloqué plus longtemps

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ok = False
        self._ready = threading.Event()
        self._timer: threading.Timer | None = None
        self._proc = None
        self.swallowed = 0

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        with self._lock:
            if self.active:
                return True
            self._ready.clear()
            self._ok, self.swallowed = False, 0
            self._thread = threading.Thread(target=self._run, name="KeyboardBlocker", daemon=True)
            self._thread.start()
        self._ready.wait(2.0)
        if self._ok:
            with self._lock:
                self._timer = threading.Timer(self.MAX_SECONDS, self.stop)
                self._timer.daemon = True
                self._timer.start()
            log.info("Clavier bloqué en attendant votre décision")
        return self._ok

    def stop(self) -> None:
        with self._lock:
            t, tid, timer = self._thread, self._tid, self._timer
            self._timer = None
        if timer:
            timer.cancel()
        if t and t.is_alive() and tid:
            user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
            if t is not threading.current_thread():
                t.join(2.0)
            log.info("Clavier débloqué (%d frappe(s) bloquée(s))", self.swallowed)

    def _run(self) -> None:
        WH_KEYBOARD_LL, HC_ACTION, PM_NOREMOVE = 13, 0, 0
        KEYDOWNS = (0x0100, 0x0104)          # WM_KEYDOWN, WM_SYSKEYDOWN
        self._tid = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)   # crée la file de messages

        def proc(code, wparam, lparam):
            if code == HC_ACTION and wparam in KEYDOWNS:
                self.swallowed += 1
                return 1
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._proc = HOOKPROC(proc)
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        self._ok = bool(hook)
        self._ready.set()
        if not hook:
            log.warning("Blocage clavier impossible (%s)", ctypes.get_last_error())
            return
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(hook)


def _device_id(devinst: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    if cfgmgr32.CM_Get_Device_IDW(devinst, buf, len(buf), 0) != 0:
        return ""
    return buf.value


def physical_device(instance: str) -> tuple[int, str] | None:
    """Remonte d'une interface (HID, USBSTOR, fonction USB &MI_xx) au périphérique USB physique."""
    node = wintypes.DWORD()
    if cfgmgr32.CM_Locate_DevNodeW(ctypes.byref(node), instance, 0) != 0:
        return None
    cur = node.value
    for _ in range(8):
        iid = _device_id(cur).upper()
        if iid.startswith("USB\\VID_") and "&MI_" not in iid:
            return cur, iid
        parent = wintypes.DWORD()
        if cfgmgr32.CM_Get_Parent(ctypes.byref(parent), cur, 0) != 0:
            break
        cur = parent.value
    return node.value, _device_id(node.value).upper()


def product_name(instance: str) -> str:
    """Nom commercial du périphérique physique, ou "" si Windows ne le connaît pas."""
    try:
        found = physical_device(instance)
    except OSError:
        return ""
    if not found:
        return ""
    ptype, buf, size = wintypes.ULONG(), ctypes.create_unicode_buffer(256), wintypes.ULONG(512)
    if cfgmgr32.CM_Get_DevNode_PropertyW(found[0], ctypes.byref(DEVPKEY_BUS_REPORTED_DESC), ctypes.byref(ptype),
                                         buf, ctypes.byref(size), 0) != 0:
        return ""
    return buf.value.strip()


def eject_device(instance: str) -> tuple[bool, str]:
    """Équivalent « Retirer le périphérique en toute sécurité » (sans droits admin). Retourne (ok, détail)."""
    found = physical_device(instance)
    if not found:
        return False, "introuvable (déjà retiré ?)"
    devinst, iid = found
    if not iid.startswith("USB\\VID_"):
        return False, f"non USB ({iid})"
    veto_type, veto = ctypes.c_int(0), ctypes.create_unicode_buffer(260)
    rc = cfgmgr32.CM_Request_Device_EjectW(devinst, ctypes.byref(veto_type), veto, len(veto), 0)
    if rc == 0 and veto_type.value == 0:
        log.warning("Périphérique éjecté : %s", iid)
        return True, iid
    detail = f"refusé par Windows (code {rc}, veto {veto_type.value} {veto.value})"
    log.warning("Éjection de %s : %s", iid, detail)
    return False, detail
