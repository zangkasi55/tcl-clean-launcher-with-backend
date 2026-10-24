import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from websockets.asyncio.client import connect as websocket_connect
from websockets.asyncio.server import serve

from tvcontrol.api import create_app, create_sandbox_app
from tvcontrol.energy import (
    EnergyConfig,
    EnergyError,
    EntityCache,
    HomeAssistantEnergy,
    metrics_from_states,
)


def ess_config(transport="browser_bridge"):
    return EnergyConfig(
        enabled=True, transport=transport,
        solar_entity="sensor.ess_link_pv_power",
        home_entity="sensor.ess_link_load_power",
        grid_import_entity="sensor.ess_link_grid_export_power",
        grid_export_entity="sensor.ess_link_grid_import_power",
        battery_discharge_entity="sensor.ess_link_battery_discharge_power",
        battery_charge_entity="sensor.ess_link_battery_charge_power",
        battery_soc_entity="sensor.ess_link_battery_soc",
    )


def states():
    values = {"solar_w": "1.14", "home_w": "0.65", "grid_import_w": "0.0",
              "grid_export_w": "0.0", "battery_discharge_w": "0.0",
              "battery_charge_w": "0.49", "battery_soc": "40.0"}
    return {entity: {"entity_id": entity, "state": values[metric],
                     "attributes": {"unit_of_measurement": "%" if metric == "battery_soc" else "kW"}}
            for metric, entity in ess_config().mappings().items()}


def test_real_card_split_sensors_produce_the_correct_signed_flows():
    result = metrics_from_states(ess_config(), states())
    assert result.model_dump() == {"solar_w": 1140, "home_w": 650, "grid_w": 0,
                                   "battery_w": -490, "battery_soc": 40}
    exporting = states()
    exporting["sensor.ess_link_grid_import_power"]["state"] = "0.7"
    assert metrics_from_states(ess_config(), exporting).grid_w == -700


def test_portal_grid_name_inversion_matches_the_real_home_power_balance():
    actual = states()
    for entity, value in {
        "sensor.ess_link_pv_power": "0.73", "sensor.ess_link_load_power": "2.18",
        "sensor.ess_link_grid_export_power": "0.63", "sensor.ess_link_grid_import_power": "0.0",
        "sensor.ess_link_battery_discharge_power": "0.82",
        "sensor.ess_link_battery_charge_power": "0.0",
    }.items():
        actual[entity]["state"] = value
    metrics = metrics_from_states(ess_config(), actual)
    assert metrics.grid_w == 630
    assert metrics.solar_w + metrics.grid_w + metrics.battery_w == pytest.approx(metrics.home_w)


def test_compressed_state_subscription_handles_changes_removals_and_units():
    config = ess_config()
    cache = EntityCache(set(config.mappings().values()))
    full = {"a": {entity: {"s": state["state"], "a": state["attributes"]}
                  for entity, state in states().items()}}
    cache.apply(full)
    assert metrics_from_states(config, cache.states).solar_w == 1140
    cache.apply({"c": {"sensor.ess_link_pv_power": {"+": {"s": "1200", "a": {
        "unit_of_measurement": "W"}}}}})
    assert metrics_from_states(config, cache.states).solar_w == 1200
    cache.apply({"c": {"sensor.ess_link_pv_power": {"-": {"a": ["unit_of_measurement"]}}}})
    with pytest.raises(EnergyError, match="expected W"):
        metrics_from_states(config, cache.states)
    cache.apply({"r": ["sensor.ess_link_load_power"]})
    with pytest.raises(EnergyError):
        metrics_from_states(config, cache.states)


def test_live_browser_bridge_never_falls_back_to_screenshot_values(tmp_path):
    app = create_app(tmp_path, sandbox=True, run_monitor=False)
    with TestClient(app) as client:
        session = client.get("/api/session").json()
        client.headers["X-CSRF-Token"] = session["csrf_token"]
        assert client.get("/api/status").json()["energy"]["source"] == "sandbox"
        assert client.put("/api/energy/config", json=ess_config().model_dump()).status_code == 200
        waiting = client.get("/api/status").json()["energy"]
        assert waiting["source"] == "home_assistant"
        assert waiting["metrics"] is None
        assert waiting["status"] != "simulated"
        first = client.post("/api/sandbox/energy/live",
                             json={"connected": True, "states": states()})
        assert first.status_code == 200
        assert first.json()["status"] == "live"
        assert first.json()["metrics"]["solar_w"] == 1140
        assert first.json()["metrics"]["battery_w"] == -490
        assert client.put("/api/sandbox/energy", json={
            "solar_w": 3470, "home_w": 880, "grid_w": 0, "battery_w": -2590,
            "battery_soc": 24}).status_code == 409
        disconnected = client.post("/api/sandbox/energy/live",
                                    json={"connected": False, "states": {}})
        assert disconnected.status_code == 503
        assert disconnected.json()["status"] == "stale"
        assert disconnected.json()["metrics"]["solar_w"] == 1140
        assert client.get("/api/status").json()["energy"]["source"] == "home_assistant"


def test_source_heartbeat_not_unchanged_sensor_values_controls_freshness(tmp_path):
    app = create_app(tmp_path, sandbox=True, run_monitor=False)
    energy = app.state.energy
    clock = datetime(2026, 10, 20, 7, tzinfo=timezone.utc)
    energy.live_now = lambda: clock
    try:
        app.state.store.put("energy_config", ess_config().model_dump())
        first = energy.accept_states(states())
        clock += timedelta(seconds=10)
        heartbeat = energy.accept_states(states())
        assert heartbeat.sampled_at == first.sampled_at
        assert heartbeat.received_at > first.received_at
        clock += timedelta(seconds=15)
        assert energy.snapshot().status == "live"
        clock += timedelta(milliseconds=1)
        assert energy.snapshot().status == "stale"
    finally:
        app.state.store.close()


def test_live_readings_use_wall_time_not_the_sandbox_app_budget_clock(tmp_path):
    app = create_app(tmp_path, sandbox=True, run_monitor=False,
                     sandbox_start=datetime(2030, 1, 1, tzinfo=timezone.utc))
    try:
        app.state.store.put("energy_config", ess_config().model_dump())
        snapshot = app.state.energy.accept_states(states())
        assert snapshot.status == "live"
        assert snapshot.received_at.year != 2030
        assert app.state.energy.snapshot().status == "live"
    finally:
        app.state.store.close()


def test_backend_websocket_subscribes_only_to_the_selected_sensors(tmp_path, monkeypatch):
    token = tmp_path / "mock-stream-token"
    token.write_text("test-only-not-a-real-home-assistant-token", encoding="utf-8")
    requested = []

    async def scenario():
        async def handler(socket):
            await socket.send(json.dumps({"type": "auth_required"}))
            auth = json.loads(await socket.recv())
            assert auth["type"] == "auth"
            await socket.send(json.dumps({"type": "auth_ok"}))
            subscription = json.loads(await socket.recv())
            requested.append(subscription)
            await socket.send(json.dumps({"id": 1, "type": "result", "success": True}))
            await socket.send(json.dumps({"id": 1, "type": "event", "event": {
                "a": {entity: {"s": state["state"], "a": state["attributes"]}
                      for entity, state in states().items()}}}))
            await socket.send(json.dumps({"id": 1, "type": "event", "event": {
                "c": {"sensor.ess_link_pv_power": {"+": {"s": "1.25"}}}}}))
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            def local_connect(uri, **kwargs):
                assert uri == "wss://home-assistant.example/api/websocket"
                return websocket_connect(f"ws://127.0.0.1:{port}", **kwargs)

            monkeypatch.setattr("tvcontrol.energy.connect", local_connect)
            provider = HomeAssistantEnergy("https://home-assistant.example", token)
            updates = provider.updates(ess_config("websocket"))
            try:
                first = metrics_from_states(ess_config(), await anext(updates))
                assert first.solar_w == 1140
                changed = metrics_from_states(ess_config(), await anext(updates))
                assert changed.solar_w == 1250
            finally:
                await updates.aclose()

    asyncio.run(scenario())
    assert requested[0]["type"] == "subscribe_entities"
    assert set(requested[0]["entity_ids"]) == set(ess_config().mappings().values())


def test_ambiguous_signed_and_split_sensor_configurations_are_rejected():
    with pytest.raises(ValueError, match="either a signed grid"):
        EnergyConfig(grid_entity="sensor.signed", grid_import_entity="sensor.imported")


def test_development_entry_point_defaults_to_live_not_screenshot_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TVCONTROL_SANDBOX_DATA_DIR", str(tmp_path))
    app = create_sandbox_app()
    try:
        assert app.state.device.host == "sandbox"
        assert app.state.energy.config().transport == "browser_bridge"
        assert app.state.energy.snapshot().source == "home_assistant"
        assert app.state.energy.snapshot().metrics is None
    finally:
        app.state.store.close()


def test_disabling_the_live_feed_cannot_be_overridden_by_old_bridge_packets(tmp_path):
    app = create_app(tmp_path, sandbox=True, run_monitor=False)
    with TestClient(app) as client:
        session = client.get("/api/session").json()
        client.headers["X-CSRF-Token"] = session["csrf_token"]
        config = ess_config().model_copy(update={"enabled": False})
        assert client.put("/api/energy/config", json=config.model_dump()).status_code == 200
        response = client.post("/api/sandbox/energy/live",
                               json={"connected": True, "states": states()})
        assert response.status_code == 409
        assert client.get("/api/status").json()["energy"]["status"] == "disabled"
