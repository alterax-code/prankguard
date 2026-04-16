"""
Inventaire persistant des périphériques PrankGuard (Vague 4).
Clé : md5(category:device_info)[:12] — stable entre sessions.
"""
import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List

from src import paths as _paths

INVENTORY_FILE: str = str(_paths.APP_DATA / "device_inventory.json")


@dataclass
class DeviceEntry:
    device_id: str
    name: str
    category: str
    authorized: bool = False
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


def _make_id(category: str, device_info: str) -> str:
    raw = f"{category}:{device_info}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()[:12]


def _load() -> Dict[str, dict]:
    if not os.path.exists(INVENTORY_FILE):
        return {}
    try:
        with open(INVENTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: Dict[str, dict]) -> None:
    try:
        os.makedirs(os.path.dirname(INVENTORY_FILE), exist_ok=True)
        with open(INVENTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def add_or_update(category: str, device_info: str, authorized: bool = False) -> DeviceEntry:
    """Ajoute ou met à jour un périphérique. Ne modifie PAS authorized si l'entrée existe déjà."""
    data = _load()
    did = _make_id(category, device_info)
    now = time.time()
    if did in data:
        data[did]["last_seen"] = now
        _save(data)
        entry = DeviceEntry(**{k: v for k, v in data[did].items()
                               if k in DeviceEntry.__dataclass_fields__})
    else:
        entry = DeviceEntry(
            device_id=did,
            name=device_info[:80],
            category=category,
            authorized=authorized,
            first_seen=now,
            last_seen=now,
        )
        data[did] = asdict(entry)
        _save(data)
    return entry


def is_authorized(category: str, device_info: str) -> bool:
    data = _load()
    did = _make_id(category, device_info)
    entry = data.get(did)
    return bool(entry and entry.get("authorized", False))


def authorize(device_id: str) -> None:
    data = _load()
    if device_id in data:
        data[device_id]["authorized"] = True
        _save(data)


def block(device_id: str) -> None:
    data = _load()
    if device_id in data:
        data[device_id]["authorized"] = False
        _save(data)


def get_all() -> List[DeviceEntry]:
    data = _load()
    entries = []
    for v in data.values():
        try:
            entries.append(DeviceEntry(**{k: v2 for k, v2 in v.items()
                                          if k in DeviceEntry.__dataclass_fields__}))
        except Exception:
            pass
    return entries


def scan_current() -> List[DeviceEntry]:
    """Scan WMI des périphériques actifs — tous auto-autorisés (baseline startup)."""
    results = []
    try:
        import wmi as _wmi
        c = _wmi.WMI()

        # USB HID
        try:
            for dev in c.Win32_USBHub():
                info = dev.Name or dev.DeviceID or "Unknown USB"
                results.append(add_or_update("USB HID", info, authorized=True))
        except Exception:
            pass

        # Network
        try:
            for dev in c.Win32_NetworkAdapter():
                if not dev.Name:
                    continue
                info = dev.Name
                if dev.MACAddress:
                    info += f" MAC:{dev.MACAddress}"
                results.append(add_or_update("Network", info, authorized=True))
        except Exception:
            pass

        # Audio
        try:
            for dev in c.Win32_SoundDevice():
                info = dev.Name or "Unknown Audio"
                results.append(add_or_update("Audio", info, authorized=True))
        except Exception:
            pass

        # Monitor
        try:
            for dev in c.Win32_DesktopMonitor():
                info = dev.Name or "Unknown Monitor"
                results.append(add_or_update("Monitor", info, authorized=True))
        except Exception:
            pass

        # Bluetooth
        try:
            for dev in c.Win32_PnPEntity():
                if dev.Name and "bluetooth" in dev.Name.lower():
                    results.append(add_or_update("Bluetooth", dev.Name, authorized=True))
        except Exception:
            pass

    except Exception:
        pass

    return results
