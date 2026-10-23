"""Bout en bout : vraie fenêtre Win32 du SystemWatcher, messages synthétiques envoyés en local
(aucun périphérique réel, aucun effet sur la session)."""
import ctypes
import threading
import uuid
from ctypes import wintypes

from prankguard import winsys

_send = winsys.user32.SendMessageW
_send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_send.restype = winsys.LRESULT


def _send_arrival(hwnd, class_guid: str, path: str) -> None:
    g = uuid.UUID(class_guid)
    hdr = winsys.DEV_BROADCAST_DEVICEINTERFACE_W
    off = hdr.dbcc_name.offset
    size = off + (len(path) + 1) * 2
    buf = ctypes.create_string_buffer(size)
    st = hdr.from_buffer(buf)
    st.dbcc_size = size
    st.dbcc_devicetype = winsys.DBT_DEVTYP_DEVICEINTERFACE
    st.dbcc_classguid.Data1, st.dbcc_classguid.Data2, st.dbcc_classguid.Data3 = g.fields[0], g.fields[1], g.fields[2]
    for i, b in enumerate(g.bytes[8:]):
        st.dbcc_classguid.Data4[i] = b
    ctypes.memmove(ctypes.addressof(buf) + off, path.encode("utf-16-le") + b"\0\0", (len(path) + 1) * 2)
    _send(hwnd, winsys.WM_DEVICECHANGE, winsys.DBT_DEVICEARRIVAL, ctypes.addressof(buf))


def test_system_watcher_end_to_end(monkeypatch):
    monkeypatch.setattr(winsys, "_registry_name", lambda inst: "")
    got, sessions, done = [], [], threading.Event()

    def on_dev(d):
        got.append(d)
        done.set()

    w = winsys.SystemWatcher(on_device=on_dev, on_session=sessions.append)
    w.BURST_WINDOW = 0.2
    w.start()
    try:
        assert w._hwnd
        # un clavier USB : interfaces USB + HID + clavier dans la même rafale -> un seul événement
        _send_arrival(w._hwnd, "a5dcbf10-6530-11d2-901f-00c04fb951ed",
                      r"\\?\USB#VID_1532&PID_00B4#6&abc&0&1#{a5dcbf10-6530-11d2-901f-00c04fb951ed}")
        _send_arrival(w._hwnd, "4d1e55b2-f16f-11cf-88cb-001111000030",
                      r"\\?\HID#VID_1532&PID_00B4&MI_00#7&x&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}")
        _send_arrival(w._hwnd, "884b96c3-56ef-11d1-bc8c-00a0c91e6bf6",
                      r"\\?\HID#VID_1532&PID_00B4&MI_00#7&x&0&0000#{884b96c3-56ef-11d1-bc8c-00a0c91e6bf6}")
        # périphérique logiciel (endpoint audio) : ignoré
        _send_arrival(w._hwnd, "6994ad04-93ef-11d0-a3cc-00a0c9223196",
                      r"\\?\SWD#MMDEVAPI#{0.0.0.00000000}.{x}#{6994ad04-93ef-11d0-a3cc-00a0c9223196}")
        assert done.wait(3)
        assert len(got) == 1
        assert (got[0].category, got[0].key) == ("keyboard", "1532:00B4")

        # clé USB : interface disque
        done.clear()
        _send_arrival(w._hwnd, "53f56307-b6bf-11d0-94f2-00a0c91efb8b",
                      r"\\?\USBSTOR#Disk&Ven_SanDisk&Prod_Ultra&Rev_1.00#4C53&0#{53f56307-b6bf-11d0-94f2-00a0c91efb8b}")
        assert done.wait(3)
        assert got[-1].category == "storage"

        # verrouillage / déverrouillage de session
        _send(w._hwnd, winsys.WM_WTSSESSION_CHANGE, winsys.WTS_SESSION_LOCK, 0)
        _send(w._hwnd, winsys.WM_WTSSESSION_CHANGE, winsys.WTS_SESSION_UNLOCK, 0)
        assert sessions == ["lock", "unlock"]
        # arrêt de Windows : on ne bloque jamais (TRUE), mais on ne quitte qu'à la confirmation
        ended = []
        w.on_endsession = lambda: ended.append(1)
        assert _send(w._hwnd, winsys.WM_QUERYENDSESSION, 0, 0) == 1
        assert ended == []                                  # arrêt peut-être annulé : on reste actif
        _send(w._hwnd, winsys.WM_ENDSESSION, 0, 0)         # annulé
        assert ended == []
        _send(w._hwnd, winsys.WM_ENDSESSION, 1, 0)         # confirmé
        assert ended == [1]
    finally:
        w.stop()
