# PrankGuard v2.0

Webcam-based security system with face recognition, USB device detection, and automatic screen locking. Built for Windows 11.

## Features

### Sprint 1 — Core
- Face recognition (owner vs threat vs shoulder surfer)
- Multi-user support with named profiles
- Instant USB/HID device detection
- Monitor, Network, Bluetooth, Printer, Audio detection
- Automatic USB port blocking (DESKTOP/LAPTOP modes)
- Shoulder surfer detection with grace period
- Camera disconnect protection
- Anti-spoofing (photo/screen detection)
- Sound alarm on threat
- Anti-close protection (password + watchdog process)
- Intrusion log with criticality levels (INFO/WARNING/CRITICAL)
- Hardware auto-profile (CPU-adaptive frame analysis)

### Sprint 2 — Advanced
- **Systray** — real-time color icon (green/orange/red) in notification area
- **Stealth mode** — start with hidden window, access via systray
- **Multi-user enrollment** — named users, per-user facial encodings
- **Email alerts** — SMTP notification on CRITICAL events (rate-limited 5 min)
- **AES-256-GCM encryption** — encoding file encrypted at rest, PBKDF2-SHA256 key derivation

## Requirements

- Python 3.12+ (Windows 11 only)
- Admin rights (for USB blocking)
- Webcam

## Install

```bat
python -m venv venv312
venv312\Scripts\activate
pip install -r requirements.txt
```

## Run

```bat
run.bat
```
or
```
python -m src.main
```

## First run

An enrollment window will open. Capture 15–30 photos of your face following the on-screen tips.

## Build distribution

```bat
build_lite.bat
```

Produces `dist\PrankGuard\PrankGuard.exe` (admin manifest, no console window).
