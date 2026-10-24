from datetime import datetime, timezone
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tvcontrol.api import create_app
from tvcontrol.models import DEFAULT_APPS
from tvcontrol.store import Store

START = datetime(2026, 10, 20, 2, tzinfo=timezone.utc)


def bootstrap(client):
    response = client.get("/api/session")
    assert response.status_code == 200
    assert response.json()["login_required"] is False
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


@pytest.fixture
def sandbox_app(tmp_path):
    return create_app(tmp_path, sandbox=True, sandbox_start=START, run_monitor=False)


@pytest.fixture
def sandbox_client(sandbox_app):
    with TestClient(sandbox_app) as client:
        bootstrap(client)
        yield client


def test_sandbox_never_constructs_a_real_adb_device(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Sandbox attempted real TV access")

    monkeypatch.setattr("tvcontrol.api.Device", forbidden)
    app = create_app(tmp_path, sandbox=True, run_monitor=False)
    with TestClient(app) as client:
        bootstrap(client)
        result = client.get("/api/status").json()
        assert result["simulation"]
        assert result["sandbox"]["real_device_access"] is False
        assert result["host"] == "sandbox"
        assert client.put("/api/target", json={"host": "192.168.1.148"}).status_code == 409


def test_exact_2399_and_2400_second_boundary_through_the_api(sandbox_client):
    assert sandbox_client.put("/api/rule", json={"enabled": True}).status_code == 200
    before = sandbox_client.post("/api/sandbox/advance", json={"seconds": 2399}).json()
    assert before["monitor"]["used_seconds"] == 2399
    assert before["sandbox"]["awake"]
    assert before["sandbox"]["standby_requests"] == 0
    after = sandbox_client.post("/api/sandbox/advance", json={"seconds": 1}).json()
    assert after["monitor"]["used_seconds"] == 2400
    assert after["sandbox"]["awake"] is False
    assert after["sandbox"]["standby_requests"] == 1
    assert after["monitor"]["standby"] == "confirmed"
    reopened = sandbox_client.put("/api/sandbox/state", json={"awake": True}).json()
    assert reopened["sandbox"]["awake"] is False
    assert reopened["sandbox"]["standby_requests"] == 2


def test_foreground_changes_and_offline_time_do_not_reset_or_inflate_usage(sandbox_client):
    sandbox_client.put("/api/rule", json={"enabled": True})
    sandbox_client.post("/api/sandbox/advance", json={"seconds": 30})
    sandbox_client.put("/api/sandbox/state", json={"package": "com.netflix.ninja"})
    result = sandbox_client.post("/api/sandbox/advance", json={"seconds": 60}).json()
    assert result["monitor"]["used_seconds"] == 30
    sandbox_client.put("/api/sandbox/state", json={"package": "com.thegate.tv", "online": False})
    offline = sandbox_client.post("/api/sandbox/advance", json={"seconds": 120}).json()
    assert offline["monitor"]["health"] == "unavailable"
    assert offline["monitor"]["used_seconds"] == 30
    sandbox_client.put("/api/sandbox/state", json={"online": True})
    result = sandbox_client.post("/api/sandbox/advance", json={"seconds": 15}).json()
    assert result["monitor"]["used_seconds"] == 45


def test_skipping_to_a_new_day_uses_the_simulated_clock_in_all_status_and_rule_paths(
        sandbox_client):
    sandbox_client.put("/api/rule", json={"enabled": True, "minutes": 1})
    sandbox_client.post("/api/sandbox/advance", json={"seconds": 60})
    exhausted = sandbox_client.get("/api/status").json()
    assert exhausted["monitor"]["used_seconds"] == 60
    next_day = sandbox_client.post("/api/sandbox/jump", json={"target": "next_day"}).json()
    assert next_day["monitor"]["used_seconds"] == 0
    assert next_day["monitor"]["remaining_seconds"] == 60
    assert sandbox_client.put("/api/rule", json={"enabled": True, "minutes": 1}).status_code == 200
    sandbox_client.put("/api/sandbox/state", json={"awake": True})
    assert sandbox_client.get("/api/status").json()["sandbox"]["awake"]


def test_intervals_across_midnight_are_split_in_the_sandbox(sandbox_client):
    sandbox_client.put("/api/rule", json={"enabled": True})
    sandbox_client.post("/api/sandbox/jump", json={"target": "before_midnight"})
    before = sandbox_client.post("/api/sandbox/advance", json={"seconds": 5}).json()
    assert before["monitor"]["used_seconds"] == 5
    after = sandbox_client.post("/api/sandbox/advance", json={"seconds": 10}).json()
    assert after["monitor"]["used_seconds"] == 5
    assert after["sandbox"]["awake"]


def test_sandbox_install_home_and_artwork_are_self_contained(sandbox_client):
    original = sandbox_client.get("/api/status").json()["inventory"]
    assert sandbox_client.post("/api/install",
                               json={"confirmation": "INSTALL_LAUNCHER"}).status_code == 200
    theme = sandbox_client.get("/api/status").json()["theme"]
    image = BytesIO()
    Image.new("RGB", (120, 120), "#58987a").save(image, "PNG")
    uploaded = sandbox_client.post("/api/images/artwork",
                                    files={"file": ("logo.png", image.getvalue(), "image/png")})
    theme["tile_artwork"] = {DEFAULT_APPS[-1]["component"]: uploaded.json()["filename"]}
    assert sandbox_client.put("/api/theme", json=theme).status_code == 200
    assert sandbox_client.post("/api/theme/apply").status_code == 200
    assert sandbox_client.post("/api/home", json={"target": "clean",
                                                 "confirmation": "CHANGE_HOME"}).status_code == 200
    assert sandbox_client.post("/api/home", json={"target": "original",
                                                 "confirmation": "CHANGE_HOME"}).status_code == 200
    final = sandbox_client.get("/api/status").json()
    assert set(original["installed"]) <= set(final["inventory"]["installed"])
    assert final["inventory"]["disabled"] == []
    screenshot = sandbox_client.get("/api/screenshot")
    assert screenshot.status_code == 200
    with Image.open(BytesIO(screenshot.content)) as result:
        assert result.size == (1280, 720)


def test_sandbox_home_keeps_the_photo_clear_and_energy_at_thirty_percent(
        sandbox_client, sandbox_app):
    sandbox_app.state.device.publish_energy({
        "status": "simulated", "metrics": {"solar_w": 1000, "home_w": 800,
        "grid_w": 0, "battery_w": -200, "battery_soc": 50}})
    response = sandbox_client.get("/api/screenshot")
    assert response.status_code == 200
    with Image.open(BytesIO(response.content)) as image:
        assert image.getpixel((600, 250)) == (11, 16, 32)
        assert image.getpixel((80, 390)) == (33, 54, 70)
        assert image.getpixel((54 + 384 - 1, 580)) == (32, 54, 60)
        assert image.getpixel((54 + 384 + 1, 580)) == (11, 16, 32)


def test_clock_state_rules_and_usage_survive_a_sandbox_restart(tmp_path):
    first = create_app(tmp_path, sandbox=True, sandbox_start=START, run_monitor=False)
    with TestClient(first) as client:
        bootstrap(client)
        client.put("/api/rule", json={"enabled": True})
        previous = client.post("/api/sandbox/advance", json={"seconds": 15}).json()
    second = create_app(tmp_path, sandbox=True, run_monitor=False)
    with TestClient(second) as client:
        bootstrap(client)
        restored = client.get("/api/status").json()
        assert restored["sandbox"]["at"] == previous["sandbox"]["at"]
        assert restored["monitor"]["used_seconds"] == 15
        assert restored["monitor"]["rule"]["enabled"]
        result = client.post("/api/sandbox/advance", json={"seconds": 5}).json()
        assert result["monitor"]["used_seconds"] == 20


def test_sandbox_cannot_reuse_a_real_database_or_inject_an_external_tv(tmp_path, device):
    store = Store(tmp_path / "controller.sqlite3")
    store.close()
    with pytest.raises(ValueError, match="non-sandbox"):
        create_app(tmp_path, sandbox=True)
    with pytest.raises(ValueError, match="external TV"):
        create_app(tmp_path, sandbox=True, device=device)


def test_real_mode_cannot_reuse_a_sandbox_data_directory(tmp_path):
    app = create_app(tmp_path, sandbox=True, run_monitor=False)
    with TestClient(app):
        pass
    with pytest.raises(ValueError, match="must remain separate"):
        create_app(tmp_path, run_monitor=False)


def test_scenario_routes_are_not_present_in_real_mode(client):
    assert client.post("/api/sandbox/advance", json={"seconds": 60}).status_code in {404, 405}


@pytest.mark.parametrize("value", [0, -1, 86401, 1.5, True])
def test_invalid_time_advances_are_rejected(sandbox_client, value):
    assert sandbox_client.post("/api/sandbox/advance", json={"seconds": value}).status_code == 422
