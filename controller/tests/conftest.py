from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tvcontrol.api import create_app
from tvcontrol.models import DEFAULT_APPS, LAUNCHER, Observation, canonical_component

PASSWORD = "parent-test-password-only"


class FakeDevice:
    simulation = True
    home_recovery_error = None

    def __init__(self):
        self.host = "192.168.1.148"
        self.package = "com.thegate.tv"
        self.awake = True
        self.sleeps = 0
        self.sleep_checks = 0
        self.sleep_delay_seconds = 0
        self.now = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)
        self.monotonic = 0.0
        self.apps = [dict(app) for app in DEFAULT_APPS]
        self.apps.append({"label": "Browser", "package": "com.tcl.browser",
                          "component": "com.tcl.browser/com.tcl.browser.MainActivity"})
        self.installed = {a["package"] for a in self.apps} | {
            "android", "com.google.android.apps.tv.launcherx",
        }
        self.original_home = canonical_component(
            "com.google.android.apps.tv.launcherx/.home.HomeActivity")
        self.current_home = self.original_home
        self.theme = None
        self.fail_sleep = False
        self.switch_before_sleep = False

    def observe(self):
        return Observation(at=self.now, monotonic=self.monotonic,
                           package=self.package, awake=self.awake)

    def sleep_if_foreground(self, package, *, valid_from=None, valid_until=None):
        self.sleep_checks += 1
        if self.fail_sleep:
            from tvcontrol.monitor import DeviceError
            raise DeviceError("Simulated ADB failure")
        if not self.awake or self.switch_before_sleep or package != self.package:
            return False
        checked_at = self.now + timedelta(seconds=self.sleep_delay_seconds)
        if ((valid_from is not None and checked_at < valid_from)
                or (valid_until is not None and checked_at >= valid_until)):
            return False
        self.sleeps += 1
        self.awake = False
        return True

    def standby(self):
        return self.sleep_if_foreground(self.package)

    def inventory(self):
        return {"properties": {"ro.software.version_id": "V8-T653T01-LF1V643",
                               "ro.build.version.release": "14"},
                "installed": sorted(self.installed), "enabled": sorted(self.installed),
                "disabled": [], "home": self.current_home, "apps": self.apps}

    def apply_theme(self, config, assets, revision):
        import json
        self.theme = json.loads(config.read_text())
        assert self.theme["hidden_apps"] == []

    def publish_energy(self, snapshot):
        self.energy = snapshot

    def publish_weather(self, snapshot):
        self.weather = snapshot

    def publish_vehicle(self, snapshot):
        self.vehicle = snapshot

    def publish_ha_cards(self, mappings):
        self.ha_cards = mappings
        return True

    def install(self, apk):
        before = self.inventory()
        self.installed.add(LAUNCHER)
        return {"before": before, "after": self.inventory()}

    def native_home_status(self):
        return None

    def select_home(self, component, baseline, *, allow_override=False, true_default=False):
        self.current_home = canonical_component(component)
        current = self.inventory()
        if true_default:
            current["home_mode"] = "native_guarded"
        return current

    def set_host(self, host):
        self.host = host

    def screenshot(self):
        stream = BytesIO()
        Image.new("RGB", (320, 180), "#123456").save(stream, "PNG")
        return stream.getvalue()

    def close(self):
        pass


@pytest.fixture
def device():
    return FakeDevice()


@pytest.fixture
def app(tmp_path, device):
    apk = tmp_path / "launcher.apk"
    apk.write_bytes(b"test-apk-not-for-installation")
    return create_app(tmp_path, device, password=PASSWORD, secure_cookie=False,
                      run_monitor=False, apk_path=apk)


@pytest.fixture
def client(app):
    with TestClient(app) as value:
        response = value.post("/api/login", json={"password": PASSWORD})
        assert response.status_code == 200
        value.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        yield value
