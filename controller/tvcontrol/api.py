import asyncio
import hmac
import ipaddress
import json
import logging
import os
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, ValidationError

from .auth import Auth
from .device import Device, approved_disabled_packages, assert_preserved, private_address
from .energy import (
    ESS_LINK_CONFIG,
    EnergyConfig,
    EnergyError,
    EnergyMetrics,
    EnergyService,
    LiveEnergyInput,
)
from .ha_cards import DirectHaCards
from .images import MAX_UPLOAD, save_image
from .models import (
    ASSET,
    DEFAULT_APPS,
    LAUNCHER,
    HomeRequest,
    Model,
    Rule,
    RuleUpdate,
    Theme,
    canonical_component,
)
from .monitor import DeviceError, Monitor
from .sandbox import SandboxAdvance, SandboxDevice, SandboxStateUpdate
from .store import Store
from .vehicle import VehicleConfig, VehicleData, VehicleService
from .weather import WeatherConfig, WeatherData, WeatherService

logger = logging.getLogger(__name__)


class Login(Model):
    password: str = Field(min_length=1, max_length=1024)


class Target(Model):
    host: str


class Confirmation(Model):
    confirmation: str


class SandboxJump(Model):
    target: Literal["before_midnight", "next_day"]


def environment_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, str(default)).lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def https_origin(value: str) -> str:
    try:
        parts = urlsplit(value)
        parts.port
    except ValueError as exc:
        raise ValueError("Public/frame origins must be exact HTTPS origins") from exc
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.path not in {"", "/"} or parts.query or parts.fragment
            or any(char.isspace() or char in "'\";*\\<>" for char in value)):
        raise ValueError("Public/frame origins must be exact HTTPS origins")
    return value.rstrip("/")


def create_app(
    data_dir: Path | None = None,
    device=None,
    *,
    password: str | None = None,
    require_login: bool | None = None,
    secure_cookie: bool | None = None,
    run_monitor: bool = True,
    apk_path: Path | None = None,
    sandbox: bool = False,
    sandbox_start: datetime | None = None,
    energy_defaults: EnergyConfig | None = None,
) -> FastAPI:
    if sandbox and device is not None:
        raise ValueError("Sandbox mode cannot accept an external TV device")
    management_networks = tuple(ipaddress.ip_network(value.strip(), strict=False)
        for value in os.environ.get("TVCONTROL_MANAGEMENT_NETWORKS", "").split(",")
        if value.strip()) if not sandbox else ()
    require_login = False if sandbox else (environment_bool("TVCONTROL_REQUIRE_LOGIN", True)
                     if require_login is None else require_login)
    secure = False if sandbox else (environment_bool("TVCONTROL_SECURE_COOKIE", True)
              if secure_cookie is None else secure_cookie)
    public_value = "" if sandbox else os.environ.get("TVCONTROL_PUBLIC_ORIGIN", "")
    public_origin = https_origin(public_value) if public_value else ""
    ingress_origins = () if sandbox else tuple(dict.fromkeys(
        https_origin(value.strip())
        for value in os.environ.get("TVCONTROL_INGRESS_ORIGINS", "").split(",") if value.strip()))
    frame_values = [] if sandbox else [
        os.environ.get("TVCONTROL_FRAME_ORIGIN", ""),
        *os.environ.get("TVCONTROL_FRAME_ORIGINS", "").split(",")]
    frame_origins = tuple(dict.fromkeys(
        https_origin(value.strip()) for value in frame_values if value.strip()))
    data_name = "TclHomeControl-sandbox" if sandbox else "TclHomeControl"
    default_data = (Path(os.environ["LOCALAPPDATA"]) / "TclHomeControl"
                    if "LOCALAPPDATA" in os.environ
                    else Path.home() / ".local" / "share" / "tcl-home-control")
    default_data = default_data.with_name(data_name if "LOCALAPPDATA" in os.environ else
                                          ("tcl-home-control-sandbox" if sandbox else
                                           "tcl-home-control"))
    data_env = "TVCONTROL_SANDBOX_DATA_DIR" if sandbox else "TVCONTROL_DATA_DIR"
    data_dir = data_dir or Path(os.environ.get(data_env, str(default_data)))
    data_dir.mkdir(parents=True, exist_ok=True)
    marker = data_dir / "runtime-mode"
    mode = "sandbox" if sandbox else "real"
    if marker.exists() and marker.read_text(encoding="ascii").strip() != mode:
        raise ValueError("Sandbox and real-TV data directories must remain separate")
    if sandbox and not marker.exists() and (data_dir / "controller.sqlite3").exists():
        raise ValueError("Refusing to reuse an existing non-sandbox database")
    marker.write_text(mode + "\n", encoding="ascii")
    store = Store(data_dir / "controller.sqlite3")
    auth = Auth(data_dir, store, password or os.environ.get("TVCONTROL_ADMIN_PASSWORD"),
                required=require_login)
    host = store.get("host") or os.environ.get("TVCONTROL_TV_HOST", "192.168.1.148")
    device = SandboxDevice(store, data_dir, sandbox_start) if sandbox else (device or Device(host, data_dir))
    monitor = Monitor(store, device, clock=device.now if sandbox else None)
    if energy_defaults is None and os.environ.get("TVCONTROL_HA_PROFILE") == "ess_link":
        energy_defaults = ESS_LINK_CONFIG
    energy = EnergyService(store, device, data_dir, monitor.now, sandbox=sandbox,
                          default_config=energy_defaults)
    weather = WeatherService(store, device, monitor.now, sandbox=sandbox)
    vehicle = VehicleService(store, device, monitor.now, sandbox=sandbox)
    operations = threading.RLock()
    image_slots = asyncio.Semaphore(1)
    assets = data_dir / "assets"
    assets.mkdir(exist_ok=True)
    apk_path = apk_path or Path(os.environ.get("TVCONTROL_APK", "/app/launcher.apk"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = asyncio.Event()
        worker = asyncio.create_task(monitor.run(stop)) if run_monitor else None
        energy_worker = asyncio.create_task(energy.run(stop)) if run_monitor else None
        delivery_worker = asyncio.create_task(energy.deliver(stop)) if run_monitor else None
        weather_worker = asyncio.create_task(weather.run(stop)) if run_monitor else None
        vehicle_worker = asyncio.create_task(vehicle.run(stop)) if run_monitor else None
        app.state.worker = worker
        app.state.energy_worker = energy_worker
        app.state.delivery_worker = delivery_worker
        app.state.weather_worker = weather_worker
        app.state.vehicle_worker = vehicle_worker
        try:
            yield
        finally:
            stop.set()
            try:
                tasks = [task for task in [worker, energy_worker, delivery_worker, weather_worker,
                                          vehicle_worker]
                         if task is not None]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException):
                        raise result
            finally:
                energy.checkpoint()
                weather.checkpoint()
                vehicle.checkpoint()
                device.close()
                store.close()

    app = FastAPI(title="TCL Home Control", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.monitor, app.state.device = store, monitor, device
    app.state.energy = energy
    app.state.weather = weather
    app.state.vehicle = vehicle

    @app.middleware("http")
    async def headers(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and (not length.isdecimal() or int(length) > MAX_UPLOAD + 1024 * 1024):
            return JSONResponse({"detail": "Request body is too large"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' blob:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; "
            f"base-uri 'none'; frame-ancestors 'self'"
            f"{(' ' + ' '.join(frame_origins)) if frame_origins else ''}"
        )
        return response

    @app.exception_handler(DeviceError)
    async def device_error(request: Request, exc: DeviceError):
        logger.warning("Device operation failed: %s", exc)
        store.event("device_error", str(exc))
        return JSONResponse({"detail": str(exc)}, status_code=503)

    def origin_check(request: Request) -> None:
        origin = request.headers.get("origin")
        expected = public_origin or str(request.base_url).rstrip("/")
        if origin and origin not in (expected, *ingress_origins):
            raise HTTPException(403, "Unexpected request origin")

    def session(request: Request) -> dict:
        current = auth.session(request.cookies.get("tvcontrol_session"))
        if current is None:
            detail = ("Sign in to the parent control panel" if require_login else
                      "Refresh the control panel to renew its browser security session")
            raise HTTPException(401, detail)
        if request.method not in {"GET", "HEAD"}:
            if management_networks:
                try:
                    client = ipaddress.ip_address(request.client.host if request.client else "")
                except ValueError as exc:
                    raise HTTPException(403, "The management client address is not trusted") from exc
                if not any(client in network for network in management_networks):
                    raise HTTPException(403, "Settings changes are restricted to parent devices")
            origin_check(request)
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), current["csrf"]):
                raise HTTPException(403, "Missing or invalid request token")
        return current

    def known_apps() -> list[dict]:
        inventory = store.get("inventory")
        return inventory["apps"] if inventory else DEFAULT_APPS

    def validate_theme(theme: Theme) -> None:
        packages = {item["package"] for item in known_apps()}
        components = {item["component"] for item in known_apps()}
        baseline = store.get("baseline")
        if baseline is not None:
            approved = approved_disabled_packages(baseline)
            components.update(item["component"] for item in baseline["apps"]
                              if item["package"] in approved)
        if not set(theme.dock_apps) <= packages:
            raise HTTPException(422, "A favorite is not in the current app catalog")
        if not set(theme.app_order) <= components or not set(theme.tile_artwork) <= components:
            raise HTTPException(422, "An activity is not in the current app catalog")
        if len(theme.dock_apps) > theme.columns:
            raise HTTPException(422, "Choose enough columns to fit every favorite")
        for filename in [theme.wallpaper_file, *theme.tile_artwork.values()]:
            if filename is not None and not (assets / filename).is_file():
                raise HTTPException(422, "An uploaded image is missing")

    def inventory() -> dict:
        current = device.inventory()
        baseline = store.get("baseline")
        if baseline is None:
            store.put("baseline", current)
        else:
            assert_preserved(baseline, current)
        store.put("inventory", current)
        return current

    if sandbox:
        inventory()
        monitor.poll_once()
        energy.poll()

    @app.get("/healthz")
    def health():
        for name in ["worker", "energy_worker", "delivery_worker", "weather_worker", "vehicle_worker"]:
            worker = getattr(app.state, name, None)
            if worker is not None and worker.done():
                logger.error("The %s stopped unexpectedly", name, exc_info=worker.exception())
                return JSONResponse({"service": "monitor_failed"}, status_code=503)
        return {"service": "ok", "monitor": monitor.health}

    @app.post("/api/login")
    def login(body: Login, request: Request):
        if not require_login:
            raise HTTPException(404, "Password login is disabled for this LAN-only deployment")
        origin_check(request)
        address = request.client.host if request.client else "unknown"
        with auth.lock:
            if not auth.allowed(address):
                raise HTTPException(429, "Too many sign-in attempts; try again in five minutes")
            result = auth.login(body.password, address)
        if result is None:
            raise HTTPException(401, "Incorrect parent password")
        token, csrf = result
        response = JSONResponse({"csrf_token": csrf})
        response.set_cookie("tvcontrol_session", token, httponly=True, secure=secure,
                            samesite="strict", max_age=8 * 3600, path="/")
        return response

    @app.get("/api/session")
    def current_session(request: Request):
        origin_check(request)
        current = auth.session(request.cookies.get("tvcontrol_session"))
        if current is not None:
            return {"csrf_token": current["csrf"], "login_required": require_login}
        if require_login:
            raise HTTPException(401, "Sign in to the parent control panel")
        token, csrf = auth.new_session(authenticated=False)
        response = JSONResponse({"csrf_token": csrf, "login_required": False})
        response.set_cookie("tvcontrol_session", token, httponly=True, secure=secure,
                            samesite="strict", max_age=8 * 3600, path="/")
        return response

    @app.post("/api/logout", dependencies=[Depends(session)])
    def logout(request: Request):
        auth.store.logout(auth.token_hash(request.cookies["tvcontrol_session"]))
        response = JSONResponse({"signed_out": True})
        response.delete_cookie("tvcontrol_session", path="/")
        return response

    @app.get("/api/status", dependencies=[Depends(session)])
    def status():
        baseline = store.get("baseline")
        current = store.get("inventory")
        native_home = device.native_home_status()
        monitor_status = monitor.status()
        if (current is not None and current.get("native_home") != native_home
                and monitor_status["health"] != "unavailable"):
            with operations:
                current = store.get("inventory")
                native_home = device.native_home_status()
                cached_home = current.get("native_home") if current else None
                if (cached_home or {}).get("phase") != (native_home or {}).get("phase"):
                    current = inventory()
                    if native_home and native_home["phase"] == "fallback":
                        store.event("home_failback", native_home["reason"])
        return {
            "simulation": device.simulation,
            "sandbox": device.snapshot() if sandbox else None,
            "energy": energy.snapshot().wire(),
            "energy_transport": energy.config().transport,
            "energy_delivery_error": energy.delivery_error,
            "weather": weather.snapshot().wire(),
            "weather_delivery_error": weather.delivery_error,
            "vehicle": vehicle.snapshot().wire(),
            "vehicle_delivery_error": vehicle.delivery_error,
            "home_recovery_error": device.home_recovery_error,
            "native_home": native_home,
            "login_required": require_login,
            "management_restricted": bool(management_networks),
            "host": device.host, "monitor": monitor_status,
            "theme": Theme.model_validate(store.get("theme")).model_dump(),
            "applied_theme": store.get("applied_theme"),
            "apps": known_apps(), "inventory": current,
            "approved_disabled_packages": sorted(approved_disabled_packages(baseline))
            if baseline is not None else [],
            "baseline_counts": {key: len(baseline[key]) for key in
                                ["installed", "enabled", "disabled"]} if baseline else None,
            "apk_ready": sandbox or apk_path.is_file(), "events": store.events(),
        }

    @app.put("/api/target", dependencies=[Depends(session)])
    def target(body: Target):
        if sandbox:
            raise HTTPException(409, "Sandbox mode cannot connect to a real TV")
        try:
            address = private_address(body.host)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with monitor.lock:
            if store.get("baseline") and address != device.host:
                raise HTTPException(409, "This data volume is paired to a TV. "
                                    "Use a separate data volume for a different TV.")
            device.set_host(address)
            store.put("host", address)
            monitor.forget_interval()
        return {"host": address}

    def saved_ha_cards() -> dict:
        document = store.get("ha_cards")
        if document is None:
            raise HTTPException(409, "Save direct HA card mappings before exporting or sending them")
        try:
            return DirectHaCards.model_validate(document).model_dump()
        except ValidationError:
            logger.warning("invalid_saved_ha_cards")
            raise HTTPException(409, "Saved direct HA mappings are invalid; save them again") from None

    @app.get("/api/ha/cards", dependencies=[Depends(session)])
    def ha_cards():
        with operations:
            saved = store.get("ha_cards") is not None
            return {"saved": saved, "config": saved_ha_cards() if saved else None}

    @app.put("/api/ha/cards", dependencies=[Depends(session)])
    def ha_cards_update(body: DirectHaCards):
        document = body.model_dump()
        with operations:
            store.put("ha_cards", document)
        return {"saved": True, "config": document}

    @app.get("/api/ha/cards/export", dependencies=[Depends(session)])
    def ha_cards_export():
        with operations:
            return JSONResponse(saved_ha_cards(), headers={
                "Content-Disposition": 'attachment; filename="ha-cards.json"'})

    @app.post("/api/ha/cards/send", dependencies=[Depends(session)])
    def ha_cards_send():
        with operations:
            document = saved_ha_cards()
            if device.simulation:
                raise HTTPException(409, "Direct HA mappings cannot be sent to a simulated TV")
            if not device.publish_ha_cards(document):
                raise HTTPException(409, "TV is busy or offline; mappings remain saved. Retry Send.")
        store.event("ha_cards_sent", "Sent non-secret direct HA card mappings; TV source unchanged")
        return {"sent": True, "credentials_transferred": False, "source_changed": False}

    @app.get("/api/energy/config", dependencies=[Depends(session)])
    def energy_config():
        return {"config": energy.config().model_dump(), "simulation": sandbox,
                "ess_link_profile": ESS_LINK_CONFIG.model_dump(),
                "token_configured": not sandbox and
                bool(os.environ.get("TVCONTROL_HA_TOKEN_FILE"))}

    @app.get("/api/weather/config", dependencies=[Depends(session)])
    def weather_config():
        return {"config": weather.config().model_dump(), "simulation": sandbox,
                "token_configured": bool(os.environ.get("TVCONTROL_HA_TOKEN_FILE"))}

    @app.put("/api/weather/config", dependencies=[Depends(session)])
    def weather_config_update(body: WeatherConfig):
        if not sandbox and body.transport != "websocket":
            raise HTTPException(422, "Real-TV weather requires the backend Home Assistant feed")
        with weather.lock:
            store.put("weather_config", body.model_dump())
            snapshot = weather.poll()
        return {"config": body.model_dump(), "weather": snapshot.wire()}

    @app.get("/api/vehicle/config", dependencies=[Depends(session)])
    def vehicle_config():
        return {"config": vehicle.config().model_dump(), "simulation": sandbox,
                "token_configured": bool(os.environ.get("TVCONTROL_HA_URL")
                                         and os.environ.get("TVCONTROL_HA_TOKEN_FILE"))}

    @app.put("/api/vehicle/config", dependencies=[Depends(session)])
    def vehicle_config_update(body: VehicleConfig):
        if not sandbox and body.transport != "websocket":
            raise HTTPException(422, "Real-TV vehicle readings require the backend Home Assistant feed")
        with vehicle.lock:
            changed = vehicle.config() != body
            store.put("vehicle_config", body.model_dump())
            snapshot = vehicle.poll() if changed else vehicle.snapshot()
        return {"config": body.model_dump(), "vehicle": snapshot.wire()}

    @app.put("/api/energy/config", dependencies=[Depends(session)])
    def energy_config_update(body: EnergyConfig):
        if not sandbox and body.transport != "websocket":
            raise HTTPException(422, "Real-TV mode requires the backend WebSocket feed")
        if body.topology_dashboard is not None and body.transport != "websocket":
            raise HTTPException(422, "HA energy-card binding requires the backend WebSocket feed")
        if (body.enabled and body.transport != "simulation" and body.topology_dashboard is None
                and any(value is None for value in body.mappings().values())):
            raise HTTPException(422, "Map all required Home Assistant sensors before enabling the feed")
        with energy.lock:
            store.put("energy_config", body.model_dump())
            snapshot = energy.poll()
        return {"config": body.model_dump(), "energy": snapshot.wire()}

    @app.post("/api/energy/refresh", dependencies=[Depends(session)])
    def refresh_energy():
        return energy.poll().wire()

    @app.get("/api/energy/events", dependencies=[Depends(session)])
    async def energy_events(request: Request):
        async def updates():
            previous = None
            while not await request.is_disconnected():
                payload = json.dumps(energy.snapshot().wire(), separators=(",", ":"))
                if payload != previous:
                    yield "data: " + payload + "\n\n"
                    previous = payload
                else:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.5)
        return StreamingResponse(updates(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    @app.put("/api/rule", dependencies=[Depends(session)])
    def rule_update(body: RuleUpdate):
        if body.package not in {a["package"] for a in known_apps()}:
            raise HTTPException(422, "Select an app from the catalog")
        rule = Rule.model_validate(body.model_dump(exclude={"confirm_exhausted"}))
        with monitor.lock:
            used = store.usage(rule.package, rule.timezone, monitor.now())
            if rule.enabled and used >= rule.minutes * 60 and not body.confirm_exhausted:
                raise HTTPException(409, "This allowance is already exhausted. "
                                    "Confirm to enable standby enforcement immediately.")
            store.put("rule", rule.model_dump())
            monitor.forget_interval()
            store.event("rule_changed", f"Daily allowance: {rule.package}, "
                        f"{rule.minutes} min, {rule.timezone}, enabled={rule.enabled}.")
        return monitor.status()

    @app.post("/api/inventory", dependencies=[Depends(session)])
    def inspect():
        with operations:
            return inventory()

    @app.get("/api/baseline", dependencies=[Depends(session)])
    def baseline_download():
        baseline = store.get("baseline")
        if baseline is None:
            raise HTTPException(409, "Inspect the TV first to capture a baseline")
        return JSONResponse(baseline, headers={
            "Content-Disposition": 'attachment; filename="tv-app-baseline.json"'})

    @app.get("/api/screenshot", dependencies=[Depends(session)])
    def screenshot():
        return Response(device.screenshot(), media_type="image/png")

    @app.put("/api/theme", dependencies=[Depends(session)])
    def theme_update(body: Theme):
        validate_theme(body)
        with operations:
            store.put("theme", body.model_dump())
        return body.model_dump()

    @app.post("/api/images/{kind}", dependencies=[Depends(session)])
    async def image_upload(kind: Literal["wallpaper", "artwork"], file: UploadFile):
        try:
            async with image_slots:
                data = await file.read(MAX_UPLOAD + 1)
                name = await asyncio.to_thread(save_image, data, kind, assets)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            await file.close()
        return {"filename": name}

    @app.get("/api/assets/{filename}", dependencies=[Depends(session)])
    def asset(filename: str):
        if not ASSET.fullmatch(filename) or not (assets / filename).is_file():
            raise HTTPException(404, "Image not found")
        return FileResponse(assets / filename, headers={
            "Cache-Control": "private, max-age=31536000, immutable"})

    def activate(theme: Theme) -> None:
        validate_theme(theme)
        if LAUNCHER not in inventory()["installed"]:
            raise HTTPException(409, "Install and test the native launcher before applying a theme")
        revision = uuid.uuid4().hex
        revisions = data_dir / "revisions"
        revisions.mkdir(exist_ok=True)
        config = revisions / f"{revision}.json"
        config.write_text(json.dumps(theme.launcher_config(), ensure_ascii=False), encoding="utf-8")
        files = {value for value in [theme.wallpaper_file, *theme.tile_artwork.values()]
                 if value is not None}
        previous = store.get("applied_theme")
        if previous is not None:
            store.put("previous_theme", previous)
        device.apply_theme(config, [assets / name for name in sorted(files)], revision)
        assert_preserved(store.get("baseline"), device.inventory())
        store.put("applied_theme", theme.model_dump())
        store.put("theme", theme.model_dump())
        store.event("theme_applied", f"Theme revision {revision} applied; existing apps preserved.")

    @app.post("/api/theme/apply", dependencies=[Depends(session)])
    def theme_apply():
        with operations:
            activate(Theme.model_validate(store.get("theme")))
        return {"applied": True}

    @app.post("/api/theme/undo", dependencies=[Depends(session)])
    def theme_undo():
        with operations:
            previous = store.get("previous_theme")
            if previous is None:
                raise HTTPException(409, "No previous applied theme is available; "
                                    "restore the original Home if needed.")
            activate(Theme.model_validate(previous))
        return {"restored": True}

    @app.post("/api/install", dependencies=[Depends(session)])
    def install(body: Confirmation):
        if body.confirmation != "INSTALL_LAUNCHER":
            raise HTTPException(422, "Explicit launcher installation confirmation is required")
        if not sandbox and not apk_path.is_file():
            raise HTTPException(409, "Build/package the launcher APK first")
        with operations:
            inventory()
            result = device.install(apk_path)
            assert_preserved(store.get("baseline"), result["after"])
            store.put("inventory", result["after"])
            energy.poll()
            store.event("launcher_installed",
                        "Opened the new launcher; existing package states and Home choice preserved.")
        return {"installed": True, "default_home_changed": False}

    @app.post("/api/home", dependencies=[Depends(session)])
    def select_home(body: HomeRequest):
        if body.confirmation != "CHANGE_HOME":
            raise HTTPException(422, "Explicit Home-change confirmation is required")
        if body.true_default and (body.target != "clean" or body.allow_home_override):
            raise HTTPException(422, "Native Home requires a clean target without compatibility mode")
        with operations:
            baseline = store.get("baseline")
            if baseline is None:
                raise HTTPException(409, "Capture the original Home before changing it")
            component = (f"{LAUNCHER}/.MainActivity" if body.target == "clean" else baseline["home"])
            if body.target == "original" and canonical_component(component).startswith(LAUNCHER + "/"):
                raise HTTPException(409, "The saved baseline is not an original stock Home")
            current = (device.select_home(component, baseline, true_default=True)
                       if body.true_default else
                       device.select_home(component, baseline, allow_override=True)
                       if body.allow_home_override and body.target == "clean" else
                       device.select_home(component, baseline))
            store.put("inventory", current)
            store.event("home_changed", "Selected " + body.target + " Home; "
                        + ("native default with NAS failback armed; stock packages retained."
                           if body.true_default else "app states preserved."))
        return {"home": current["home"], "mode": current.get("home_mode", "native")}

    @app.post("/api/standby", dependencies=[Depends(session)])
    def standby(body: Confirmation):
        if body.confirmation != "STANDBY_NOW":
            raise HTTPException(422, "Confirm that the TV should enter standby now")
        requested = device.standby()
        store.event("manual_standby", "Standby requested." if requested else "TV was already asleep.")
        return {"standby_requested": requested, "confirmed": False}

    if sandbox:
        @app.put("/api/sandbox/vehicle", dependencies=[Depends(session)])
        def sandbox_vehicle(body: VehicleData):
            if vehicle.config().transport != "simulation":
                raise HTTPException(409, "Simulation cannot overwrite the live vehicle feed")
            with vehicle.lock:
                store.put("sandbox_vehicle_data", body.model_dump(mode="json"))
                return vehicle.poll().wire()

        @app.put("/api/sandbox/weather", dependencies=[Depends(session)])
        def sandbox_weather(body: WeatherData):
            if weather.config().transport != "simulation":
                raise HTTPException(409, "Simulation cannot overwrite the live weather feed")
            with weather.lock:
                store.put("sandbox_weather_data", body.model_dump(mode="json"))
                return weather.poll().wire()

        @app.put("/api/sandbox/state", dependencies=[Depends(session)])
        def sandbox_state(body: SandboxStateUpdate):
            if not body.model_dump(exclude_none=True):
                raise HTTPException(422, "Choose an app, power state or connection state")
            with operations, monitor.lock:
                monitor.poll_once()
                try:
                    device.update(body)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
                monitor.forget_interval()
                monitor.poll_once()
                energy.poll()
                return {"sandbox": device.snapshot(), "monitor": monitor.status()}

        @app.post("/api/sandbox/advance", dependencies=[Depends(session)])
        def sandbox_advance(body: SandboxAdvance):
            with operations, monitor.lock:
                monitor.poll_once()
                remaining = body.seconds
                while remaining:
                    step = min(monitor.poll_seconds, remaining)
                    device.advance_clock(step)
                    monitor.poll_once()
                    remaining -= step
                monitor.poll_once()
                energy.poll()
                store.event("sandbox_time", f"Advanced the simulated clock by {body.seconds} seconds.")
                return {"sandbox": device.snapshot(), "monitor": monitor.status()}

        @app.post("/api/sandbox/jump", dependencies=[Depends(session)])
        def sandbox_jump(body: SandboxJump):
            with operations, monitor.lock:
                tz = ZoneInfo(store.rule().timezone)
                now = device.now()
                next_day = now.astimezone(tz).date() + timedelta(days=1)
                midnight = datetime.combine(next_day, datetime.min.time(), tz).astimezone(timezone.utc)
                target = midnight + timedelta(seconds=1 if body.target == "next_day" else -10)
                if target <= now:
                    target = datetime.combine(next_day + timedelta(days=1), datetime.min.time(),
                                              tz).astimezone(timezone.utc) - timedelta(seconds=10)
                monitor.forget_interval()
                device.advance_clock((target - now).total_seconds())
                monitor.poll_once()
                energy.poll()
                store.event("sandbox_jump", "Skipped unobserved simulated time to " + body.target)
                return {"sandbox": device.snapshot(), "monitor": monitor.status()}

        @app.put("/api/sandbox/energy", dependencies=[Depends(session)])
        def sandbox_energy(body: EnergyMetrics):
            with energy.lock:
                if energy.config().transport != "simulation":
                    raise HTTPException(409, "Simulation inputs cannot overwrite the live energy feed")
                store.put("sandbox_energy_metrics", body.model_dump())
                snapshot = energy.poll()
            return snapshot.wire()

        @app.post("/api/sandbox/energy/live", dependencies=[Depends(session)])
        def sandbox_live_energy(body: LiveEnergyInput):
            if energy.config().transport != "browser_bridge" or not energy.config().enabled:
                raise HTTPException(409, "Enable the Home Assistant browser bridge first")
            if not body.connected:
                snapshot = energy.unavailable("The authorized Home Assistant page is disconnected.")
                return JSONResponse(snapshot.wire(), status_code=503)
            try:
                snapshot = energy.accept_states({
                    entity: state.model_dump() for entity, state in body.states.items()})
            except EnergyError as exc:
                snapshot = energy.unavailable(str(exc))
                return JSONResponse(snapshot.wire(), status_code=503)
            return snapshot.wire()

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
    return app


def create_sandbox_app() -> FastAPI:
    return create_app(sandbox=True, energy_defaults=ESS_LINK_CONFIG.model_copy(
        update={"transport": "browser_bridge"}))
