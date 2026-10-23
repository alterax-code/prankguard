"""
Application PrankGuard — orchestrateur PyWebView.
Remplace l'ancien orchestrateur CustomTkinter (archivé dans app_customtkinter_deprecated.py).
"""
import os
import threading
import time
import winsound
from collections import deque
from pathlib import Path

import webview

from src import paths
from src.config import Config
from src.logger import logger
from src.face_analyzer import FaceAnalyzer
from src.systray import SystrayIcon
from src.enrollment import load_authorized_users_from_bytes
from src.crypto import is_encrypted, decrypt_encodings, encrypt_encodings
from src.anti_spoof import AntiSpoof
from src.intrusion_report import IntrusionReporter
from src.email_alert import EmailAlerter
from src.state_machine import StateMachine
from src.security.locker import Locker
from src.devices.watcher import DeviceWatcher
from src.devices.poller import PollingWatcher
from src.security.hardening import hash_password
from src import audit as _audit
from src.motion_detector import MotionDetector
from src import events_db as _edb
from src import device_inventory as _inventory
from src.challenge_response import ChallengeResponse
from src.network_monitor import SSIDMonitor
from src.watchdog import WatchdogThread, start_external_watchdog


class _FakeTkWidget:
    """Shim pour SSIDMonitor.start() qui attend un widget Tk avec .after()."""

    def after(self, ms: int, fn) -> None:
        threading.Timer(ms / 1000, fn).start()


class PrankGuardApp:
    """
    Orchestrateur principal PrankGuard — version PyWebView.
    Instancie tous les modules métier, expose une Api JS, lance la fenêtre web.
    """

    def __init__(self, config: Config):
        self.config = config
        self._closing = False

        # Buffer de logs thread-safe pour le polling JS
        self._log_deque: deque = deque(maxlen=500)

        # Alarme
        self._alarm_thread: threading.Thread | None = None
        self._alarm_stop = threading.Event()
        self._alarm_active = False
        self._threat_start: float | None = None

        # État courant
        self._pause_until: float = 0.0
        self._fps_current: float = 0.0
        self._cam_ok: bool = False
        self._last_face_state: str = "NONE"
        self._last_spoof_suspect: bool = False
        self._intrusion_active: bool = False

        # Référence fenêtre webview (remplie dans run())
        self._window: webview.Window | None = None

        # ── 1. Encodings ──────────────────────────────────────────────────────
        self.authorized_users: dict = {}
        if config.encodings_path.exists():
            try:
                raw = config.encodings_path.read_bytes()
                if is_encrypted(raw):
                    raw = decrypt_encodings(raw)
                self.authorized_users = load_authorized_users_from_bytes(raw)
            except Exception as exc:
                logger.error(f"Chargement encodings échoué: {exc}")

        # ── 2. Modules métier ─────────────────────────────────────────────────
        self.face_analyzer = FaceAnalyzer(
            authorized_users=self.authorized_users,
            tolerance=config.face_tolerance,
            min_face_size=config.min_face_size,
            center_threshold=config.center_threshold,
            analyze_every_n=config.analyze_every_n_frames,
            detection_scale=config.detection_scale,
        )
        self.state_machine = StateMachine(
            sec_mode=config.sec_mode,
            threat_lock_delay=config.threat_lock_delay,
            no_owner_lock_delay=config.no_owner_lock_delay,
            shoulder_grace_period=config.shoulder_grace_period,
            camera_lost_lock_delay=config.camera_lost_lock_delay,
        )
        self.locker = Locker(
            usb_mode=config.usb_mode,
            lock_cooldown=config.lock_cooldown,
        )
        self.anti_spoof = AntiSpoof()
        self.intrusion_reporter = IntrusionReporter(config.intrusion_log_path)
        self.motion_detector = MotionDetector()
        self.challenge_response = ChallengeResponse()
        self._ssid_monitor = SSIDMonitor()
        self._watchdog = WatchdogThread()
        self.email_alerter = EmailAlerter(
            smtp_host=config.smtp_host,
            smtp_port=config.smtp_port,
            smtp_user=config.smtp_user,
            smtp_password_b64=config.smtp_password_b64,
            recipient=config.smtp_recipient,
        )

        # ── 3. Device watchers ────────────────────────────────────────────────
        self.usb_watcher = DeviceWatcher(callback=lambda dt: self._on_device(dt, None))
        self.poll_watcher = PollingWatcher(callback=self._on_device)
        self._sync_poll_toggles()
        self.usb_watcher.start()
        self.poll_watcher.start()

        # ── 4. Inventaire initial ─────────────────────────────────────────────
        threading.Thread(target=_inventory.scan_current, daemon=True).start()

        # ── 5. Cooldown device ───────────────────────────────────────────────
        self.locker.set_device_cooldown(5.0)

        # ── 6. Audit démarrage ────────────────────────────────────────────────
        _audit.log_event("APP_START", {"version": "2.2.0", "mode": config.sec_mode})

        # ── 7. Watchdog ───────────────────────────────────────────────────────
        self._watchdog.start()
        if config.close_protection_enabled:
            try:
                start_external_watchdog()
            except Exception:
                pass

        # ── 8. Logger → deque ─────────────────────────────────────────────────
        logger.set_gui_callback(self._on_log_line)
        logger.start("PrankGuard démarré (interface web)")

        # ── 9. SSID monitor ───────────────────────────────────────────────────
        if config.watch_ssid:
            try:
                self._ssid_monitor.start(_FakeTkWidget(), self._on_ssid_change)
            except Exception:
                pass

        # ── 10. Systray ───────────────────────────────────────────────────────
        self._systray = SystrayIcon(
            on_show_hide=self._toggle_window,
            on_quit=self._on_close_request,
        )
        self._systray.start()

    # ── Synchronisation toggles polling ──────────────────────────────────────

    def _sync_poll_toggles(self) -> None:
        c = self.config
        self.poll_watcher.watch_usb_hid = c.watch_usb_hid
        self.poll_watcher.watch_printers = c.watch_printers
        self.poll_watcher.watch_monitors = c.watch_monitors
        self.poll_watcher.watch_network = c.watch_network
        self.poll_watcher.watch_bluetooth = c.watch_bluetooth
        self.poll_watcher.watch_audio = c.watch_audio
        self.usb_watcher.enabled = c.watch_usb

    # ── Callbacks internes ────────────────────────────────────────────────────

    def _on_log_line(self, line: str) -> None:
        self._log_deque.append({"ts": time.time(), "line": line})

    def _on_device(self, device_type: str, device_info=None) -> None:
        if self._closing or self.poll_watcher.paused:
            return
        is_auth = _inventory.is_authorized(device_type, device_info or "")
        if not is_auth:
            _audit.log_event("DEVICE_PLUGGED", {"type": device_type, "info": str(device_info)})
            logger.device(f"Nouveau périphérique: {device_type} — {device_info}")
            self.locker.set_device_cooldown(5.0)
            if self.locker.can_lock():
                self.locker.do_lock(f"Device {device_type}")
            if self._window:
                import json
                payload = json.dumps({"type": device_type, "info": str(device_info or "")})
                try:
                    self._window.evaluate_js(f"window._onDeviceNotification && window._onDeviceNotification({payload})")
                except Exception:
                    pass

    def _on_ssid_change(self, old_ssid: str, new_ssid: str) -> None:
        logger.info(f"SSID changé: {old_ssid!r} → {new_ssid!r}")
        if self.config.sec_mode == "SECURE" and self.locker.can_lock():
            self.locker.do_lock("SSID changé")
        if self._window:
            try:
                self._window.evaluate_js(
                    f"window._onSsidChange && window._onSsidChange({repr(old_ssid)}, {repr(new_ssid)})"
                )
            except Exception:
                pass

    # ── Alarme ────────────────────────────────────────────────────────────────

    def start_alarm(self) -> None:
        if self._alarm_active or not self.config.sound_alarm_enabled:
            return
        self._alarm_active = True
        self._alarm_stop.clear()

        def _loop():
            while not self._alarm_stop.is_set():
                path = self.config.alarm_sound_path
                if path and os.path.exists(path):
                    try:
                        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    except Exception:
                        pass
                else:
                    winsound.Beep(2500, 500)
                time.sleep(1.0)
            self._alarm_active = False

        self._alarm_thread = threading.Thread(target=_loop, daemon=True)
        self._alarm_thread.start()

    def stop_alarm(self) -> None:
        self._alarm_stop.set()
        self._alarm_active = False

    # ── Rechargement utilisateurs ─────────────────────────────────────────────

    def reload_users(self) -> None:
        if not self.config.encodings_path.exists():
            return
        try:
            raw = self.config.encodings_path.read_bytes()
            if is_encrypted(raw):
                raw = decrypt_encodings(raw)
            self.authorized_users = load_authorized_users_from_bytes(raw)
        except Exception as exc:
            logger.error(f"Rechargement encodings échoué: {exc}")
            return
        self.face_analyzer = FaceAnalyzer(
            authorized_users=self.authorized_users,
            tolerance=self.config.face_tolerance,
            min_face_size=self.config.min_face_size,
            center_threshold=self.config.center_threshold,
            analyze_every_n=self.config.analyze_every_n_frames,
            detection_scale=self.config.detection_scale,
        )
        self.motion_detector.reset()

    # ── Gestion fenêtre ───────────────────────────────────────────────────────

    def _toggle_window(self) -> None:
        if not self._window:
            return
        try:
            self._window.show()
        except Exception:
            pass

    def _on_close_request(self) -> None:
        if self.config.close_protection_enabled:
            if self._window:
                try:
                    self._window.evaluate_js("window._onCloseRequest && window._onCloseRequest()")
                except Exception:
                    pass
        else:
            self._do_close()

    def _do_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self._systray.stop()
        except Exception:
            pass
        try:
            paths.SHUTDOWN_FLAG.write_text("shutdown")
        except Exception:
            pass
        try:
            self.usb_watcher.stop()
        except Exception:
            pass
        try:
            self.poll_watcher.stop()
        except Exception:
            pass
        try:
            self._ssid_monitor.stop()
        except Exception:
            pass
        try:
            self._watchdog.stop()
        except Exception:
            pass
        self.stop_alarm()
        _audit.log_event("APP_STOP", {})
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass

    # ── Point d'entrée ────────────────────────────────────────────────────────

    def run(self) -> None:
        """Lance la fenêtre PyWebView — bloquant jusqu'à fermeture."""
        from src.gui.api import Api
        api = Api(self)

        html_path = Path(__file__).parent / "web" / "index.html"
        url = "file:///" + str(html_path).replace("\\", "/")

        self._window = webview.create_window(
            "PrankGuard",
            url=url,
            js_api=api,
            width=1200,
            height=850,
            min_size=(1000, 700),
            background_color="#0a0a0f",
            frameless=False,
        )

        webview.start(debug=False)

        # Nettoyage après fermeture fenêtre
        if not self._closing:
            self._do_close()
