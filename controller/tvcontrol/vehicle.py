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


class VehicleConfig(Model):
    enabled: StrictBool = False
    battery_soc_entity: str | None = Field(default=None, pattern=r"^sensor\.[a-z0-9_]+$")
    charging_entity: str | None = Field(default=None, pattern=r"^binary_sensor\.[a-z0-9_]+$")
    transport: Literal["websocket", "simulation"] = "websocket"

    @model_validator(mode="after")
    def selected_entities(self):
        if self.enabled and self.transport == "websocket" and (
                self.battery_soc_entity is None or self.charging_entity is None):
            raise ValueError("Select both vehicle battery and charging entities before enabling the feed")
        return self


class VehicleData(Model):
    battery_soc_entity: str = Field(pattern=r"^sensor\.[a-z0-9_]+$")
    charging_entity: str = Field(pattern=r"^binary_sensor\.[a-z0-9_]+$")
    battery_soc: float = Field(ge=0, le=100, allow_inf_nan=False)
    charging: StrictBool

    @field_validator("battery_soc", mode="before")
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Vehicle battery percentage must be a finite numeric value")
        return value


class VehicleSnapshot(Model):
    source: Literal["home_assistant", "sandbox"]
    status: Literal["live", "simulated", "stale", "unavailable", "unconfigured", "disabled"]
    received_at: datetime | None = None
    data: VehicleData | None = None
    message: str | None = None

    @model_validator(mode="after")
    def real_payload(self):
        if self.received_at is not None and self.received_at.tzinfo is None:
            raise ValueError("Vehicle observation times require an explicit timezone")
        if self.status in {"live", "simulated"} and (
                self.data is None or self.received_at is None):
            raise ValueError("Live vehicle readings require battery, charging and observation time")
        if (self.status == "live" and self.source != "home_assistant"
                or self.status == "simulated" and self.source != "sandbox"):
            raise ValueError("Vehicle readings must identify their actual live or simulated source")
        return self

    def wire(self) -> dict:
        result = self.model_dump(mode="json")
        result.update(received_at_ms=int(self.received_at.timestamp() * 1000)
                      if self.received_at is not None else None,
                      stale_after_ms=STALE_SECONDS * 1000)
        return result


def vehicle_from_states(config: VehicleConfig, states: dict[str, dict]) -> VehicleData:
    soc = states.get(config.battery_soc_entity)
    charging = states.get(config.charging_entity)
    if (not isinstance(soc, dict) or soc.get("entity_id") != config.battery_soc_entity
            or not isinstance(soc.get("state"), str)
            or soc["state"] in {"unknown", "unavailable"}):
        raise EnergyError("The selected vehicle battery entity is unavailable.")
    if (not isinstance(charging, dict) or charging.get("entity_id") != config.charging_entity
            or not isinstance(charging.get("state"), str)
            or charging.get("state") not in {"on", "off"}):
        raise EnergyError("The selected vehicle charging state is unavailable; it is not assumed off.")
    attributes = soc.get("attributes")
    if not isinstance(attributes, dict) or attributes.get("unit_of_measurement") != "%":
        raise EnergyError("The vehicle battery entity must report a percentage (%) unit.")
    try:
        if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", soc["state"].strip()):
            raise ValueError("Invalid percentage")
        return VehicleData(battery_soc_entity=config.battery_soc_entity,
                           charging_entity=config.charging_entity,
                           battery_soc=float(soc["state"]), charging=charging["state"] == "on")
    except (ValueError, OverflowError) as exc:
        raise EnergyError("Home Assistant returned an invalid vehicle battery percentage (0-100).") from exc


class HomeAssistantVehicle:
    receive_timeout = 5
    refresh_seconds = 60

    def __init__(self, url: str, token_file: Path):
        self.client = HomeAssistantEnergy(url, token_file)
        self.monotonic = time.monotonic

    async def updates(self, config: VehicleConfig) -> AsyncIterator[VehicleData | None]:
        if config.battery_soc_entity is None or config.charging_entity is None:
            raise EnergyError("Select both vehicle entities before subscribing.")
        entities = [config.battery_soc_entity, config.charging_entity]
        cache = EntityCache(set(entities))
        subscription = command = 1
        async with self.client.connection() as socket:
            await socket.send(json.dumps({"id": subscription, "type": "subscribe_entities",
                                          "entity_ids": entities}))
            refresh_at = self.monotonic() + self.refresh_seconds
            while True:
                if self.monotonic() >= refresh_at:
                    command += 1
                    await socket.send(json.dumps({"id": command, "type": "unsubscribe_events",
                                                  "subscription": subscription}))
                    command += 1
                    subscription = command
                    cache = EntityCache(set(entities))
                    await socket.send(json.dumps({"id": subscription, "type": "subscribe_entities",
                                                  "entity_ids": entities}))
                    refresh_at = self.monotonic() + self.refresh_seconds
                try:
                    message = json.loads(await asyncio.wait_for(socket.recv(),
                                                                timeout=self.receive_timeout))
                except TimeoutError:
                    pong = await socket.ping()
                    await asyncio.wait_for(pong, timeout=self.receive_timeout)
                    # A pong is connection health, not a fresh observation of either entity.
                    yield None
                    continue
                if message.get("type") == "result" and not message.get("success"):
                    raise EnergyError("Home Assistant rejected the vehicle subscription; check both entities.")
                if message.get("type") == "event" and message.get("id") == subscription:
                    yield vehicle_from_states(config, cache.apply(message["event"]))


class VehiclePublisher(Protocol):
    def publish_vehicle(self, snapshot: dict) -> bool | None: ...


class VehicleService:
    def __init__(self, store: Store, device: VehiclePublisher, clock: Callable[[], datetime],
                 *, sandbox: bool):
        self.store, self.device, self.now, self.sandbox = store, device, clock, sandbox
        self.live_now = lambda: datetime.now(timezone.utc) if sandbox else self.now()
        self.lock = threading.RLock()
        self.delivery_lock = threading.Lock()
        self.monotonic = time.monotonic
        self.delivery_error: str | None = None
        self._last_signature = None
        self._last_delivery = float("-inf")
        self._latest: VehicleSnapshot | None = None
        if store.get("vehicle_config") is None:
            store.put("vehicle_config", VehicleConfig().model_dump())
        saved = store.get("vehicle_snapshot")
        if saved is not None:
            self._latest = VehicleSnapshot.model_validate(saved)
            if self._latest.status == "live":
                self._latest = self._latest.model_copy(update={"status": "stale",
                    "message": "Waiting for live vehicle readings after restart."})

    def config(self) -> VehicleConfig:
        return VehicleConfig.model_validate(self.store.get("vehicle_config"))

    def snapshot(self) -> VehicleSnapshot:
        with self.lock:
            config = self.config()
            source = "sandbox" if config.transport == "simulation" else "home_assistant"
            if not config.enabled:
                return VehicleSnapshot(source=source, status="disabled", message="Vehicle feed is disabled.")
            current = self._latest
            if (current is None or current.source != source
                    or source == "home_assistant" and current.data is not None and (
                        current.data.battery_soc_entity != config.battery_soc_entity
                        or current.data.charging_entity != config.charging_entity)):
                return VehicleSnapshot(source=source, status="unconfigured",
                                       message="No readings have been received for the selected vehicle.")
            if current.status == "live" and current.received_at is not None:
                age = (self.live_now() - current.received_at).total_seconds()
                if age > STALE_SECONDS or age < -5:
                    return current.model_copy(update={"status": "stale",
                        "message": "Vehicle readings are stale; activity is last known, not current."})
            return current

    def poll(self) -> VehicleSnapshot:
        with self.lock:
            config = self.config()
            source = "sandbox" if config.transport == "simulation" else "home_assistant"
            data = self.store.get("sandbox_vehicle_data") if self.sandbox else None
            if not config.enabled:
                result = VehicleSnapshot(source=source, status="disabled", message="Vehicle feed is disabled.")
            elif config.transport == "simulation" and self.sandbox:
                result = VehicleSnapshot(source="sandbox",
                    status="simulated" if data is not None else "unconfigured",
                    received_at=self.now() if data is not None else None,
                    data=VehicleData.model_validate(data) if data is not None else None,
                    message="Explicit simulated vehicle readings." if data is not None
                    else "Set explicit sandbox vehicle data before testing.")
            elif not os.environ.get("TVCONTROL_HA_URL") or not os.environ.get("TVCONTROL_HA_TOKEN_FILE"):
                return self.unavailable(
                    "Configure TVCONTROL_HA_URL and TVCONTROL_HA_TOKEN_FILE on the backend "
                    "before enabling live vehicle readings. No credentials belong on the TV.")
            else:
                result = VehicleSnapshot(source="home_assistant", status="unconfigured",
                    message="Waiting for vehicle battery and charging readings from backend Home Assistant.")
            self._latest = result
            return result

    def accept(self, data: VehicleData, selected: VehicleConfig | None = None) -> VehicleSnapshot:
        with self.lock:
            config = self.config()
            if (not config.enabled or config.transport != "websocket"
                    or selected is not None and config != selected
                    or config.battery_soc_entity != data.battery_soc_entity
                    or config.charging_entity != data.charging_entity):
                raise EnergyError("The selected live vehicle feed is disabled or changed.")
            self._latest = VehicleSnapshot(source="home_assistant", status="live",
                received_at=self.live_now(), data=data,
                message="Read-only Home Assistant vehicle battery and charging status.")
            return self._latest

    def unavailable(self, message: str) -> VehicleSnapshot:
        with self.lock:
            previous = self.snapshot()
            data = previous.data if previous.source == "home_assistant" else None
            if previous.message != message:
                logger.warning("Vehicle unavailable: %s", message)
                self.store.event("vehicle_unavailable", message)
            self._latest = VehicleSnapshot(source="home_assistant",
                status="stale" if data is not None else "unavailable",
                received_at=previous.received_at if data is not None else None,
                data=data, message=message)
            return self._latest

    def checkpoint(self) -> None:
        with self.lock:
            if self._latest is not None:
                self.store.put("vehicle_snapshot", self._latest.model_dump(mode="json"))

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
                delivered = self.device.publish_vehicle(snapshot.wire())
            except DeviceError as exc:
                message = "Vehicle panel delivery failed: " + str(exc)
                if self.delivery_error != message:
                    self.store.event("vehicle_delivery_error", message)
                self.delivery_error = message
                return False
            if delivered is False:
                self.delivery_error = "Vehicle delivery deferred while TV control is busy or offline."
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
            ready = (config.enabled and config.transport == "websocket"
                     and os.environ.get("TVCONTROL_HA_URL")
                     and os.environ.get("TVCONTROL_HA_TOKEN_FILE"))
            if ready:
                try:
                    provider = HomeAssistantVehicle(os.environ["TVCONTROL_HA_URL"],
                                                   Path(os.environ["TVCONTROL_HA_TOKEN_FILE"]))
                    async with aclosing(provider.updates(config)) as updates:
                        async for data in updates:
                            if stop.is_set() or self.config() != config:
                                break
                            if data is not None:
                                await asyncio.to_thread(self.accept, data, config)
                            await asyncio.to_thread(self.deliver)
                except EnergyError as exc:
                    if self.config() == config:
                        await asyncio.to_thread(self.unavailable, str(exc))
                        await asyncio.to_thread(self.deliver)
            else:
                await asyncio.to_thread(self.poll)
                await asyncio.to_thread(self.deliver)
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                continue
