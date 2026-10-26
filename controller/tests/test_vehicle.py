import asyncio
import base64
import json
import shlex
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from websockets.asyncio.client import connect as websocket_connect
from websockets.asyncio.server import serve

from tvcontrol.api import create_app
from tvcontrol.device import Device
from tvcontrol.energy import EnergyError
from tvcontrol.models import LAUNCHER
from tvcontrol.monitor import DeviceError
from tvcontrol.store import Store
from tvcontrol.vehicle import (
    HomeAssistantVehicle,
    VehicleConfig,
    VehicleData,
    VehicleService,
    VehicleSnapshot,
    vehicle_from_states,
)


def config(**overrides):
    return VehicleConfig(**{
        "enabled": True, "battery_soc_entity": "sensor.vehicle_battery",
        "charging_entity": "binary_sensor.vehicle_charging", **overrides})


def states(soc="47", charging="off"):
    return {
        "sensor.vehicle_battery": {"entity_id": "sensor.vehicle_battery", "state": soc,
                                   "attributes": {"unit_of_measurement": "%"}},
        "binary_sensor.vehicle_charging": {
            "entity_id": "binary_sensor.vehicle_charging", "state": charging, "attributes": {}},
    }


def data(soc="47", charging="off"):
    return vehicle_from_states(config(), states(soc, charging))


def event(subscription=1, soc="47", charging="off"):
    return {"type": "event", "id": subscription, "event": {"a": {
        entity: {"s": value["state"], "a": value["attributes"]}
        for entity, value in states(soc, charging).items()}}}


@pytest.fixture
def vehicle_service(tmp_path, monkeypatch):
    monkeypatch.setenv("TVCONTROL_HA_URL", "https://ha.example")
    monkeypatch.setenv("TVCONTROL_HA_TOKEN_FILE", str(tmp_path / "test-token"))
    store = Store(tmp_path / "vehicle.sqlite3")
    frames, at = [], [datetime(2026, 10, 22, 5, tzinfo=timezone.utc)]
    device = SimpleNamespace(publish_vehicle=lambda frame: frames.append(frame))
    service = VehicleService(store, device, lambda: at[0], sandbox=False)
    store.put("vehicle_config", config().model_dump())
    service.monotonic = lambda: at[0].timestamp()
    try:
        yield SimpleNamespace(service=service, store=store, frames=frames, at=at, device=device)
    finally:
        store.close()


@pytest.fixture
def vehicle_sandbox_client(tmp_path):
    app = create_app(tmp_path / "vehicle-sandbox", sandbox=True, run_monitor=False)
    with TestClient(app) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf_token"]
        yield client


@pytest.mark.parametrize(("soc", "charging", "expected"), [
    ("0", "off", 0), ("100", "on", 100), ("47.25", "on", 47.25), (" .5 ", "off", .5)])
def test_vehicle_percentage_and_activity_are_exact_read_only_telemetry(soc, charging, expected):
    reading = data(soc, charging)
    assert reading.battery_soc == expected
    assert reading.charging is (charging == "on")
    assert reading.battery_soc_entity == config().battery_soc_entity
    assert reading.charging_entity == config().charging_entity


@pytest.mark.parametrize("soc", [
    None, True, 47, [], {}, "", "unknown", "unavailable", "nan", "NaN", "inf",
    "1e2", "-.1", "100.1", "47%", "0x20"])
def test_invalid_battery_never_becomes_a_live_zero(soc):
    with pytest.raises(EnergyError):
        data(soc)


@pytest.mark.parametrize("charging", [None, False, 0, [], {}, "", "unknown", "unavailable", "false"])
def test_invalid_charging_never_becomes_not_charging(charging):
    with pytest.raises(EnergyError, match="not assumed off"):
        data(charging=charging)


@pytest.mark.parametrize("change", ["missing", "wrong-id", "wrong-unit", "bad-attributes"])
def test_missing_or_different_entities_and_units_are_rejected(change):
    values = states()
    battery = values["sensor.vehicle_battery"]
    if change == "missing":
        values.pop("binary_sensor.vehicle_charging")
    elif change == "wrong-id":
        battery["entity_id"] = "sensor.different_vehicle"
    elif change == "wrong-unit":
        battery["attributes"]["unit_of_measurement"] = "kWh"
    else:
        battery["attributes"] = []
    with pytest.raises(EnergyError):
        vehicle_from_states(config(), values)


@pytest.mark.parametrize("soc", [True, "47", None, float("nan"), float("inf"), -1, 101])
def test_typed_vehicle_data_cannot_coerce_invalid_percentages(soc):
    with pytest.raises(ValueError):
        VehicleData(**{**data().model_dump(), "battery_soc": soc})


def test_live_snapshots_require_real_data_aware_times_and_correct_source():
    at = datetime.now(timezone.utc)
    for changes in [
        {"data": None}, {"received_at": None}, {"received_at": datetime(2026, 10, 22)},
        {"source": "sandbox"}, {"status": "simulated"}]:
        with pytest.raises(ValueError):
            VehicleSnapshot(**{"source": "home_assistant", "status": "live",
                               "received_at": at, "data": data(), **changes})
    wire = VehicleSnapshot(source="home_assistant", status="live", received_at=at, data=data()).wire()
    assert wire["received_at_ms"] == int(at.timestamp() * 1000)
    assert wire["stale_after_ms"] == 900000
    assert wire["data"]["charging"] is False


def test_service_preserves_last_known_readings_but_not_false_freshness(vehicle_service):
    service, at = vehicle_service.service, vehicle_service.at
    assert service.poll().status == "unconfigured"
    original = service.accept(data())
    at[0] += timedelta(seconds=900)
    assert service.snapshot().status == "live"
    at[0] += timedelta(milliseconds=1)
    assert service.snapshot().status == "stale"
    at[0] = original.received_at - timedelta(seconds=5)
    assert service.snapshot().status == "live"
    at[0] -= timedelta(milliseconds=1)
    assert service.snapshot().status == "stale"
    lost = service.unavailable("Test feed disconnected")
    assert lost.status == "stale" and lost.data == data()
    assert lost.received_at == original.received_at
    service.checkpoint()
    restored = VehicleService(vehicle_service.store, vehicle_service.device,
                              lambda: at[0], sandbox=False)
    assert restored.snapshot().status == "stale"
    assert restored.snapshot().data == original.data


def test_missing_backend_authorization_is_explicit_and_retains_only_real_history(
        vehicle_service, monkeypatch):
    service = vehicle_service.service
    monkeypatch.delenv("TVCONTROL_HA_URL")
    assert service.poll().status == "unavailable"
    assert "TVCONTROL_HA_URL" in service.snapshot().message
    service.accept(data())
    assert service.poll().status == "stale"
    assert service.snapshot().data == data()


def test_changed_or_disabled_feed_rejects_late_updates_and_hides_other_vehicle(vehicle_service):
    service, store = vehicle_service.service, vehicle_service.store
    service.accept(data())
    selected = config()
    store.put("vehicle_config", config(battery_soc_entity="sensor.second_vehicle").model_dump())
    assert service.snapshot().data is None
    with pytest.raises(EnergyError, match="disabled or changed"):
        service.accept(data(), selected)
    store.put("vehicle_config", config(enabled=False).model_dump())
    assert service.snapshot().status == "disabled"
    assert service.snapshot().data is None
    with pytest.raises(EnergyError):
        service.accept(data())
    store.put("vehicle_config", config(transport="simulation").model_dump())
    assert service.snapshot().source == "sandbox"
    assert service.snapshot().data is None
    with pytest.raises(EnergyError):
        service.accept(data())


def test_delivery_needs_launcher_and_throttles_only_unchanged_successful_frames(vehicle_service):
    service, store, frames, at = (vehicle_service.service, vehicle_service.store,
                                vehicle_service.frames, vehicle_service.at)
    service.accept(data())
    assert not service.deliver()
    assert frames == []
    store.put("inventory", {"installed": [LAUNCHER]})
    assert service.deliver()
    at[0] += timedelta(seconds=59)
    service.accept(data())
    assert not service.deliver()
    at[0] += timedelta(seconds=1)
    service.accept(data())
    assert service.deliver()
    service.accept(data(charging="on"))
    assert service.deliver()
    assert frames[-1]["data"]["charging"] is True
    service.unavailable("Test disconnect")
    assert service.deliver()
    assert frames[-1]["status"] == "stale"
    assert len(frames) == 4


def test_busy_failed_and_locked_deliveries_retry_without_dropping_latest(vehicle_service):
    service, store, device = (vehicle_service.service, vehicle_service.store, vehicle_service.device)
    store.put("inventory", {"installed": [LAUNCHER]})
    service.accept(data())
    with service.delivery_lock:
        assert not service.deliver()
    device.publish_vehicle = lambda frame: False
    assert not service.deliver()
    assert "deferred" in service.delivery_error

    def fail(frame):
        raise DeviceError("Test unavailable TV")

    device.publish_vehicle = fail
    assert not service.deliver()
    assert "Test unavailable TV" in service.delivery_error
    device.publish_vehicle = lambda frame: vehicle_service.frames.append(frame)
    assert service.deliver()
    assert service.delivery_error is None
    assert vehicle_service.frames[-1]["data"] == data().model_dump()


@pytest.mark.parametrize("payload", [
    "[]", "not-json", json.dumps({"type": "event", "id": 1, "event": []}),
    json.dumps({"type": "result", "id": 1, "success": False}),
    json.dumps(event(charging="unavailable")),
    json.dumps({"type": "event", "id": 1, "event": {"a": {
        "sensor.vehicle_battery": {"s": "47", "a": {"unit_of_measurement": "%"}}}}}),
])
def test_invalid_websocket_messages_fail_explicitly(tmp_path, monkeypatch, payload):
    token = tmp_path / "test-token"
    token.write_text("test-only-vehicle-token", encoding="utf-8")

    async def scenario():
        async def handler(socket):
            await socket.send(json.dumps({"type": "auth_required"}))
            assert json.loads(await socket.recv())["type"] == "auth"
            await socket.send(json.dumps({"type": "auth_ok"}))
            await socket.recv()
            await socket.send(payload)
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            def local_connect(url, **kwargs):
                assert url == "wss://ha.example/api/websocket"
                return websocket_connect(f"ws://127.0.0.1:{port}", **kwargs)

            monkeypatch.setattr("tvcontrol.energy.connect", local_connect)
            updates = HomeAssistantVehicle("https://ha.example", token).updates(config())
            try:
                with pytest.raises(EnergyError):
                    await anext(updates)
            finally:
                await updates.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("complete_refresh", [True, False])
def test_subscription_renewal_ignores_old_id_and_discards_old_entity_cache(
        tmp_path, complete_refresh):
    async def scenario():
        requests, clock, queue = [], [0], [event()]

        class Socket:
            async def send(self, payload):
                message = json.loads(payload)
                requests.append(message)
                if message["type"] == "subscribe_entities" and message["id"] == 3:
                    refreshed = event(3, "48", "on")
                    if not complete_refresh:
                        refreshed["event"]["a"].pop("binary_sensor.vehicle_charging")
                    queue.extend([event(1, "99", "on"), refreshed])

            async def recv(self):
                return json.dumps(queue.pop(0))

        @asynccontextmanager
        async def connection():
            yield Socket()

        provider = HomeAssistantVehicle("https://ha.example", tmp_path / "unused-test-token")
        provider.client.connection = connection
        provider.monotonic = lambda: clock[0]
        updates = provider.updates(config())
        try:
            assert await anext(updates) == data()
            clock[0] = 61
            if complete_refresh:
                assert await anext(updates) == data("48", "on")
            else:
                with pytest.raises(EnergyError, match="not assumed off"):
                    await anext(updates)
        finally:
            await updates.aclose()
        assert requests == [
            {"id": 1, "type": "subscribe_entities", "entity_ids": [
                "sensor.vehicle_battery", "binary_sensor.vehicle_charging"]},
            {"id": 2, "type": "unsubscribe_events", "subscription": 1},
            {"id": 3, "type": "subscribe_entities", "entity_ids": [
                "sensor.vehicle_battery", "binary_sensor.vehicle_charging"]}]

    asyncio.run(scenario())


def test_heartbeat_yields_no_reading_and_does_not_renew_observation(tmp_path):
    async def scenario():
        class Socket:
            async def send(self, payload):
                pass

            async def recv(self):
                await asyncio.Event().wait()

            async def ping(self):
                future = asyncio.get_running_loop().create_future()
                future.set_result(None)
                return future

        @asynccontextmanager
        async def connection():
            yield Socket()

        provider = HomeAssistantVehicle("https://ha.example", tmp_path / "unused-test-token")
        provider.client.connection = connection
        provider.receive_timeout = .01
        updates = provider.updates(config())
        try:
            assert await anext(updates) is None
        finally:
            await updates.aclose()

    asyncio.run(scenario())


def test_worker_heartbeats_keep_timestamp_and_shutdown_closes_subscription(
        vehicle_service, monkeypatch):
    async def scenario():
        closed, stop = [], asyncio.Event()

        async def updates(selected):
            try:
                yield data()
                vehicle_service.at[0] += timedelta(seconds=20)
                yield None
                stop.set()
                yield None
            finally:
                closed.append(True)

        monkeypatch.setattr("tvcontrol.vehicle.HomeAssistantVehicle",
                            lambda *args: SimpleNamespace(updates=updates))
        original = vehicle_service.at[0]
        await asyncio.wait_for(vehicle_service.service.run(stop), timeout=2)
        assert vehicle_service.service.snapshot().received_at == original
        assert closed == [True]

    asyncio.run(scenario())


def test_vehicle_api_config_auth_and_real_vs_simulated_source_isolation(
        client, app, vehicle_sandbox_client):
    sandbox = vehicle_sandbox_client
    for invalid in [{"enabled": True}, {"enabled": True, "transport": "simulation"},
                    {**config().model_dump(), "battery_soc_entity": "switch.charger"}]:
        assert client.put("/api/vehicle/config", json=invalid).status_code == 422
    assert client.put("/api/vehicle/config", json=config().model_dump()).status_code == 200
    app.state.vehicle.accept(data())
    assert client.put("/api/vehicle/config", json=config().model_dump()).json()[
        "vehicle"]["status"] == "live"
    assert client.get("/api/vehicle/config").json()["config"] == config().model_dump()
    assert client.get("/api/status").json()["vehicle"]["data"]["charging"] is False
    assert client.put("/api/sandbox/vehicle", json=data().model_dump()).status_code == 405
    assert "/api/sandbox/vehicle" not in app.openapi()["paths"]
    assert sandbox.put("/api/sandbox/vehicle", json=data().model_dump()).status_code == 409
    assert sandbox.put("/api/vehicle/config", json={
        "enabled": True, "transport": "simulation"}).status_code == 200
    response = sandbox.put("/api/sandbox/vehicle", json=data("0", "on").model_dump())
    assert response.status_code == 200
    assert response.json()["source"] == "sandbox"
    assert response.json()["status"] == "simulated"
    assert sandbox.get("/api/status").json()["vehicle"]["data"]["battery_soc"] == 0
    assert sandbox.put("/api/vehicle/config", json=config().model_dump()).status_code == 200
    assert sandbox.get("/api/status").json()["vehicle"]["data"] is None
    client.cookies.clear()
    assert client.get("/api/vehicle/config").status_code == 401
    assert client.put("/api/vehicle/config", json=config().model_dump()).status_code == 401


def test_vehicle_visibility_survives_theme_apply_without_changing_existing_fields(client, device):
    assert client.post("/api/inventory").status_code == 200
    assert client.post("/api/install", json={"confirmation": "INSTALL_LAUNCHER"}).status_code == 200
    theme = client.get("/api/status").json()["theme"]
    assert theme["vehicle_panel"] is True
    theme["vehicle_panel"] = False
    assert client.put("/api/theme", json=theme).status_code == 200
    assert client.post("/api/theme/apply").status_code == 200
    assert device.theme["vehicle_panel"] is False
    for key in ("dock_apps", "wallpaper_file", "energy_panel", "weather_panel"):
        assert device.theme.get(key) == theme.get(key)
    for invalid in (0, 1, "false", None):
        assert client.put("/api/theme", json={**theme, "vehicle_panel": invalid}).status_code == 422


def test_vehicle_delivery_is_verified_atomic_local_json(tmp_path, monkeypatch):
    device = Device("192.168.1.148", tmp_path)
    device.connection = SimpleNamespace(available=True)
    commands = []
    monkeypatch.setattr(device, "_shell",
                        lambda command, **kwargs: commands.append(command) or "")
    snapshot = VehicleSnapshot(source="home_assistant", status="live",
                               received_at=datetime.now(timezone.utc), data=data()).wire()
    assert device.publish_vehicle(snapshot)
    assert json.loads(base64.b64decode(shlex.split(commands[0])[6])) == snapshot
    assert "vehicle.pending.json" in commands[0]
    assert "sha256sum" in commands[0]
    assert " && mv " in commands[-1]
    assert commands[-1].endswith("/vehicle.json")
    assert not any("http" in command for command in commands)


@pytest.mark.parametrize(("energy", "vehicle", "weather"), [
    (False, False, False), (False, False, True), (False, True, False), (False, True, True),
    (True, False, False), (True, False, True), (True, True, False), (True, True, True)])
def test_sandbox_vehicle_card_is_ten_percent_and_obeys_visibility(
        vehicle_sandbox_client, energy, vehicle, weather, monkeypatch):
    client = vehicle_sandbox_client
    rectangles = []
    original = ImageDraw.ImageDraw.rounded_rectangle

    def record_rectangle(self, xy, *args, **kwargs):
        rectangles.append((xy, kwargs.get("fill")))
        return original(self, xy, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "rounded_rectangle", record_rectangle)
    assert client.post("/api/inventory").status_code == 200
    assert client.post("/api/install", json={"confirmation": "INSTALL_LAUNCHER"}).status_code == 200
    theme = client.get("/api/status").json()["theme"]
    theme.update(energy_panel=energy, vehicle_panel=vehicle, weather_panel=weather)
    assert client.put("/api/theme", json=theme).status_code == 200
    assert client.post("/api/theme/apply").status_code == 200
    response = client.get("/api/screenshot")
    assert response.status_code == 200
    with Image.open(BytesIO(response.content)) as image:
        assert image.size == (1280, 720)
        left = 54 + (400 if energy else 0)
        color = (32, 48, 60)
        cards = [xy for xy, fill in rectangles
                 if fill == "#20303c" and xy[2] - xy[0] < 200]
        assert len(cards) == int(vehicle)
        if vehicle:
            assert cards == [(left, 520, left + 127, 676)]
            assert image.getpixel((left + 5, 600)) == color
            row = [image.getpixel((x, 600)) == color for x in range(image.width)]
            start = next(x for x in range(left, left + 64) if row[x])
            end = next(x for x in range(left + 127, left + 131) if not row[x])
            assert end - start == 128


@pytest.mark.parametrize(("status", "age", "expected_status", "expected_activity"), [
    ("live", 0, "live", "Charging"),
    ("live", 901, "stale", "Last: Charging"),
    ("live", -6, "stale", "Last: Charging"),
    ("live", None, "stale", "Last: Charging"),
    ("stale", 0, "stale", "Last: Charging"),
])
def test_sandbox_live_vehicle_expiry_and_zero_charge_fill(
        vehicle_sandbox_client, monkeypatch, status, age, expected_status, expected_activity):
    client = vehicle_sandbox_client
    assert client.put("/api/vehicle/config", json=config().model_dump()).status_code == 200
    snapshot = VehicleSnapshot(source="home_assistant", status=status,
                               received_at=datetime.now(timezone.utc), data=data("0", "on")).wire()
    snapshot["received_at_ms"] = (int((datetime.now(timezone.utc) - timedelta(seconds=age))
                                    .timestamp() * 1000) if age is not None else None)
    client.app.state.device.publish_vehicle(snapshot)
    drawn = []
    original = ImageDraw.ImageDraw.text

    def record_text(self, xy, text, *args, **kwargs):
        drawn.append(text)
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    response = client.get("/api/screenshot")
    assert response.status_code == 200
    assert expected_activity in drawn and expected_status in drawn
    assert "0%" in drawn
    with Image.open(BytesIO(response.content)) as image:
        assert image.getpixel((454 + 31, 570)) == (32, 48, 60)
