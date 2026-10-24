from datetime import datetime, timedelta

import pytest

from tvcontrol.device import Device, assert_preserved
from tvcontrol.home import (
    CLEAN_HOME,
    HOME_PACKAGES,
    SETUP_HOME,
    STOCK_HOME,
    HomeBackup,
    NativeHome,
    exit_records,
    managed_home_packages,
    saved_wifi_owners,
)
from tvcontrol.models import LAUNCHER, ORIGINAL_HOME, canonical_component
from tvcontrol.monitor import DeviceError

STOCK_COMPONENT = canonical_component(ORIGINAL_HOME)


class HomeShell:
    def __init__(self):
        self.commands = []
        self.states = {STOCK_HOME: 0, SETUP_HOME: 1}
        self.owner = "android.uid.system:1000"
        self.sdk = "34"
        self.exits = ""
        self.clock = datetime(2026, 10, 23, 19, 0)
        self.fail = None
        self.unavailable = False
        self.wrong_home = None

    def __call__(self, command):
        self.commands.append(command)
        if command == self.fail:
            self.fail = None
            raise DeviceError("Injected TV command failure")
        if command == "getprop ro.build.version.sdk":
            return self.sdk
        if command.startswith("dumpsys package "):
            package = command.split()[-1]
            return (f"Packages:\n  Package [{package}]\n"
                    f"    User 0: installed=true enabled={self.states[package]}\n"
                    "Hidden system packages:\n    User 0: installed=true enabled=0")
        if command == "dumpsys wifi":
            return ("cname=com.google.android.tungsten.setupwraith\n"
                    "WifiConfigManager - Configured networks Begin ----\n"
                    f"cname={self.owner}\nWifiConfigManager - Configured networks End ----")
        if command.startswith("cmd package resolve-activity"):
            if self.wrong_home is not None:
                return self.wrong_home
            if self.states[STOCK_HOME] in {0, 1}:
                return ORIGINAL_HOME
            if self.unavailable:
                return "com.android.tv.settings/.system.FallbackHome"
            return CLEAN_HOME
        if command.startswith("dumpsys activity exit-info "):
            return self.exits
        if command.startswith("date "):
            return self.clock.isoformat(sep=" ") + "\n" + self.exits
        if command.startswith("pm "):
            _, action, _, user, package = command.split()
            assert user == "0"
            self.states[package] = {"disable-user": 3, "enable": 1, "default-state": 0}[action]
            return ""
        if command.startswith("cmd package set-home-activity "):
            return "Success"
        if command.startswith("cmd role add-role-holder "):
            return ""
        if command.startswith("am start "):
            return "Status: ok"
        if command == "input keyevent 3":
            return ""
        if command.startswith("dumpsys power "):
            return ("mWakefulness=Awake\n__TVCONTROL_ACTIVITIES__\n"
                    "topResumedActivity=ActivityRecord{123 u0 "
                    "com.google.android.youtube.tv/.MainActivity t1}")
        raise AssertionError("Unexpected command: " + command)


def exit_info(clock, reasons):
    return "\n".join(
        f"timestamp={(clock - timedelta(seconds=seconds)).isoformat(sep=' ', timespec='milliseconds')} "
        f"pid={100 + index} realUid=10001 user=0 reason={reason} (TEST)"
        for index, (seconds, reason) in enumerate(reasons))


@pytest.fixture
def home(tmp_path):
    shell = HomeShell()
    return shell, NativeHome(shell, tmp_path)


def arm(shell, guard):
    prepared = guard.preflight(ORIGINAL_HOME)
    guard.activate(prepared)
    return prepared


def test_wifi_check_ignores_historical_owners_but_rejects_unverifiable_dumps():
    shell = HomeShell()
    assert saved_wifi_owners(shell("dumpsys wifi")) == {"android.uid.system:1000"}
    for raw in ["", "cname=android.uid.system:1000",
                "WifiConfigManager - Configured networks End\n"
                "WifiConfigManager - Configured networks Begin"]:
        with pytest.raises(DeviceError, match="Wi-Fi ownership"):
            saved_wifi_owners(raw)


def test_native_preflight_blocks_wizard_owned_networks_without_writing_or_disabling(home):
    shell, guard = home
    shell.owner = SETUP_HOME
    with pytest.raises(DeviceError, match="saved Wi-Fi"):
        guard.preflight(ORIGINAL_HOME)
    assert not guard.path.exists()
    assert not any(command.startswith("pm ") for command in shell.commands)


@pytest.mark.parametrize("sdk", ["28", "", "unknown"])
def test_native_preflight_requires_exit_diagnostics(home, sdk):
    shell, guard = home
    shell.sdk = sdk
    with pytest.raises(DeviceError, match="Android 11"):
        guard.preflight(ORIGINAL_HOME)
    assert not guard.path.exists()


def test_preflight_rejects_unknown_stock_launcher_and_disabled_packages(home):
    shell, guard = home
    with pytest.raises(DeviceError, match="Google TV launcher"):
        guard.preflight("com.example.stock/.Home")
    shell.states[SETUP_HOME] = 3
    with pytest.raises(DeviceError, match="both original"):
        guard.preflight(ORIGINAL_HOME)


def test_activation_verifies_actual_resolver_and_preserves_exact_original_states(home):
    shell, guard = home
    prepared = arm(shell, guard)
    assert prepared.package_states == {STOCK_HOME: 0, SETUP_HOME: 1}
    assert guard.read().phase == "active"
    assert guard.resolve() == CLEAN_HOME
    assert all(value == 3 for value in shell.states.values())
    changes = [command for command in shell.commands if command.startswith("pm ")]
    assert changes == [f"pm disable-user --user 0 {SETUP_HOME}",
                       f"pm disable-user --user 0 {STOCK_HOME}"]
    assert guard.status()["armed"]
    assert guard.status()["requires_nas_adb"]
    with pytest.raises(DeviceError, match="restore original Home first"):
        guard.preflight(ORIGINAL_HOME)


def test_manual_restore_restores_defaults_not_just_explicit_enabled(home):
    shell, guard = home
    arm(shell, guard)
    assert guard.restore("Manual recovery")
    assert shell.states == {STOCK_HOME: 0, SETUP_HOME: 1}
    assert guard.resolve() == STOCK_COMPONENT
    assert guard.read().phase == "restored"
    assert guard.status()["managed_packages"] == []
    assert not guard.restore("Do not repeat")
    assert shell.commands.index(f"pm default-state --user 0 {STOCK_HOME}") < \
        shell.commands.index(f"pm enable --user 0 {SETUP_HOME}")


def test_failed_disable_rolls_back_both_packages_and_never_claims_active(home):
    shell, guard = home
    prepared = guard.preflight(ORIGINAL_HOME)
    shell.fail = f"pm disable-user --user 0 {STOCK_HOME}"
    with pytest.raises(DeviceError, match="Injected"):
        guard.activate(prepared)
    assert guard.read().phase == "fallback"
    assert shell.states == {STOCK_HOME: 0, SETUP_HOME: 1}
    assert not guard.status()["armed"]


def test_reported_success_without_correct_home_is_rolled_back(home):
    shell, guard = home
    prepared = guard.preflight(ORIGINAL_HOME)
    shell.wrong_home = ORIGINAL_HOME
    with pytest.raises(DeviceError, match="actual HOME resolver"):
        guard.activate(prepared)
    assert guard.read().phase == "fallback"
    assert shell.states == {STOCK_HOME: 0, SETUP_HOME: 1}


def test_interrupted_activation_is_restored_by_a_new_monitor_instance(home):
    shell, guard = home
    prepared = guard.preflight(ORIGINAL_HOME)
    guard.write(prepared)
    shell.states[SETUP_HOME] = 3
    restarted = NativeHome(shell, guard.path.parent)
    restarted.check(100)
    assert restarted.read().phase == "fallback"
    assert "interrupted" in restarted.read().reason
    assert shell.states == {STOCK_HOME: 0, SETUP_HOME: 1}


def test_missing_home_must_persist_for_fifteen_seconds_and_a_good_sample_resets_it(home):
    shell, guard = home
    arm(shell, guard)
    shell.unavailable = True
    guard.check(100)
    guard.check(114.99)
    assert guard.read().phase == "active"
    shell.unavailable = False
    guard.check(115)
    shell.unavailable = True
    guard.check(120)
    guard.check(134.99)
    assert guard.read().phase == "active"
    guard.check(135)
    assert guard.read().phase == "fallback"
    assert "15 seconds" in guard.read().reason


def test_three_new_crashes_within_two_minutes_trigger_failback_not_old_crashes(home):
    shell, guard = home
    shell.exits = exit_info(shell.clock, [(20, 4), (21, 5), (22, 4)])
    arm(shell, guard)
    shell.exits = exit_info(shell.clock, [(1, 4), (2, 5)]) + "\n" + shell.exits
    guard.check(100)
    assert guard.read().phase == "active"
    shell.exits = exit_info(shell.clock, [(1, 4), (2, 5), (3, 4)])
    guard.check(110)
    assert guard.read().phase == "fallback"
    assert "three times" in guard.read().reason


def test_an_android_anr_triggers_failback_once(home):
    shell, guard = home
    callbacks = []
    guard.recovered = lambda: callbacks.append("restored")
    arm(shell, guard)
    shell.exits = exit_info(shell.clock, [(1, 6)])
    guard.check(100)
    assert guard.read().phase == "fallback"
    assert "ANR" in guard.read().reason
    guard.check(110)
    assert callbacks == ["restored"]


def test_old_future_and_normal_process_exits_do_not_trigger_recovery(home):
    shell, guard = home
    arm(shell, guard)
    shell.exits = exit_info(shell.clock, [
        (121, 4), (122, 5), (123, 6), (-1, 6),
        (1, 1), (2, 2), (3, 3), (4, 9), (5, 10), (6, 13), (7, 16)])
    guard.check(100)
    assert guard.read().phase == "active"


def test_recovery_command_error_stays_pending_and_is_retried(home, monkeypatch):
    shell, guard = home
    arm(shell, guard)
    shell.exits = exit_info(shell.clock, [(1, 6)])
    shell.fail = f"pm enable --user 0 {SETUP_HOME}"
    monkeypatch.setattr("tvcontrol.home.time.monotonic", lambda: 100)
    guard.poll()
    assert guard.read().phase == "restoring"
    assert guard.status()["error"] == "Injected TV command failure"
    count = len(shell.commands)
    guard.poll()
    assert len(shell.commands) == count
    monkeypatch.setattr("tvcontrol.home.time.monotonic", lambda: 110)
    guard.poll()
    assert guard.read().phase == "fallback"
    assert guard.status()["error"] is None
    assert shell.states == {STOCK_HOME: 0, SETUP_HOME: 1}


@pytest.mark.parametrize("change", [
    {"version": 2}, {"package_states": {STOCK_HOME: 0}}, {"phase": "invented"},
    {"package_states": {STOCK_HOME: True, SETUP_HOME: 0}},
    {"package_states": {STOCK_HOME: 3, SETUP_HOME: 0}},
    {"original_home": "com.unknown.stock/.Home"},
])
def test_invalid_backup_is_never_executed(home, change):
    shell, guard = home
    value = guard.preflight(ORIGINAL_HOME).model_dump()
    value.update(change)
    import json
    guard.path.write_text(json.dumps(value))
    shell.commands.clear()
    with pytest.raises(DeviceError, match="invalid"):
        guard.read()
    assert shell.commands == []


def test_exit_parser_does_not_require_exit_diagnostics_for_a_never_run_app():
    assert exit_records("No process exits recorded") == []
    assert exit_records(exit_info(datetime(2026, 10, 23), [(1, 4)]))[0][2] == 4


def test_managed_package_exception_never_allows_an_unrelated_app_to_be_disabled(home):
    shell, guard = home
    arm(shell, guard)
    before = {"installed": [*HOME_PACKAGES, LAUNCHER, "com.example.player"],
              "enabled": [*HOME_PACKAGES, LAUNCHER, "com.example.player"], "disabled": []}
    after = {**before, "home": CLEAN_HOME, "native_home": guard.status(),
             "enabled": [LAUNCHER, "com.example.player"], "disabled": list(HOME_PACKAGES)}
    assert_preserved(before, after)
    assert managed_home_packages(after) == frozenset(HOME_PACKAGES)
    with pytest.raises(DeviceError, match="preservation"):
        assert_preserved(before, {**after, "enabled": [LAUNCHER],
                                  "disabled": [*HOME_PACKAGES, "com.example.player"]})
    for status in [{**guard.status(), "armed": False},
                   {**guard.status(), "phase": "fallback"},
                   {**guard.status(), "managed_packages": ["com.example.player"]}]:
        with pytest.raises(DeviceError, match="exception"):
            managed_home_packages({**after, "native_home": status})


@pytest.mark.parametrize(("power", "component", "expected"), [
    ("Awake", "com.gathin.tvhome/.MainActivity", True),
    ("Awake", "com.android.tv.settings/.system.FallbackHome", True),
    ("Awake", "com.android.tv.settings/.MainSettings", False),
    ("Awake", "com.google.android.youtube.tv/.MainActivity", False),
    ("Asleep", "com.gathin.tvhome/.MainActivity", False),
])
def test_recovery_never_wakes_tv_or_replaces_playback_or_settings(
        tmp_path, monkeypatch, power, component, expected):
    device = Device("192.168.1.137", tmp_path)
    commands = []

    def shell(command):
        commands.append(command)
        if command.startswith("dumpsys power"):
            return (f"mWakefulness={power}\n__TVCONTROL_ACTIVITIES__\n"
                    f"topResumedActivity=ActivityRecord{{123 u0 {component} t1}}")
        assert command == "input keyevent 3"
        return ""

    monkeypatch.setattr(device, "_shell", shell)
    device._show_recovered_home()
    assert ("input keyevent 3" in commands) == expected


def test_backup_requires_original_explicit_enabled_states():
    with pytest.raises(ValueError):
        HomeBackup(phase="switching", original_home=ORIGINAL_HOME,
                   package_states={STOCK_HOME: 0, SETUP_HOME: 2}, prior_exits=[])


def device_with_home(tmp_path, monkeypatch):
    shell = HomeShell()
    device = Device("192.168.1.137", tmp_path)
    monkeypatch.setattr(device, "_shell", shell)
    device.native_home = NativeHome(shell, tmp_path, device._show_recovered_home)
    services = {"selected": True}
    monkeypatch.setattr(device, "_set_home_override",
                        lambda enabled: services.update(selected=enabled))
    monkeypatch.setattr(device, "home", device.native_home.resolve)
    packages = ["android", LAUNCHER, *HOME_PACKAGES, "com.example.player"]

    def inventory():
        disabled = [package for package, value in shell.states.items() if value == 3]
        return {"home": device.native_home.resolve(), "installed": packages,
                "enabled": [package for package in packages if package not in disabled],
                "disabled": disabled, "native_home": device.native_home.status(),
                "home_override_selected": services["selected"],
                "home_override_enabled": services["selected"]}

    monkeypatch.setattr(device, "inventory", inventory)
    return device, shell, services, inventory()


def test_device_wires_guarded_mode_and_original_restore_end_to_end(tmp_path, monkeypatch):
    device, shell, services, baseline = device_with_home(tmp_path, monkeypatch)
    after = device.select_home(CLEAN_HOME, baseline, true_default=True)
    assert after["home_mode"] == "native_guarded"
    assert after["home"] == CLEAN_HOME
    assert after["native_home"]["armed"]
    assert not services["selected"]
    assert_preserved(baseline, after)
    restored = device.select_home(ORIGINAL_HOME, baseline)
    assert restored["home"] == STOCK_COMPONENT
    assert restored["native_home"]["phase"] == "restored"
    assert not restored["disabled"]
    assert shell.states == {STOCK_HOME: 0, SETUP_HOME: 1}


def test_device_rolls_back_when_final_inventory_detects_an_unrelated_change(tmp_path, monkeypatch):
    device, shell, services, baseline = device_with_home(tmp_path, monkeypatch)
    actual_inventory = device.inventory

    def inventory():
        current = actual_inventory()
        if current["native_home"] and current["native_home"]["armed"]:
            current["enabled"] = [p for p in current["enabled"] if p != "com.example.player"]
            current["disabled"].append("com.example.player")
        return current

    monkeypatch.setattr(device, "inventory", inventory)
    with pytest.raises(DeviceError, match="preservation"):
        device.select_home(CLEAN_HOME, baseline, true_default=True)
    assert device.native_home.read().phase == "fallback"
    assert shell.states == {STOCK_HOME: 0, SETUP_HOME: 1}
    assert services["selected"]


def test_device_does_not_disable_packages_if_clean_home_cannot_open(tmp_path, monkeypatch):
    device, shell, services, baseline = device_with_home(tmp_path, monkeypatch)
    shell.fail = f"am start -W -c android.intent.category.HOME -n {CLEAN_HOME}"
    with pytest.raises(DeviceError, match="Injected"):
        device.select_home(CLEAN_HOME, baseline, true_default=True)
    assert not device.native_home.path.exists()
    assert shell.states == {STOCK_HOME: 0, SETUP_HOME: 1}
    assert services["selected"]
