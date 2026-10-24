import asyncio
import json
import logging
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import Field, StrictBool, field_validator, model_validator
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .energy_topology import (
    EnergyBindings,
    EnergyTopology,
    bindings_from_dashboard,
    topology_from_states,
)
from .models import LAUNCHER, Model
from .monitor import DeviceError
from .store import Store

logger = logging.getLogger(__name__)
ENTITY = re.compile(r"^sensor\.[a-z0-9_]+$")


class EnergyMetrics(Model):
    solar_w: float = Field(ge=0, allow_inf_nan=False)
    home_w: float = Field(ge=0, allow_inf_nan=False)
    grid_w: float = Field(allow_inf_nan=False)
    battery_w: float = Field(allow_inf_nan=False)
    battery_soc: float = Field(ge=0, le=100, allow_inf_nan=False)

    @field_validator("*", mode="before")
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Energy readings must be finite numeric values")
        return value


class EnergyConfig(Model):
    enabled: StrictBool = False
    solar_entity: str | None = None
    home_entity: str | None = None
    grid_entity: str | None = None
    battery_entity: str | None = None
    battery_soc_entity: str | None = None
    grid_import_entity: str | None = None
    grid_export_entity: str | None = None
    battery_discharge_entity: str | None = None
    battery_charge_entity: str | None = None
    transport: Literal["websocket", "browser_bridge", "simulation"] = "websocket"
    grid_import_positive: StrictBool = True
    battery_discharge_positive: StrictBool = True
    topology_dashboard: str | None = Field(default=None, pattern=r"^[a-z0-9_-]+$")
    topology_view: str = Field(default="Home", min_length=1, max_length=80)

    @field_validator("solar_entity", "home_entity", "grid_entity",
                     "battery_entity", "battery_soc_entity", "grid_import_entity",
                     "grid_export_entity", "battery_discharge_entity", "battery_charge_entity")
    @classmethod
    def entity_id(cls, value: str | None) -> str | None:
        if value is not None and not ENTITY.fullmatch(value):
            raise ValueError("Use an exact sensor.entity_id from Home Assistant")
        return value

    def mappings(self) -> dict[str, str | None]:
        result = {"solar_w": self.solar_entity, "home_w": self.home_entity,
                  "battery_soc": self.battery_soc_entity}
        if self.grid_entity is not None:
            result["grid_w"] = self.grid_entity
        else:
            result.update(grid_import_w=self.grid_import_entity, grid_export_w=self.grid_export_entity)
        if self.battery_entity is not None:
            result["battery_w"] = self.battery_entity
        else:
            result.update(battery_discharge_w=self.battery_discharge_entity,
                          battery_charge_w=self.battery_charge_entity)
        return result

    @model_validator(mode="after")
    def distinct_power_modes(self):
        if self.grid_entity and (self.grid_import_entity or self.grid_export_entity):
            raise ValueError("Use either a signed grid sensor or the separate import/export pair")
        if self.battery_entity and (self.battery_discharge_entity or self.battery_charge_entity):
            raise ValueError("Use either a signed battery sensor or the separate charge/discharge pair")
        return self


class EnergySnapshot(Model):
    source: Literal["sandbox", "home_assistant"]
    status: Literal["simulated", "live", "stale", "unavailable", "unconfigured", "disabled"]
    sampled_at: datetime | None = None
    received_at: datetime | None = None
    metrics: EnergyMetrics | None = None
    topology: EnergyTopology | None = None
    message: str | None = None

    def wire(self) -> dict:
        return {**self.model_dump(mode="json"),
                "sampled_at_ms": int(self.sampled_at.timestamp() * 1000)
                if self.sampled_at is not None else None,
                "received_at_ms": int(self.received_at.timestamp() * 1000)
                if self.received_at is not None else None, "stale_after_ms": 15000,
                "delivery_stale_after_ms": 30000}


ESS_LINK_CONFIG = EnergyConfig(
    enabled=True, transport="websocket",
    solar_entity="sensor.ess_link_pv_power", home_entity="sensor.ess_link_load_power",
    battery_soc_entity="sensor.ess_link_battery_soc",
    # The actual Portal Balance card reverses the raw grid import/export sensor names.
    grid_import_entity="sensor.ess_link_grid_export_power",
    grid_export_entity="sensor.ess_link_grid_import_power",
    battery_discharge_entity="sensor.ess_link_battery_discharge_power",
    battery_charge_entity="sensor.ess_link_battery_charge_power",
)


class HAState(Model):
    entity_id: str
    state: str
    attributes: dict[str, str | None]


class LiveEnergyInput(Model):
    connected: StrictBool
    states: dict[str, HAState]


class EnergyError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise EnergyError("Home Assistant redirected the request; check its API URL.")


def parse_state(payload: dict, metric: str) -> float:
    value = payload.get("state")
    if isinstance(value, bool):
        raise EnergyError(f"{metric}: Home Assistant returned a non-numeric reading.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EnergyError(f"{metric}: sensor is unavailable or not numeric.") from exc
    if not math.isfinite(number):
        raise EnergyError(f"{metric}: sensor reading is not finite.")
    attributes = payload.get("attributes")
    if not isinstance(attributes, dict):
        raise EnergyError(f"{metric}: sensor unit metadata is missing.")
    unit = attributes.get("unit_of_measurement")
    if metric == "battery_soc":
        if unit != "%" or not 0 <= number <= 100:
            raise EnergyError("Battery charge must be a percentage between 0 and 100.")
    elif unit == "kW":
        number *= 1000
    elif unit != "W":
        raise EnergyError(f"{metric}: expected W or kW, not energy totals such as kWh.")
    if not math.isfinite(number):
        raise EnergyError(f"{metric}: converted sensor reading is not finite.")
    if metric in {"solar_w", "home_w", "grid_import_w", "grid_export_w",
                  "battery_charge_w", "battery_discharge_w"} and number < 0:
        raise EnergyError(f"{metric}: expected nonnegative power.")
    return number


def metrics_from_states(config: EnergyConfig, states: dict[str, dict]) -> EnergyMetrics:
    if any(entity is None for entity in config.mappings().values()):
        raise EnergyError("Map every required Home Assistant sensor before enabling live readings.")
    readings = {}
    for metric, entity in config.mappings().items():
        if entity not in states or states[entity].get("entity_id") != entity:
            raise EnergyError("A configured energy sensor is missing from the live subscription.")
        readings[metric] = parse_state(states[entity], metric)
    grid = readings.get("grid_w")
    if grid is None:
        grid = readings["grid_import_w"] - readings["grid_export_w"]
    battery = readings.get("battery_w")
    if battery is None:
        battery = readings["battery_discharge_w"] - readings["battery_charge_w"]
    return EnergyMetrics(solar_w=readings["solar_w"], home_w=readings["home_w"],
                         grid_w=grid if config.grid_import_positive else -grid,
                         battery_w=battery if config.battery_discharge_positive else -battery,
                         battery_soc=readings["battery_soc"])


class EntityCache:
    def __init__(self, entities: set[str]):
        self.entities = entities
        self.states: dict[str, dict] = {}

    def apply(self, event: dict) -> dict[str, dict]:
        for entity, value in event.get("a", {}).items():
            if entity in self.entities:
                self.states[entity] = {"entity_id": entity, "state": value.get("s"),
                                       "attributes": dict(value.get("a", {}))}
        for entity in event.get("r", []):
            self.states.pop(entity, None)
        for entity, change in event.get("c", {}).items():
            if entity not in self.states:
                continue
            additions, removals = change.get("+", {}), change.get("-", {})
            if "s" in additions:
                self.states[entity]["state"] = additions["s"]
            self.states[entity]["attributes"].update(additions.get("a", {}))
            for key in removals.get("a", []):
                self.states[entity]["attributes"].pop(key, None)
        return self.states


class HomeAssistantEnergy:
    def __init__(self, url: str, token_file: Path):
        try:
            parts = urlsplit(url)
            parts.port
        except ValueError as exc:
            raise EnergyError("The Home Assistant URL has an invalid address or port.") from exc
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.path not in {"", "/"}):
            raise EnergyError("Configure a Home Assistant origin, without credentials or API paths.")
        if parts.scheme == "http":
            from .device import private_address
            try:
                private_address(parts.hostname)
            except ValueError as exc:
                raise EnergyError("Plain HTTP is only supported for a private LAN HA address.") from exc
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.topology_bindings: EnergyBindings | None = None
        self.monotonic = time.monotonic

    def read_token(self) -> str:
        try:
            token = self.token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise EnergyError("The backend's Home Assistant token file is unavailable.") from exc
        if not token or not token.isascii() or any(char.isspace() for char in token):
            raise EnergyError("The backend's Home Assistant token file is invalid.")
        return token

    def fetch(self, config: EnergyConfig) -> EnergyMetrics:
        if any(value is None for value in config.mappings().values()):
            raise EnergyError("Map every required Home Assistant sensor before enabling live readings.")
        token = self.read_token()
        states = {}
        for metric, entity in config.mappings().items():
            request = urllib.request.Request(self.url + "/api/states/" + str(entity),
                                             headers={"Authorization": "Bearer " + token,
                                                      "Accept": "application/json"})
            try:
                with self.opener.open(request, timeout=5) as response:
                    body = response.read(65537)
                if len(body) > 65536:
                    raise EnergyError("Home Assistant returned an unexpectedly large sensor response.")
                payload = json.loads(body)
            except urllib.error.HTTPError as exc:
                if exc.code in {401, 403}:
                    raise EnergyError("Home Assistant rejected the backend token.") from exc
                raise EnergyError(f"Home Assistant sensor request failed with HTTP {exc.code}.") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise EnergyError("Home Assistant is unreachable; live energy readings stopped.") from exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise EnergyError("Home Assistant did not return valid sensor JSON.") from exc
            if not isinstance(payload, dict) or payload.get("entity_id") != entity:
                raise EnergyError("Home Assistant returned a different or invalid sensor entity.")
            states[str(entity)] = payload
        return metrics_from_states(config, states)

    @asynccontextmanager
    async def connection(self):
        token = self.read_token()
        socket_url = self.url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        try:
            async with connect(socket_url + "/api/websocket", open_timeout=10,
                               ping_interval=None, max_size=1024 * 1024, proxy=None) as socket:
                greeting = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
                if greeting.get("type") != "auth_required":
                    raise EnergyError("Home Assistant did not start the expected WebSocket handshake.")
                await socket.send(json.dumps({"type": "auth", "access_token": token}))
                authenticated = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
                if authenticated.get("type") != "auth_ok":
                    raise EnergyError("Home Assistant rejected the backend WebSocket authorization.")
                yield socket
        except (WebSocketException, OSError, TimeoutError) as exc:
            raise EnergyError("The live Home Assistant WebSocket is disconnected.") from exc
        except (json.JSONDecodeError, TypeError, AttributeError, KeyError) as exc:
            raise EnergyError("Home Assistant sent an invalid WebSocket message.") from exc

    async def updates(self, config: EnergyConfig) -> AsyncIterator[dict[str, dict]]:
        entities = set(config.mappings().values())
        if config.topology_dashboard is None and None in entities:
            raise EnergyError("Complete the energy sensor mapping before subscribing.")
        try:
            async with self.connection() as socket:
                subscription_id = 1
                if config.topology_dashboard is not None:
                    await socket.send(json.dumps({"id": 1, "type": "lovelace/config",
                                                  "url_path": config.topology_dashboard}))
                    result = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
                    if result.get("id") != 1 or not result.get("success"):
                        raise EnergyError("Home Assistant rejected the energy dashboard request.")
                    try:
                        self.topology_bindings = bindings_from_dashboard(
                            result["result"], config.topology_view)
                    except (ValueError, KeyError) as exc:
                        raise EnergyError("The HA Energy Distribution card bindings are invalid.") from exc
                    entities = self.topology_bindings.entities()
                    subscription_id = 2
                cache = EntityCache({entity for entity in entities if entity is not None})
                await socket.send(json.dumps({"id": subscription_id, "type": "subscribe_entities",
                                               "entity_ids": sorted(cache.entities)}))
                last_config_check = self.monotonic()
                config_request_id = None
                next_request_id = subscription_id + 1
                while True:
                    if config.topology_dashboard is not None:
                        elapsed = self.monotonic() - last_config_check
                        if config_request_id is not None and elapsed > 10:
                            raise EnergyError("The HA energy-card configuration update timed out.")
                        if config_request_id is None and elapsed >= 60:
                            config_request_id = next_request_id
                            next_request_id += 1
                            await socket.send(json.dumps({"id": config_request_id, "type": "lovelace/config",
                                "url_path": config.topology_dashboard}))
                            last_config_check = self.monotonic()
                    try:
                        raw = await asyncio.wait_for(socket.recv(), timeout=5)
                    except TimeoutError:
                        pong = await socket.ping()
                        await asyncio.wait_for(pong, timeout=5)
                        if cache.states:
                            yield cache.states
                        continue
                    message = json.loads(raw)
                    if message.get("type") == "result" and not message.get("success"):
                        if message.get("id") == config_request_id:
                            raise EnergyError("Home Assistant rejected the energy dashboard refresh.")
                        raise EnergyError("Home Assistant rejected the energy state subscription.")
                    if (message.get("type") == "result" and config_request_id is not None
                            and message.get("id") == config_request_id):
                        config_request_id = None
                        try:
                            updated = bindings_from_dashboard(message["result"], config.topology_view)
                        except (ValueError, KeyError) as exc:
                            raise EnergyError("The updated HA energy-card bindings are invalid.") from exc
                        if updated.entities() != cache.entities:
                            return
                        self.topology_bindings = updated
                        if cache.states:
                            yield cache.states
                    if message.get("type") == "event" and message.get("id") == subscription_id:
                        yield cache.apply(message["event"])
        except (WebSocketException, OSError, TimeoutError) as exc:
            raise EnergyError("The live Home Assistant WebSocket is disconnected.") from exc
        except (json.JSONDecodeError, TypeError, AttributeError, KeyError) as exc:
            raise EnergyError("Home Assistant sent an invalid WebSocket message.") from exc


class EnergyPublisher(Protocol):
    def publish_energy(self, snapshot: dict) -> bool | None: ...


class EnergyService:
    def __init__(self, store: Store, device: EnergyPublisher, data_dir: Path,
                 clock: Callable[[], datetime], *, sandbox: bool,
                 default_config: EnergyConfig | None = None):
        self.store, self.device, self.data_dir = store, device, data_dir
        self.lock = threading.RLock()
        self.delivery_lock = threading.Lock()
        self.monotonic = time.monotonic
        self._latest: EnergySnapshot | None = None
        self._pending: EnergySnapshot | None = None
        self._last_delivery = float("-inf")
        self._last_attempt = float("-inf")
        self._last_signature: tuple | None = None
        self.delivery_error: str | None = None
        self.now, self.sandbox = clock, sandbox
        self.live_now = lambda: datetime.now(timezone.utc) if self.sandbox else self.now()
        if self.store.get("energy_config") is None:
            configured = default_config or EnergyConfig(
                enabled=sandbox, transport="simulation" if sandbox else "websocket")
            self.store.put("energy_config", configured.model_dump())
        elif sandbox and "transport" not in self.store.get("energy_config"):
            self.store.put("energy_config", {
                **self.store.get("energy_config"), "transport": "simulation"})
        if sandbox and self.store.get("sandbox_energy_metrics") is None:
            self.store.put("sandbox_energy_metrics", EnergyMetrics(
                solar_w=3470, home_w=880, grid_w=0, battery_w=-2590,
                battery_soc=24).model_dump())
        saved = self.store.get("energy_snapshot")
        if saved is not None:
            self._latest = EnergySnapshot.model_validate(saved)
            if self._latest.status == "live":
                self._latest = self._latest.model_copy(update={
                    "status": "stale", "message": "Waiting for the live source after restart."})

    def config(self) -> EnergyConfig:
        return EnergyConfig.model_validate(self.store.get("energy_config"))

    def snapshot(self) -> EnergySnapshot:
        saved = self._latest
        source = "sandbox" if self.config().transport == "simulation" else "home_assistant"
        if saved is None:
            return EnergySnapshot(source=source,
                                  status="unconfigured", message="No energy readings yet.")
        snapshot = saved
        if snapshot.source != source:
            return EnergySnapshot(source=source, status="unconfigured",
                                  message="Waiting for the selected live energy source.")
        contact = snapshot.received_at or snapshot.sampled_at
        if snapshot.status == "live" and contact is not None:
            age = (self.live_now() - contact).total_seconds()
            if age > 15 or age < -5:
                return snapshot.model_copy(update={
                    "status": "stale", "message": "Energy readings are stale; showing the last sample."})
        return snapshot

    def poll(self) -> EnergySnapshot:
        with self.lock:
            return self._poll()

    def _poll(self) -> EnergySnapshot:
        config = self.config()
        source = "sandbox" if config.transport == "simulation" else "home_assistant"
        if not config.enabled:
            snapshot = EnergySnapshot(source=source, status="disabled",
                                      message="Energy readings are disabled.")
        elif config.transport == "simulation" and self.sandbox:
            snapshot = EnergySnapshot(source=source, status="simulated", sampled_at=self.now(),
                                      metrics=EnergyMetrics.model_validate(
                                          self.store.get("sandbox_energy_metrics")))
        elif config.transport == "browser_bridge" and self.sandbox:
            snapshot = self.snapshot()
            if snapshot.status == "unconfigured":
                snapshot = snapshot.model_copy(update={
                    "message": "Waiting for live updates from the connected Home Assistant page."})
        elif not os.environ.get("TVCONTROL_HA_URL") or not os.environ.get("TVCONTROL_HA_TOKEN_FILE"):
            snapshot = EnergySnapshot(source=source, status="unconfigured",
                                      message="Set the backend HA URL and token file, then map sensors.")
        elif config.topology_dashboard is not None:
            snapshot = self.snapshot()
            if snapshot.topology is None:
                snapshot = EnergySnapshot(source=source, status="unconfigured",
                    message="Waiting for the HA Energy Distribution card subscription.")
        else:
            try:
                provider = HomeAssistantEnergy(os.environ["TVCONTROL_HA_URL"],
                                               Path(os.environ["TVCONTROL_HA_TOKEN_FILE"]))
                metrics = provider.fetch(config)
                snapshot = EnergySnapshot(source=source, status="live",
                                          sampled_at=self.live_now(), received_at=self.live_now(),
                                          metrics=metrics)
            except EnergyError as exc:
                return self.unavailable(str(exc))
        return self._commit(snapshot)

    def _commit(self, snapshot: EnergySnapshot) -> EnergySnapshot:
        self._latest = snapshot
        self._pending = snapshot
        return snapshot

    def checkpoint(self) -> None:
        with self.lock:
            if self._latest is not None:
                self.store.put("energy_snapshot", self._latest.model_dump(mode="json"))

    def deliver_pending(self) -> bool:
        if not self.delivery_lock.acquire(blocking=False):
            return False
        try:
            with self.lock:
                pending = self._pending
                if pending is None:
                    return False
                inventory = self.store.get("inventory")
                if inventory is None or LAUNCHER not in inventory["installed"]:
                    return False
                snapshot = self.snapshot()
                signature = (snapshot.source, snapshot.status,
                             tuple(snapshot.metrics.model_dump().values())
                             if snapshot.metrics else None, snapshot.message,
                             snapshot.topology.model_dump_json() if snapshot.topology else None)
                now = self.monotonic()
                if now - self._last_attempt < 1:
                    return False
                if signature == self._last_signature and now - self._last_delivery < 10:
                    return False
                self._last_attempt = now
            try:
                delivered = self.device.publish_energy(snapshot.wire())
            except DeviceError as exc:
                message = "Energy panel delivery failed: " + str(exc)
                with self.lock:
                    if self.delivery_error != message:
                        self.store.event("energy_delivery_error", message)
                    self.delivery_error = message
                return False
            with self.lock:
                if delivered is False:
                    self.delivery_error = "Energy delivery deferred while TV control is busy or offline."
                    return False
                self.delivery_error = None
                self._last_signature = signature
                self._last_delivery = now
                if self._pending is pending:
                    self._pending = None
                return True
        finally:
            self.delivery_lock.release()

    async def deliver(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.to_thread(self.deliver_pending)
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.25)
            except TimeoutError:
                continue

    def unavailable(self, message: str) -> EnergySnapshot:
        with self.lock:
            previous = self.snapshot()
            real_metrics = previous.metrics if previous.source == "home_assistant" else None
            topology = previous.topology if previous.source == "home_assistant" else None
            snapshot = EnergySnapshot(source="home_assistant",
                status="stale" if real_metrics is not None or topology is not None else "unavailable",
                sampled_at=previous.sampled_at if real_metrics is not None or topology is not None else None,
                received_at=previous.received_at if real_metrics is not None or topology is not None else None,
                metrics=real_metrics, topology=topology, message=message)
            if previous.message != message:
                logger.warning("Energy readings unavailable: %s", message)
                self.store.event("energy_unavailable", message)
            return self._commit(snapshot)

    def accept_states(self, states: dict[str, dict],
                      topology_bindings: EnergyBindings | None = None) -> EnergySnapshot:
        with self.lock:
            config = self.config()
            if not config.enabled or config.transport == "simulation":
                raise EnergyError("The live energy source is not enabled.")
            topology = None
            if config.topology_dashboard is not None:
                if topology_bindings is None:
                    raise EnergyError("Waiting for the HA energy-card bindings.")
                topology = topology_from_states(topology_bindings, states)
                metrics = None
            else:
                metrics = metrics_from_states(config, states)
            previous = self.snapshot()
            now = self.live_now()
            sampled_at = (previous.sampled_at if previous.source == "home_assistant"
                          and previous.metrics == metrics and previous.topology == topology
                          and previous.sampled_at else now)
            return self._commit(EnergySnapshot(
                source="home_assistant", status="live", sampled_at=sampled_at,
                received_at=now, metrics=metrics, topology=topology,
                message="Live Home Assistant browser feed." if config.transport == "browser_bridge"
                else "Live Home Assistant WebSocket subscription."))

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            config = self.config()
            ready = (config.enabled and config.transport == "websocket"
                     and (config.topology_dashboard is not None or all(config.mappings().values()))
                     and os.environ.get("TVCONTROL_HA_URL")
                     and os.environ.get("TVCONTROL_HA_TOKEN_FILE"))
            if ready:
                try:
                    provider = HomeAssistantEnergy(os.environ["TVCONTROL_HA_URL"],
                                                   Path(os.environ["TVCONTROL_HA_TOKEN_FILE"]))
                    async with aclosing(provider.updates(config)) as updates:
                        async for states in updates:
                            if stop.is_set() or self.config() != config:
                                break
                            try:
                                await asyncio.to_thread(self.accept_states, states,
                                                        provider.topology_bindings)
                            except EnergyError as exc:
                                await asyncio.to_thread(self.unavailable, str(exc))
                except EnergyError as exc:
                    await asyncio.to_thread(self.unavailable, str(exc))
            else:
                await asyncio.to_thread(self.poll)
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                continue
