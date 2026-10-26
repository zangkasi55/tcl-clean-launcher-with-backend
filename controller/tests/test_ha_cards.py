import base64
import hashlib
import json
import shlex
from copy import deepcopy
from types import SimpleNamespace

import pytest
from conftest import PASSWORD
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tvcontrol.api import create_app
from tvcontrol.device import Device
from tvcontrol.ha_cards import DirectHaCards
from tvcontrol.monitor import DeviceError


def mappings():
    return {
        "schema_version": 1,
        "energy": {"enabled": True, "topology_dashboard": "test-energy", "topology_view": "Home"},
        "weather": {"enabled": True, "entity_id": "weather.test"},
        "vehicle": {"enabled": True, "battery_soc_entity": "sensor.test_soc",
                    "charging_entity": "binary_sensor.test_charging"},
    }


def test_direct_contract_requires_version_and_all_cards():
    for key in mappings():
        document = mappings()
        del document[key]
        with pytest.raises(ValidationError):
            DirectHaCards.model_validate(document)
    for version in [True, False, 1.0, "1", 0, 2, None]:
        with pytest.raises(ValidationError):
            DirectHaCards.model_validate({**mappings(), "schema_version": version})


@pytest.mark.parametrize("card", ["energy", "weather", "vehicle"])
def test_direct_contract_forbids_credentials_and_simulation_even_when_disabled(card):
    for field in ["token", "access_token", "url", "origin", "secret"]:
        document = mappings()
        document[card][field] = "synthetic-private-value"
        with pytest.raises(ValidationError):
            DirectHaCards.model_validate(document)
        with pytest.raises(ValidationError):
            DirectHaCards.model_validate({**mappings(), field: "synthetic-private-value"})
    for mode in ["simulation", "browser_bridge", None, "https"]:
        document = mappings()
        document[card].update(enabled=False, transport=mode)
        with pytest.raises(ValidationError):
            DirectHaCards.model_validate(document)


@pytest.mark.parametrize(("card", "field", "value"), [
    ("energy", "enabled", "true"),
    ("energy", "grid_import_positive", 1),
    ("energy", "topology_dashboard", "../dashboard"),
    ("energy", "topology_view", ""),
    ("weather", "entity_id", "sensor.test"),
    ("vehicle", "battery_soc_entity", "weather.test"),
    ("vehicle", "charging_entity", "sensor.test"),
    ("vehicle", "enabled", 1),
])
def test_direct_contract_rejects_invalid_nested_values(card, field, value):
    document = mappings()
    document[card][field] = value
    with pytest.raises(ValidationError):
        DirectHaCards.model_validate(document)


def test_direct_energy_completeness_and_signed_directional_exclusivity():
    document = mappings()
    document["energy"] = {"enabled": True}
    with pytest.raises(ValidationError, match="required energy"):
        DirectHaCards.model_validate(document)
    document["energy"].update(
        solar_entity="sensor.solar", home_entity="sensor.home",
        battery_soc_entity="sensor.soc", grid_entity="sensor.grid", battery_entity="sensor.battery")
    assert DirectHaCards.model_validate(document).energy.enabled
    document["energy"]["grid_import_entity"] = "sensor.import"
    with pytest.raises(ValidationError, match="signed grid"):
        DirectHaCards.model_validate(document)
    document["energy"]["topology_dashboard"] = "test-energy"
    with pytest.raises(ValidationError, match="signed grid"):
        DirectHaCards.model_validate(document)


def test_direct_mapping_limit_counts_compact_utf8_bytes():
    document = {"schema_version": 1, "energy": {}, "weather": {}, "vehicle": {}}
    initial = DirectHaCards.model_validate(document).model_dump()
    size = len(json.dumps(initial, ensure_ascii=False, separators=(",", ":")).encode())
    document["energy"]["solar_entity"] = "sensor." + "a" * (16384 - size - 5)
    valid = DirectHaCards.model_validate(document).model_dump()
    assert len(json.dumps(valid, ensure_ascii=False, separators=(",", ":")).encode()) == 16384
    document["energy"]["solar_entity"] += "a"
    with pytest.raises(ValidationError, match="16 KiB"):
        DirectHaCards.model_validate(document)


def test_direct_api_requires_auth_csrf_and_trusted_origin(app):
    with TestClient(app) as client:
        for path in ["/api/ha/cards", "/api/ha/cards/export"]:
            assert client.get(path).status_code == 401
        assert client.put("/api/ha/cards", json=mappings()).status_code == 401
        assert client.post("/api/ha/cards/send").status_code == 401
        login = client.post("/api/login", json={"password": PASSWORD}).json()
        assert client.put("/api/ha/cards", json=mappings()).status_code == 403
        client.headers["X-CSRF-Token"] = login["csrf_token"]
        assert client.put("/api/ha/cards", json=mappings(),
                          headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert client.post("/api/ha/cards/send",
                           headers={"Origin": "https://untrusted.example"}).status_code == 403


def test_unsaved_mapping_export_and_send_never_report_success(client):
    assert client.get("/api/ha/cards").json() == {"saved": False, "config": None}
    assert client.get("/api/ha/cards/export").status_code == 409
    assert client.post("/api/ha/cards/send").status_code == 409


def test_direct_api_roundtrip_does_not_poll_or_modify_backend_sources(
        client, app, device, monkeypatch):
    before = {key: app.state.store.get(key) for key in [
        "energy_config", "weather_config", "vehicle_config", "theme", "rule", "baseline"]}
    inventory = deepcopy(device.inventory())

    def forbidden(*args, **kwargs):
        pytest.fail("Direct mapping setup touched a backend feed, credential or TV application")

    for name in ["energy", "weather", "vehicle"]:
        monkeypatch.setattr(getattr(app.state, name), "poll", forbidden)
    monkeypatch.setattr(device, "apply_theme", forbidden)
    monkeypatch.setattr(device, "select_home", forbidden)
    monkeypatch.setattr(device, "install", forbidden)
    response = client.put("/api/ha/cards", json=mappings())
    assert response.status_code == 200
    saved = response.json()["config"]
    assert saved == DirectHaCards.model_validate(mappings()).model_dump()
    assert client.get("/api/ha/cards").json() == {"saved": True, "config": saved}
    exported = client.get("/api/ha/cards/export")
    assert exported.json() == saved
    assert exported.headers["content-disposition"] == 'attachment; filename="ha-cards.json"'
    assert exported.headers["cache-control"] == "no-store"
    monkeypatch.setattr(device, "simulation", False)
    sent = client.post("/api/ha/cards/send", json={"vehicle": {"enabled": False}})
    assert sent.status_code == 200
    assert sent.json() == {"sent": True, "credentials_transferred": False, "source_changed": False}
    assert device.ha_cards == saved
    assert inventory == device.inventory()
    assert {key: app.state.store.get(key) for key in before} == before
    assert [event["kind"] for event in app.state.store.events()].count("ha_cards_sent") == 1


def test_failed_mapping_save_retains_previous_document(client):
    saved = client.put("/api/ha/cards", json=mappings()).json()["config"]
    invalid = mappings()
    invalid["vehicle"]["charging_entity"] = "sensor.wrong"
    assert client.put("/api/ha/cards", json=invalid).status_code == 422
    assert client.get("/api/ha/cards/export").json() == saved


def test_mapping_persists_across_controller_restart(tmp_path, device):
    directory = tmp_path / "direct-persistence"
    for first in [True, False]:
        app = create_app(directory, device, password=PASSWORD, secure_cookie=False, run_monitor=False)
        with TestClient(app) as client:
            login = client.post("/api/login", json={"password": PASSWORD}).json()
            client.headers["X-CSRF-Token"] = login["csrf_token"]
            if first:
                assert client.put("/api/ha/cards", json=mappings()).status_code == 200
            assert client.get("/api/ha/cards/export").json() == (
                DirectHaCards.model_validate(mappings()).model_dump())


def test_simulation_busy_and_adb_errors_leave_saved_mappings_retryable(
        client, app, device, monkeypatch):
    saved = client.put("/api/ha/cards", json=mappings()).json()["config"]
    assert client.post("/api/ha/cards/send").status_code == 409
    monkeypatch.setattr(device, "simulation", False)
    monkeypatch.setattr(device, "publish_ha_cards", lambda document: False)
    assert client.post("/api/ha/cards/send").status_code == 409

    def unavailable(document):
        raise DeviceError("Synthetic disconnected TV")

    monkeypatch.setattr(device, "publish_ha_cards", unavailable)
    assert client.post("/api/ha/cards/send").status_code == 503
    assert client.get("/api/ha/cards/export").json() == saved
    assert not any(event["kind"] == "ha_cards_sent" for event in app.state.store.events())


def test_invalid_saved_mapping_is_not_exported_or_sent(client, app, caplog):
    app.state.store.put("ha_cards", {**mappings(), "token": "synthetic-do-not-log"})
    for path in ["/api/ha/cards", "/api/ha/cards/export"]:
        assert client.get(path).status_code == 409
    assert client.post("/api/ha/cards/send").status_code == 409
    assert "invalid_saved_ha_cards" in caplog.text
    assert "synthetic-do-not-log" not in caplog.text


def test_device_publishes_only_atomic_verified_mapping_file(tmp_path, monkeypatch):
    device = Device("192.168.1.148", tmp_path)
    device.connection = SimpleNamespace(available=True)
    commands = []
    monkeypatch.setattr(device, "_shell", lambda command, **kwargs: commands.append((command, kwargs)))
    assert device.publish_ha_cards(mappings())
    assert len(commands) == 1
    command, options = commands[0]
    parts = shlex.split(command)
    payload = base64.b64decode(parts[parts.index("printf") + 2])
    assert json.loads(payload) == DirectHaCards.model_validate(mappings()).model_dump()
    assert hashlib.sha256(payload).hexdigest() in command
    assert "ha-cards.pending.json" in command
    assert " && mv " in command and command.endswith("/ha-cards.json")
    assert all(text not in command for text in ["am start", "pm ", "input ", "config.json"])
    assert options == {"read_timeout_s": 2, "timeout_s": 2}
    assert device.lock.acquire(blocking=False)
    device.lock.release()
    commands.clear()
    with pytest.raises(ValidationError):
        device.publish_ha_cards({**mappings(), "token": "synthetic"})
    assert commands == []
    device.connection.available = False
    assert not device.publish_ha_cards(mappings())
    assert commands == []
