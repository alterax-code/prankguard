"""Serveur HTTP local — test d'intégration sans caméra réelle ni verrouillage de session."""
import http.client
import json
import socket
import time

import numpy as np
import pytest

from prankguard import paths, winsys
from prankguard.config import ConfigStore, Settings
from prankguard.events import EventStore
from prankguard.gallery import Gallery
from prankguard.server import Server

SNAPSHOT_STATE = {"state": "secure", "armed": True, "owner": "alice"}


class FakeCamera:
    """Fournit toujours une « nouvelle » image synthétique ; jamais de vraie webcam."""

    def __init__(self):
        rng = np.random.default_rng(0)
        self.frame = rng.integers(0, 255, (120, 160, 3), dtype=np.uint8)   # bruit -> JPEG volumineux

    def wait_newer(self, seq, timeout):
        time.sleep(0.01)
        return seq + 1, time.time(), self.frame


class FakeEngine:
    def __init__(self):
        self.camera = FakeCamera()
        self.calls = []

    def snapshot(self):
        return dict(SNAPSHOT_STATE)

    def set_armed(self, armed):
        self.calls.append(("set_armed", armed))

    def snooze(self, minutes):
        self.calls.append(("snooze", minutes))

    def lock_now(self):                       # JAMAIS de vrai verrouillage
        self.calls.append(("lock_now",))

    def start_enroll(self, name):
        self.calls.append(("start_enroll", name))

    def finish_enroll(self):
        self.calls.append(("finish_enroll",))
        return {"ok": True, "name": "fake"}

    def cancel_enroll(self):
        self.calls.append(("cancel_enroll",))

    def decide_device(self, prompt_id, decision):
        self.calls.append(("decide", prompt_id, decision))
        return prompt_id == 7

    def reload_gallery(self):
        self.calls.append(("reload_gallery",))


class FakeApp:
    def __init__(self, base):
        self.engine = FakeEngine()
        self.config = ConfigStore(base / "config.json")
        self.events = EventStore(base / "ev.sqlite3", base / "snaps")
        self.gallery = Gallery(base / "faces.bin", encrypt=False)
        self.stopping = False
        self.ui_visible = True
        self.debug = False
        self.windows = []

    def window_action(self, action):
        self.windows.append(action)


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    base = tmp_path_factory.mktemp("server")
    mp = pytest.MonkeyPatch()
    mp.setattr(winsys, "get_autostart", lambda *a, **k: False)    # pas d'accès registre
    app = FakeApp(base)
    server = Server(app)
    server.start()
    server.fake_app = app
    server.base = base
    yield server
    app.stopping = True
    server.stop()
    app.events.close()
    mp.undo()


@pytest.fixture(autouse=True)
def clean_state(srv):
    app = srv.fake_app
    app.config.update(Settings().to_dict())
    app.engine.calls.clear()
    app.windows.clear()
    app.events.clear()
    app.gallery.people.clear()
    yield


def call(srv, method, path, body=None, token=True, host=None, headers=None, raw_body=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    h = dict(headers or {})
    if token is True:
        h["X-PG-Token"] = srv.token
    elif token:
        h["X-PG-Token"] = token
    if host:
        h["Host"] = host
    payload = raw_body
    if body is not None:
        payload = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    try:
        conn.request(method, path, body=payload, headers=h)
        r = conn.getresponse()
        return r.status, r, r.read()
    finally:
        conn.close()


def jget(srv, path, **kw):
    status, r, data = call(srv, "GET", path, **kw)
    assert status == 200, (path, status, data[:200])
    return json.loads(data)


def jpost(srv, path, body=None, **kw):
    status, r, data = call(srv, "POST", path, body=body if body is not None else {}, **kw)
    assert status == 200, (path, status, data[:200])
    return json.loads(data)


def raw_get(srv, path, until, host=None, timeout=5.0):
    """GET brut sur socket : lit jusqu'à ce que `until(buf)` soit vrai (pour les flux infinis)."""
    host = host or f"127.0.0.1:{srv.port}"
    s = socket.create_connection(("127.0.0.1", srv.port), timeout=timeout)
    try:
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        buf = b""
        while not until(buf):
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf
    finally:
        s.close()


# ── Authentification / Host ──────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/api/state", "/api/settings", "/api/info", "/api/events", "/api/profiles",
                                  "/media/snap/x.jpg", "/media/face/alice"])
def test_get_without_token_is_401(srv, path):
    assert call(srv, "GET", path, token=False)[0] == 401


def test_wrong_token_is_401(srv):
    assert call(srv, "GET", "/api/state", token="nope")[0] == 401
    assert call(srv, "GET", "/api/state?t=nope", token=False)[0] == 401


def test_token_accepted_via_query_string(srv):
    status, _, data = call(srv, "GET", f"/api/state?t={srv.token}", token=False)
    assert status == 200 and json.loads(data) == SNAPSHOT_STATE


def test_post_without_token_is_401_and_has_no_effect(srv):
    status, _, _ = call(srv, "POST", "/api/lock", body={}, token=False)
    assert status == 401
    status, _, _ = call(srv, "POST", "/api/settings", body={"stranger_delay": 9.0}, token=False)
    assert status == 401
    assert srv.fake_app.engine.calls == []
    assert srv.fake_app.config.s.stranger_delay == Settings().stranger_delay


def test_bad_host_header_is_403(srv):
    assert call(srv, "GET", "/api/state", host="evil.com")[0] == 403
    assert call(srv, "GET", "/api/state", host=f"127.0.0.1:{srv.port + 1}")[0] == 403
    assert call(srv, "GET", "/api/state", host=f"127.0.0.1.evil.com:{srv.port}")[0] == 403
    assert call(srv, "GET", "/", host="evil.com", token=False)[0] == 403       # statique aussi


def test_bad_host_on_post_is_rejected(srv):
    status, _, _ = call(srv, "POST", "/api/lock", body={}, host="evil.com")
    assert status in (401, 403)
    assert srv.fake_app.engine.calls == []


def test_localhost_host_header_is_accepted(srv):
    assert call(srv, "GET", "/api/state", host=f"localhost:{srv.port}")[0] == 200


def test_url_contains_port_and_token(srv):
    assert srv.url == f"http://127.0.0.1:{srv.port}/#t={srv.token}"
    assert len(srv.token) >= 24


def test_non_ascii_token_is_401_not_a_dropped_connection(srv):
    status, _, _ = call(srv, "GET", "/api/state?t=%C3%A9", token=False)
    assert status == 401


# ── API JSON ─────────────────────────────────────────────────────────────────

def test_state(srv):
    status, r, data = call(srv, "GET", "/api/state")
    assert status == 200 and r.getheader("Content-Type").startswith("application/json")
    assert json.loads(data) == SNAPSHOT_STATE
    assert r.getheader("Cache-Control") == "no-store"
    assert r.getheader("X-Content-Type-Options") == "nosniff"


def test_info(srv):
    info = jget(srv, "/api/info")
    assert info["debug"] is False and info["autostart"] is False
    assert info["data_dir"] == str(paths.HOME) and info["version"]


def test_settings_get_returns_all_defaults(srv):
    assert jget(srv, "/api/settings") == Settings().to_dict()


def test_settings_post_applies_and_clamps(srv):
    res = jpost(srv, "/api/settings", {"match_threshold": 9, "stranger_delay": 2.0, "surfer_action": "bogus",
                                       "anti_spoof": "yes", "nonexistent": 1})
    assert res["applied"] == {"match_threshold": 0.70, "stranger_delay": 2.0}
    assert res["settings"]["match_threshold"] == 0.70
    assert res["settings"]["surfer_action"] == Settings().surfer_action
    assert res["settings"]["anti_spoof"] is True
    assert jget(srv, "/api/settings")["stranger_delay"] == 2.0
    assert srv.fake_app.config.s.match_threshold == 0.70
    # persisté sur disque
    assert ConfigStore(srv.fake_app.config.path).s.stranger_delay == 2.0


def test_settings_post_empty_body_is_noop(srv):
    assert jpost(srv, "/api/settings", {})["applied"] == {}
    status, _, data = call(srv, "POST", "/api/settings")           # sans corps du tout
    assert status == 200 and json.loads(data)["applied"] == {}


def test_post_non_object_json_is_treated_as_empty(srv):
    status, _, data = call(srv, "POST", "/api/settings", raw_body=b"[1, 2, 3]",
                           headers={"Content-Length": "9"})
    assert status == 200 and json.loads(data)["applied"] == {}


def test_post_invalid_json_returns_error_not_crash(srv):
    status, _, data = call(srv, "POST", "/api/settings", raw_body=b"{not json")
    assert status in (400, 500) and "error" in json.loads(data)
    assert call(srv, "GET", "/api/state")[0] == 200                # le serveur reste vivant


def test_unknown_post_route_is_404(srv):
    assert call(srv, "POST", "/api/nope", body={})[0] == 404


def test_engine_control_routes(srv):
    eng = srv.fake_app.engine
    jpost(srv, "/api/arm", {"armed": False})
    jpost(srv, "/api/arm", {"armed": True})
    jpost(srv, "/api/snooze", {"minutes": 5})
    jpost(srv, "/api/lock")
    jpost(srv, "/api/enroll/cancel")
    assert jpost(srv, "/api/enroll/finish") == {"ok": True, "name": "fake"}
    assert eng.calls == [("set_armed", False), ("set_armed", True), ("snooze", 5.0), ("lock_now",),
                         ("cancel_enroll",), ("finish_enroll",)]


def test_enroll_start_requires_name_and_trims(srv):
    eng = srv.fake_app.engine
    assert jpost(srv, "/api/enroll/start", {"name": "   "}) == {"ok": False, "error": "Nom requis"}
    assert jpost(srv, "/api/enroll/start", {}) == {"ok": False, "error": "Nom requis"}
    assert eng.calls == []
    assert jpost(srv, "/api/enroll/start", {"name": "  Alice " + "x" * 100})["ok"] is True
    (name,) = [c[1] for c in eng.calls if c[0] == "start_enroll"]
    assert name.startswith("Alice x") and len(name) == 40


def test_window_action(srv):
    jpost(srv, "/api/window", {"action": "minimize"})
    assert srv.fake_app.windows == ["minimize"]


def test_trust_and_untrust_devices(srv):
    jpost(srv, "/api/devices/trust", {"key": "046D:C52B"})
    jpost(srv, "/api/devices/trust", {"key": "046D:C52B"})          # idempotent
    jpost(srv, "/api/devices/trust", {"key": "1234:ABCD"})
    assert jpost(srv, "/api/devices/trust", {"key": "  "}) == {"ok": False}
    assert jget(srv, "/api/settings")["trusted_devices"] == ["046D:C52B", "1234:ABCD"]
    jpost(srv, "/api/devices/untrust", {"key": "046D:C52B"})
    assert jget(srv, "/api/settings")["trusted_devices"] == ["1234:ABCD"]


def test_events_api_filter_and_stats(srv):
    ev = srv.fake_app.events
    ev.add("lock", "critical", "Verrouillé", "Inconnu")
    ev.add("device", "warning", "USB")
    res = jget(srv, "/api/events")
    assert [e["title"] for e in res["events"]] == ["USB", "Verrouillé"]
    assert res["stats24"] == {"lock": 1, "device": 1}
    only = jget(srv, "/api/events?kind=lock&limit=10")["events"]
    assert [e["kind"] for e in only] == ["lock"]
    before = res["events"][0]["id"]
    assert [e["title"] for e in jget(srv, f"/api/events?before={before}")["events"]] == ["Verrouillé"]
    jpost(srv, "/api/events/clear")
    assert jget(srv, "/api/events")["events"] == []


def test_profiles_api_and_face_thumbnails(srv):
    g = srv.fake_app.gallery
    emb = np.eye(128, dtype=np.float32)[:5]
    g.upsert("Alice Smith", emb, b"\xff\xd8thumb\xff\xd9")
    assert jget(srv, "/api/profiles")["profiles"][0]["name"] == "Alice Smith"
    status, r, data = call(srv, "GET", "/media/face/Alice%20Smith")
    assert status == 200 and r.getheader("Content-Type") == "image/jpeg" and data.startswith(b"\xff\xd8")
    assert call(srv, "GET", "/media/face/ghost")[0] == 404

    assert jpost(srv, "/api/profiles/rename", {"old": "Alice Smith", "new": "  Bob "})["ok"] is True
    assert jpost(srv, "/api/profiles/rename", {"old": "ghost", "new": "Z"})["ok"] is False
    assert jpost(srv, "/api/profiles/rename", {"old": "Bob", "new": "  "})["ok"] is False
    assert [p["name"] for p in jget(srv, "/api/profiles")["profiles"]] == ["Bob"]
    assert jpost(srv, "/api/profiles/delete", {"name": "Bob"})["ok"] is True
    assert jpost(srv, "/api/profiles/delete", {"name": "Bob"})["ok"] is False
    assert jget(srv, "/api/profiles")["profiles"] == []
    assert srv.fake_app.engine.calls.count(("reload_gallery",)) == 5   # 3 rename + 2 delete


# ── Statique ─────────────────────────────────────────────────────────────────

def test_index_served_without_token_with_csp(srv):
    status, r, data = call(srv, "GET", "/", token=False)
    assert status == 200 and b"<html" in data.lower()
    assert r.getheader("Content-Type") == "text/html; charset=utf-8"
    csp = r.getheader("Content-Security-Policy")
    assert "default-src 'self'" in csp and "script-src 'self'" in csp
    assert r.getheader("X-Content-Type-Options") == "nosniff"
    assert call(srv, "GET", "/index.html", token=False)[2] == data


@pytest.mark.parametrize("path,ctype", [("/app.js", "application/javascript"), ("/app.css", "text/css")])
def test_static_content_types(srv, path, ctype):
    status, r, data = call(srv, path=path, method="GET", token=False)
    assert status == 200 and data
    assert r.getheader("Content-Type").startswith(ctype)
    assert r.getheader("Content-Security-Policy")


def test_static_missing_file_404(srv):
    assert call(srv, "GET", "/nope.js", token=False)[0] == 404


@pytest.mark.parametrize("path", [
    "/../config.py", "/../../pyproject.toml", "/%2e%2e/config.py", "/..%2fconfig.py", "/..%5cconfig.py",
    "/..\\config.py", "/web/../../config.py", "/C:/Windows/win.ini", "/../config.json",
])
def test_static_path_traversal_is_404(srv, path):
    status, _, data = call(srv, "GET", path, token=False)
    assert status == 404
    assert b"ConfigStore" not in data


def test_snapshot_media_served_and_traversal_blocked(srv):
    import cv2
    frame = np.full((32, 48, 3), 90, np.uint8)
    ev = srv.fake_app.events.add("lock", "critical", "photo", frame=frame)
    status, r, data = call(srv, "GET", f"/media/snap/{ev['snapshot']}")
    assert status == 200 and r.getheader("Content-Type") == "image/jpeg" and data[:2] == b"\xff\xd8"
    assert cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR).shape == (32, 48, 3)

    secret = srv.base / "x.jpg"                                     # hors du dossier snapshots
    secret.write_bytes(b"TOPSECRET")
    for p in ("/media/snap/../x", "/media/snap/../x.jpg", "/media/snap/%2e%2e%2fx.jpg",
              "/media/snap/..%5cx.jpg", "/media/snap/missing.jpg", "/media/snap/"):
        status, _, data = call(srv, "GET", p)
        assert status == 404 and b"TOPSECRET" not in data, p


# ── Flux ─────────────────────────────────────────────────────────────────────

def test_mjpeg_requires_token(srv):
    buf = raw_get(srv, "/stream.mjpg", until=lambda b: b"\r\n\r\n" in b)
    assert buf.startswith(b"HTTP/1.1 401")


def test_mjpeg_stream_yields_multipart_jpeg(srv):
    def enough(buf):
        return b"\r\n\r\n" in buf and len(buf.split(b"\r\n\r\n", 1)[1]) >= 4096

    buf = raw_get(srv, f"/stream.mjpg?t={srv.token}", until=enough)
    head, body = buf.split(b"\r\n\r\n", 1)
    assert head.startswith(b"HTTP/1.1 200")
    assert b"multipart/x-mixed-replace; boundary=pgframe" in head
    assert body.startswith(b"--pgframe\r\nContent-Type: image/jpeg\r\nContent-Length: ")
    jpeg_start = body.index(b"\r\n\r\n") + 4
    assert body[jpeg_start:jpeg_start + 2] == b"\xff\xd8"


def test_mjpeg_client_counter_returns_to_zero_after_disconnect(srv):
    raw_get(srv, f"/stream.mjpg?t={srv.token}", until=lambda b: b"--pgframe" in b)
    deadline = time.time() + 5
    while srv.encoder.clients and time.time() < deadline:
        time.sleep(0.05)
    assert srv.encoder.clients == 0


def test_sse_requires_token(srv):
    assert raw_get(srv, "/sse", until=lambda b: b"\r\n\r\n" in b).startswith(b"HTTP/1.1 401")


def test_sse_first_event_is_state_then_receives_pushes(srv):
    host = f"127.0.0.1:{srv.port}"
    s = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
    try:
        s.sendall(f"GET /sse?t={srv.token} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        buf = b""
        while b"event: state" not in buf:
            chunk = s.recv(4096)
            assert chunk, buf
            buf += chunk
        head, body = buf.split(b"\r\n\r\n", 1)
        assert head.startswith(b"HTTP/1.1 200") and b"text/event-stream" in head
        assert body.find(b"event:") == body.find(b"event: state") >= 0       # 1er événement = state
        assert b'"owner": "alice"' in body

        # événements poussés ensuite (on réessaie : le handler peut ne pas être encore en attente)
        deadline, got = time.time() + 5, b""
        i = 0
        while b"event: event" not in got and time.time() < deadline:
            i += 1
            srv.hub.push_event({"id": i, "title": "Verrouillé é"})
            s.settimeout(0.3)
            try:
                got += s.recv(4096)
            except socket.timeout:
                pass
        assert b"event: event" in got and "Verrouillé é".encode() in got
        srv.hub.push_state({"state": "alert"})
        deadline = time.time() + 5
        while b'"state": "alert"' not in got and time.time() < deadline:
            try:
                got += s.recv(4096)
            except socket.timeout:
                pass
        assert b'"state": "alert"' in got
    finally:
        s.close()


def test_device_decision_is_forwarded(srv):
    assert jpost(srv, "/api/devices/decide", {"id": 7, "decision": "always"}) == {"ok": True}
    assert jpost(srv, "/api/devices/decide", {"id": 8, "decision": "once"}) == {"ok": False}
    assert jpost(srv, "/api/devices/decide", {"id": "x", "decision": "once"}) == {"ok": False}
    assert ("decide", 7, "always") in srv.fake_app.engine.calls


def test_trust_from_journal_keeps_device_name(srv):
    jpost(srv, "/api/devices/trust", {"key": "0781:5581", "name": "SanDisk Ultra"})
    assert jget(srv, "/api/settings")["device_names"]["0781:5581"] == "SanDisk Ultra"


def test_forget_learning_endpoint(srv):
    import numpy as np
    g = srv.fake_app.gallery
    g.upsert("zoe", np.eye(128, dtype=np.float32)[:3], b"")
    g.add_adaptive("zoe", np.eye(128, dtype=np.float32)[5])
    assert jpost(srv, "/api/profiles/forget-learning", {"name": "zoe"}) == {"ok": True}
    assert next(p for p in jget(srv, "/api/profiles")["profiles"] if p["name"] == "zoe")["learned"] == 0
    assert jpost(srv, "/api/profiles/forget-learning", {"name": "inconnu"}) == {"ok": False}
