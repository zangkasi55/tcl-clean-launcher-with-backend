import re
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

PACKAGE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
COMPONENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*/[A-Za-z_.$][A-Za-z0-9_.$]*$")
ASSET = re.compile(r"^(?:wallpaper|artwork)-[a-f0-9]{64}\.(?:jpg|png)$")
LAUNCHER = "com.gathin.tvhome"
ORIGINAL_HOME = "com.google.android.apps.tv.launcherx/.home.HomeActivity"
DEFAULT_APPS = [
    {"label": "YT", "package": "com.thegate.tv",
     "component": "com.thegate.tv/com.thegate.tv.MainActivity"},
    {"label": "Netflix", "package": "com.netflix.ninja",
     "component": "com.netflix.ninja/com.netflix.ninja.MainActivity"},
    {"label": "GodTV", "package": "com.iptv.newiptvapplication",
     "component": "com.iptv.newiptvapplication/com.iptv.newiptvapplication.LoginActivity"},
    {"label": "Monomax", "package": "com.doonung.dtv",
     "component": "com.doonung.dtv/com.doonung.activity.tv.ui.splash.TVSplashActivity"},
    {"label": "YouTube", "package": "com.google.android.youtube.tv",
     "component": "com.google.android.youtube.tv/com.google.android.apps.youtube.tv.activity.ShellActivity"},
]


def canonical_component(value: str) -> str:
    if not COMPONENT.fullmatch(value):
        raise ValueError("Invalid Android activity component")
    package, activity = value.split("/", 1)
    return f"{package}/{package + activity if activity.startswith('.') else activity}"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Rule(Model):
    enabled: StrictBool = False
    package: str = "com.thegate.tv"
    minutes: StrictInt = Field(default=40, ge=1, le=1440)
    timezone: str = "Asia/Bangkok"

    @field_validator("package")
    @classmethod
    def package_name(cls, value: str) -> str:
        if not PACKAGE.fullmatch(value):
            raise ValueError("Select a valid installed app package")
        return value

    @field_validator("timezone")
    @classmethod
    def timezone_name(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Use an IANA timezone, for example Asia/Bangkok") from exc
        return value


class RuleUpdate(Rule):
    confirm_exhausted: StrictBool = False


class Theme(Model):
    dock_apps: list[str] = Field(default_factory=lambda: [a["package"] for a in DEFAULT_APPS])
    app_order: list[str] = Field(default_factory=lambda: [a["component"] for a in DEFAULT_APPS])
    columns: StrictInt = Field(default=5, ge=4, le=7)
    background_color: str = "#0B1020"
    gradient_color: str = "#060A13"
    gradient_opacity: StrictInt = Field(default=0, ge=0, le=100)
    clock_color: str = "#FFFFFF"
    date_color: str = "#FFFFFF"
    input_color: str = "#FFFFFF"
    wifi_color: str = "#FFFFFF"
    settings_color: str = "#FFFFFF"
    animations: StrictBool = True
    ambient_glow: StrictBool = True
    advanced_effects: StrictBool = False
    sky_intensity: StrictInt = Field(default=0, ge=0, le=100)
    energy_panel: StrictBool = True
    weather_panel: StrictBool = True
    wallpaper_file: str | None = None
    tile_artwork: dict[str, str] = Field(default_factory=dict)

    @field_validator("dock_apps")
    @classmethod
    def favorites(cls, values: list[str]) -> list[str]:
        if not 1 <= len(values) <= 7 or len(values) != len(set(values)):
            raise ValueError("Choose one to seven distinct favorites")
        if any(not PACKAGE.fullmatch(value) for value in values):
            raise ValueError("Invalid favorite package")
        return values

    @field_validator("app_order")
    @classmethod
    def ordered_components(cls, values: list[str]) -> list[str]:
        values = [canonical_component(value) for value in values]
        if len(values) != len(set(values)) or len(values) > 256:
            raise ValueError("App order must contain distinct activity components")
        return values

    @field_validator("background_color", "gradient_color", "clock_color", "date_color",
                     "input_color", "wifi_color", "settings_color")
    @classmethod
    def color(cls, value: str) -> str:
        if not re.fullmatch(r"#[a-fA-F0-9]{6}", value):
            raise ValueError("Use an opaque #RRGGBB color")
        return value.upper()

    @field_validator("wallpaper_file")
    @classmethod
    def wallpaper(cls, value: str | None) -> str | None:
        if value is not None and not ASSET.fullmatch(value):
            raise ValueError("Select an uploaded wallpaper")
        return value

    @field_validator("tile_artwork")
    @classmethod
    def artwork(cls, values: dict[str, str]) -> dict[str, str]:
        result = {}
        for component, filename in values.items():
            if not ASSET.fullmatch(filename) or not filename.startswith("artwork-"):
                raise ValueError("Select an uploaded artwork image")
            result[canonical_component(component)] = filename
        return result

    def launcher_config(self) -> dict:
        result = self.model_dump(exclude_none=True)
        result.update(hidden_apps=[], module_cards=False, rows_below_dock=0,
                      live_tv_package="com.tcl.tv", wallpaper=self.wallpaper_file is not None)
        return result


class Observation(Model):
    at: datetime
    monotonic: float
    package: str | None
    awake: bool

    @field_validator("at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("TV observations require a timezone-aware timestamp")
        return value.astimezone(timezone.utc)


class HomeRequest(Model):
    target: Literal["clean", "original"]
    confirmation: str
    allow_home_override: StrictBool = False
    true_default: StrictBool = False
