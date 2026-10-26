from datetime import datetime, timedelta, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

import pytest
from conftest import PASSWORD
from PIL import Image

from tvcontrol.models import DEFAULT_APPS
from tvcontrol.monitor import DeviceError


def image_bytes(color="#365e6b"):
    stream = BytesIO()
    Image.new("RGBA", (900, 600), color).save(stream, "PNG")
    return stream.getvalue()


def test_unauthenticated_and_missing_csrf_are_rejected(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 401
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
        response = client.post("/api/login", json={"password": PASSWORD})
        assert response.status_code == 200
        assert client.put("/api/rule", json={"enabled": True}).status_code == 403
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        assert client.put("/api/rule", json={"enabled": True},
                          headers={"Origin": "https://untrusted.example"}).status_code == 403


def test_default_rule_and_distinct_apps(client):
    result = client.get("/api/status").json()
    assert result["monitor"]["rule"] == {
        "enabled": False, "package": "com.thegate.tv", "minutes": 40, "timezone": "Asia/Bangkok"}
    assert [app["label"] for app in result["apps"]] == [
        "YT", "Netflix", "GodTV", "Monomax", "YouTube"]
    assert len({app["package"] for app in result["apps"]}) == 5


def test_rule_is_configurable_and_validated(client, app):
    assert client.put("/api/rule", json={"enabled": True, "package": "com.netflix.ninja",
                                       "minutes": 25, "timezone": "Asia/Bangkok"}).status_code == 200
    assert app.state.store.rule().minutes == 25
    for payload in [{"minutes": 0}, {"minutes": 2.5}, {"minutes": True},
                    {"timezone": "invalid/zone"}, {"package": "com.missing.app"}]:
        assert client.put("/api/rule", json=payload).status_code == 422


def test_lowering_an_exhausted_limit_requires_confirmation_and_keeps_usage(client, app):
    now = datetime.now(timezone.utc)
    midday = datetime.combine(now.astimezone(ZoneInfo("Asia/Bangkok")).date(),
                              datetime.min.time(), ZoneInfo("Asia/Bangkok")) + timedelta(hours=12)
    app.state.store.add_interval("com.thegate.tv", "Asia/Bangkok",
                                 midday, midday + timedelta(seconds=600))
    response = client.put("/api/rule", json={"enabled": True, "minutes": 5})
    assert response.status_code == 409
    assert client.put("/api/rule", json={"enabled": True, "minutes": 5,
                                       "confirm_exhausted": True}).status_code == 200
    assert app.state.store.usage("com.thegate.tv", "Asia/Bangkok", now) == 600


def test_install_and_recovery_preserve_original_home_and_all_apps(client, device):
    assert client.post("/api/inventory").status_code == 200
    original = set(device.installed)
    assert client.post("/api/install", json={"confirmation": "wrong"}).status_code == 422
    response = client.post("/api/install", json={"confirmation": "INSTALL_LAUNCHER"})
    assert response.status_code == 200
    assert device.current_home == device.original_home
    assert original <= device.installed
    assert client.post("/api/home", json={"target": "clean",
                                         "confirmation": "CHANGE_HOME"}).status_code == 200
    assert client.post("/api/home", json={"target": "original",
                                         "confirmation": "CHANGE_HOME"}).status_code == 200
    assert device.current_home == device.original_home
    assert client.get("/api/baseline").json()["disabled"] == []


def test_native_home_requires_explicit_clean_target_without_compatibility(client, device, monkeypatch):
    assert client.post("/api/inventory").status_code == 200
    selected = []
    select = device.select_home

    def choose(component, baseline, **options):
        selected.append(options)
        return select(component, baseline, **options)

    monkeypatch.setattr(device, "select_home", choose)
    for body in [
        {"target": "original", "true_default": True},
        {"target": "clean", "true_default": True, "allow_home_override": True},
    ]:
        response = client.post("/api/home", json={**body, "confirmation": "CHANGE_HOME"})
        assert response.status_code == 422
    assert selected == []
    response = client.post("/api/home", json={
        "target": "clean", "true_default": True, "confirmation": "CHANGE_HOME"})
    assert response.status_code == 200
    assert response.json()["mode"] == "native_guarded"
    assert selected == [{"true_default": True}]


def test_status_refreshes_cached_inventory_after_automatic_home_failback(
        client, app, device, monkeypatch):
    assert client.post("/api/inventory").status_code == 200
    fallback = {"phase": "fallback", "armed": False, "managed_packages": [],
                "original_home": device.original_home, "reason": "Three launcher crashes.",
                "error": None, "requires_nas_adb": True}
    monkeypatch.setattr(device, "native_home_status", lambda: fallback)
    inventory = device.inventory
    monkeypatch.setattr(device, "inventory", lambda: {**inventory(), "native_home": fallback})
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["native_home"] == fallback
    assert response.json()["inventory"]["native_home"] == fallback
    assert sum(event["kind"] == "home_failback" for event in app.state.store.events()) == 1
    assert client.get("/api/status").status_code == 200
    assert sum(event["kind"] == "home_failback" for event in app.state.store.events()) == 1


@pytest.mark.parametrize(("cached_error", "current_error"), [
    (None, "TV is offline."),
    ("TV is offline.", None),
    ("Previous ADB error.", "TV is offline."),
])
def test_status_home_error_changes_do_not_probe_cached_inventory(
        client, app, device, monkeypatch, cached_error, current_error):
    assert client.post("/api/inventory").status_code == 200
    cached_home = {"phase": "active", "armed": True, "error": cached_error}
    cached = {**app.state.store.get("inventory"), "native_home": cached_home}
    app.state.store.put("inventory", cached)
    live_home = {**cached_home, "error": current_error}
    monkeypatch.setattr(device, "native_home_status", lambda: live_home)

    def offline():
        raise DeviceError("TV is offline.")

    monkeypatch.setattr(device, "inventory", offline)
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["native_home"] == live_home
    assert response.json()["inventory"] == cached
    assert app.state.store.get("inventory") == cached
    assert not any(event["kind"] == "home_failback" for event in app.state.store.events())


@pytest.mark.parametrize("phase", ["restoring", "fallback"])
def test_status_defers_home_inventory_refresh_until_tv_reconnects(
        client, app, device, monkeypatch, phase):
    assert client.post("/api/inventory").status_code == 200
    cached = {**app.state.store.get("inventory"),
              "native_home": {"phase": "active", "armed": True, "error": None}}
    app.state.store.put("inventory", cached)
    live_home = {"phase": phase, "armed": False, "error": "TV is offline.",
                 "reason": "Clean Home was unavailable."}
    monkeypatch.setattr(device, "native_home_status", lambda: live_home)
    inventory = device.inventory

    def offline():
        raise DeviceError("TV is offline.")

    monkeypatch.setattr(device, "inventory", offline)
    app.state.monitor.health = "unavailable"
    app.state.monitor.error = "TV is offline."
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["native_home"] == live_home
    assert response.json()["inventory"] == cached
    assert response.json()["monitor"]["error"] == "TV is offline."
    assert not any(event["kind"] == "home_failback" for event in app.state.store.events())

    app.state.monitor.health, app.state.monitor.error = "online", None
    recovered = {**live_home, "error": None}
    monkeypatch.setattr(device, "native_home_status", lambda: recovered)
    monkeypatch.setattr(device, "inventory", lambda: {**inventory(), "native_home": recovered})
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["inventory"]["native_home"] == recovered
    expected_events = 1 if phase == "fallback" else 0
    assert sum(event["kind"] == "home_failback"
               for event in app.state.store.events()) == expected_events
    assert client.get("/api/status").status_code == 200
    assert sum(event["kind"] == "home_failback"
               for event in app.state.store.events()) == expected_events


def test_explicit_privacy_exceptions_are_visible_and_keep_saved_theme_references(
        client, app, device, monkeypatch):
    assert client.post("/api/inventory").status_code == 200
    browser = next(a for a in device.apps if a["package"] == "com.tcl.browser")
    theme = client.get("/api/status").json()["theme"]
    theme["app_order"].append(browser["component"])
    assert client.put("/api/theme", json=theme).status_code == 200
    baseline = app.state.store.get("baseline")
    baseline["approved_disabled_packages"] = ["com.tcl.browser"]
    app.state.store.put("baseline", baseline)
    original_inventory = device.inventory

    def privacy_inventory():
        current = original_inventory()
        current["enabled"].remove("com.tcl.browser")
        current["disabled"] = ["com.tcl.browser"]
        current["apps"] = [a for a in current["apps"] if a["package"] != "com.tcl.browser"]
        return current

    monkeypatch.setattr(device, "inventory", privacy_inventory)
    assert client.post("/api/inventory").status_code == 200
    status = client.get("/api/status").json()
    assert status["approved_disabled_packages"] == ["com.tcl.browser"]
    assert "com.tcl.browser" in client.get("/api/baseline").json()["enabled"]
    assert client.post("/api/install", json={"confirmation": "INSTALL_LAUNCHER"}).status_code == 200
    assert client.post("/api/theme/apply").status_code == 200
    assert client.post("/api/home", json={"target": "clean",
                                         "confirmation": "CHANGE_HOME"}).status_code == 200


def test_wallpaper_and_youtube_artwork_apply_without_touching_yt(client, device):
    client.post("/api/inventory")
    client.post("/api/install", json={"confirmation": "INSTALL_LAUNCHER"})
    wallpaper = client.post("/api/images/wallpaper",
                            files={"file": ("photo.png", image_bytes(), "image/png")}).json()
    artwork = client.post("/api/images/artwork",
                          files={"file": ("logo.png", image_bytes(), "image/png")}).json()
    theme = client.get("/api/status").json()["theme"]
    theme["wallpaper_file"] = wallpaper["filename"]
    theme["tile_artwork"] = {DEFAULT_APPS[-1]["component"]: artwork["filename"]}
    assert client.put("/api/theme", json=theme).status_code == 200
    assert client.post("/api/theme/apply").status_code == 200
    assert device.theme["tile_artwork"] == theme["tile_artwork"]
    assert DEFAULT_APPS[0]["component"] not in device.theme["tile_artwork"]
    assert device.theme["hidden_apps"] == []
    assert client.get("/api/assets/" + artwork["filename"]).status_code == 200
    theme["tile_artwork"] = {}
    assert client.put("/api/theme", json=theme).status_code == 200
    assert client.post("/api/theme/apply").status_code == 200
    assert device.theme["tile_artwork"] == {}
    assert client.post("/api/theme/undo").status_code == 200
    assert DEFAULT_APPS[-1]["component"] in device.theme["tile_artwork"]


def test_two_consecutive_wallpaper_changes_replace_the_applied_image(client, device):
    assert client.post("/api/inventory").status_code == 200
    assert client.post("/api/install", json={"confirmation": "INSTALL_LAUNCHER"}).status_code == 200
    names = []
    for color in ["#365e6b", "#bc6335"]:
        upload = client.post("/api/images/wallpaper", files={
            "file": ("same-name.png", image_bytes(color), "image/png")})
        assert upload.status_code == 200
        filename = upload.json()["filename"]
        names.append(filename)
        theme = client.get("/api/status").json()["theme"]
        theme["wallpaper_file"] = filename
        assert client.put("/api/theme", json=theme).status_code == 200
        assert client.post("/api/theme/apply").status_code == 200
        state = client.get("/api/status").json()
        assert state["theme"]["wallpaper_file"] == filename
        assert state["applied_theme"]["wallpaper_file"] == filename
        assert device.theme["wallpaper_file"] == filename
        with Image.open(BytesIO(client.get("/api/assets/" + filename).content)) as image:
            expected = Image.new("RGB", (1, 1), color).getpixel((0, 0))
            assert all(abs(a - b) <= 2 for a, b in zip(image.getpixel((100, 100)), expected))
    assert len(set(names)) == 2
    assert client.post("/api/theme/undo").status_code == 200
    assert device.theme["wallpaper_file"] == names[0]


def test_saved_images_survive_restart_and_apply_separately(tmp_path, device):
    from fastapi.testclient import TestClient

    from tvcontrol.api import create_app

    apk = tmp_path / "launcher.apk"
    apk.write_bytes(b"test-apk-not-for-installation")
    theme = None
    assets = {}
    for stage in range(3):
        app = create_app(tmp_path, device, require_login=False, secure_cookie=False,
                         run_monitor=False, apk_path=apk)
        with TestClient(app) as current:
            session = current.get("/api/session")
            assert session.status_code == 200
            current.headers["X-CSRF-Token"] = session.json()["csrf_token"]
            if stage == 0:
                assert current.post("/api/inventory").status_code == 200
                assert current.post("/api/install", json={
                    "confirmation": "INSTALL_LAUNCHER"}).status_code == 200
                theme = current.get("/api/status").json()["theme"]
                for kind in ["wallpaper", "artwork"]:
                    upload = current.post(f"/api/images/{kind}", files={
                        "file": ("photo.png", image_bytes(), "image/png")})
                    assert upload.status_code == 200
                    filename = upload.json()["filename"]
                    assets[filename] = current.get("/api/assets/" + filename).content
                    if kind == "wallpaper":
                        theme["wallpaper_file"] = filename
                    else:
                        theme["tile_artwork"][DEFAULT_APPS[-1]["component"]] = filename
                assert current.put("/api/theme", json=theme).status_code == 200
                assert device.theme is None
            state = current.get("/api/status").json()
            assert state["theme"] == theme
            for filename, payload in assets.items():
                response = current.get("/api/assets/" + filename)
                assert response.status_code == 200
                assert response.content == payload
            if stage < 2:
                assert state["applied_theme"] is None
            if stage == 1:
                assert current.post("/api/theme/apply").status_code == 200
                assert device.theme["wallpaper_file"] == theme["wallpaper_file"]
                assert device.theme["tile_artwork"] == theme["tile_artwork"]
            if stage == 2:
                assert state["applied_theme"] == theme


def test_image_pickers_include_local_save_apply_preview_and_feedback(client):
    from html.parser import HTMLParser

    class ImageControls(HTMLParser):
        def __init__(self):
            super().__init__()
            self.kind = None
            self.controls = {}

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "article":
                self.kind = attrs.get("data-image-kind")
                if self.kind:
                    self.controls[self.kind] = []
            elif self.kind:
                self.controls[self.kind].append((tag, attrs))

        def handle_endtag(self, tag):
            if tag == "article":
                self.kind = None

    parser = ImageControls()
    response = client.get("/")
    assert response.status_code == 200
    parser.feed(response.text)
    assert set(parser.controls) == {"wallpaper", "artwork"}
    for kind, controls in parser.controls.items():
        assert any(tag == "input" and attrs.get("id") == f"{kind}-upload"
                   for tag, attrs in controls)
        assert any(tag == "input" and attrs.get("id") == f"{kind}-upload"
                   and "image/heic" in attrs.get("accept", "")
                   and "image/heif" in attrs.get("accept", "") for tag, attrs in controls)
        for action in ["data-save-theme", "data-apply-theme"]:
            assert any(tag == "button" and action in attrs for tag, attrs in controls)
        assert any(tag == "img" and attrs.get("id") == f"{kind}-thumbnail"
                   for tag, attrs in controls)
        assert any(attrs.get("id") == f"{kind}-feedback" and attrs.get("role") == "status"
                   for _, attrs in controls)
    wallpaper = parser.controls["wallpaper"]
    for identifier in ["wallpaper-crop", "wallpaper-canvas", "crop-zoom", "crop-x",
                       "crop-y", "crop-save", "crop-reset", "crop-cancel"]:
        assert any(attrs.get("id") == identifier for _, attrs in wallpaper)
    assert any(tag == "canvas" and attrs.get("width") == "960"
               and attrs.get("height") == "540" and attrs.get("tabindex") == "0"
               for tag, attrs in wallpaper)
    assert 'src="app.js?v=2.6.25" type="module"' in response.text


def test_failed_theme_activation_can_undo_to_the_last_working_design(client, device, monkeypatch):
    from tvcontrol.monitor import DeviceError

    client.post("/api/inventory")
    client.post("/api/install", json={"confirmation": "INSTALL_LAUNCHER"})
    theme = client.get("/api/status").json()["theme"]
    theme["background_color"] = "#112233"
    assert client.put("/api/theme", json=theme).status_code == 200
    assert client.post("/api/theme/apply").status_code == 200
    theme["background_color"] = "#445566"
    assert client.put("/api/theme", json=theme).status_code == 200
    working_apply = device.apply_theme

    def failed_apply(config, assets, revision):
        working_apply(config, assets, revision)
        raise DeviceError("Theme committed but launcher restart failed")

    monkeypatch.setattr(device, "apply_theme", failed_apply)
    assert client.post("/api/theme/apply").status_code == 503
    monkeypatch.setattr(device, "apply_theme", working_apply)
    assert client.post("/api/theme/undo").status_code == 200
    assert device.theme["background_color"] == "#112233"


def test_bad_upload_paths_and_arbitrary_actions_are_not_accepted(client):
    assert client.post("/api/images/artwork",
                       files={"file": ("bad.png", b"not an image", "image/png")}).status_code == 422
    assert client.post("/api/images/unknown",
                       files={"file": ("bad.png", b"data", "image/png")}).status_code == 422
    theme = client.get("/api/status").json()["theme"]
    theme["wallpaper_file"] = "../private-adb/adbkey"
    assert client.put("/api/theme", json=theme).status_code == 422
    assert client.post("/api/shell", json={"command": "reboot"}).status_code in {404, 405}
    assert client.post("/api/standby", json={"confirmation": "no"}).status_code == 422


def test_real_standby_requires_explicit_confirmation(client, device):
    assert client.post("/api/standby", json={"confirmation": "STANDBY_NOW"}).json() == {
        "standby_requested": True, "confirmed": False}
    assert device.sleeps == 1
    assert client.post("/api/standby", json={"confirmation": "STANDBY_NOW"}).json() == {
        "standby_requested": False, "confirmed": False}
    assert device.sleeps == 1


def test_pairing_cannot_switch_a_baselined_data_volume_to_another_tv(client):
    client.post("/api/inventory")
    assert client.put("/api/target", json={"host": "192.168.1.149"}).status_code == 409
