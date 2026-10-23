"""Serveur HTTP local (127.0.0.1 uniquement) : UI statique, API JSON, état temps réel (SSE), vidéo (MJPEG).

Sécurité : port aléatoire, jeton secret obligatoire pour toute route non statique,
vérification de l'en-tête Host (anti DNS-rebinding), aucune écoute réseau externe.
"""
from __future__ import annotations

import json
import mimetypes
import secrets
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse

import cv2

from . import __version__, paths, winsys
from .log import get

log = get("server")

# Le registre Windows peut associer .js à text/plain : on force les bons types.
for _ext, _type in ((".js", "application/javascript"), (".css", "text/css"), (".svg", "image/svg+xml"),
                    (".html", "text/html"), (".woff2", "font/woff2"), (".ico", "image/x-icon")):
    mimetypes.add_type(_type, _ext)

STREAM_MAX_FPS = 30
JPEG_QUALITY = 72


class Hub:
    """Diffusion de l'état (dernier seulement) et des événements (tous) vers les clients SSE."""

    def __init__(self):
        self._cond = threading.Condition()
        self._state: dict | None = None
        self._state_v = 0
        self._events: list[tuple[int, dict]] = []
        self._ev_seq = 0

    def push_state(self, snap: dict) -> None:
        with self._cond:
            self._state = snap
            self._state_v += 1
            self._cond.notify_all()

    def push_event(self, ev: dict) -> None:
        with self._cond:
            self._ev_seq += 1
            self._events.append((self._ev_seq, ev))
            self._events = self._events[-50:]
            self._cond.notify_all()

    def wait(self, state_v: int, ev_seq: int, timeout: float):
        with self._cond:
            if self._state_v == state_v and self._ev_seq == ev_seq:
                self._cond.wait(timeout)
            evs = [e for e in self._events if e[0] > ev_seq]
            return self._state_v, self._state, self._ev_seq, evs


class FrameEncoder:
    """Encode chaque image une seule fois, quel que soit le nombre de clients vidéo."""

    def __init__(self, camera):
        self.camera = camera
        self._lock = threading.Lock()
        self._seq = -1
        self._jpg = b""
        self.clients = 0

    def get(self, after_seq: int, timeout: float = 1.0) -> tuple[int, bytes]:
        seq, _, frame = self.camera.wait_newer(after_seq, timeout)
        if frame is None or seq == after_seq:
            return after_seq, b""
        with self._lock:
            if seq != self._seq:
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if ok:
                    self._seq, self._jpg = seq, buf.tobytes()
            return self._seq, self._jpg


class Server:
    def __init__(self, app):
        self.app = app
        self.token = secrets.token_urlsafe(24)
        self.hub = Hub()
        self.encoder = FrameEncoder(app.engine.camera)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/#t={self.token}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.25},
                                        name="HTTP", daemon=True)
        self._thread.start()
        log.info("UI servie sur 127.0.0.1:%d", self.port)

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _make_handler(srv: Server):
    app = srv.app
    routes_get: dict[str, Callable] = {}
    routes_post: dict[str, Callable] = {}

    def get_(path):
        def deco(fn):
            routes_get[path] = fn
            return fn
        return deco

    def post_(path):
        def deco(fn):
            routes_post[path] = fn
            return fn
        return deco

    # ── API ──
    @get_("/api/state")
    def _state(h, q):
        return app.engine.snapshot()

    @get_("/api/info")
    def _info(h, q):
        return {"version": __version__, "data_dir": str(paths.HOME), "autostart": winsys.get_autostart(),
                "debug": app.debug}

    @get_("/api/settings")
    def _settings(h, q):
        return app.config.s.to_dict()

    @post_("/api/settings")
    def _settings_post(h, body):
        applied = app.config.update(body or {})
        return {"applied": applied, "settings": app.config.s.to_dict()}

    @get_("/api/events")
    def _events(h, q):
        before = int(q.get("before", ["0"])[0] or 0) or None
        kind = q.get("kind", [""])[0] or None
        return {"events": app.events.recent(int(q.get("limit", ["60"])[0]), before, kind),
                "stats24": app.events.stats(time.time() - 86400)}

    @post_("/api/events/clear")
    def _events_clear(h, body):
        app.events.clear()
        return {"ok": True}

    @get_("/api/profiles")
    def _profiles(h, q):
        return {"profiles": app.gallery.summary()}

    @post_("/api/profiles/delete")
    def _profiles_delete(h, body):
        name = str(body.get("name", ""))
        ok = app.gallery.remove(name)
        app.engine.reload_gallery()
        if ok:
            left = len(app.gallery.people)
            app.events.add("enroll", "warning" if not left else "info", f"Profil « {name} » supprimé",
                           "Plus aucun profil : reconnaissance faciale inactive" if not left else f"{left} profil(s) restant(s)")
        return {"ok": ok}

    @post_("/api/profiles/rename")
    def _profiles_rename(h, body):
        new = str(body.get("new", "")).strip()[:40]
        old = str(body.get("old", ""))
        ok = bool(new) and app.gallery.rename(old, new)
        app.engine.reload_gallery()
        if ok:
            app.events.add("enroll", "info", f"Profil « {old} » renommé en « {new} »")
        return {"ok": ok}

    @post_("/api/profiles/forget-learning")
    def _profiles_forget(h, body):
        name = str(body.get("name", ""))
        ok = app.gallery.forget_adaptive(name)
        app.engine.reload_gallery()
        if ok:
            app.events.add("enroll", "info", f"Vues apprises de « {name} » oubliées")
        return {"ok": ok}

    @post_("/api/enroll/start")
    def _enroll_start(h, body):
        name = str(body.get("name", "")).strip()[:40]
        if not name:
            return {"ok": False, "error": "Nom requis"}
        app.engine.start_enroll(name)
        return {"ok": True}

    @post_("/api/enroll/finish")
    def _enroll_finish(h, body):
        return app.engine.finish_enroll()

    @post_("/api/enroll/cancel")
    def _enroll_cancel(h, body):
        app.engine.cancel_enroll()
        return {"ok": True}

    @post_("/api/arm")
    def _arm(h, body):
        app.engine.set_armed(bool(body.get("armed", True)))
        return {"ok": True}

    @post_("/api/snooze")
    def _snooze(h, body):
        app.engine.snooze(float(body.get("minutes", 15)))
        return {"ok": True}

    @post_("/api/lock")
    def _lock(h, body):
        app.engine.lock_now()
        return {"ok": True}

    @post_("/api/devices/trust")
    def _trust(h, body):
        key = str(body.get("key", "")).strip()[:200]
        if not key:
            return {"ok": False}
        trusted = list(app.config.s.trusted_devices)
        if key not in trusted:
            trusted.append(key)
        changes = {"trusted_devices": trusted}
        name = str(body.get("name", "")).strip()[:200]
        if name:
            changes["device_names"] = {**app.config.s.device_names, key: name}
        app.config.update(changes)
        return {"ok": True}

    @post_("/api/devices/decide")
    def _decide(h, body):
        try:
            pid = int(body.get("id"))
        except (TypeError, ValueError):
            return {"ok": False}
        return {"ok": app.engine.decide_device(pid, str(body.get("decision", "")))}

    @post_("/api/devices/untrust")
    def _untrust(h, body):
        key = str(body.get("key", ""))
        app.config.update({"trusted_devices": [k for k in app.config.s.trusted_devices if k != key]})
        return {"ok": True}

    @post_("/api/window")
    def _window(h, body):
        app.window_action(str(body.get("action", "")))
        return {"ok": True}

    @post_("/api/open-folder")
    def _open_folder(h, body):
        import os
        os.startfile(str(paths.HOME))
        return {"ok": True}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "PrankGuard"
        sys_version = ""

        def log_message(self, fmt, *args):
            pass

        # ── Utilitaires ──
        def _host_ok(self) -> bool:
            host = self.headers.get("Host", "")
            return host in (f"127.0.0.1:{srv.port}", f"localhost:{srv.port}")

        def _authed(self, q) -> bool:
            tok = self.headers.get("X-PG-Token") or q.get("t", [""])[0]
            return secrets.compare_digest(tok.encode("utf-8", "surrogateescape"), srv.token.encode())

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _client_gone(self) -> bool:
            """Vrai si le client a fermé la connexion (lisible + 0 octet en lecture anticipée)."""
            try:
                r, _, _ = select.select([self.connection], [], [], 0)
                return bool(r) and self.connection.recv(1, socket.MSG_PEEK) == b""
            except OSError:
                return True

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        # ── GET ──
        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            p = u.path
            if p in routes_get or p.startswith(("/stream", "/sse", "/media/")):
                if not self._authed(q):
                    return self._send(401, b"unauthorized", "text/plain")
                if p == "/stream.mjpg":
                    return self._stream()
                if p == "/sse":
                    return self._sse()
                if p.startswith("/media/snap/"):
                    f = app.events.snapshot_path(unquote(p[len("/media/snap/"):]))
                    if not f:
                        return self._send(404, b"", "text/plain")
                    return self._send(200, f.read_bytes(), "image/jpeg")
                if p.startswith("/media/face/"):
                    data = app.gallery.thumb(unquote(p[len("/media/face/"):]))
                    if not data:
                        return self._send(404, b"", "text/plain")
                    return self._send(200, data, "image/jpeg")
                try:
                    return self._json(routes_get[p](self, q))
                except Exception as e:
                    log.exception("GET %s", p)
                    return self._json({"error": str(e)}, 500)
            return self._static(p)

        def do_POST(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if not self._host_ok() or not self._authed(q):
                return self._send(401, b"unauthorized", "text/plain")
            fn = routes_post.get(u.path)
            if not fn:
                return self._send(404, b"", "text/plain")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}") if n > 0 else {}
                if not isinstance(body, dict):
                    body = {}
            except ValueError:
                return self._json({"error": "requête invalide"}, 400)
            try:
                return self._json(fn(self, body))
            except Exception as e:
                log.exception("POST %s", u.path)
                return self._json({"error": str(e)}, 500)

        def _static(self, p: str):
            if p == "/":
                p = "/index.html"
            f = (paths.WEB_DIR / p.lstrip("/")).resolve()
            root = paths.WEB_DIR.resolve()
            if root not in f.parents or not f.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            csp = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                   "script-src 'self'; connect-src 'self'")
            return self._send(200, f.read_bytes(), ctype, {"Content-Security-Policy": csp})

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=pgframe")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            srv.encoder.clients += 1
            seq, min_dt, last = -1, 1.0 / STREAM_MAX_FPS, 0.0
            try:
                while not app.stopping:
                    if not app.ui_visible:
                        time.sleep(0.25)   # fenêtre masquée / réduite : aucun encodage
                        if self._client_gone():
                            break
                        continue
                    seq2, jpg = srv.encoder.get(seq)
                    if not jpg:
                        if self._client_gone():
                            break
                        continue
                    seq = seq2
                    wait = last + min_dt - time.time()
                    if wait > 0:
                        time.sleep(wait)
                    last = time.time()
                    self.wfile.write(b"--pgframe\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
            except (ConnectionError, OSError):
                pass
            finally:
                srv.encoder.clients -= 1
                self.close_connection = True

        def _sse(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(b"retry: 1000\n\n")
                self.wfile.write(b"event: state\ndata: " + json.dumps(app.engine.snapshot()).encode() + b"\n\n")
                self.wfile.flush()
                sv, ev = srv.hub._state_v, srv.hub._ev_seq
                last_ping = time.time()
                while not app.stopping:
                    nsv, state, nev, evs = srv.hub.wait(sv, ev, 10.0)
                    out = b""
                    for _, e in evs:
                        out += b"event: event\ndata: " + json.dumps(e, ensure_ascii=False).encode() + b"\n\n"
                    if nsv != sv and state is not None:
                        out += b"event: state\ndata: " + json.dumps(state).encode() + b"\n\n"
                    sv, ev = nsv, nev
                    if not out and time.time() - last_ping > 9:
                        out = b": ping\n\n"
                    if out:
                        last_ping = time.time()
                        self.wfile.write(out)
                        self.wfile.flush()
            except (ConnectionError, OSError):
                pass
            finally:
                self.close_connection = True

    return Handler
