"""winsys.py — uniquement les helpers purs + DPAPI (jamais de verrouillage, registre ni SystemWatcher)."""
import pytest

from prankguard import winsys
from prankguard.winsys import DeviceArrival, classify, merge_burst

KEYBOARD = "884B96C3-56EF-11D1-BC8C-00A0C91E6BF6"
HID = "4d1e55b2-f16f-11cf-88cb-001111000030"
USB = "a5dcbf10-6530-11d2-901f-00c04fb951ed"
STORAGE = "53f56307-b6bf-11d0-94f2-00a0c91efb8b"


@pytest.fixture(autouse=True)
def no_registry(monkeypatch):
    monkeypatch.setattr(winsys, "_registry_name", lambda instance: "")


def test_instance_from_path_strips_prefix_and_class_guid():
    p = r"\\?\USB#VID_046D&PID_C52B&MI_00#7&2a4f&0&0000#{884b96c3-56ef-11d1-bc8c-00a0c91e6bf6}"
    assert winsys._instance_from_path(p) == r"USB\VID_046D&PID_C52B&MI_00\7&2a4f&0&0000"


def test_instance_from_path_without_prefix_or_guid():
    assert winsys._instance_from_path(r"HID#VID_1&PID_2#abc") == r"HID\VID_1&PID_2\abc"
    assert winsys._instance_from_path("") == ""


def test_classify_keyboard_extracts_vidpid_key():
    p = r"\\?\HID#VID_046D&PID_C52B&MI_00#7&2a4f&0&0000#{884b96c3-56ef-11d1-bc8c-00a0c91e6bf6}"
    d = classify(KEYBOARD, p)             # GUID en majuscules : insensible à la casse
    assert d is not None
    assert d.category == "keyboard" and d.key == "046D:C52B"
    assert d.instance == r"HID\VID_046D&PID_C52B&MI_00\7&2a4f&0&0000"


def test_classify_key_is_uppercase_vidpid_regardless_of_input_case():
    d = classify(USB, r"\\?\USB#vid_046d&pid_c52b#5&1&0#{a5dcbf10-6530-11d2-901f-00c04fb951ed}")
    assert d.key == "046D:C52B" and d.category == "usb"


def test_classify_other_categories():
    d = classify(STORAGE, r"\\?\USBSTOR#Disk&Ven_X&Prod_Y#123#{53f56307-b6bf-11d0-94f2-00a0c91efb8b}")
    assert d.category == "storage" and d.key == r"USBSTOR\DISK&VEN_X&PROD_Y\123"
    assert classify("00000000-0000-0000-0000-000000000000", r"\\?\PCI#DEV_1#2#{0}").category == "other"


def test_classify_bluetooth_overrides_generic_classes():
    p = r"\\?\BTHENUM#DEV_AABBCCDDEEFF#7&1&0&BluetoothDevice_AABBCCDDEEFF#{4d1e55b2-f16f-11cf-88cb-001111000030}"
    d = classify(HID, p)
    assert d.category == "bluetooth"
    # une clé de stockage Bluetooth garde sa catégorie
    assert classify(STORAGE, p).category == "storage"


@pytest.mark.parametrize("bus", ["SWD", "ROOT", "HTREE", "ACPI", "UMB", "SW", "MMDEVAPI", "swd"])
def test_classify_ignores_virtual_buses(bus):
    assert classify(USB, rf"\\?\{bus}#MMDEVAPI#{{0.0.1}}.x#{{a5dcbf10-6530-11d2-901f-00c04fb951ed}}") is None


def test_classify_empty_path_ignored():
    assert classify(USB, "") is None


def arrival(category, key, name="", instance="X\\Y"):
    return DeviceArrival(category=category, name=name, key=key, instance=instance, ts=42.0)


def test_merge_burst_prefers_keyboard_and_keeps_vidpid_key():
    items = [
        arrival("usb", "046D:C52B", "USB Composite Device", r"USB\VID_046D&PID_C52B\1"),
        arrival("hid", "046D:C52B", "", r"HID\VID_046D&PID_C52B&MI_01\2"),
        arrival("keyboard", r"HID\KEYBOARD_NOVIDPID\3", "Keyboard HID", r"HID\KEYBOARD_NOVIDPID\3"),
    ]
    m = merge_burst(items)
    assert m.category == "keyboard"
    assert m.key == "046D:C52B"                       # le VID:PID est conservé même si le clavier n'en a pas
    assert m.name == "Keyboard HID" and m.ts == 42.0
    assert m.instance == r"HID\KEYBOARD_NOVIDPID\3"


def test_merge_burst_priority_order():
    cats = ["usb", "hid", "other", "camera", "storage", "mouse"]
    m = merge_burst([arrival(c, f"k-{c}") for c in cats])
    assert m.category == "mouse"
    assert merge_burst([arrival("usb", "k1"), arrival("hid", "k2")]).category == "hid"


def test_merge_burst_name_fallbacks():
    assert merge_burst([arrival("keyboard", "a"), arrival("usb", "b", "Some Name")]).name == "Some Name"
    assert merge_burst([arrival("keyboard", "a"), arrival("usb", "b")]).name == "Keyboard"


def test_merge_burst_single_item_and_unknown_category():
    one = arrival("storage", "046D:C52B", "Disk")
    m = merge_burst([one])
    assert (m.category, m.key, m.name) == ("storage", "046D:C52B", "Disk")
    assert merge_burst([arrival("weird", "k"), arrival("usb", "k2")]).category == "usb"


def test_dpapi_roundtrip():
    secret = b"embeddings \x00\x01\xff" * 100
    enc = winsys.dpapi_protect(secret)
    assert enc != secret and secret[:16] not in enc
    assert winsys.dpapi_unprotect(enc) == secret


def test_dpapi_unprotect_garbage_raises():
    with pytest.raises(OSError):
        winsys.dpapi_unprotect(b"definitely not a DPAPI blob")


def test_launch_command_targets_module():
    cmd = winsys.launch_command()
    assert "-m prankguard --background" in cmd and cmd.startswith('"')


def test_safety_net_blocks_dangerous_calls():
    with pytest.raises(AssertionError):
        winsys.lock_workstation()
    with pytest.raises(AssertionError):
        winsys.set_autostart(True)


@pytest.mark.parametrize("flags, locked", [(0, True), (1, False)])
def test_session_lock_state_comes_from_wts(monkeypatch, flags, locked):
    monkeypatch.setattr(winsys, "_wts_session_flags", lambda: flags)
    assert winsys.is_session_locked() is locked


def test_session_lock_state_falls_back_when_wts_unknown(monkeypatch):
    monkeypatch.setattr(winsys, "_wts_session_flags", lambda: None)
    assert winsys.is_session_locked() is False      # session de test interactive, déverrouillée


def test_wts_session_flags_reads_current_session():
    assert winsys._wts_session_flags() == 1         # les tests tournent dans une session ouverte
