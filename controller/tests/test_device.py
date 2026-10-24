import base64
import hashlib
import shlex
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from adb_shell import exceptions
from adb_shell.transport.tcp_transport import TcpTransport

from tvcontrol.device import (
    CompleteTcpTransport,
    Device,
    assert_preserved,
    parse_observation,
    private_address,
)
from tvcontrol.models import Observation, canonical_component
from tvcontrol.monitor import DeviceError


def test_tcp_transport_completes_partial_writes_with_one_deadline(monkeypatch):
    clock = iter([10, 10.1, 10.2, 10.5])
    monkeypatch.setattr("tvcontrol.device.time.monotonic", lambda: next(clock))
    writes, timeouts = [], []
    lengths = iter([3, 2, 5])

    def write(self, data, timeout):
        writes.append(data)
        timeouts.append(timeout)
        return next(lengths)

    monkeypatch.setattr(TcpTransport, "bulk_write", write)
    transport = CompleteTcpTransport("192.168.1.148", 5555)
    assert transport.bulk_write(b"abcdefghij", 2) == 10
    assert writes == [b"abcdefghij", b"defghij", b"fghij"]
    assert timeouts == pytest.approx([1.9, 1.8, 1.5])


def test_tcp_transport_does_not_reset_timeout_after_a_short_write(monkeypatch):
    clock = iter([10, 10.75, 11.25])
    monkeypatch.setattr("tvcontrol.device.time.monotonic", lambda: next(clock))
    writes = []
    monkeypatch.setattr(TcpTransport, "bulk_write",
                        lambda self, data, timeout: writes.append(data) or 1)
    with pytest.raises(exceptions.TcpTimeoutException, match="partial write"):
        CompleteTcpTransport("192.168.1.148", 5555).bulk_write(b"abcd", 1)
    assert writes == [b"abcd"]


def test_tcp_transport_surfaces_a_closed_socket(monkeypatch):
    monkeypatch.setattr(TcpTransport, "bulk_write", lambda *args: 0)
    with pytest.raises(ConnectionResetError, match="closed"):
        CompleteTcpTransport("192.168.1.148", 5555).bulk_write(b"abcd", 2)


def test_tcp_transport_supports_unbounded_and_empty_writes(monkeypatch):
    writes = []

    def write(self, data, timeout):
        writes.append((data, timeout))
        return min(2, len(data))

    monkeypatch.setattr(TcpTransport, "bulk_write", write)
    transport = CompleteTcpTransport("192.168.1.148", 5555)
    assert transport.bulk_write(b"abcd", None) == 4
    assert transport.bulk_write(b"", 2) == 0
    assert writes == [(b"abcd", None), (b"cd", None)]


def raw(package="com.thegate.tv", power="Awake"):
    return (f"mWakefulness={power}\n__TVCONTROL_ACTIVITIES__\n"
            f"topResumedActivity=ActivityRecord{{123 u0 {package}/.MainActivity t10}}")


def test_explicit_privacy_approval_preserves_original_baseline_and_other_app_states():
    before = {
        "installed": ["com.example.player", "com.tcl.logkit"],
        "enabled": ["com.example.player", "com.tcl.logkit"], "disabled": [],
        "approved_disabled_packages": ["com.tcl.logkit"],
    }
    after = {
        "installed": list(before["installed"]), "enabled": ["com.example.player"],
        "disabled": ["com.tcl.logkit"],
    }
    assert_preserved(before, after)
    assert before["enabled"] == ["com.example.player", "com.tcl.logkit"]
    with pytest.raises(DeviceError, match="preservation|disabled"):
        assert_preserved(before, {**after, "enabled": [], "disabled": before["installed"]})
    with pytest.raises(DeviceError, match="installed"):
        assert_preserved(before, {**after, "installed": ["com.example.player"]})
    with pytest.raises(DeviceError, match="preservation|disabled"):
        assert_preserved({k: v for k, v in before.items() if k != "approved_disabled_packages"}, after)


@pytest.mark.parametrize("approval", [
    "com.tcl.logkit", None, True, ["com.tcl.logkit", "com.tcl.logkit"],
    ["com.missing.package"], ["bad; command"], [123], ["android"],
])
def test_invalid_privacy_approval_never_weakens_app_preservation(approval):
    before = {
        "installed": ["android", "com.tcl.logkit"], "enabled": ["android", "com.tcl.logkit"],
        "disabled": [], "approved_disabled_packages": approval,
    }
    with pytest.raises(DeviceError, match="approval"):
        assert_preserved(before, before)


def test_android_14_foreground_and_power():
    observed = parse_observation(raw(), datetime.now(timezone.utc), 10)
    assert observed.package == "com.thegate.tv"
    assert observed.awake
    assert not parse_observation(raw(power="Asleep"), observed.at, 20).awake


def test_ambiguous_foreground_or_missing_power_is_an_error():
    at = datetime.now(timezone.utc)
    with pytest.raises(DeviceError):
        parse_observation("not a valid dump", at, 0)
    with pytest.raises(DeviceError):
        parse_observation(raw() + "\nmResumedActivity: ActivityRecord{9 u0 com.netflix.ninja/.A}", at, 0)


def test_sleep_uses_discrete_key_and_does_not_wake_asleep_tv(tmp_path, monkeypatch):
    device = Device("192.168.1.148", tmp_path)
    commands = []
    state = {"power": "Awake"}

    def shell(command):
        commands.append(command)
        return raw(power=state["power"]) if "dumpsys" in command else ""

    monkeypatch.setattr(device, "_shell", shell)
    assert device.sleep_if_foreground("com.netflix.ninja") is False
    assert device.sleep_if_foreground("com.thegate.tv") is True
    assert commands.count("input keyevent 223") == 1
    state["power"] = "Asleep"
    assert device.sleep_if_foreground("com.thegate.tv") is False
    assert commands.count("input keyevent 223") == 1
    assert all("keyevent 26" not in command and "KEYCODE_POWER" not in command for command in commands)


def test_sleep_rechecks_the_daily_window_after_adb_latency(tmp_path, monkeypatch):
    device = Device("192.168.1.148", tmp_path)
    midnight = datetime(2026, 10, 20, 17, tzinfo=timezone.utc)
    commands = []
    monkeypatch.setattr(device, "observe", lambda: Observation(
        at=midnight, monotonic=10, package="com.thegate.tv", awake=True))
    monkeypatch.setattr(device, "_shell", lambda command: commands.append(command) or "")
    assert device.sleep_if_foreground(
        "com.thegate.tv", valid_from=midnight - timedelta(seconds=1),
        valid_until=midnight) is False
    assert commands == []


def test_theme_activation_stops_when_the_config_move_fails(tmp_path, monkeypatch):
    device = Device("192.168.1.148", tmp_path)
    config = tmp_path / "theme.json"
    config.write_text("{}")
    commands = []
    monkeypatch.setattr(device, "_push", lambda local, remote: None)

    def shell(command):
        commands.append(command)
        if command.startswith("mv "):
            if " && " in command:
                raise DeviceError("Configuration rename failed")
            return ""
        return "Status: ok" if command.startswith("am start") else ""

    monkeypatch.setattr(device, "_shell", shell)
    with pytest.raises(DeviceError, match="rename failed"):
        device.apply_theme(config, [], "test-revision")
    assert not any(command.startswith("am start") for command in commands)


@pytest.mark.parametrize("host", ["8.8.8.8", "127.0.0.1", "169.254.1.2", "224.0.0.1", "not-a-host"])
def test_only_explicit_private_tv_addresses(host):
    with pytest.raises(ValueError):
        private_address(host)


def test_preservation_allows_new_launcher_but_no_removed_or_disabled_apps():
    before = {"installed": ["one", "two"], "enabled": ["one", "two"], "disabled": []}
    assert_preserved(before, {"installed": ["one", "two", "launcher"],
                              "enabled": ["one", "two", "launcher"], "disabled": []})
    with pytest.raises(DeviceError):
        assert_preserved(before, {"installed": ["one", "two"], "enabled": ["one"],
                                  "disabled": ["two"]})


def test_large_file_transfer_uses_bounded_shell_chunks_not_unreliable_sync(tmp_path, monkeypatch):
    local = tmp_path / "large.apk"
    payload = bytes(range(256)) * 1300
    local.write_bytes(payload)
    device = Device("192.168.1.148", tmp_path)
    connection = SimpleNamespace(max_chunk_size=4096, available=True,
                                 push=lambda *args, **kwargs: pytest.fail("Used unreliable SYNC push"))
    monkeypatch.setattr(device, "_connect", lambda: connection)
    chunks, commands = [], []
    destination = "/data/local/tmp/tcl-canary.apk"

    def shell(command, **kwargs):
        commands.append(command)
        parts = shlex.split(command)
        if parts[0] == "printf" and ">>" in parts:
            chunks.append(parts[2])
            assert len(command.encode()) + 160 < connection.max_chunk_size
        if parts[0] == "base64":
            assert base64.b64decode("".join(chunks)) == payload
            assert hashlib.sha256(payload).hexdigest() in command
            assert " && mv " in command
        return ""

    monkeypatch.setattr(device, "_shell", shell)
    device._push(local, destination)
    assert len(chunks) > 1
    assert commands[-1].startswith("rm -f ")
    assert destination not in shlex.split(commands[-1])[2:]


def test_failed_chunked_transfer_never_replaces_existing_destination(tmp_path, monkeypatch):
    local = tmp_path / "config.json"
    local.write_text("{}")
    device = Device("192.168.1.148", tmp_path)
    connection = SimpleNamespace(max_chunk_size=4096, available=True,
                                 push=lambda *args, **kwargs: pytest.fail("Used SYNC push"))
    monkeypatch.setattr(device, "_connect", lambda: connection)
    commands = []

    def shell(command, **kwargs):
        commands.append(command)
        if command.startswith("base64 -d "):
            raise DeviceError("Synthetic checksum mismatch")
        return ""

    monkeypatch.setattr(device, "_shell", shell)
    with pytest.raises(DeviceError, match="checksum"):
        device._push(local, "/sdcard/Android/data/com.gathin.tvhome/files/config.json")
    assert commands[-1].startswith("rm -f ")
    assert not any(command.startswith("rm -f /sdcard/Android/data/com.gathin.tvhome/files/config.json ")
                   for command in commands)


def test_long_commands_keep_their_read_budget_while_energy_stays_bounded(tmp_path):
    device = Device("192.168.1.148", tmp_path)
    observed = []

    def shell(command, **options):
        observed.append(options)
        marker = command.split("__TVCONTROL_RC_", 1)[1].split("__=", 1)[0]
        return "\n__TVCONTROL_RC_" + marker + "__=0\n"

    device.connection = SimpleNamespace(available=True, shell=shell)
    device._shell("sleep 6")
    device._shell("printf telemetry", read_timeout_s=2, timeout_s=2)
    assert observed[0]["transport_timeout_s"] == 30
    assert observed[0]["read_timeout_s"] == 30
    assert observed[1]["transport_timeout_s"] == 2
    assert observed[1]["timeout_s"] == 2


@pytest.mark.parametrize(("sdk", "expected_command"), [
    ("34", "cmd role add-role-holder --user 0 android.app.role.HOME com.gathin.tvhome 0"),
    ("26", "cmd package set-home-activity --user 0 com.gathin.tvhome/com.gathin.tvhome.MainActivity"),
])
def test_home_selection_uses_the_supported_android_role_without_disabling_apps(
        tmp_path, monkeypatch, sdk, expected_command):
    device = Device("192.168.1.148", tmp_path)
    commands = []
    current = {"installed": ["stock", "clean"], "enabled": ["stock", "clean"], "disabled": []}

    def shell(command):
        commands.append(command)
        return sdk if command.startswith("getprop ") else ""

    monkeypatch.setattr(device, "_shell", shell)
    monkeypatch.setattr(device, "home", lambda: "com.gathin.tvhome/com.gathin.tvhome.MainActivity")
    monkeypatch.setattr(device, "inventory", lambda: current)
    device.select_home("com.gathin.tvhome/.MainActivity", current)
    assert expected_command in commands
    assert not any("disable" in command or "uninstall" in command for command in commands)
    assert commands[-1] == "input keyevent 3"


@pytest.mark.parametrize("existing", ["", "com.example.reader/.Service"])
def test_home_override_preserves_other_services_and_restores_its_global_state(
        tmp_path, monkeypatch, existing):
    device = Device("192.168.1.148", tmp_path)
    settings = {"enabled_accessibility_services": existing,
                "accessibility_enabled": "1" if existing else "0"}
    original = dict(settings)

    def shell(command):
        if command == "settings get global boot_count":
            return "61"
        parts = shlex.split(command)
        if parts[:3] == ["settings", "get", "secure"]:
            return settings[parts[3]]
        if parts[:3] == ["settings", "put", "secure"]:
            settings[parts[3]] = parts[4]
            return ""
        raise AssertionError("Unexpected settings command: " + command)

    monkeypatch.setattr(device, "_shell", shell)
    device._set_home_override(True)
    assert "com.gathin.tvhome/.HomeOverrideService" in settings["enabled_accessibility_services"]
    if existing:
        assert existing in settings["enabled_accessibility_services"]
    assert settings["accessibility_enabled"] == "1"
    device._set_home_override(False)
    assert settings == original
    assert not (tmp_path / "home-override-backup.json").exists()


@pytest.mark.parametrize("master", ["0", "1"])
def test_tv_reboot_restores_only_a_still_selected_home_override(tmp_path, master):
    import json

    device = Device("192.168.1.148", tmp_path)
    path = tmp_path / "home-override-backup.json"
    path.write_text(json.dumps({"accessibility_enabled": "0", "boot_count": 61}))
    commands = []
    state = {"rebound": False}
    services = "com.reader/.Speech:com.gathin.tvhome/.HomeOverrideService"

    def shell(command, **kwargs):
        commands.append(command)
        if command.startswith("settings get global"):
            return f"62\n1\n{services}\n{master}\n"
        if command.startswith("dumpsys power"):
            return raw("com.gathin.tvhome")
        if command.startswith("settings put secure enabled_accessibility_services"):
            state["rebound"] = "HomeOverrideService" in command
            return ""
        if command.startswith("dumpsys accessibility"):
            return "Enabled services:{com.reader/.Speech}"
        if command.startswith("dumpsys activity services") and state["rebound"]:
            return "ServiceRecord HomeOverrideService app=ProcessRecord{live} hasBound=true"
        return "1\n" if command == "settings get secure accessibility_enabled" else ""

    device._restore_home_override_after_boot(SimpleNamespace(shell=shell))
    assert "settings put secure accessibility_enabled 1" in commands
    assert "settings put secure enabled_accessibility_services com.reader/.Speech" in commands
    assert any(services in command for command in commands if command.startswith("settings put"))
    assert json.loads(path.read_text())["boot_count"] == 62


@pytest.mark.parametrize("phase", ["release", "bind"])
def test_boot_rebind_failure_restores_selected_list_and_keeps_old_boot_metadata(
        tmp_path, monkeypatch, phase):
    import json

    device = Device("192.168.1.148", tmp_path)
    path = tmp_path / "home-override-backup.json"
    path.write_text(json.dumps({"accessibility_enabled": "0", "boot_count": 61}))
    clock = iter(range(100))
    monkeypatch.setattr("tvcontrol.device.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("tvcontrol.device.time.sleep", lambda _: None)
    commands = []
    services = "com.reader/.Speech:com.gathin.tvhome/.HomeOverrideService"

    def shell(command, **kwargs):
        commands.append(command)
        if command.startswith("settings get global"):
            return f"62\n1\n{services}\n0\n"
        if command.startswith("dumpsys power"):
            return raw("com.gathin.tvhome")
        if command.startswith("dumpsys accessibility") and phase == "release":
            return f"Enabled services:{{{services}}}"
        return ""

    with pytest.raises(DeviceError, match="release|bind"):
        device._restore_home_override_after_boot(SimpleNamespace(shell=shell))
    assert any(services in command for command in commands if command.startswith("settings put"))
    assert json.loads(path.read_text())["boot_count"] == 61


@pytest.mark.parametrize(("raw", "bound"), [
    ("", False),
    ("ServiceRecord HomeOverrideService app=null hasBound=true", False),
    ("ServiceRecord HomeOverrideService app=ProcessRecord{live} hasBound=false", False),
    ("ServiceRecord HomeOverrideService app=ProcessRecord{live} hasBound=true", True),
])
def test_home_binding_checks_process_and_actual_binder_connection(raw, bound):
    assert Device._home_service_bound(SimpleNamespace(shell=lambda *a, **kw: raw)) is bound


def test_tcl_boot_opens_only_stock_home_before_binding_its_service():
    commands = []
    stock = raw("com.google.android.apps.tv.launcherx").replace(
        "/.MainActivity", "/.home.HomeActivity")

    def shell(command, **kwargs):
        commands.append(command)
        return stock if command.startswith("dumpsys power") else "Status: ok"

    Device._bootstrap_home_after_boot(SimpleNamespace(shell=shell))
    assert commands[-1].startswith("am start -W -c android.intent.category.HOME")


@pytest.mark.parametrize(("package", "activity", "power"), [
    ("com.thegate.tv", ".MainActivity", "Awake"),
    ("com.android.tv.settings", ".MainSettings", "Awake"),
    ("com.tcl.tv", ".InputActivity", "Awake"),
    ("com.google.android.apps.tv.launcherx", ".accounts.AccountActivity", "Awake"),
    ("com.google.android.apps.tv.launcherx", ".home.HomeActivity", "Asleep"),
])
def test_boot_home_start_never_interrupts_an_app_settings_input_or_standby(
        package, activity, power):
    commands = []
    state = raw(package, power).replace("/.MainActivity", "/" + activity)

    def shell(command, **kwargs):
        commands.append(command)
        return state

    with pytest.raises(DeviceError, match="left untouched"):
        Device._bootstrap_home_after_boot(SimpleNamespace(shell=shell))
    assert len(commands) == 1


def test_pending_home_recovery_keeps_adb_available_and_throttles_retries(tmp_path, monkeypatch):
    device = Device("192.168.1.148", tmp_path)
    connection = SimpleNamespace(available=True)
    device.connection = connection
    calls = []

    def recover(conn):
        calls.append(conn)
        raise DeviceError("Waiting for TV Home; usage monitoring continues.")

    monkeypatch.setattr(device, "_restore_home_override_after_boot", recover)
    device._attempt_home_recovery(connection)
    assert device.home_recovery_error and device._home_recovery_pending
    assert device._connect() is connection
    device._attempt_home_recovery(connection)
    assert calls == [connection]


@pytest.mark.parametrize(("boot", "services"), [
    (61, "com.gathin.tvhome/.HomeOverrideService"), (62, "com.other.reader/.Service"),
])
def test_reconnect_does_not_override_manual_same_boot_or_unselected_services(
        tmp_path, boot, services):
    import json

    device = Device("192.168.1.148", tmp_path)
    (tmp_path / "home-override-backup.json").write_text(json.dumps({
        "accessibility_enabled": "0", "boot_count": 61}))
    commands = []

    def shell(command, **kwargs):
        commands.append(command)
        return f"{boot}\n1\n{services}\n0\n"

    device._restore_home_override_after_boot(SimpleNamespace(shell=shell))
    assert not any(command.startswith("settings put") for command in commands)


def test_boot_recovery_waits_for_android_startup_before_updating_metadata(tmp_path):
    import json

    device = Device("192.168.1.148", tmp_path)
    backup = tmp_path / "home-override-backup.json"
    backup.write_text(json.dumps({"accessibility_enabled": "0", "boot_count": 61}))
    with pytest.raises(DeviceError, match="Waiting for TV startup"):
        device._restore_home_override_after_boot(SimpleNamespace(
            shell=lambda *args, **kwargs:
                "62\n\ncom.gathin.tvhome/.HomeOverrideService\n0\n"))
    assert json.loads(backup.read_text())["boot_count"] == 61


@pytest.mark.parametrize("value", ['{"accessibility_enabled":[]}',
                                  '{"accessibility_enabled":"0","boot_count":true}',
                                  '{"broken":'])
def test_corrupt_home_recovery_data_is_an_explicit_device_error(tmp_path, value):
    path = tmp_path / "home-override-backup.json"
    path.write_text(value)
    with pytest.raises(DeviceError, match="recovery state"):
        Device._read_home_backup(path)


def test_rejected_home_preference_does_not_silently_enable_accessibility(tmp_path, monkeypatch):
    device = Device("192.168.1.148", tmp_path)
    commands = []

    def shell(command):
        commands.append(command)
        return "34" if command.startswith("getprop ") else ""

    monkeypatch.setattr(device, "_shell", shell)
    monkeypatch.setattr(device, "home",
                        lambda: "com.google.android.apps.tv.launcherx/com.google.android.apps.tv.launcherx.home.HomeActivity")
    with pytest.raises(DeviceError, match="Confirm the minimal"):
        device.select_home("com.gathin.tvhome/.MainActivity", {
            "home": "com.google.android.apps.tv.launcherx/.home.HomeActivity"})
    assert "cmd role add-role-holder --user 0 android.app.role.HOME com.google.android.apps.tv.launcherx 0" in commands
    assert not any(command.startswith("settings put") for command in commands)


@pytest.mark.parametrize("selected", [False, True])
def test_explicit_home_selection_opens_app_before_enabling_and_verifying_service(
        tmp_path, monkeypatch, selected):
    device = Device("192.168.1.148", tmp_path)
    stages = []
    stock = "com.google.android.apps.tv.launcherx/.home.HomeActivity"
    baseline = {"home": stock, "installed": ["stock"], "enabled": ["stock"], "disabled": []}
    after = {**baseline, "home_override_enabled": True}

    def shell(command):
        if command.startswith("getprop"):
            return "34"
        if command.startswith("am start"):
            stages.append("opened")
            return "Status: ok"
        return ""

    monkeypatch.setattr(device, "_shell", shell)
    monkeypatch.setattr(device, "home", lambda: canonical_component(stock))
    monkeypatch.setattr(device, "_home_services",
                        lambda: ["com.gathin.tvhome/.HomeOverrideService"] if selected else [])
    monkeypatch.setattr(device, "_set_home_override", lambda enabled: stages.append("enabled"))
    monkeypatch.setattr(device, "_connect", lambda: SimpleNamespace(available=True))
    monkeypatch.setattr(device, "_attempt_home_recovery", lambda connection: stages.append("verified"))
    monkeypatch.setattr(device, "inventory", lambda: after)
    result = device.select_home("com.gathin.tvhome/.MainActivity", baseline, allow_override=True)
    assert stages == ["opened", "enabled", "verified"]
    assert result["home_mode"] == "accessibility_override"


@pytest.mark.parametrize("failure", ["open", "bind"])
def test_explicit_home_selection_never_reports_success_without_open_and_binding(
        tmp_path, monkeypatch, failure):
    device = Device("192.168.1.148", tmp_path)
    stock = "com.google.android.apps.tv.launcherx/.home.HomeActivity"
    baseline = {"home": stock, "installed": [], "enabled": [], "disabled": []}
    enabled = []

    def shell(command):
        if command.startswith("getprop"):
            return "34"
        return "Status: ok" if command.startswith("am start") and failure != "open" else ""

    monkeypatch.setattr(device, "_shell", shell)
    monkeypatch.setattr(device, "home", lambda: canonical_component(stock))
    monkeypatch.setattr(device, "_home_services", lambda: [])
    monkeypatch.setattr(device, "_set_home_override", lambda value: enabled.append(value))
    monkeypatch.setattr(device, "_connect", lambda: SimpleNamespace(available=True))

    def recover(connection):
        device.home_recovery_error = "The TV did not bind its selected Home service."

    monkeypatch.setattr(device, "_attempt_home_recovery", recover)
    with pytest.raises(DeviceError, match="did not open|did not bind"):
        device.select_home("com.gathin.tvhome/.MainActivity", baseline, allow_override=True)
    assert enabled == ([] if failure == "open" else [True])


def test_home_override_metadata_cannot_read_screen_content_or_filter_keys():
    import xml.etree.ElementTree as ET
    from pathlib import Path

    path = Path(__file__).resolve().parents[2].joinpath(
        "launcher", "app", "src", "main", "res", "xml", "home_override_service.xml")
    root = ET.parse(path).getroot()
    ns = "{http://schemas.android.com/apk/res/android}"
    assert root.get(ns + "canRetrieveWindowContent") == "false"
    assert root.get(ns + "canRequestFilterKeyEvents") == "false"
    assert root.get(ns + "packageNames") == "com.google.android.apps.tv.launcherx"
    assert root.get(ns + "accessibilityEventTypes") == "typeWindowStateChanged"


def test_energy_row_is_integrated_once_in_every_home_layout_locale():
    import xml.etree.ElementTree as ET
    from pathlib import Path

    resources = Path(__file__).resolve().parents[2].joinpath(
        "launcher", "app", "src", "main", "res")
    for path in resources.glob("layout*/activity_main.xml"):
        nodes = list(ET.parse(path).iter())
        assert not any(node.tag.endswith("EnergyFlowView") for node in nodes), path
        ns = "{http://schemas.android.com/apk/res/android}"
        ids = {node.get(ns + "id") for node in nodes}
        assert {"@+id/appsTitle", "@+id/pageHint", "@+id/appsShade", "@+id/wallpaperShade"} <= ids
    panel = ET.parse(resources / "layout" / "item_home_panel.xml")
    assert sum(node.tag.endswith("EnergyFlowView") for node in panel.iter()) == 1
    assert sum(node.tag.endswith("WeatherView") for node in panel.iter()) == 1
    ns = "{http://schemas.android.com/apk/res/android}"
    assert any(node.get(ns + "id") == "@+id/reservedHomeSpace" for node in panel.iter())
    assert not any(node.get(ns + "text") == "@string/all_apps" for node in panel.iter())
    information = next(node for node in panel.iter() if any(
        child.tag.endswith("EnergyFlowView") for child in node))
    assert information.get(ns + "orientation") == "horizontal"
    assert any(child.tag.endswith("WeatherView") for child in information)
