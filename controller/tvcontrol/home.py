import logging
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt, ValidationError, field_validator

from .models import LAUNCHER, ORIGINAL_HOME, Model, canonical_component
from .monitor import DeviceError

logger = logging.getLogger(__name__)
STOCK_HOME = ORIGINAL_HOME.split("/")[0]
SETUP_HOME = "com.google.android.tungsten.setupwraith"
HOME_PACKAGES = (STOCK_HOME, SETUP_HOME)
CLEAN_HOME = canonical_component(f"{LAUNCHER}/.MainActivity")


class HomeBackup(Model):
    version: Literal[1] = 1
    phase: Literal["switching", "active", "restoring", "fallback", "restored"]
    original_home: str
    package_states: dict[str, StrictInt]
    prior_exits: list[str] = Field(max_length=64)
    reason: str = ""

    @field_validator("original_home")
    @classmethod
    def stock_component(cls, value: str) -> str:
        if canonical_component(value) != canonical_component(ORIGINAL_HOME):
            raise ValueError("Only the recorded Google TV Home is supported")
        return canonical_component(value)

    @field_validator("package_states")
    @classmethod
    def exact_packages(cls, value: dict[str, int]) -> dict[str, int]:
        if set(value) != set(HOME_PACKAGES) or any(state not in {0, 1} for state in value.values()):
            raise ValueError("Expected the two original enabled Google TV package states")
        return value


def exit_records(raw: str) -> list[tuple[str, datetime, int]]:
    records = []
    for stamp, pid, reason in re.findall(
            r"timestamp=(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?)"
            r"\s+pid=(\d+)[^\n]*?\breason=(\d+)\s+\(", raw):
        try:
            at = datetime.fromisoformat(stamp)
        except ValueError as exc:
            raise DeviceError("The TV returned an invalid launcher exit timestamp.") from exc
        records.append((f"{stamp}/{pid}/{reason}", at, int(reason)))
    return records


def saved_wifi_owners(raw: str) -> set[str]:
    start = "WifiConfigManager - Configured networks Begin"
    end = "WifiConfigManager - Configured networks End"
    if start not in raw or end not in raw or raw.index(start) >= raw.index(end):
        raise DeviceError("Cannot verify saved Wi-Fi ownership; native Home was not changed.")
    configured = raw.split(start, 1)[1].split(end, 1)[0]
    return set(re.findall(r"\bcname=([^\s]+)", configured))


class NativeHome:
    """An opt-in, reversible HOME replacement; recovery needs authorized NAS ADB."""

    def __init__(self, shell: Callable[[str], str], data_dir: Path,
                 recovered: Callable[[], None] | None = None):
        self.shell = shell
        self.recovered = recovered
        self.path = data_dir / "native-home-backup.json"
        self.next_check = 0.0
        self.missing_since: float | None = None
        self.error: str | None = None

    def read(self) -> HomeBackup | None:
        if not self.path.exists():
            return None
        try:
            return HomeBackup.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise DeviceError("The native Home recovery backup cannot be read or is invalid.") from exc

    def write(self, backup: HomeBackup) -> None:
        temporary = self.path.with_suffix(".pending.json")
        try:
            temporary.write_text(backup.model_dump_json(), encoding="utf-8")
            temporary.replace(self.path)
        except OSError as exc:
            raise DeviceError("The native Home recovery backup could not be saved.") from exc

    def status(self) -> dict | None:
        backup = self.read()
        if backup is None:
            return None
        return {
            "phase": backup.phase,
            "armed": backup.phase == "active",
            "managed_packages": list(HOME_PACKAGES)
            if backup.phase in {"switching", "active", "restoring"} else [],
            "original_home": backup.original_home,
            "reason": backup.reason,
            "error": self.error,
            "requires_nas_adb": True,
        }

    def package_state(self, package: str) -> int:
        if package not in HOME_PACKAGES:
            raise DeviceError("The native Home recovery package is not supported.")
        raw = self.shell("dumpsys package " + package).split("Hidden system packages:", 1)[0]
        states = re.findall(r"\bUser 0:[^\n]*\benabled=(\d+)\b", raw)
        if len(states) != 1:
            raise DeviceError("Cannot determine the original enabled state of " + package)
        return int(states[0])

    def resolve(self) -> str | None:
        raw = self.shell("cmd package resolve-activity --brief --user 0 "
                         "-a android.intent.action.MAIN -c android.intent.category.HOME")
        components = [line.strip() for line in raw.splitlines() if "/" in line
                      and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*/[A-Za-z_.$][A-Za-z0-9_.$]*",
                                       line.strip())]
        return canonical_component(components[0]) if len(components) == 1 else None

    def preflight(self, original_home: str) -> HomeBackup:
        previous = self.read()
        if previous is not None and previous.phase in {"switching", "active", "restoring"}:
            raise DeviceError("Native Home already owns a recovery backup; restore original Home first.")
        if canonical_component(original_home) != canonical_component(ORIGINAL_HOME):
            raise DeviceError("Native Home supports the recorded Google TV launcher only.")
        sdk = self.shell("getprop ro.build.version.sdk")
        if not sdk.isdecimal() or int(sdk) < 30:
            raise DeviceError("Native Home failback requires Android 11 or later exit diagnostics.")
        states = {package: self.package_state(package) for package in HOME_PACKAGES}
        if any(value not in {0, 1} for value in states.values()):
            raise DeviceError("Restore both original Google TV packages before selecting native Home.")
        owners = saved_wifi_owners(self.shell("dumpsys wifi"))
        if SETUP_HOME in owners:
            raise DeviceError("The setup wizard owns saved Wi-Fi. Preserve/recreate those profiles "
                              "before native Home; disabling the wizard can delete its networks.")
        raw = self.shell(f"dumpsys activity exit-info {LAUNCHER}")
        return HomeBackup(phase="switching", original_home=original_home, package_states=states,
                          prior_exits=[record[0] for record in exit_records(raw)][:64])

    def activate(self, backup: HomeBackup) -> None:
        self.write(backup)
        try:
            # Remove the recovery HOME candidate before Google Home to avoid a setup crash loop.
            for package in reversed(HOME_PACKAGES):
                self.shell("pm disable-user --user 0 " + package)
                if self.package_state(package) != 3:
                    raise DeviceError("The TV did not disable its competing Home package: " + package)
            self.shell("cmd package set-home-activity --user 0 " + CLEAN_HOME)
            if self.resolve() != CLEAN_HOME:
                raise DeviceError("Clean Home did not become the actual HOME resolver.")
            backup.phase = "active"
            self.write(backup)
            self.next_check = 0
            self.missing_since = None
            self.error = None
        except DeviceError as exc:
            try:
                self.restore("Native Home activation failed: " + str(exc), automatic=True)
            except DeviceError as recovery:
                raise DeviceError(f"Native Home failed; recovery is pending: {recovery}") from exc
            raise

    def restore(self, reason: str, *, automatic: bool = False) -> bool:
        backup = self.read()
        if backup is None or backup.phase in {"restored", "fallback"}:
            return False
        backup.phase, backup.reason = "restoring", reason
        self.write(backup)
        # Restore Google Home first; opening only the setup recovery activity is unsafe.
        for package in HOME_PACKAGES:
            state = backup.package_states[package]
            command = "default-state" if state == 0 else "enable"
            self.shell(f"pm {command} --user 0 {package}")
            if self.package_state(package) != state:
                raise DeviceError("Original Home package restoration is pending: " + package)
        self.shell("cmd package set-home-activity --user 0 " + backup.original_home)
        if self.resolve() != backup.original_home:
            raise DeviceError("Original Home packages were restored but HOME resolution is unverified.")
        backup.phase = "fallback" if automatic else "restored"
        self.write(backup)
        self.error = None
        self.missing_since = None
        logger.warning("Native Home %s: %s", backup.phase, reason)
        if automatic and self.recovered is not None:
            self.recovered()
        return True

    def poll(self) -> None:
        now = time.monotonic()
        if now < self.next_check:
            return
        self.next_check = now + 10
        try:
            self.check(now)
        except DeviceError as exc:
            message = str(exc)
            if self.error != message:
                logger.warning("Native Home failback pending: %s", message)
            self.error = message
        else:
            self.error = None

    def check(self, now: float) -> None:
        backup = self.read()
        if backup is None or backup.phase in {"restored", "fallback"}:
            return
        if backup.phase != "active":
            self.restore("Recovered an interrupted native Home change.", automatic=True)
            return
        if self.resolve() != CLEAN_HOME:
            if self.missing_since is None:
                self.missing_since = now
            elif now - self.missing_since >= 15:
                self.restore("Clean Home was unavailable for at least 15 seconds.", automatic=True)
            return
        self.missing_since = None
        raw = self.shell(f"date '+%Y-%m-%d %H:%M:%S'; dumpsys activity exit-info {LAUNCHER}")
        stamp, separator, exits = raw.partition("\n")
        try:
            device_now = datetime.fromisoformat(stamp.strip())
        except ValueError as exc:
            raise DeviceError("Cannot verify the TV clock for native Home crash recovery.") from exc
        if not separator:
            raise DeviceError("The TV did not return launcher exit diagnostics.")
        recent = [reason for key, at, reason in exit_records(exits)
                  if key not in backup.prior_exits and 0 <= (device_now - at).total_seconds() <= 120]
        if 6 in recent:
            self.restore("Clean Home stopped responding (Android ANR).", automatic=True)
        elif sum(reason in {4, 5} for reason in recent) >= 3:
            self.restore("Clean Home crashed three times within two minutes.", automatic=True)


def managed_home_packages(inventory: dict) -> frozenset[str]:
    status = inventory.get("native_home")
    if not status or not status.get("managed_packages"):
        return frozenset()
    packages = status["managed_packages"]
    if (status.get("phase") != "active" or status.get("armed") is not True
            or not isinstance(packages, list) or len(packages) != len(HOME_PACKAGES)
            or set(packages) != set(HOME_PACKAGES) or inventory.get("home") != CLEAN_HOME
            or not set(packages) <= set(inventory["disabled"])
            or not set(packages) <= set(inventory["installed"])):
        raise DeviceError("The guarded native Home package-state exception is invalid.")
    return frozenset(packages)
