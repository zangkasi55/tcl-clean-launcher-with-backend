import asyncio
import json
import logging
import os
import re
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, StrictBool, field_validator, model_validator

from .energy import EnergyError, EntityCache, HomeAssistantEnergy
from .models import LAUNCHER, Model
from .monitor import DeviceError
from .store import Store

logger = logging.getLogger(__name__)
STALE_SECONDS = 900


class WeatherConfig(Model):
    enabled: StrictBool = False
    entity_id: str | None = None
    transport: Literal["websocket", "simulation"] = "websocket"

    @field_validator("entity_id")
    @classmethod
    def entity(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"weather\.[a-z0-9_]+", value):
            raise ValueError("Use an exact weather.entity_id from Home Assistant")
        return value

    @model_validator(mode="after")
    def selected_entity(self):
        if self.enabled and self.transport == "websocket" and self.entity_id is None:
            raise ValueError("Select a Home Assistant weather entity before enabling the feed")
        return self


class ForecastDay(Model):
    datetime: datetime
    condition: str = Field(min_length=1, max_length=40)
    temperature: float = Field(ge=-150, le=300, allow_inf_nan=False)
    templow: float | None = Field(default=None, ge=-150, le=300, allow_inf_nan=False)

    @field_validator("temperature", "templow", mode="before")
    @classmethod
    def numeric(cls, value):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError("Weather temperatures must be finite numeric values")
        return value

    @field_validator("datetime")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Forecast times require an explicit timezone")
        return value.astimezone(timezone.utc)


class WeatherData(Model):
    entity_id: str = Field(pattern=r"^weather\.[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=80)
    condition: str = Field(min_length=1, max_length=40)
    temperature: float = Field(ge=-150, le=300, allow_inf_nan=False)
    temperature_unit: Literal["°C", "°F"]
    forecast: list[ForecastDay] = Field(min_length=1, max_length=3)

    @field_validator("temperature", mode="before")
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Weather temperature must be a finite numeric value")
        return value

    @model_validator(mode="after")
    def chronological(self):
        dates = [day.datetime for day in self.forecast]
        if any(later <= earlier for earlier, later in zip(dates, dates[1:])):
            raise ValueError("Forecast days must be in increasing time order")
        return self


class WeatherSnapshot(Model):
    source: Literal["home_assistant", "sandbox"]
    status: Literal["live", "simulated", "stale", "unavailable", "unconfigured", "disabled"]
    received_at: datetime | None = None
    data: WeatherData | None = None
    message: str | None = None

    @model_validator(mode="after")
    def real_payload(self):
        if self.received_at is not None and self.received_at.tzinfo is None:
            raise ValueError("Weather contact times require an explicit timezone")
        if self.status in {"live", "simulated"} and (
                self.received_at is None or self.data is None):
            raise ValueError("Live or simulated weather requires forecast data and a contact time")
        return self

    def wire(self) -> dict:
        result = self.model_dump(mode="json")
        result.update(received_at_ms=int(self.received_at.timestamp() * 1000)
                      if self.received_at is not None else None,
                      stale_after_ms=STALE_SECONDS * 1000)
        if self.data is not None:
            for item, day in zip(result["data"]["forecast"], self.data.forecast):
                item["time_ms"] = int(day.datetime.timestamp() * 1000)
        return result


def weather_from_state(entity: str, state: dict, forecast: list) -> WeatherData:
    if state.get("entity_id") != entity or state.get("state") in {
            None, "unknown", "unavailable"}:
        raise EnergyError("The selected Home Assistant weather entity is unavailable.")
    attributes = state.get("attributes")
    if not isinstance(attributes, dict) or not isinstance(forecast, list) or not forecast:
        raise EnergyError("The selected weather entity did not provide a daily forecast.")
    try:
        days = [ForecastDay.model_validate({key: item[key] for key in
                ["datetime", "condition", "temperature", "templow"] if key in item})
                for item in forecast[:3] if isinstance(item, dict)]
        if len(days) != min(3, len(forecast)):
            raise ValueError("Invalid forecast entries")
        return WeatherData(
            entity_id=entity, name=attributes.get("friendly_name") or entity,
            condition=state["state"], temperature=attributes.get("temperature"),
            temperature_unit=attributes.get("temperature_unit"), forecast=days)
    except ValueError as exc:
        raise EnergyError("Home Assistant returned invalid weather temperatures, units or dates.") from exc


class HomeAssistantWeather:
    def __init__(self, url: str, token_file: Path):
        self.client = HomeAssistantEnergy(url, token_file)

    async def updates(self, config: WeatherConfig) -> AsyncIterator[WeatherData]:
        if config.entity_id is None:
            raise EnergyError("Select a weather entity before subscribing.")
        cache = EntityCache({config.entity_id})
        forecast = None
        async with self.client.connection() as socket:
            await socket.send(json.dumps({"id": 1, "type": "subscribe_entities",
                                          "entity_ids": [config.entity_id]}))
            await socket.send(json.dumps({"id": 2, "type": "weather/subscribe_forecast",
                                          "entity_id": config.entity_id, "forecast_type": "daily"}))
            while True:
                try:
                    message = json.loads(await asyncio.wait_for(socket.recv(), timeout=5))
                except TimeoutError:
                    pong = await socket.ping()
                    await asyncio.wait_for(pong, timeout=5)
                    message = {}
                if message.get("type") == "result" and not message.get("success"):
                    raise EnergyError("Home Assistant rejected the daily weather subscription; "
                                      "check the entity and its daily-forecast support.")
                if message.get("type") == "event":
                    if message.get("id") == 1:
                        cache.apply(message["event"])
                    elif message.get("id") == 2:
                        if message["event"].get("type") != "daily":
                            raise EnergyError("Home Assistant returned an unexpected forecast type.")
                        forecast = message["event"].get("forecast")
                        if not isinstance(forecast, list) or not forecast:
                            raise EnergyError("Home Assistant has no daily forecast for this entity.")
                if config.entity_id in cache.states and forecast is not None:
                    yield weather_from_state(config.entity_id, cache.states[config.entity_id], forecast)


class WeatherPublisher(Protocol):
    def publish_weather(self, snapshot: dict) -> bool | None: ...


class WeatherService:
    def __init__(self, store: Store, device: WeatherPublisher, clock: Callable[[], datetime],
                 *, sandbox: bool):
        self.store, self.device, self.now, self.sandbox = store, device, clock, sandbox
        self.live_now = lambda: datetime.now(timezone.utc) if sandbox else self.now()
        self.lock = threading.RLock()
        self.delivery_lock = threading.Lock()
        self.monotonic = time.monotonic
        self.delivery_error: str | None = None
        self._last_signature = None
        self._last_delivery = float("-inf")
        self._latest: WeatherSnapshot | None = None
        if store.get("weather_config") is None:
            store.put("weather_config", WeatherConfig().model_dump())
        saved = store.get("weather_snapshot")
        if saved is not None:
            self._latest = WeatherSnapshot.model_validate(saved)
            if self._latest.status == "live":
                self._latest = self._latest.model_copy(update={"status": "stale",
                    "message": "Waiting for live weather after restart."})

    def config(self) -> WeatherConfig:
        return WeatherConfig.model_validate(self.store.get("weather_config"))

    def snapshot(self) -> WeatherSnapshot:
        source = "sandbox" if self.config().transport == "simulation" else "home_assistant"
        current = self._latest
        if current is None or current.source != source:
            return WeatherSnapshot(source=source, status="unconfigured",
                                   message="No weather forecast has been received.")
        if current.status == "live" and current.received_at is not None:
            age = (self.live_now() - current.received_at).total_seconds()
            if age > STALE_SECONDS or age < -5:
                return current.model_copy(update={"status": "stale",
                    "message": "Weather is stale; showing the last received forecast."})
        return current

    def poll(self) -> WeatherSnapshot:
        with self.lock:
            config = self.config()
            source = "sandbox" if config.transport == "simulation" else "home_assistant"
            if not config.enabled:
                result = WeatherSnapshot(source=source, status="disabled",
                                         message="Weather feed is disabled.")
            elif config.transport == "simulation" and self.sandbox:
                data = self.store.get("sandbox_weather_data")
                result = WeatherSnapshot(source=source,
                    status="simulated" if data is not None else "unconfigured",
                    received_at=self.now() if data is not None else None,
                    data=WeatherData.model_validate(data) if data is not None else None,
                    message="Explicit simulated weather." if data is not None
                    else "Set explicit sandbox weather data before testing.")
            else:
                result = WeatherSnapshot(source=source, status="unconfigured",
                    message="Waiting for the backend Home Assistant daily forecast. "
                            "Configure its weather entity and backend authorization.")
            self._latest = result
            return result

    def accept(self, data: WeatherData) -> WeatherSnapshot:
        with self.lock:
            config = self.config()
            if (not config.enabled or config.transport != "websocket"
                    or config.entity_id != data.entity_id):
                raise EnergyError("The selected live weather feed is disabled or changed.")
            self._latest = WeatherSnapshot(source="home_assistant", status="live",
                received_at=self.live_now(), data=data, message="Live Home Assistant daily forecast.")
            return self._latest

    def unavailable(self, message: str) -> WeatherSnapshot:
        with self.lock:
            previous = self.snapshot()
            data = previous.data if previous.source == "home_assistant" else None
            if previous.message != message:
                logger.warning("Weather unavailable: %s", message)
                self.store.event("weather_unavailable", message)
            self._latest = WeatherSnapshot(source="home_assistant",
                status="stale" if data is not None else "unavailable",
                received_at=previous.received_at if data is not None else None,
                data=data, message=message)
            return self._latest

    def checkpoint(self) -> None:
        with self.lock:
            if self._latest is not None:
                self.store.put("weather_snapshot", self._latest.model_dump(mode="json"))

    def deliver(self) -> bool:
        if not self.delivery_lock.acquire(blocking=False):
            return False
        try:
            with self.lock:
                inventory = self.store.get("inventory")
                if inventory is None or LAUNCHER not in inventory["installed"]:
                    return False
                snapshot = self.snapshot()
                signature = json.dumps([snapshot.source, snapshot.status, snapshot.message,
                    snapshot.data.model_dump(mode="json") if snapshot.data else None], sort_keys=True)
                now = self.monotonic()
                if signature == self._last_signature and now - self._last_delivery < 60:
                    return False
            try:
                delivered = self.device.publish_weather(snapshot.wire())
            except DeviceError as exc:
                message = "Weather panel delivery failed: " + str(exc)
                if self.delivery_error != message:
                    self.store.event("weather_delivery_error", message)
                self.delivery_error = message
                return False
            if delivered is False:
                self.delivery_error = "Weather delivery deferred while TV control is busy or offline."
                return False
            with self.lock:
                self.delivery_error = None
                self._last_signature = signature
                self._last_delivery = now
            return True
        finally:
            self.delivery_lock.release()

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            config = self.config()
            ready = (config.enabled and config.transport == "websocket" and config.entity_id
                     and os.environ.get("TVCONTROL_HA_URL")
                     and os.environ.get("TVCONTROL_HA_TOKEN_FILE"))
            if ready:
                try:
                    provider = HomeAssistantWeather(os.environ["TVCONTROL_HA_URL"],
                                                   Path(os.environ["TVCONTROL_HA_TOKEN_FILE"]))
                    async with aclosing(provider.updates(config)) as updates:
                        async for data in updates:
                            if stop.is_set() or self.config() != config:
                                break
                            await asyncio.to_thread(self.accept, data)
                            await asyncio.to_thread(self.deliver)
                except EnergyError as exc:
                    await asyncio.to_thread(self.unavailable, str(exc))
                    await asyncio.to_thread(self.deliver)
            else:
                await asyncio.to_thread(self.poll)
                await asyncio.to_thread(self.deliver)
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                continue
