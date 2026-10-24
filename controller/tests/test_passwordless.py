import hashlib
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from tvcontrol.api import create_app
from tvcontrol.auth import Auth
from tvcontrol.store import Store


def test_lan_mode_opens_without_password_but_keeps_csrf(tmp_path, device):
    app = create_app(tmp_path, device, require_login=False, secure_cookie=False, run_monitor=False)
    with TestClient(app) as client:
        bootstrap = client.get("/api/session")
        assert bootstrap.status_code == 200
        assert bootstrap.json()["login_required"] is False
        assert client.get("/api/status").json()["login_required"] is False
        assert client.put("/api/rule", json={"minutes": 45}).status_code == 403
        client.headers["X-CSRF-Token"] = bootstrap.json()["csrf_token"]
        assert client.put("/api/rule", json={"minutes": 45}).status_code == 200
        assert client.put("/api/rule", json={"minutes": 45},
                          headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert client.post("/api/login", json={"password": "unused"}).status_code == 404
        assert not (tmp_path / "initial-admin-password.txt").exists()
        assert not (tmp_path / "admin-auth.json").exists()


def test_passwordless_session_can_renew_without_sign_in(tmp_path, device):
    app = create_app(tmp_path, device, require_login=False, secure_cookie=False, run_monitor=False)
    with TestClient(app) as client:
        first = client.get("/api/session").json()
        with app.state.store.lock, app.state.store.db:
            app.state.store.db.execute("UPDATE sessions SET expires=0")
        assert client.get("/api/status").status_code == 401
        second = client.get("/api/session").json()
        assert not second["login_required"]
        assert first["csrf_token"] != second["csrf_token"]
        assert client.get("/api/status").status_code == 200


def test_guest_cookie_cannot_become_authenticated_if_passwords_are_reenabled(tmp_path):
    store = Store(tmp_path / "mode.sqlite3")
    try:
        protected = Auth(tmp_path, store, "a-private-parent-password", required=True)
        guest_mode = Auth(tmp_path, store, required=False)
        token, _ = guest_mode.new_session(authenticated=False)
        assert guest_mode.session(token) is not None
        assert protected.session(token) is None
    finally:
        store.close()


def test_version_one_sessions_migrate_without_losing_existing_data(tmp_path):
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    token_hash = hashlib.sha256(b"old-authenticated-session").hexdigest()
    old.executescript("""
        CREATE TABLE sessions(token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL);
        PRAGMA user_version=1;
    """)
    old.execute("INSERT INTO sessions VALUES (?,?,?)", (token_hash, "old-csrf", time.time() + 600))
    old.commit()
    old.close()
    store = Store(path)
    try:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert store.session(token_hash, time.time())["authenticated"] == 1
        assert (tmp_path / "controller-before-passwordless.sqlite3").is_file()
    finally:
        store.close()


def test_invalid_auth_flag_cannot_silently_disable_passwords(tmp_path, device, monkeypatch):
    monkeypatch.setenv("TVCONTROL_REQUIRE_LOGIN", "tru")
    with pytest.raises(ValueError, match="must be true or false"):
        create_app(tmp_path, device, run_monitor=False)


def test_multiple_ha_frame_origins_preserve_secure_cookies_and_same_origin_csrf(
        tmp_path, device, monkeypatch):
    origin = "https://tcl.nitarnhome.synology.me"
    monkeypatch.setenv("TVCONTROL_PUBLIC_ORIGIN", origin)
    monkeypatch.setenv("TVCONTROL_FRAME_ORIGIN", "https://home.nitarnhome.synology.me/")
    monkeypatch.setenv("TVCONTROL_FRAME_ORIGINS",
                       "https://nitarnhome.synology.me, https://home.nitarnhome.synology.me")
    app = create_app(tmp_path, device, require_login=False, secure_cookie=True, run_monitor=False)
    with TestClient(app, base_url=origin) as client:
        response = client.get("/")
        assert "worker-src 'self' blob:;" in response.headers["Content-Security-Policy"]
        assert "script-src 'self';" in response.headers["Content-Security-Policy"]
        assert "'unsafe-eval'" not in response.headers["Content-Security-Policy"]
        ancestors = response.headers["Content-Security-Policy"].split("frame-ancestors ")[1]
        assert ancestors == (
            "'self' https://home.nitarnhome.synology.me https://nitarnhome.synology.me")
        bootstrap = client.get("/api/session", headers={"Origin": origin})
        cookie = bootstrap.headers["Set-Cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
        client.headers["X-CSRF-Token"] = bootstrap.json()["csrf_token"]
        assert client.put("/api/rule", json={"minutes": 45},
                          headers={"Origin": origin}).status_code == 200
        for untrusted_origin in ["https://nitarnhome.synology.me",
                                 "https://home.nitarnhome.synology.me",
                                 "https://attacker.example"]:
            assert client.put("/api/rule", json={"minutes": 50},
                              headers={"Origin": untrusted_origin}).status_code == 403
        assert app.state.store.rule().minutes == 45


@pytest.mark.parametrize("origin", [
    "http://home.example", "https://*.example", "https://home.example/path",
    "https://user:password@home.example", "https://home.example?query=yes",
    "https://home.example#fragment", "https://home.example;other",
    "https://home.example 'unsafe-inline'", "https://home.example\\evil",
    "https://home.example:bad",
])
def test_frame_allowlist_rejects_non_origin_values(tmp_path, device, monkeypatch, origin):
    monkeypatch.setenv("TVCONTROL_FRAME_ORIGINS", origin)
    with pytest.raises(ValueError, match="exact HTTPS origins"):
        create_app(tmp_path, device, require_login=False, run_monitor=False)
