import base64
import json
import shlex
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tvcontrol.api import create_app
from tvcontrol.device import Device
from tvcontrol.energy import (
    EnergyConfig,
    EnergyError,
    EnergyMetrics,
    EnergySnapshot,
    HomeAssistantEnergy,
    NoRedirect,
    parse_state,
)

START = datetime(2026, 10, 20, 2, tzinfo=timezone.utc)


def mapping():
    return EnergyConfig(enabled=True, solar_entity="sensor.sample_solar",
                        home_entity="sensor.sample_home", grid_entity="sensor.sample_grid",
                        battery_entity="sensor.sample_battery", battery_soc_entity="sensor.sample_soc")


def example():
    return EnergyMetrics(solar_w=3470, home_w=880, grid_w=0,
                         battery_w=-2590, battery_soc=24)

def test_native_delivery_grace_does_not_weaken_the_upstream_contact_deadline():
    snapshot = EnergySnapshot(source="home_assistant", status="live",
                              sampled_at=START, received_at=START, metrics=example())
    wire = snapshot.wire()
    assert wire["stale_after_ms"] == 15000
    assert wire["delivery_stale_after_ms"] == 30000
    stale = snapshot.model_copy(update={"status": "stale"})
    assert stale.wire()["status"] == "stale"
    assert stale.wire()["received_at_ms"] == wire["received_at_ms"]


@pytest.mark.parametrize(("metric", "state", "unit", "expected"), [
    ("solar_w", "3.47", "kW", 3470),
    ("home_w", "880", "W", 880),
    ("grid_w", "-0.32", "kW", -320),
    ("battery_w", "-2.59", "kW", -2590),
    ("battery_soc", "24", "%", 24),
])
def test_real_sensor_units_are_converted_without_guessing(metric, state, unit, expected):
    assert parse_state({"state": state, "attributes": {"unit_of_measurement": unit}},
                       metric) == expected


@pytest.mark.parametrize(("metric", "state", "unit"), [
    ("solar_w", "unavailable", "W"), ("solar_w", "NaN", "W"),
    ("solar_w", "2.1", "kWh"), ("home_w", "-10", "W"),
    ("battery_w", "1e308", "kW"), ("battery_soc", "101", "%"),
    ("battery_soc", "24", "W"), ("grid_w", True, "W"),
])
def test_bad_sensor_readings_are_errors_not_zero_watts(metric, state, unit):
    with pytest.raises(EnergyError):
        parse_state({"state": state, "attributes": {"unit_of_measurement": unit}}, metric)


def test_ha_requests_are_read_only_and_apply_explicit_sign_conventions(tmp_path, monkeypatch):
    token = tmp_path / "mock-token"
    token.write_text("test-only-placeholder-never-used-on-a-network", encoding="utf-8")
    provider = HomeAssistantEnergy("https://home-assistant.example", token)
    config = mapping().model_copy(update={
        "grid_import_positive": False, "battery_discharge_positive": False})
    values = {"solar_w": ("3.47", "kW"), "home_w": ("880", "W"),
              "grid_w": ("-100", "W"), "battery_w": ("2590", "W"),
              "battery_soc": ("24", "%")}
    payloads = {entity: {"entity_id": entity, "state": values[metric][0],
                        "attributes": {"unit_of_measurement": values[metric][1]}}
                for metric, entity in config.mappings().items()}
    requests = []

    def fake_open(request, timeout):
        requests.append(request)
        entity = request.full_url.rsplit("/", 1)[-1]
        return BytesIO(json.dumps(payloads[entity]).encode())

    monkeypatch.setattr(provider.opener, "open", fake_open)
    result = provider.fetch(config)
    assert result.solar_w == 3470
    assert result.grid_w == 100
    assert result.battery_w == -2590
    assert len(requests) == 5
    assert all(request.get_method() == "GET" for request in requests)
    assert all(request.get_header("Authorization").startswith("Bearer ") for request in requests)


def test_redirects_cannot_forward_the_backend_token():
    with pytest.raises(EnergyError, match="redirected"):
        NoRedirect().redirect_request(None, None, 302, "", {}, "https://different.example")


@pytest.mark.parametrize("url", [
    "https://[invalid", "https://home-assistant.example:invalid",
    "https://home-assistant.example/api/states", "http://8.8.8.8",
])
def test_invalid_ha_origins_are_reported_without_crashing_the_energy_worker(tmp_path, url):
    with pytest.raises(EnergyError):
        HomeAssistantEnergy(url, tmp_path / "token-not-read")


def test_missing_token_and_wrong_sensor_identity_are_explicit_errors(tmp_path, monkeypatch):
    provider = HomeAssistantEnergy("https://home-assistant.example", tmp_path / "absent-token")
    with pytest.raises(EnergyError, match="token file"):
        provider.fetch(mapping())
    token = tmp_path / "token"
    token.write_text("test-only-mocked-token", encoding="utf-8")
    provider = HomeAssistantEnergy("https://home-assistant.example", token)
    monkeypatch.setattr(provider.opener, "open", lambda *args, **kwargs: BytesIO(
        b'{"entity_id":"sensor.different","state":"0","attributes":{"unit_of_measurement":"W"}}'))
    with pytest.raises(EnergyError, match="different"):
        provider.fetch(mapping())


def test_live_failure_and_old_samples_keep_last_values_but_never_claim_live(
        app, tmp_path, monkeypatch):
    energy = app.state.energy
    now = START
    energy.now = lambda: now
    app.state.store.put("energy_config", mapping().model_dump())
    monkeypatch.setenv("TVCONTROL_HA_URL", "https://home-assistant.example")
    monkeypatch.setenv("TVCONTROL_HA_TOKEN_FILE", str(tmp_path / "not-read-by-mock"))
    monkeypatch.setattr(HomeAssistantEnergy, "fetch", lambda self, config: example())
    assert energy.poll().status == "live"
    now += timedelta(seconds=121)
    assert energy.snapshot().status == "stale"

    def failed(self, config):
        raise EnergyError("Simulated Home Assistant outage")

    monkeypatch.setattr(HomeAssistantEnergy, "fetch", failed)
    reading = energy.poll()
    assert reading.status == "stale"
    assert reading.metrics.solar_w == 3470
    assert "outage" in reading.message
    app.state.store.close()


def test_sandbox_energy_is_explicitly_simulated_and_never_contacts_ha(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Sandbox attempted Home Assistant access")

    monkeypatch.setenv("TVCONTROL_HA_URL", "https://home-assistant.example")
    monkeypatch.setenv("TVCONTROL_HA_TOKEN_FILE", str(tmp_path / "unused"))
    monkeypatch.setattr("tvcontrol.energy.HomeAssistantEnergy", forbidden)
    app = create_app(tmp_path, sandbox=True, sandbox_start=START, run_monitor=False)
    with TestClient(app) as client:
        session = client.get("/api/session").json()
        client.headers["X-CSRF-Token"] = session["csrf_token"]
        reading = client.get("/api/status").json()["energy"]
        assert reading["source"] == "sandbox"
        assert reading["status"] == "simulated"
        assert reading["metrics"] == example().model_dump()
        assert "token" not in json.dumps(reading)
        changed = example().model_copy(update={"solar_w": 0, "battery_w": 1800, "battery_soc": 73})
        response = client.put("/api/sandbox/energy", json=changed.model_dump())
        assert response.status_code == 200
        assert response.json()["metrics"]["battery_w"] == 1800
        assert client.post("/api/energy/refresh").json()["status"] == "simulated"
        assert client.post("/api/sandbox/advance", json={"seconds": 60}).status_code == 200
        assert client.get("/api/status").json()["energy"]["sampled_at"] != reading["sampled_at"]


def test_energy_settings_validate_sensor_ids_and_never_accept_browser_tokens(client):
    assert client.put("/api/energy/config", json={"enabled": True}).status_code == 422
    assert client.put("/api/energy/config", json={"solar_entity": "sensor.invalid/../value"}).status_code == 422
    assert client.put("/api/energy/config", json={"token": "must-not-be-accepted"}).status_code == 422
    assert client.put("/api/sandbox/energy", json=example().model_dump()).status_code in {404, 405}


def test_energy_metrics_reject_nonfinite_booleans_and_invalid_battery_percentages():
    for change in [{"solar_w": True}, {"grid_w": float("inf")},
                   {"battery_soc": -1}, {"home_w": -2}]:
        with pytest.raises(ValidationError):
            EnergyMetrics.model_validate({**example().model_dump(), **change})


def test_native_delivery_commits_a_verified_local_snapshot_not_a_network_feed(tmp_path, monkeypatch):
    device = Device("192.168.1.148", tmp_path)
    device.connection = SimpleNamespace(available=True)
    commands = []
    monkeypatch.setattr(device, "_shell",
                        lambda command, **kwargs: commands.append(command) or "")
    payload = {"status": "simulated", "metrics": example().model_dump(), "sampled_at_ms": 1}
    assert device.publish_energy(payload)
    encoded = shlex.split(commands[0])[6]
    assert json.loads(base64.b64decode(encoded)) == payload
    assert "/energy.pending.json" in commands[0]
    assert "sha256sum" in commands[0]
    assert " && mv " in commands[-1]
    assert commands[-1].endswith("/energy.json")
    assert not any("http" in command for command in commands)
