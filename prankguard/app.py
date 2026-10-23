"""Assemblage : réglages, galerie, journal, moteur, serveur local, systray et fenêtre WebView2."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request

from . import APP_NAME, __version__, paths, winsys
from .config import ConfigStore
from .engine import Engine
from .events import EventStore
from .gallery import Gallery
from .log import get
from .server import Server
from .tray import Tray

log = get("app")


class PrankGuardApp:
    def __init__(self, background: bool = False, headless: bool = False, debug: bool = False, dry_run: bool = False):
        self.background = background
        self.headless = headless
        self.debug = debug
        self.stopping = False
        self.window = None
        self.prompt_window = None
        self._prompt_lock = threading.Lock()
        self.ui_visible = True
        paths.ensure_dirs()
        self.config = ConfigStore()
        self.gallery = Gallery()
        self.events = EventStore()
        self.engine = Engine(self.config, self.gallery, self.events, dry_run=dry_run)
        self.server = Server(self)
        self.tray = None if headless else Tray(self)
        self._quit_evt = threading.Event()

        self.engine.on_change = self._on_engine_change
        self.engine.notify = self._notify
        self.engine.on_device_prompt = self._on_device_prompt
        self.events.subscribe(self.server.hub.push_event)
        self.engine.watcher.on_endsession = self._on_endsession
        self._quit_done = threading.Event()

    # ── Liaisons ──
    def _on_engine_change(self, snap: dict) -> None:
        self.server.hub.push_state(snap)
        if self.tray:
            self.tray.set_state(snap["state"])

    def _notify(self, title: str, msg: str) -> None:
        if self.tray:
            self.tray.notify(title, msg)

    def _on_device_prompt(self, info: dict | None) -> None:
        """Petite fenêtre au premier plan pour approuver un branchement (même si l'UI est masquée)."""
        if self.headless or self.window is None or self.stopping:
            return              # sans interface : pas de réponse possible, le délai verrouillera
        import webview
        with self._prompt_lock:
            if info is None:
                w, self.prompt_window = self.prompt_window, None
                if w:
                    try:
                        w.destroy()
                    except Exception:
                        log.exception("Fermeture de la fenêtre de décision")
                return
            if self.prompt_window is not None:
                return          # déjà ouverte : elle se met à jour par SSE
            url = f"http://127.0.0.1:{self.server.port}/prompt.html#t={self.server.token}"
            self.prompt_window = webview.create_window(
                "PrankGuard — nouveau périphérique", url, width=480, height=340, resizable=False,
                frameless=True, easy_drag=True, on_top=True, focus=True, background_color="#0b0e14")
        if self.tray:
            self.tray.notify("Nouveau périphérique", "Autorisez-le ou refusez-le dans la fenêtre PrankGuard.")

    # ── Fenêtre ──
    def show_window(self) -> None:
        if self.window:
            try:
                self.window.show()
                self.window.restore()
                self.ui_visible = True
            except Exception:
                pass

    def window_action(self, action: str) -> None:
        if not self.window:
            return
        if action == "show":
            self.show_window()
        elif action == "hide":
            self.ui_visible = False
            self.window.hide()
        elif action == "minimize":
            self.ui_visible = False
            self.window.minimize()
        elif action == "quit":
            threading.Thread(target=self.quit, daemon=True).start()

    def _on_closing(self):
        if self.stopping:
            return True
        self.ui_visible = False
        self.engine.cancel_enroll()  # un enrôlement ne doit pas survivre à la fenêtre
        self.window.hide()          # fermer = réduire dans la zone de notification
        if not getattr(self, "_hint_shown", False):
            self._hint_shown = True
            self._notify(APP_NAME, "PrankGuard continue de vous protéger en arrière-plan.")
        return False

    # ── Cycle de vie ──
    def run(self) -> None:
        self._write_instance_file()
        self.server.start()
        self.engine.start()
        if self.tray:
            self.tray.start()
        if self.headless:
            log.info("Mode sans interface : %s", self.server.url)
            try:
                while not self._quit_evt.wait(0.5):
                    pass
            except KeyboardInterrupt:
                pass
            self.quit()
            return

        import webview
        s = self.config.s
        hidden = self.background and s.onboarding_done and s.start_minimized
        self.window = webview.create_window(
            APP_NAME, self.server.url, width=1240, height=820, min_size=(980, 660),
            background_color="#0b0e14", hidden=hidden, text_select=False)
        self.ui_visible = not hidden
        self.window.events.closing += self._on_closing
        self.window.events.minimized += lambda: setattr(self, "ui_visible", False)
        self.window.events.restored += lambda: setattr(self, "ui_visible", True)
        storage = str(paths.HOME / "webview")
        webview.start(gui="edgechromium", debug=self.debug, private_mode=True, storage_path=storage)
        self.quit()

    def _on_endsession(self) -> None:
        if not self.stopping:
            threading.Thread(target=self.quit, name="EndSession", daemon=True).start()

    def quit(self) -> None:
        if self.stopping:
            self._quit_done.wait(6)   # un second appel attend la fin du premier
            return
        self.stopping = True
        log.info("Arrêt de PrankGuard")
        steps = [
            lambda: self.events.add("system", "info", "PrankGuard arrêté"),
            self.engine.stop,
            self.server.stop,
            lambda: self.tray and self.tray.stop(),
            lambda: self.window and self.window.destroy(),
            lambda: paths.INSTANCE_FILE.unlink(missing_ok=True),
            self.events.close,
        ]
        for step in steps:   # chaque étape est isolée : une erreur n'empêche pas les suivantes
            try:
                step()
            except Exception:
                log.exception("Erreur pendant l'arrêt")
        self._quit_evt.set()
        self._quit_done.set()

    def _write_instance_file(self) -> None:
        paths.INSTANCE_FILE.write_text(json.dumps({"pid": os.getpid(), "port": self.server.port,
                                                   "token": self.server.token, "version": __version__}))


def signal_existing_instance() -> bool:
    """Demande à l'instance déjà lancée d'afficher sa fenêtre."""
    try:
        info = json.loads(paths.INSTANCE_FILE.read_text())
        req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/window",
                                     data=b'{"action":"show"}', method="POST",
                                     headers={"X-PG-Token": info["token"], "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2).read()
        return True
    except Exception:
        return False
