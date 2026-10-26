import json
import threading
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import Field, StrictBool, StrictInt

from .models import DEFAULT_APPS, LAUNCHER, ORIGINAL_HOME, Model, Observation, canonical_component
from .monitor import DeviceError
from .store import Store

SAMPLE_APPS = [
    *DEFAULT_APPS,
    {"label": "3Plus", "package": "com.beci.thaitv3android",
     "component": "com.beci.thaitv3android/com.beci.thaitv3android.view.activity.SplashScreenActivity"},
    {"label": "Disney+", "package": "in.startv.hotstar.dplus.tv",
     "component": "in.startv.hotstar.dplus.tv/com.hotstar.MainActivity"},
    {"label": "BrowseHere", "package": "com.tcl.browser",
     "component": "com.tcl.browser/com.tcl.browser.portal.home.activity.HomePageActivity"},
    {"label": "Prime Video", "package": "com.amazon.amazonvideo.livingroom",
     "component": "com.amazon.amazonvideo.livingroom/com.amazon.ignition.IgnitionActivity"},
    {"label": "TrueID TV", "package": "com.truedmp.idtv",
     "component": "com.truedmp.idtv/com.truedmp.idtv.ui.MainActivity"},
    {"label": "WeTV", "package": "com.tencent.qqlivei18n",
     "component": "com.tencent.qqlivei18n/com.ktcp.video.activity.HomeActivity"},
]


class SandboxState(Model):
    at: datetime
    monotonic: float = 0
    package: str = "com.thegate.tv"
    awake: StrictBool = True
    online: StrictBool = True
    launcher_installed: StrictBool = False
    home: str = canonical_component(ORIGINAL_HOME)
    standby_requests: StrictInt = 0
    theme: dict | None = None


class SandboxStateUpdate(Model):
    package: str | None = None
    awake: StrictBool | None = None
    online: StrictBool | None = None


class SandboxAdvance(Model):
    seconds: StrictInt = Field(ge=1, le=86400)


class SandboxDevice:
    """An in-process TV model; it has no ADB connection or network transport."""

    simulation = True
    home_recovery_error = None
    host = "sandbox"

    def __init__(self, store: Store, data_dir: Path, start_at: datetime | None = None):
        self.store = store
        self.data_dir = data_dir
        self.lock = threading.RLock()
        saved = store.get("sandbox_state")
        self.state = (SandboxState.model_validate(saved) if saved else
                      SandboxState(at=start_at or datetime.now(timezone.utc)))
        if self.state.at.tzinfo is None:
            raise ValueError("Sandbox time must be timezone-aware")
        self._save()
        self._energy_panel = store.get("sandbox_energy_panel")

    def _save(self) -> None:
        self.store.put("sandbox_state", self.state.model_dump(mode="json"))

    def now(self) -> datetime:
        with self.lock:
            return self.state.at

    def advance_clock(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("The sandbox clock cannot run backwards")
        with self.lock:
            self.state.at += timedelta(seconds=seconds)
            self.state.monotonic += seconds
            self._save()

    def update(self, change: SandboxStateUpdate) -> None:
        with self.lock:
            if change.package is not None:
                if change.package not in {app["package"] for app in SAMPLE_APPS}:
                    raise ValueError("Select a simulated app from the catalog")
                self.state.package = change.package
            if change.awake is not None:
                self.state.awake = change.awake
            if change.online is not None:
                self.state.online = change.online
            self._save()

    def _require_online(self) -> None:
        if not self.state.online:
            raise DeviceError("Sandbox TV is intentionally offline; no usage is charged.")

    def observe(self) -> Observation:
        with self.lock:
            self._require_online()
            return Observation(at=self.state.at, monotonic=self.state.monotonic,
                               package=self.state.package, awake=self.state.awake)

    def sleep_if_foreground(self, package: str, *, valid_from: datetime | None = None,
                            valid_until: datetime | None = None) -> bool:
        with self.lock:
            current = self.observe()
            if (not current.awake or current.package != package
                    or (valid_from is not None and current.at < valid_from)
                    or (valid_until is not None and current.at >= valid_until)):
                return False
            self.state.awake = False
            self.state.standby_requests += 1
            self._save()
            return True

    def standby(self) -> bool:
        return self.sleep_if_foreground(self.state.package)

    def inventory(self) -> dict:
        with self.lock:
            self._require_online()
            packages = {app["package"] for app in SAMPLE_APPS}
            packages.update({"android", ORIGINAL_HOME.split("/")[0]})
            if self.state.launcher_installed:
                packages.add(LAUNCHER)
            return {
                "properties": {"ro.software.version_id": "SANDBOX (not live firmware)",
                               "ro.build.version.release": "14",
                               "ro.product.model": "Simulated TCL"},
                "installed": sorted(packages), "enabled": sorted(packages), "disabled": [],
                "home": self.state.home, "apps": [dict(app) for app in SAMPLE_APPS],
                "captured_at": self.now().isoformat(), "simulation": True,
            }

    def install(self, apk: Path) -> dict:
        with self.lock:
            before = self.inventory()
            self.state.launcher_installed = True
            self._save()
            return {"before": before, "after": self.inventory()}

    def native_home_status(self) -> None:
        return None

    def select_home(self, component: str, baseline: dict, *, true_default: bool = False) -> dict:
        with self.lock:
            self._require_online()
            if true_default:
                raise DeviceError("Native package changes and NAS failback require a real TV.")
            component = canonical_component(component)
            allowed = {canonical_component(ORIGINAL_HOME)}
            if self.state.launcher_installed:
                allowed.add(canonical_component(f"{LAUNCHER}/.MainActivity"))
            if component not in allowed:
                raise DeviceError("Install the simulated launcher before selecting it as Home.")
            self.state.home = component
            self._save()
            return self.inventory()

    def apply_theme(self, config: Path, assets: list[Path], revision: str) -> None:
        with self.lock:
            self._require_online()
            if not self.state.launcher_installed:
                raise DeviceError("Install the simulated launcher first.")
            self.state.theme = json.loads(config.read_text(encoding="utf-8"))
            self._save()

    def publish_energy(self, snapshot: dict) -> None:
        with self.lock:
            self._require_online()
            self._energy_panel = snapshot

    def publish_weather(self, snapshot: dict) -> None:
        with self.lock:
            self._require_online()
            self.store.put("sandbox_weather_panel", snapshot)

    def publish_vehicle(self, snapshot: dict) -> None:
        with self.lock:
            self._require_online()
            self.store.put("sandbox_vehicle_panel", snapshot)

    def screenshot(self) -> bytes:
        with self.lock:
            self._require_online()
            theme = self.state.theme or {}
            image = Image.new("RGB", (1280, 720), theme.get("background_color", "#0B1020"))
            wallpaper = theme.get("wallpaper_file")
            if wallpaper:
                with Image.open(self.data_dir / "assets" / wallpaper) as source:
                    image = ImageOps.fit(source.convert("RGB"), image.size)
            opacity = theme.get("gradient_opacity", 0)
            if opacity:
                shade = Image.new("RGB", image.size, theme.get("gradient_color", "#060A13"))
                mask = Image.linear_gradient("L").resize(image.size).point(
                    lambda value: round(value * opacity / 100))
                image.paste(shade, (0, 0), mask)
            draw = ImageDraw.Draw(image)
            title = ImageFont.load_default(size=34)
            text = ImageFont.load_default(size=20)
            draw.text((54, 38), "SANDBOX / NOT A REAL TV", font=text, fill="#A4E7C4")
            draw.text((54, 75), self.now().strftime("%H:%M"), font=text,
                      fill=theme.get("clock_color", "#FFFFFF"))
            draw.text((145, 75), self.now().strftime("%a %d %b"), font=text,
                      fill=theme.get("date_color", "#FFFFFF"))
            for label, x, key in [("Inputs", 900, "input_color"), ("Wi-Fi", 1030, "wifi_color"),
                                  ("Settings", 1140, "settings_color")]:
                draw.text((x, 38), label, font=text, fill=theme.get(key, "#FFFFFF"))
            if not self.state.awake:
                draw.text((54, 105), "Standby", font=title, fill="white")
            else:
                draw.text((54, 342), "Your favorites", font=title, fill="white")
                favorites = theme.get("dock_apps", [app["package"] for app in DEFAULT_APPS])
                for index, package in enumerate(favorites):
                    left = 54 + index * (1172 / len(favorites))
                    right = left + 1172 / len(favorites) - 12
                    draw.rounded_rectangle((left, 380, right, 480), radius=12, fill="#213646")
                    app = next(item for item in SAMPLE_APPS if item["package"] == package)
                    artwork = theme.get("tile_artwork", {}).get(app["component"])
                    if artwork:
                        with Image.open(self.data_dir / "assets" / artwork) as source:
                            tile = ImageOps.contain(source.convert("RGB"),
                                                    (int(right - left), 100))
                            image.paste(tile, (int(left), 380))
                    else:
                        draw.text((left + 12, 418), app["label"], font=text, fill="white")
                draw.text((54, 110), "Foreground: " + self.state.package,
                          font=ImageFont.load_default(size=14), fill="white")
                energy = self._energy_panel
                if energy is not None and energy.get("topology") and theme.get("energy_panel", True):
                    topology = energy["topology"]
                    values = topology["values"]

                    def power(value):
                        if value is None:
                            return "n/a"
                        return f"{value / 1000:.2f} kW" if abs(value) >= 1000 else f"{value:.0f} W"

                    draw.rounded_rectangle((54, 520, 438, 676), radius=14, fill="#20363c")
                    draw.text((72, 532), "Energy Distribution", font=text, fill="#A4E7C4")
                    draw.text((72, 559), topology["status_text"],
                              font=ImageFont.load_default(size=12), fill="#FFCC80")
                    readings = [("Enphase", power(topology["enphase_monitor"])),
                                ("ESS PV", power(values["ess"])),
                                ("Grid in/out", power(values["gridImport"]) + "/"
                                 + power(values["gridExport"])),
                                ("Battery", power(values["charge"]) + "/" + power(values["discharge"])),
                                ("Home CT" if topology["home_fallback_active"] else "Home",
                                 power(topology["home_display"])),
                                ("ESS PV state", "Not installed" if topology["planned"] else "Measured")]
                    for index, (label, reading) in enumerate(readings):
                        left, top = 72 + index % 3 * 116, 582 + index // 3 * 42
                        draw.text((left, top), label, font=ImageFont.load_default(size=11),
                                  fill="#BBC8D3")
                        draw.text((left, top + 16), reading, font=ImageFont.load_default(size=12),
                                  fill="#E8EDF4")
                elif energy is not None and energy.get("metrics") and theme.get("energy_panel", True):
                    metrics = energy["metrics"]
                    draw.rounded_rectangle((54, 520, 54 + 1280 * 0.3, 676),
                                           radius=14, fill="#20363c")
                    draw.text((72, 532), "Energy Distribution", font=text, fill="#A4E7C4")
                    source = "LIVE ENERGY" if energy.get("status") == "live" else energy.get(
                        "status", "unavailable").upper()
                    draw.text((72, 560), source, font=ImageFont.load_default(size=14),
                              fill="#BBC8D3")
                    labels = [("Solar", metrics["solar_w"]), ("Home", metrics["home_w"]),
                              ("Grid", metrics["grid_w"]), ("Battery", metrics["battery_w"])]
                    for index, (label, watts) in enumerate(labels):
                        left = 72 + (index % 2) * 176
                        top = 586 + (index // 2) * 26
                        draw.text((left, top), f"{label}: {watts:g} W",
                                  font=ImageFont.load_default(size=14), fill="#E8EDF4")
                    draw.text((72, 648), f"Battery charge: {metrics['battery_soc']:g}%",
                              font=ImageFont.load_default(size=14), fill="#FF4F92")
                vehicle_left = 54 + (400 if theme.get("energy_panel", True) else 0)
                if theme.get("vehicle_panel", True):
                    config = self.store.get("vehicle_config") or {}
                    vehicle = self.store.get("sandbox_vehicle_panel")
                    if config.get("transport") == "simulation":
                        vehicle = {"status": "simulated", "data": self.store.get("sandbox_vehicle_data")}
                    elif vehicle is not None and vehicle.get("source") == "sandbox":
                        vehicle = None
                    if not config.get("enabled"):
                        vehicle = None
                    data = vehicle.get("data") if vehicle else None
                    if vehicle is not None and vehicle.get("status") == "live":
                        received = vehicle.get("received_at_ms")
                        now_ms = datetime.now(timezone.utc).timestamp() * 1000
                        if (not isinstance(received, (int, float))
                                or not -5000 <= now_ms - received <= 900000):
                            vehicle = {**vehicle, "status": "stale"}
                    current = vehicle is not None and vehicle.get("status") in {"live", "simulated"}
                    activity = ("Charging" if data["charging"] else "Not charging") if data else "Unknown"
                    if data is not None and not current:
                        activity = "Last: " + activity
                    draw.rounded_rectangle((vehicle_left, 520, vehicle_left + 127, 676),
                                           radius=14, fill="#20303c")
                    draw.text((vehicle_left + 64, 544), "Haval", anchor="mm",
                              font=text, fill="#E8F3FF")
                    draw.rounded_rectangle((vehicle_left + 28, 562, vehicle_left + 96, 584),
                                           radius=3, outline="#A4E7C4", width=2)
                    if data is not None and data["battery_soc"] > 0:
                        draw.rectangle((vehicle_left + 31, 565,
                                        vehicle_left + 30 + max(1, round(62 * data["battery_soc"] / 100)), 581),
                                       fill="#A4E7C4")
                    draw.text((vehicle_left + 64, 612),
                              f"{data['battery_soc']:.0f}%" if data else "--%", anchor="mm",
                              font=ImageFont.load_default(size=30), fill="#E8F3FF")
                    draw.text((vehicle_left + 64, 642), activity, anchor="mm",
                              font=ImageFont.load_default(size=12), fill="#BBC8D3")
                    draw.text((vehicle_left + 64, 662),
                              vehicle.get("status", "unavailable") if vehicle else "Unavailable",
                              anchor="mm", font=ImageFont.load_default(size=11), fill="#BBC8D3")
                if theme.get("weather_panel", True):
                    weather_left = vehicle_left + (144 if theme.get("vehicle_panel", True) else 0)
                    config = self.store.get("weather_config") or {}
                    weather = self.store.get("sandbox_weather_panel")
                    if config.get("transport") == "simulation":
                        weather = {"status": "simulated",
                                   "data": self.store.get("sandbox_weather_data")}
                    elif weather is not None and weather.get("source") == "sandbox":
                        weather = None
                    if not config.get("enabled"):
                        weather = None
                    data = weather.get("data") if weather else None
                    draw.rounded_rectangle((weather_left, 520, weather_left + 384, 676), radius=14, fill="#20303c")
                    draw.text((weather_left + 18, 532), "Weather", font=text, fill="#E8F3FF")
                    draw.text((weather_left + 18, 560), weather.get("status", "unconfigured") if weather
                              else "No forecast", font=ImageFont.load_default(size=14),
                              fill="#BBC8D3")
                    if data is not None:
                        draw.text((weather_left + 18, 582),
                                  f"{data['temperature']:.1f}{data['temperature_unit']}",
                                  font=ImageFont.load_default(size=24), fill="#E8F3FF")
                        for index, day in enumerate(data["forecast"]):
                            at = datetime.fromisoformat(day["datetime"].replace("Z", "+00:00"))
                            left = weather_left + 18 + index * 118
                            draw.text((left, 618), at.strftime("%a"),
                                      font=ImageFont.load_default(size=14), fill="#BBC8D3")
                            low = "--" if day.get("templow") is None else f"{day['templow']:.1f}"
                            draw.text((left, 644), f"{day['temperature']:.1f}/{low}",
                                      font=ImageFont.load_default(size=14), fill="#E8F3FF")
                draw.text((1000, 648), "Down: All apps", font=text, fill="white")
            draw.text((54, 694), "Simulated clock: " + self.now().isoformat(),
                      font=ImageFont.load_default(size=14), fill="#BBC8D3")
            result = BytesIO()
            image.save(result, "PNG")
            return result.getvalue()

    def snapshot(self) -> dict:
        with self.lock:
            return {**self.state.model_dump(mode="json"), "clock_mode": "manual",
                    "catalog_is_sample": True, "real_device_access": False}

    def close(self) -> None:
        pass
