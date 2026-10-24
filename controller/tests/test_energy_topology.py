import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from websockets.asyncio.client import connect as websocket_connect
from websockets.asyncio.server import serve

from tvcontrol.energy import EnergyConfig, EnergyService, HomeAssistantEnergy
from tvcontrol.energy_topology import (
    EnergyBindings,
    bindings_from_dashboard,
    topology_from_states,
)
from tvcontrol.models import LAUNCHER
from tvcontrol.store import Store


def bindings(offline=True):
    return EnergyBindings(
        enphase_solar_entity="sensor.ess_link_pv_power",
        ess_solar_entity="sensor.ess_solar_power_planned",
        home_entity="sensor.ess_link_load_power",
        grid_import_entity="sensor.ess_link_grid_export_power",
        grid_export_entity="sensor.ess_link_grid_import_power",
        battery_charge_entity="sensor.ess_link_battery_charge_power",
        battery_discharge_entity="sensor.ess_link_battery_discharge_power",
        battery_soc_entity="sensor.ess_link_battery_soc",
        enphase_monitor_entity="sensor.envoy_122219081316_current_power_production",
        home_fallback_entity="sensor.envoy_122219081316_current_power_consumption",
        ess_solar_planned=True, ess_cloud_offline=offline, balance_tolerance_w=50)


def states():
    values = {
        "sensor.ess_link_pv_power": ("2.54", "kW"),
        "sensor.ess_solar_power_planned": ("0", "W"),
        "sensor.ess_link_load_power": ("5.51", "kW"),
        "sensor.ess_link_grid_export_power": ("2.97", "kW"),
        "sensor.ess_link_grid_import_power": ("0.0", "kW"),
        "sensor.ess_link_battery_charge_power": ("0.0", "kW"),
        "sensor.ess_link_battery_discharge_power": ("0.0", "kW"),
        "sensor.ess_link_battery_soc": ("16.0", "%"),
        "sensor.envoy_122219081316_current_power_production": ("2.578", "kW"),
        "sensor.envoy_122219081316_current_power_consumption": ("5.054", "kW"),
    }
    return {entity: {"entity_id": entity, "state": value, "attributes": {
        "unit_of_measurement": unit, "device_class": "battery" if unit == "%" else "power"}}
        for entity, (value, unit) in values.items()}


def dashboard(offline=True):
    return {"views": [{"title": "Home", "cards": [{
        "type": "custom:button-card", "variables": bindings(offline).model_dump(),
        "custom_fields": {"flow": "[[[ throw new Error('Must not execute dashboard JavaScript'); ]]]"}
    }]}]}


def test_matches_current_ha_offline_model_without_using_cached_ess_values_as_live():
    model = topology_from_states(bindings(), states())
    assert model.status_text == "ESS cloud offline"
    assert model.status == "error"
    assert model.enphase_monitor == 2578
    assert model.values["ess"] == 0
    assert model.planned
    assert model.home_display == 5054
    assert model.home_fallback_active
    assert model.soc is None
    assert model.solar_input is None
    assert model.imbalance is None
    for key in ["enphase", "home", "gridImport", "gridExport", "charge", "discharge"]:
        assert model.values[key] is None
    edges = {edge.id: edge for edge in model.edges}
    assert edges["enphase-inverter"].active
    assert edges["enphase-inverter"].direction == 1
    assert not edges["ess-inverter-pv"].active
    assert edges["ess-inverter-pv"].planned
    assert edges["inverter-home"].quality == "ct-only"
    assert not edges["inverter-home"].active
    assert edges["inverter-grid"].quality == "unavailable"
    assert edges["inverter-grid"].direction == 0


def test_live_balance_planned_pv_and_directional_meters_follow_the_ha_model():
    readings = states()
    for entity, value in {
        "sensor.ess_link_pv_power": "0.73", "sensor.ess_link_load_power": "2.18",
        "sensor.ess_link_grid_export_power": "0.63",
        "sensor.ess_link_battery_discharge_power": "0.82",
        "sensor.envoy_122219081316_current_power_production": "0.73",
    }.items():
        readings[entity]["state"] = value
    model = topology_from_states(bindings(False), readings)
    assert model.balanced
    assert model.imbalance == pytest.approx(0)
    assert model.solar_input == 730
    assert model.soc == 16
    edges = {edge.id: edge for edge in model.edges}
    assert edges["inverter-grid"].direction == -1
    assert edges["inverter-grid"].watts == 630
    assert edges["inverter-battery"].direction == -1
    assert edges["inverter-battery"].watts == 820
    assert edges["inverter-home"].active
    readings["sensor.ess_solar_power_planned"]["state"] = "100"
    uncommissioned = topology_from_states(bindings(False), readings)
    assert not uncommissioned.balanced
    assert any("commissioning" in issue for issue in uncommissioned.issues)


def test_signed_enphase_standby_and_invalid_sensor_metadata_are_not_zero():
    readings = states()
    readings["sensor.envoy_122219081316_current_power_production"]["state"] = "-0.04"
    model = topology_from_states(bindings(), readings)
    edge = next(item for item in model.edges if item.id == "enphase-inverter")
    assert model.enphase_monitor == -40
    assert edge.direction == -1
    assert edge.watts == 40
    assert edge.active
    readings["sensor.envoy_122219081316_current_power_consumption"]["attributes"] = {
        "unit_of_measurement": "kWh", "device_class": "energy"}
    invalid = topology_from_states(bindings(), readings)
    assert invalid.home_display is None
    assert not invalid.home_fallback_active
    assert any("CT load" in note for note in invalid.notes)


def test_card_discovery_reads_only_declared_bindings_and_requires_one_matching_card():
    config = dashboard()
    assert bindings_from_dashboard(config, "Home") == bindings()
    config["views"][0]["cards"].append(config["views"][0]["cards"][0])
    with pytest.raises(ValueError, match="unambiguous"):
        bindings_from_dashboard(config, "Home")
    with pytest.raises(ValueError, match="unambiguous"):
        bindings_from_dashboard(dashboard(), "Missing view")
    for bad in ["not-a-sensor", "sensor.bad/../path"]:
        with pytest.raises(ValueError):
            EnergyBindings.model_validate({**bindings().model_dump(), "home_entity": bad})


def test_shared_renderer_templates_do_not_hide_the_declared_energy_bindings():
    config = dashboard(False)
    card = config["views"][0]["cards"][0]
    card.pop("custom_fields")
    card["template"] = "ess_energy_shared_renderer"
    config["button_card_templates"] = {"ess_energy_shared_renderer": {
        "custom_fields": {"flow": "[[[ throw new Error('Never execute renderer code'); ]]]"}}}
    assert bindings_from_dashboard(config, "Home") == bindings(False)
    card["template"] = ["base_style", "ess_energy_shared_renderer"]
    assert bindings_from_dashboard(config, "Home") == bindings(False)


def test_energy_card_can_be_in_sections_and_a_conditional_wrapper():
    config = dashboard()
    view = config["views"][0]
    card = view.pop("cards")[0]
    card.pop("custom_fields")
    card["template"] = "ess_energy_shared_renderer"
    view.update(type="sections", path="home", sections=[{
        "type": "grid", "cards": [{"type": "conditional", "card": card}]}])
    assert bindings_from_dashboard(config, "home") == bindings()
    view["sections"][0]["cards"].append({"type": "conditional", "card": card})
    with pytest.raises(ValueError, match="unambiguous"):
        bindings_from_dashboard(config, "home")


def test_templated_card_recovers_the_old_negative_nighttime_sample(tmp_path):
    store = Store(tmp_path / "recovery.sqlite3")
    frames = []
    clock = [datetime(2026, 10, 22, 17, tzinfo=timezone.utc)]
    service = EnergyService(store, SimpleNamespace(publish_energy=lambda frame: frames.append(frame)),
                            tmp_path, lambda: clock[0], sandbox=False)
    try:
        store.put("energy_config", EnergyConfig(enabled=True,
                  topology_dashboard="nitarn-home", topology_view="Home").model_dump())
        store.put("inventory", {"installed": [LAUNCHER]})
        night = states()
        night["sensor.envoy_122219081316_current_power_production"]["state"] = "-0.001"
        service.accept_states(night, bindings(False))
        assert service.snapshot().topology.enphase_monitor == -1
        service.unavailable("The HA Energy Distribution card bindings are invalid.")
        assert service.snapshot().status == "stale"
        clock[0] = datetime(2026, 10, 23, 4, tzinfo=timezone.utc)
        config = dashboard(False)
        card = config["views"][0]["cards"][0]
        card.pop("custom_fields")
        card["template"] = "ess_energy_shared_renderer"
        fresh = states()
        fresh["sensor.envoy_122219081316_current_power_production"]["state"] = "3.438"
        service.accept_states(fresh, bindings_from_dashboard(config, "Home"))
        assert service.snapshot().status == "live"
        assert service.snapshot().topology.enphase_monitor == 3438
        assert service.snapshot().received_at == clock[0]
        assert service.deliver_pending()
        assert frames[-1]["topology"]["enphase_monitor"] == 3438
        assert frames[-1]["status"] == "live"
    finally:
        store.close()


def test_partial_topology_survives_delivery_errors_and_restart_without_fabricated_metrics(tmp_path):
    store = Store(tmp_path / "topology.sqlite3")
    frames = []
    clock = datetime(2026, 10, 22, tzinfo=timezone.utc)
    service = EnergyService(store, SimpleNamespace(publish_energy=lambda frame: frames.append(frame)),
                            tmp_path, lambda: clock, sandbox=False)
    try:
        store.put("energy_config", EnergyConfig(enabled=True,
                  topology_dashboard="nitarn-home", topology_view="Home").model_dump())
        store.put("inventory", {"installed": [LAUNCHER]})
        result = service.accept_states(states(), bindings())
        assert result.metrics is None
        assert result.topology.home_display == 5054
        assert service.deliver_pending()
        assert frames[0]["topology"]["values"]["gridImport"] is None
        stale = service.unavailable("Test HA connection lost")
        assert stale.status == "stale"
        assert stale.topology == result.topology
        assert stale.metrics is None
        service.checkpoint()
    finally:
        store.close()


def test_backend_reads_dashboard_bindings_and_refreshes_offline_flags_without_executing_js(
        tmp_path, monkeypatch):
    token = tmp_path / "test-topology-token"
    token.write_text("test-only-energy-topology-token")
    requests = []
    clock = [0.0]

    async def scenario():
        async def handler(socket):
            await socket.send(json.dumps({"type": "auth_required"}))
            await socket.recv()
            await socket.send(json.dumps({"type": "auth_ok"}))
            requests.append(json.loads(await socket.recv()))
            await socket.send(json.dumps({"id": 1, "type": "result", "success": True,
                                          "result": dashboard()}))
            requests.append(json.loads(await socket.recv()))
            await socket.send(json.dumps({"id": 2, "type": "result", "success": True}))
            await socket.send(json.dumps({"id": 2, "type": "event", "event": {"a": {
                entity: {"s": item["state"], "a": item["attributes"]}
                for entity, item in states().items()}}}))
            requests.append(json.loads(await socket.recv()))
            await socket.send(json.dumps({"id": 3, "type": "result", "success": True,
                                          "result": dashboard(False)}))
            repeated = json.loads(await socket.recv())
            requests.append(repeated)
            if repeated["id"] <= 3:
                await socket.send(json.dumps({"id": repeated["id"], "type": "result",
                    "success": False, "error": {"code": "id_reuse"}}))
            else:
                await socket.send(json.dumps({"id": repeated["id"], "type": "result",
                    "success": True, "result": dashboard()}))
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            monkeypatch.setattr("tvcontrol.energy.connect", lambda url, **kwargs:
                websocket_connect(f"ws://127.0.0.1:{port}", **kwargs))
            provider = HomeAssistantEnergy("https://ha.example", token)
            provider.monotonic = lambda: clock[0]
            updates = provider.updates(EnergyConfig(enabled=True,
                topology_dashboard="nitarn-home", topology_view="Home"))
            try:
                first = await anext(updates)
                assert provider.topology_bindings.ess_cloud_offline
                assert topology_from_states(provider.topology_bindings, first).home_display == 5054
                clock[0] = 61
                await anext(updates)
                assert not provider.topology_bindings.ess_cloud_offline
                clock[0] = 122
                await anext(updates)
                assert provider.topology_bindings.ess_cloud_offline
            finally:
                await updates.aclose()

    asyncio.run(scenario())
    assert requests[0] == {"id": 1, "type": "lovelace/config", "url_path": "nitarn-home"}
    assert requests[1]["type"] == "subscribe_entities"
    assert set(requests[1]["entity_ids"]) == bindings().entities()
    assert requests[2] == {"id": 3, "type": "lovelace/config", "url_path": "nitarn-home"}
    assert requests[3] == {"id": 4, "type": "lovelace/config", "url_path": "nitarn-home"}
