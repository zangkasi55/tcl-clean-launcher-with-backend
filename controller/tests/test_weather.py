import asyncio
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from websockets.asyncio.client import connect as websocket_connect
from websockets.asyncio.server import serve

from tvcontrol.api import create_app
from tvcontrol.energy import EnergyError
from tvcontrol.models import LAUNCHER
from tvcontrol.store import Store
from tvcontrol.weather import (
    HomeAssistantWeather,
    WeatherConfig,
    WeatherService,
    WeatherSnapshot,
    weather_from_state,
)


@pytest.fixture
def weather_sandbox_client(tmp_path):
    app = create_app(tmp_path / "weather-sandbox", sandbox=True, run_monitor=False)
    with TestClient(app) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf_token"]
        yield client


def state():
    return {"entity_id": "weather.forecast_home", "state": "sunny", "attributes": {
        "friendly_name": "Forecast Home", "temperature": 25.6, "temperature_unit": "°C"}}


def forecast():
    return [{"datetime": f"2026-10-{22 + index}T05:00:00+00:00",
             "temperature": 32.5 + index / 10, "templow": 25.6,
             "condition": ["sunny", "rainy", "partlycloudy"][index],
             "wind_speed": 11.5} for index in range(3)]


def data():
    return weather_from_state("weather.forecast_home", state(), forecast())


def test_daily_forecast_keeps_real_units_precision_dates_and_three_days():
    result = data()
    assert result.temperature == 25.6
    assert result.temperature_unit == "°C"
    assert result.forecast[0].temperature == 32.5
    assert result.forecast[0].datetime == datetime(2026, 10, 22, 5, tzinfo=timezone.utc)
    assert [day.condition for day in result.forecast] == ["sunny", "rainy", "partlycloudy"]
    assert len(weather_from_state("weather.forecast_home", state(),
                                 forecast() + forecast()).forecast) == 3


@pytest.mark.parametrize("bad", [True, "25.6", float("nan"), float("inf"), 301, -151])
def test_invalid_current_temperatures_are_not_displayed_as_live(bad):
    payload = state()
    payload["attributes"]["temperature"] = bad
    with pytest.raises(EnergyError, match="invalid weather"):
        weather_from_state("weather.forecast_home", payload, forecast())


def test_unavailable_units_missing_forecasts_and_naive_dates_are_explicit_errors():
    for change in [{"state": "unavailable"}, {"entity_id": "weather.somewhere_else"}]:
        with pytest.raises(EnergyError, match="unavailable"):
            weather_from_state("weather.forecast_home", {**state(), **change}, forecast())
    payload = state()
    payload["attributes"]["temperature_unit"] = "K"
    with pytest.raises(EnergyError):
        weather_from_state("weather.forecast_home", payload, forecast())
    with pytest.raises(EnergyError, match="daily forecast"):
        weather_from_state("weather.forecast_home", state(), [])
    days = forecast()
    days[0]["datetime"] = "2026-10-22T05:00:00"
    with pytest.raises(EnergyError):
        weather_from_state("weather.forecast_home", state(), days)
    with pytest.raises(EnergyError):
        weather_from_state("weather.forecast_home", state(), list(reversed(forecast())))
    days = forecast()
    days[0].pop("templow")
    assert weather_from_state("weather.forecast_home", state(), days).forecast[0].templow is None


def test_service_staleness_delivery_restart_and_no_simulated_fallback(tmp_path):
    path = tmp_path / "weather.sqlite3"
    store = Store(path)
    frames = []
    at = [datetime(2026, 10, 22, 5, tzinfo=timezone.utc)]
    service = WeatherService(store, SimpleNamespace(publish_weather=lambda frame: frames.append(frame)),
                             lambda: at[0], sandbox=False)
    try:
        assert service.poll().status == "disabled"
        store.put("weather_config", WeatherConfig(enabled=True,
                  entity_id="weather.forecast_home").model_dump())
        store.put("inventory", {"installed": [LAUNCHER]})
        service.monotonic = lambda: (at[0] - datetime(2026, 10, 22, 5,
                                                    tzinfo=timezone.utc)).total_seconds()
        assert service.poll().status == "unconfigured"
        service.accept(data())
        assert service.deliver()
        assert frames[0]["data"]["forecast"][0]["time_ms"] == int(
            datetime(2026, 10, 22, 5, tzinfo=timezone.utc).timestamp() * 1000)
        at[0] += timedelta(seconds=10)
        service.accept(data())
        assert not service.deliver()
        at[0] += timedelta(seconds=50)
        service.accept(data())
        assert service.deliver()
        at[0] += timedelta(seconds=900)
        assert service.snapshot().status == "live"
        at[0] += timedelta(milliseconds=1)
        assert service.snapshot().status == "stale"
        lost = service.unavailable("Test connection lost")
        assert lost.source == "home_assistant"
        assert lost.status == "stale"
        assert lost.data.temperature == 25.6
        service.checkpoint()
    finally:
        store.close()
    reopened = Store(path)
    try:
        restored = WeatherService(reopened, SimpleNamespace(), lambda: at[0], sandbox=False)
        assert restored.snapshot().status == "stale"
        assert restored.snapshot().data == data()
    finally:
        reopened.close()


def test_busy_tv_defers_weather_without_losing_the_latest_forecast(tmp_path):
    store = Store(tmp_path / "busy.sqlite3")
    device = SimpleNamespace(publish_weather=lambda frame: False)
    service = WeatherService(store, device, lambda: datetime.now(timezone.utc), sandbox=False)
    try:
        store.put("weather_config", WeatherConfig(enabled=True,
                  entity_id="weather.forecast_home").model_dump())
        store.put("inventory", {"installed": [LAUNCHER]})
        service.accept(data())
        assert not service.deliver()
        assert "deferred" in service.delivery_error
        delivered = []
        device.publish_weather = lambda frame: delivered.append(frame)
        assert service.deliver()
        assert service.delivery_error is None
        assert delivered[0]["data"]["temperature"] == 25.6
    finally:
        store.close()

def test_live_weather_requires_an_actual_forecast_and_aware_contact_time():
    for fields in [{"data": None, "received_at": datetime.now(timezone.utc)},
                   {"data": data(), "received_at": None},
                   {"data": data(), "received_at": datetime(2026, 10, 22)}]:
        with pytest.raises(ValueError):
            WeatherSnapshot(source="home_assistant", status="live", **fields)


def test_weather_api_validation_and_simulation_isolation(client, weather_sandbox_client):
    sandbox_client = weather_sandbox_client
    assert client.put("/api/weather/config", json={
        "enabled": True, "entity_id": "sensor.not_weather"}).status_code == 422
    assert client.put("/api/weather/config", json={
        "enabled": True, "entity_id": "weather.forecast_home",
        "transport": "simulation"}).status_code == 422
    assert client.put("/api/weather/config", json={
        "enabled": True, "entity_id": "weather.forecast_home"}).status_code == 200
    assert client.get("/api/weather/config").json()["config"]["entity_id"] == "weather.forecast_home"
    assert sandbox_client.put("/api/sandbox/weather",
                              json=data().model_dump(mode="json")).status_code == 409
    assert sandbox_client.put("/api/weather/config", json={
        "enabled": True, "transport": "simulation"}).status_code == 200
    result = sandbox_client.put("/api/sandbox/weather", json=data().model_dump(mode="json"))
    assert result.status_code == 200
    assert result.json()["source"] == "sandbox"
    assert result.json()["status"] == "simulated"
    assert sandbox_client.get("/api/status").json()["weather"]["data"]["temperature"] == 25.6


def test_weather_websocket_uses_only_the_selected_entity_and_daily_subscription(tmp_path, monkeypatch):
    token = tmp_path / "test-token"
    token.write_text("test-only-weather-token", encoding="utf-8")
    requests = []

    async def scenario():
        async def handler(socket):
            await socket.send(json.dumps({"type": "auth_required"}))
            auth = json.loads(await socket.recv())
            assert auth["type"] == "auth"
            await socket.send(json.dumps({"type": "auth_ok"}))
            requests.extend([json.loads(await socket.recv()), json.loads(await socket.recv())])
            await socket.send(json.dumps({"id": 1, "type": "result", "success": True}))
            await socket.send(json.dumps({"id": 2, "type": "result", "success": True}))
            await socket.send(json.dumps({"id": 1, "type": "event", "event": {
                "a": {"weather.forecast_home": {"s": "sunny", "a": state()["attributes"]}}}}))
            await socket.send(json.dumps({"id": 2, "type": "event", "event": {
                "type": "daily", "forecast": forecast()}}))
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            def local_connect(url, **kwargs):
                assert url == "wss://ha.example/api/websocket"
                return websocket_connect(f"ws://127.0.0.1:{port}", **kwargs)

            monkeypatch.setattr("tvcontrol.energy.connect", local_connect)
            provider = HomeAssistantWeather("https://ha.example", token)
            updates = provider.updates(WeatherConfig(enabled=True,
                entity_id="weather.forecast_home"))
            try:
                received = await anext(updates)
                assert received == data()
            finally:
                await updates.aclose()

    asyncio.run(scenario())
    assert requests == [
        {"id": 1, "type": "subscribe_entities", "entity_ids": ["weather.forecast_home"]},
        {"id": 2, "type": "weather/subscribe_forecast", "entity_id": "weather.forecast_home",
         "forecast_type": "daily"}]


def test_header_and_gradient_colors_round_trip_without_touching_other_theme_fields(client, device):
    assert client.post("/api/inventory").status_code == 200
    assert client.post("/api/install", json={"confirmation": "INSTALL_LAUNCHER"}).status_code == 200
    theme = client.get("/api/status").json()["theme"]
    assert theme["gradient_opacity"] == 0
    updates = {"gradient_color": "#123456", "gradient_opacity": 55,
               "clock_color": "#ffcc00", "date_color": "#336699", "input_color": "#aa1133",
               "wifi_color": "#22aa55", "settings_color": "#5599ff"}
    theme.update(updates)
    response = client.put("/api/theme", json=theme)
    assert response.status_code == 200
    assert client.post("/api/theme/apply").status_code == 200
    for key, value in updates.items():
        assert device.theme[key] == (value.upper() if isinstance(value, str) else value)
    for key in ["gradient_color", "clock_color", "date_color", "input_color",
                "wifi_color", "settings_color"]:
        assert client.put("/api/theme", json={**theme, key: "not-a-color"}).status_code == 422
    for value in [-1, 101, True, 55.5]:
        assert client.put("/api/theme", json={**theme, "gradient_opacity": value}).status_code == 422
    assert device.theme["hidden_apps"] == []


def test_app_color_glow_can_be_disabled_independently_of_gradient_and_navigation(client, device):
    assert client.post("/api/inventory").status_code == 200
    assert client.post("/api/install", json={"confirmation": "INSTALL_LAUNCHER"}).status_code == 200
    theme = client.get("/api/status").json()["theme"]
    assert theme["ambient_glow"] is True
    image = BytesIO()
    Image.new("RGB", (32, 32), "#315b6f").save(image, "PNG")
    upload = client.post("/api/images/wallpaper", files={
        "file": ("glow-wallpaper.png", image.getvalue(), "image/png")})
    assert upload.status_code == 200
    theme["wallpaper_file"] = upload.json()["filename"]
    theme.update(ambient_glow=False, gradient_color="#123456", gradient_opacity=25)
    assert client.put("/api/theme", json=theme).status_code == 200
    assert client.post("/api/theme/apply").status_code == 200
    assert device.theme["ambient_glow"] is False
    assert device.theme["gradient_opacity"] == 25
    assert device.theme["gradient_color"] == "#123456"
    assert device.theme["animations"] == theme["animations"]
    assert device.theme["dock_apps"] == theme["dock_apps"]
    assert device.theme["wallpaper_file"] == theme["wallpaper_file"]
    theme["ambient_glow"] = True
    assert client.put("/api/theme", json=theme).status_code == 200
    assert client.post("/api/theme/apply").status_code == 200
    assert device.theme["ambient_glow"] is True
    for invalid in [0, 1, "false", None]:
        assert client.put("/api/theme", json={**theme, "ambient_glow": invalid}).status_code == 422
