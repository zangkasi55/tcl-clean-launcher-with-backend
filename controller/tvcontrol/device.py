import base64
import hashlib
import ipaddress
import json
import logging
import os
import re
import shlex
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from adb_shell import exceptions
from adb_shell.adb_device import AdbDevice
from adb_shell.auth.keygen import keygen
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from adb_shell.transport.tcp_transport import TcpTransport

from .home import CLEAN_HOME, NativeHome, managed_home_packages
from .models import COMPONENT, DEFAULT_APPS, LAUNCHER, PACKAGE, Observation, canonical_component
from .monitor import DeviceError

logger = logging.getLogger(__name__)
ADB_ERRORS = (
    OSError, exceptions.AdbConnectionError, exceptions.AdbTimeoutError,
    exceptions.AdbCommandFailureException, exceptions.DeviceAuthError,
    exceptions.InvalidResponseError, exceptions.InvalidChecksumError,
    exceptions.InvalidCommandError, exceptions.TcpTimeoutException,
    exceptions.DevicePathInvalidError, exceptions.PushFailedError,
)
THEME_DIR = f"/sdcard/Android/data/{LAUNCHER}/files"
HOME_OVERRIDE_SERVICE = f"{LAUNCHER}/.HomeOverrideService"


class CompleteTcpTransport(TcpTransport):
    def bulk_write(self, data: bytes, transport_timeout_s: float | None) -> int:
        # adb-shell does not retry a short socket.send(), leaving its ADB packet incomplete.
        deadline = (None if transport_timeout_s is None
                    else time.monotonic() + transport_timeout_s)
        offset = 0
        while offset < len(data):
            remaining = None if deadline is None else max(0, deadline - time.monotonic())
            if remaining == 0 and offset:
                raise exceptions.TcpTimeoutException(
                    "Sending an ADB packet timed out after a partial write.")
            written = super().bulk_write(data[offset:], remaining)
            if written <= 0:
                raise ConnectionResetError("The TV closed its ADB connection during a write.")
            offset += written
        return offset


def parse_observation(raw: str, at: datetime, monotonic: float) -> Observation:
    power, separator, activities = raw.partition("__TVCONTROL_ACTIVITIES__")
    wakefulness = re.search(r"\bmWakefulness=(Awake|Asleep|Dozing|Dreaming)\b", power)
    if not separator or wakefulness is None:
        raise DeviceError("TV power state is unknown; usage and standby were not assumed.")
    awake = wakefulness[1] == "Awake"
    packages = re.findall(
        r"(?:topResumedActivity|mResumedActivity)\s*[:=].*?\bu\d+\s+"
        r"([A-Za-z_][A-Za-z0-9_.]*)/[A-Za-z0-9_.$]+", activities)
    distinct = set(packages)
    if awake and len(distinct) != 1:
        raise DeviceError("Foreground app is unknown or ambiguous; usage was not charged.")
    return Observation(at=at, monotonic=monotonic, package=packages[0] if packages else None,
                       awake=awake)


def private_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("Use the TV's private LAN IPv4 address") from exc
    tailscale = ipaddress.ip_network("100.64.0.0/10")
    if (address.version != 4 or address.is_loopback or address.is_link_local
            or address.is_multicast or address.is_unspecified
            or not (address.is_private or address in tailscale)):
        raise ValueError("Only a private LAN or Tailscale TV address is allowed")
    return str(address)


class Device:
    simulation = False

    def __init__(self, host: str, data_dir: Path):
        self.host = private_address(host)
        self.lock = threading.RLock()
        self.data_dir = data_dir
        self.connection: AdbDevice | None = None
        self._priority_state = threading.Lock()
        self._priority_waiters = 0
        self.home_recovery_error: str | None = None
        self._home_recovery_pending = False
        self._home_recovery_retry_at = 0.0
        self.native_home = NativeHome(self._shell, data_dir, self._show_recovered_home)

    @contextmanager
    def priority_io(self):
        with self._priority_state:
            self._priority_waiters += 1
        try:
            with self.lock:
                yield
        finally:
            with self._priority_state:
                self._priority_waiters -= 1

    def _connect(self) -> AdbDevice:
        if self.connection is not None and self.connection.available:
            return self.connection
        key_dir = self.data_dir / "private-adb"
        key_dir.mkdir(mode=0o700, exist_ok=True)
        key = key_dir / "adbkey"
        public = key_dir / "adbkey.pub"
        if key.exists() != public.exists():
            raise DeviceError("Incomplete ADB identity. Restore both key files before reconnecting.")
        if not key.exists():
            keygen(str(key))
            os.chmod(key, 0o600)
        signer = PythonRSASigner(public.read_text(), key.read_text())
        dev = AdbDevice(CompleteTcpTransport(self.host, 5555), default_transport_timeout_s=5)
        self.connection = dev
        if not dev.connect(rsa_keys=[signer], auth_timeout_s=5,
                           transport_timeout_s=5, read_timeout_s=6):
            raise DeviceError("Approve this controller's USB debugging prompt on the TV.")
        self._attempt_home_recovery(dev)
        return dev

    def _attempt_home_recovery(self, connection: AdbDevice) -> None:
        now = time.monotonic()
        if now < self._home_recovery_retry_at:
            return
        self._home_recovery_retry_at = now + 5
        try:
            self._restore_home_override_after_boot(connection)
        except DeviceError as exc:
            message = str(exc)
            if message != self.home_recovery_error:
                logger.warning("Home recovery pending: %s", message)
            self.home_recovery_error = message
            self._home_recovery_pending = True
        else:
            self.home_recovery_error = None
            self._home_recovery_pending = False

    def _restore_home_override_after_boot(self, connection: AdbDevice) -> None:
        backup = self.data_dir / "home-override-backup.json"
        if not backup.exists():
            return
        saved = self._read_home_backup(backup)
        raw = connection.shell("settings get global boot_count; getprop sys.boot_completed; "
                               "settings get secure enabled_accessibility_services; "
                               "settings get secure accessibility_enabled",
                               transport_timeout_s=5, read_timeout_s=5, timeout_s=10)
        parts = raw.splitlines()
        if len(parts) != 4 or not parts[0].isdecimal() or parts[3] not in {"0", "1"}:
            raise DeviceError("Cannot verify the selected Home override after TV reconnect.")
        boot = int(parts[0])
        previous_boot = saved.get("boot_count")
        selected = any(self._is_home_service(value) for value in parts[2].split(":"))
        if previous_boot is not None and previous_boot != boot and parts[1] != "1":
            raise DeviceError("Waiting for TV startup before restoring its selected Home override.")
        if (previous_boot is not None and selected
                and (previous_boot != boot or self._home_recovery_pending)):
            if not self._home_service_bound(connection):
                self._bootstrap_home_after_boot(connection)
                self._rebind_home_override(connection, parts[2])
            connection.shell("settings put secure accessibility_enabled 1",
                             transport_timeout_s=5, read_timeout_s=5, timeout_s=10)
            flag = connection.shell("settings get secure accessibility_enabled",
                                    transport_timeout_s=5, read_timeout_s=5, timeout_s=10).strip()
            if flag != "1":
                raise DeviceError("The TV did not restore its selected Home compatibility service.")
            logger.info("Restored the selected Home override after TV boot %s", boot)
        if previous_boot != boot:
            saved["boot_count"] = boot
            self._write_home_backup(backup, saved)

    @staticmethod
    def _bootstrap_home_after_boot(connection: AdbDevice) -> None:
        raw = connection.shell(
            "dumpsys power | sed -n '/mWakefulness=/p'; "
            "printf '\\n__TVCONTROL_ACTIVITIES__\\n'; "
            "dumpsys activity activities | sed -n '/topResumedActivity=/p;/mResumedActivity:/p'",
            transport_timeout_s=2, read_timeout_s=2, timeout_s=3)
        observation = parse_observation(raw, datetime.now(timezone.utc), time.monotonic())
        components = {canonical_component(package + "/" + activity)
                      for package, activity in re.findall(
                          r"(?:topResumedActivity|mResumedActivity)\s*[:=].*?\bu\d+\s+"
                          r"([A-Za-z_][A-Za-z0-9_.]*)/([A-Za-z0-9_.$]+)", raw)}
        clean = canonical_component(f"{LAUNCHER}/.MainActivity")
        stock = ("com.google.android.apps.tv.launcherx/"
                 "com.google.android.apps.tv.launcherx.home.HomeActivity")
        if not observation.awake or not components or not components <= {clean, stock}:
            raise DeviceError("Waiting for TV Home; the current app or input is left untouched. "
                              "Usage monitoring continues.")
        if clean in components:
            return
        started = connection.shell(
            f"am start -W -c android.intent.category.HOME -n {LAUNCHER}/.MainActivity",
            transport_timeout_s=10, read_timeout_s=10, timeout_s=15)
        if "Status: ok" not in started:
            raise DeviceError("The TV did not open clean Home for its boot service startup.")

    @staticmethod
    def _home_service_bound(connection: AdbDevice) -> bool:
        raw = connection.shell(f"dumpsys activity services {HOME_OVERRIDE_SERVICE}",
                               transport_timeout_s=2, read_timeout_s=2, timeout_s=3)
        return ("HomeOverrideService" in raw and "app=ProcessRecord{" in raw
                and "hasBound=true" in raw)

    def _rebind_home_override(self, connection: AdbDevice, services: str) -> None:
        others = ":".join(value for value in services.split(":")
                          if not self._is_home_service(value))
        connection.shell("settings put secure enabled_accessibility_services " + shlex.quote(others),
                         transport_timeout_s=2, read_timeout_s=2, timeout_s=3)
        try:
            deadline = time.monotonic() + 5
            while True:
                enabled = connection.shell("dumpsys accessibility | sed -n '/Enabled services:/p'",
                                           transport_timeout_s=2, read_timeout_s=2, timeout_s=3)
                selected = re.findall(re.escape(LAUNCHER) + r"/[A-Za-z0-9_.$]+", enabled)
                if not any(self._is_home_service(value) for value in selected):
                    break
                if time.monotonic() >= deadline:
                    raise DeviceError("The TV did not release its owned Home service for boot recovery.")
                time.sleep(0.1)
        finally:
            connection.shell("settings put secure enabled_accessibility_services "
                             + shlex.quote(services) + "; settings put secure accessibility_enabled 1",
                             transport_timeout_s=2, read_timeout_s=2, timeout_s=3)
        deadline = time.monotonic() + 5
        while not self._home_service_bound(connection):
            if time.monotonic() >= deadline:
                raise DeviceError("The TV did not bind its selected Home service after boot.")
            time.sleep(0.1)

    @staticmethod
    def _read_home_backup(path: Path) -> dict:
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeviceError("The Home compatibility recovery state cannot be read.") from exc
        if (not isinstance(saved, dict) or saved.get("accessibility_enabled") not in ("0", "1")
                or ("boot_count" in saved and
                    (type(saved["boot_count"]) is not int or saved["boot_count"] < 0))):
            raise DeviceError("The Home compatibility recovery state is invalid.")
        return saved

    @staticmethod
    def _write_home_backup(path: Path, value: dict) -> None:
        temporary = path.with_suffix(".pending.json")
        try:
            temporary.write_text(json.dumps(value), encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            raise DeviceError("The Home compatibility recovery state could not be saved.") from exc

    def _failed(self, exc: Exception) -> DeviceError:
        self.close()
        return DeviceError(
            f"ADB unavailable: {type(exc).__name__}. Check the TV, LAN connection, "
            "debugging setting and this controller's authorization.")

    def close(self) -> None:
        with self.lock:
            if self.connection is not None:
                try:
                    self.connection.close()
                except OSError as exc:
                    logger.warning("ADB socket close failed: %s", exc)
                self.connection = None

    def set_host(self, host: str) -> None:
        with self.lock:
            host = private_address(host)
            self.close()
            self.host = host

    def _shell(self, command: str, *, read_timeout_s: float = 30,
               timeout_s: float = 60) -> str:
        marker = "__TVCONTROL_RC_" + uuid.uuid4().hex + "__="
        try:
            raw = self._connect().shell(
                command + f"; printf '\\n{marker}%s\\n' \"$?\"",
                transport_timeout_s=min(read_timeout_s, timeout_s),
                read_timeout_s=read_timeout_s, timeout_s=timeout_s)
        except ADB_ERRORS as exc:
            raise self._failed(exc) from exc
        output, separator, status = raw.rpartition("\n" + marker)
        if not separator or not status.strip().isdecimal():
            raise DeviceError("TV command did not return a verifiable exit status.")
        if int(status.strip()) != 0:
            raise DeviceError(f"TV command failed: {output.strip()[:350]}")
        return output.strip()

    def observe(self) -> Observation:
        with self.priority_io():
            if (self._home_recovery_pending and self.connection is not None
                    and self.connection.available):
                try:
                    self._attempt_home_recovery(self.connection)
                except ADB_ERRORS as exc:
                    raise self._failed(exc) from exc
            self.native_home.poll()
            raw = self._shell(
                "dumpsys power | sed -n '/mWakefulness=/p'; "
                "printf '\\n__TVCONTROL_ACTIVITIES__\\n'; "
                "dumpsys activity activities | sed -n '/topResumedActivity=/p;/mResumedActivity:/p'")
            return parse_observation(raw, datetime.now(timezone.utc), time.monotonic())

    def sleep_if_foreground(self, package: str, *, valid_from: datetime | None = None,
                            valid_until: datetime | None = None) -> bool:
        with self.priority_io():
            current = self.observe()
            if not current.awake or current.package != package:
                return False
            if ((valid_from is not None and current.at < valid_from)
                    or (valid_until is not None and current.at >= valid_until)):
                return False
            self._shell("input keyevent 223")
            return True

    def standby(self) -> bool:
        with self.priority_io():
            if not self.observe().awake:
                return False
            self._shell("input keyevent 223")
            return True

    @staticmethod
    def _packages(output: str) -> list[str]:
        result = []
        for line in output.splitlines():
            if not line.startswith("package:"):
                raise DeviceError("Unexpected Android package inventory response.")
            name = line.removeprefix("package:").strip()
            if name != "android" and not PACKAGE.fullmatch(name):
                raise DeviceError("Invalid package identifier in the TV inventory.")
            result.append(name)
        return sorted(set(result))

    def home(self) -> str:
        output = self._shell("cmd package resolve-activity --brief --user 0 "
                             "-a android.intent.action.MAIN -c android.intent.category.HOME")
        values = [canonical_component(line.strip()) for line in output.splitlines()
                  if COMPONENT.fullmatch(line.strip())]
        if len(values) != 1:
            raise DeviceError("Could not determine a unique current Home activity.")
        return values[0]

    def _home_services(self) -> list[str]:
        value = self._shell("settings get secure enabled_accessibility_services")
        return [] if value in {"", "null"} else value.split(":")

    @staticmethod
    def _is_home_service(value: str) -> bool:
        return value in {HOME_OVERRIDE_SERVICE,
                        f"{LAUNCHER}/{LAUNCHER}.HomeOverrideService"}

    def _set_home_override(self, enabled: bool) -> None:
        services = self._home_services()
        others = [value for value in services if not self._is_home_service(value)]
        backup = self.data_dir / "home-override-backup.json"
        if enabled:
            boot = self._shell("settings get global boot_count")
            if not boot.isdecimal():
                raise DeviceError("Cannot record the TV boot for Home compatibility recovery.")
            if not backup.exists():
                prior = self._shell("settings get secure accessibility_enabled")
                if prior not in {"0", "1"}:
                    raise DeviceError("Cannot back up the existing accessibility state.")
                saved = {"accessibility_enabled": prior}
            else:
                saved = self._read_home_backup(backup)
            saved["boot_count"] = int(boot)
            self._write_home_backup(backup, saved)
            desired = ":".join([*others, HOME_OVERRIDE_SERVICE])
            self._shell("settings put secure enabled_accessibility_services " + shlex.quote(desired))
            self._shell("settings put secure accessibility_enabled 1")
            if not any(self._is_home_service(value) for value in self._home_services()):
                raise DeviceError("The TV did not enable the clean Home compatibility service.")
        elif len(others) != len(services):
            self._shell("settings put secure enabled_accessibility_services "
                        + shlex.quote(":".join(others)))
            if not others and backup.exists():
                prior = self._read_home_backup(backup)["accessibility_enabled"]
                if prior not in {"0", "1"}:
                    raise DeviceError("The saved accessibility state is invalid.")
                self._shell("settings put secure accessibility_enabled " + prior)
            if any(self._is_home_service(value) for value in self._home_services()):
                raise DeviceError("The clean Home compatibility service is still enabled.")
            if backup.exists():
                backup.unlink()
        elif not enabled and backup.exists():
            backup.unlink()

    def inventory(self) -> dict:
        with self.lock:
            props = ["ro.product.manufacturer", "ro.product.model", "ro.product.device",
                     "ro.software.version_id", "ro.build.version.release",
                     "ro.build.version.sdk", "ro.build.display.id"]
            properties = {}
            for name in props:
                properties[name] = self._shell("getprop " + name)
            installed = self._packages(self._shell("pm list packages --user 0"))
            enabled = self._packages(self._shell("pm list packages -e --user 0"))
            disabled = self._packages(self._shell("pm list packages -d --user 0"))
            if set(installed) != set(enabled) | set(disabled) or set(enabled) & set(disabled):
                raise DeviceError("Enabled/disabled inventories do not reconcile.")
            components = set()
            for category in ["LEANBACK_LAUNCHER", "LAUNCHER"]:
                output = self._shell("cmd package query-activities --components --user 0 "
                                     "-a android.intent.action.MAIN "
                                     "-c android.intent.category." + category)
                for line in output.splitlines():
                    if COMPONENT.fullmatch(line.strip()):
                        components.add(canonical_component(line.strip()))
            if not components:
                raise DeviceError("The TV did not report any launchable apps.")
            labels = {a["package"]: a["label"] for a in DEFAULT_APPS}
            labels.update({
                "com.beci.thaitv3android": "3Plus", "in.startv.hotstar.dplus.tv": "Disney+",
                "com.tcl.browser": "BrowseHere", "com.amazon.amazonvideo.livingroom": "Prime Video",
                "com.truedmp.idtv": "TrueID TV", "com.tencent.qqlivei18n": "WeTV",
                "com.android.tv.settings": "TV Settings", "com.android.vending": "Play Store",
                "com.google.android.youtube.tvmusic": "YouTube Music",
            })
            apps = [{"package": c.split("/")[0], "component": c,
                     "label": labels.get(c.split("/")[0], c.split("/")[0])}
                    for c in sorted(components)]
            selected = any(self._is_home_service(value) for value in self._home_services())
            override_active = selected and self._shell(
                "settings get secure accessibility_enabled") == "1"
            return {"properties": properties, "installed": installed, "enabled": enabled,
                    "disabled": disabled, "home": self.home(), "apps": apps,
                    "home_override_enabled": override_active,
                    "home_override_selected": selected,
                    "native_home": self.native_home.status(),
                    "captured_at": datetime.now(timezone.utc).isoformat()}

    def native_home_status(self) -> dict | None:
        with self.lock:
            return self.native_home.status()

    def _show_recovered_home(self) -> None:
        raw = self._shell(
            "dumpsys power | sed -n '/mWakefulness=/p'; "
            "printf '\\n__TVCONTROL_ACTIVITIES__\\n'; "
            "dumpsys activity activities | sed -n '/topResumedActivity=/p;/mResumedActivity:/p'")
        observation = parse_observation(raw, datetime.now(timezone.utc), time.monotonic())
        if observation.awake and observation.package in {
                LAUNCHER, "com.google.android.apps.tv.launcherx", "com.android.tv.settings"}:
            components = re.findall(
                r"(?:topResumedActivity|mResumedActivity)\s*[:=].*?\bu\d+\s+"
                r"([A-Za-z_][A-Za-z0-9_.]*/[A-Za-z0-9_.$]+)", raw)
            allowed = {CLEAN_HOME,
                       canonical_component("com.google.android.apps.tv.launcherx/.home.HomeActivity"),
                       canonical_component("com.android.tv.settings/.system.FallbackHome")}
            if components and all(canonical_component(value) in allowed for value in components):
                self._shell("input keyevent 3")

    def screenshot(self) -> bytes:
        with self.lock:
            try:
                image = self._connect().shell("screencap -p", decode=False,
                                             read_timeout_s=30, timeout_s=60)
            except ADB_ERRORS as exc:
                raise self._failed(exc) from exc
            if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise DeviceError("The TV did not return a valid screenshot.")
            return image

    def _push(self, local: Path, remote: str) -> None:
        try:
            connection = self._connect()
        except ADB_ERRORS as exc:
            raise self._failed(exc) from exc
        data = local.read_bytes()
        chunk = min(48 * 1024, connection.max_chunk_size * 3 // 4 - 256)
        chunk -= chunk % 3
        if chunk < 3:
            raise DeviceError("The TV advertised an invalid ADB payload size.")
        transfer = remote + ".transfer-" + uuid.uuid4().hex
        encoded, staged = shlex.quote(transfer + ".b64"), shlex.quote(transfer + ".tmp")
        expected = hashlib.sha256(data).hexdigest()
        self._shell(f"printf '%s' '' > {encoded}")
        try:
            for offset in range(0, len(data), chunk):
                part = base64.b64encode(data[offset:offset + chunk]).decode("ascii")
                self._shell(f"printf '%s' {shlex.quote(part)} >> {encoded}")
            self._shell(f"base64 -d {encoded} > {staged} && "
                        f'test "$(sha256sum {staged} | cut -d \' \' -f1)" = {expected}'
                        f" && mv {staged} {shlex.quote(remote)}")
        finally:
            self._shell(f"rm -f {encoded} {staged}")

    def apply_theme(self, config: Path, assets: list[Path], revision: str) -> None:
        with self.lock:
            self._shell("mkdir -p " + THEME_DIR)
            for asset in assets:
                self._push(asset, f"{THEME_DIR}/{asset.name}")
            staged = f"{THEME_DIR}/config-{revision}.tmp"
            self._push(config, staged)
            self._shell(f"mv {shlex.quote(staged)} {THEME_DIR}/config.json && "
                        f"printf '%s' {shlex.quote(revision)} > {THEME_DIR}/theme_revision")
            started = self._shell(f"am start -W -f 0x10008000 -n {LAUNCHER}/.MainActivity")
            if "Status: ok" not in started:
                raise DeviceError("Theme files were committed but the launcher did not restart. "
                                  "Open Home manually or restore the previous design.")

    def publish_energy(self, snapshot: dict) -> bool:
        return self._publish_panel(snapshot, "energy", "Energy")

    def publish_weather(self, snapshot: dict) -> bool:
        return self._publish_panel(snapshot, "weather", "Weather")

    def _publish_panel(self, snapshot: dict, name: str, label: str) -> bool:
        payload = json.dumps(snapshot, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
        if len(payload) > 16384:
            raise DeviceError(f"{label} snapshot exceeds the native 16 KiB limit.")
        with self._priority_state:
            if self._priority_waiters or not self.lock.acquire(blocking=False):
                return False
        try:
            if self.connection is None or not self.connection.available:
                return False
            encoded = base64.b64encode(payload).decode("ascii")
            digest = hashlib.sha256(payload).hexdigest()
            remote = f"{THEME_DIR}/{name}.pending.json"
            command = (f"mkdir -p {THEME_DIR} && printf '%s' {shlex.quote(encoded)} "
                       f"| base64 -d > {remote} && "
                       f'test "$(sha256sum {remote} | cut -d \' \' -f1)" = {digest}'
                       f" && mv {remote} {THEME_DIR}/{name}.json")
            self._shell(command, read_timeout_s=2, timeout_s=2)
            return True
        finally:
            self.lock.release()

    def install(self, apk: Path) -> dict:
        with self.lock:
            before = self.inventory()
            remote = "/data/local/tmp/tvcontrol-" + uuid.uuid4().hex + ".apk"
            try:
                self._push(apk, remote)
                output = self._shell("pm install -r " + shlex.quote(remote))
                if "Success" not in output:
                    raise DeviceError("Android did not confirm the launcher installation.")
            finally:
                self._shell("rm -f " + shlex.quote(remote))
            started = self._shell(f"am start -W -n {LAUNCHER}/.MainActivity")
            if "Status: ok" not in started:
                raise DeviceError("The launcher installed but did not open successfully.")
            after = self.inventory()
            if (before.get("home_override_selected", before.get("home_override_enabled"))
                    and not after.get("home_override_enabled")):
                self._set_home_override(True)
                after = self.inventory()
            assert_preserved(before, after)
            if after["home"] != before["home"]:
                raise DeviceError("The Home activity changed unexpectedly during installation.")
            return {"before": before, "after": after}

    def select_home(self, component: str, baseline: dict, *, allow_override: bool = False,
                    true_default: bool = False) -> dict:
        component = canonical_component(component)
        with self.lock:
            if true_default:
                if component != CLEAN_HOME:
                    raise DeviceError("True-default mode is available for Clean Home only.")
                before = self.inventory()
                assert_preserved(baseline, before)
                prepared = self.native_home.preflight(baseline["home"])
                started = self._shell(f"am start -W -c android.intent.category.HOME -n {CLEAN_HOME}")
                if "Status: ok" not in started:
                    raise DeviceError("Clean Home did not open; native Home was not changed.")
                selected = before.get("home_override_selected", False)
                try:
                    self._set_home_override(False)
                    self.native_home.activate(prepared)
                    after = self.inventory()
                    assert_preserved(baseline, after)
                    assert_preserved(before, after)
                    self._shell("input keyevent 3")
                except DeviceError as exc:
                    self.native_home.restore("Native Home verification failed: " + str(exc),
                                             automatic=True)
                    if selected:
                        self._set_home_override(True)
                        if not before.get("home_override_enabled"):
                            self._shell("settings put secure accessibility_enabled 0")
                    raise
                self.home_recovery_error = None
                self._home_recovery_pending = False
                after["home_mode"] = "native_guarded"
                return after
            self.native_home.restore("Original Home restored from the control portal.")
            sdk = self._shell("getprop ro.build.version.sdk")
            if not sdk.isdecimal():
                raise DeviceError("Cannot determine the Android version for Home selection.")
            if int(sdk) >= 29:
                self._shell("cmd role add-role-holder --user 0 android.app.role.HOME "
                            + shlex.quote(component.split("/", 1)[0]) + " 0")
            else:
                self._shell("cmd package set-home-activity --user 0 " + shlex.quote(component))
            actual = self.home()
            if actual != component:
                requested_clean = component.split("/", 1)[0] == LAUNCHER
                if requested_clean:
                    original = canonical_component(baseline["home"])
                    if int(sdk) >= 29:
                        self._shell("cmd role add-role-holder --user 0 android.app.role.HOME "
                                    + shlex.quote(original.split("/", 1)[0]) + " 0")
                    else:
                        self._shell("cmd package set-home-activity --user 0 "
                                    + shlex.quote(original))
                already_enabled = any(self._is_home_service(value)
                                      for value in self._home_services())
                if not requested_clean or not (allow_override or already_enabled):
                    raise DeviceError("This firmware needs the optional Home compatibility override. "
                                      "No existing package was disabled. Confirm the minimal "
                                      "accessibility override or keep using the launcher manually.")
                if canonical_component(baseline["home"]).split("/", 1)[0] \
                        != "com.google.android.apps.tv.launcherx":
                    raise DeviceError("This Home compatibility service supports Google TV Home only.")
                started = self._shell(
                    f"am start -W -c android.intent.category.HOME -n {LAUNCHER}/.MainActivity")
                if "Status: ok" not in started:
                    raise DeviceError("The launcher did not open; its Home override was not enabled.")
                self._set_home_override(True)
                self._home_recovery_pending = True
                self._home_recovery_retry_at = 0
                try:
                    self._attempt_home_recovery(self._connect())
                except ADB_ERRORS as exc:
                    raise self._failed(exc) from exc
                if self.home_recovery_error:
                    raise DeviceError(self.home_recovery_error)
            else:
                self._set_home_override(False)
            after = self.inventory()
            assert_preserved(baseline, after)
            override = after.get("home_override_enabled", False)
            if not override:
                self._shell("input keyevent 3")
            after["home_mode"] = "accessibility_override" if override else "native"
            return after


def approved_disabled_packages(baseline: dict) -> frozenset[str]:
    approved = baseline.get("approved_disabled_packages", [])
    protected = {"android", LAUNCHER, baseline.get("home", "").partition("/")[0]}
    protected.update(app["package"] for app in DEFAULT_APPS)
    if (not isinstance(approved, list)
            or any(not isinstance(value, str) or not PACKAGE.fullmatch(value) for value in approved)
            or len(set(approved)) != len(approved)
            or not set(approved) <= set(baseline["enabled"])
            or set(approved) & protected):
        raise DeviceError("The recorded package-state approval is invalid.")
    return frozenset(approved)


def assert_preserved(before: dict, after: dict) -> None:
    approved = approved_disabled_packages(before) | managed_home_packages(after)
    for state in ["installed", "enabled", "disabled"]:
        missing = set(before[state]) - set(after[state])
        if state == "enabled":
            missing -= approved
        if missing:
            raise DeviceError(f"App preservation check failed for {state}: {', '.join(sorted(missing))}")
    newly_disabled = set(after["disabled"]) - set(before["disabled"]) - approved
    if newly_disabled:
        raise DeviceError("Existing apps became disabled; stop and restore the saved baseline.")
