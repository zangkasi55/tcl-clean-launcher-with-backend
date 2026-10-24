from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tvcontrol.models import Observation, Rule
from tvcontrol.monitor import DeviceError, Monitor
from tvcontrol.store import Store

START = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "usage.sqlite3")
    value.put("rule", Rule(enabled=True).model_dump())
    yield value
    value.close()


def sample(seconds, package="com.thegate.tv", awake=True, start=START):
    return Observation(at=start + timedelta(seconds=seconds), monotonic=seconds,
                       package=package, awake=awake)


def test_exact_40_minute_threshold_not_a_shortened_proxy(store, device):
    monitor = Monitor(store, device)
    for second in range(0, 2400, 5):
        assert monitor.record(sample(second)) is False
    assert monitor.record(sample(2399)) is False
    assert store.usage("com.thegate.tv", "Asia/Bangkok", START) == 2399
    assert monitor.record(sample(2400)) is True
    assert store.usage("com.thegate.tv", "Asia/Bangkok", START) == 2400


def test_visits_accumulate_without_counting_other_apps_or_standby(store, device):
    monitor = Monitor(store, device)
    for observation in [sample(0), sample(5), sample(10), sample(15, "com.netflix.ninja"),
                        sample(20), sample(25), sample(30, awake=False),
                        sample(35), sample(40)]:
        monitor.record(observation)
    assert store.usage("com.thegate.tv", "Asia/Bangkok", START) == 20
    assert store.usage("com.netflix.ninja", "Asia/Bangkok", START) == 0


def test_paused_video_counts_without_playback_session_metadata(store, device):
    monitor = Monitor(store, device)
    monitor.record(sample(0))
    monitor.record(sample(5))
    assert store.usage("com.thegate.tv", "Asia/Bangkok", START) == 5


def test_restart_retains_consumption_and_enforces_an_exhausted_app(tmp_path, device):
    path = tmp_path / "persistent.sqlite3"
    first = Store(path)
    first.put("rule", Rule(enabled=True).model_dump())
    first.add_interval("com.thegate.tv", "Asia/Bangkok", START, START + timedelta(seconds=2400))
    first.close()
    second = Store(path)
    try:
        monitor = Monitor(second, device)
        assert monitor.record(sample(3000)) is True
        assert second.usage("com.thegate.tv", "Asia/Bangkok", START) == 2400
    finally:
        second.close()


def test_midnight_splits_the_interval_in_configured_timezone(store, device):
    start = datetime(2026, 10, 20, 16, 59, 58, tzinfo=timezone.utc)
    monitor = Monitor(store, device)
    monitor.record(sample(0, start=start))
    monitor.record(sample(4, start=start))
    assert store.usage("com.thegate.tv", "Asia/Bangkok", start) == 2
    assert store.usage("com.thegate.tv", "Asia/Bangkok", start + timedelta(seconds=4)) == 2


def test_ledger_handles_a_dst_day_without_assuming_24_hours(store):
    start = datetime(2026, 11, 1, 4, tzinfo=timezone.utc)
    end = datetime(2026, 11, 2, 5, tzinfo=timezone.utc)
    store.add_interval("com.thegate.tv", "America/New_York", start, end)
    assert store.usage("com.thegate.tv", "America/New_York", start) == 25 * 3600
    local = ZoneInfo("America/New_York")
    store.add_interval("com.netflix.ninja", "America/New_York",
                       datetime(2026, 11, 1, tzinfo=local),
                       datetime(2026, 11, 2, tzinfo=local))
    assert store.usage("com.netflix.ninja", "America/New_York", start) == 25 * 3600


def test_unknown_gap_and_clock_change_are_not_silently_charged(store, device):
    monitor = Monitor(store, device)
    monitor.record(sample(0))
    monitor.record(sample(5))
    assert monitor.record(sample(100)) is False
    assert monitor.health == "gap"
    assert monitor.error
    assert store.usage("com.thegate.tv", "Asia/Bangkok", START) == 5
    monitor.record(Observation(at=START + timedelta(hours=2), monotonic=105,
                               package="com.thegate.tv", awake=True))
    assert monitor.health == "gap"
    assert store.usage("com.thegate.tv", "Asia/Bangkok", START) == 5


def test_small_wall_clock_slew_cannot_make_the_allowance_expire_early(store, device):
    monitor = Monitor(store, device)
    monitor.record(sample(0))
    monitor.record(Observation(at=START + timedelta(seconds=6), monotonic=5,
                               package="com.thegate.tv", awake=True))
    assert monitor.health == "online"
    assert store.usage("com.thegate.tv", "Asia/Bangkok", START) == 5
    monitor.record(sample(10))
    assert store.usage("com.thegate.tv", "Asia/Bangkok", START) == 10


def test_changes_and_disable_do_not_reset_used_time(store, device):
    monitor = Monitor(store, device)
    monitor.record(sample(0))
    monitor.record(sample(5))
    store.put("rule", Rule(enabled=False, minutes=20).model_dump())
    assert monitor.record(sample(10)) is False
    store.put("rule", Rule(enabled=True, package="com.netflix.ninja").model_dump())
    monitor.record(sample(15, package="com.netflix.ninja"))
    store.put("rule", Rule(enabled=True).model_dump())
    monitor.record(sample(20))
    assert store.usage("com.thegate.tv", "Asia/Bangkok", START) == 5


def test_standby_is_reenforced_on_wake_and_confirmed_separately(store, device):
    store.add_interval("com.thegate.tv", "Asia/Bangkok", device.now - timedelta(seconds=2400),
                       device.now)
    monitor = Monitor(store, device)
    monitor.inspect_once()
    assert device.sleeps == 1
    assert monitor.standby_status == "requested"
    device.monotonic += 5
    device.now += timedelta(seconds=5)
    monitor.inspect_once()
    assert monitor.standby_status == "confirmed"
    assert device.sleeps == 1
    device.awake = True
    device.monotonic += 5
    device.now += timedelta(seconds=5)
    monitor.inspect_once()
    assert device.sleeps == 2


def test_app_switch_before_sleep_prevents_wrong_app_shutdown(store, device):
    store.add_interval("com.thegate.tv", "Asia/Bangkok", device.now - timedelta(seconds=2400),
                       device.now)
    device.switch_before_sleep = True
    monitor = Monitor(store, device)
    monitor.inspect_once()
    assert device.sleeps == 0
    assert monitor.previous is None


def test_failed_standby_is_not_reported_as_success(store, device):
    store.add_interval("com.thegate.tv", "Asia/Bangkok", device.now - timedelta(seconds=2400),
                       device.now)
    device.fail_sleep = True
    monitor = Monitor(store, device)
    with pytest.raises(DeviceError):
        monitor.inspect_once()
    assert monitor.standby_status == "not_requested"
    assert device.sleeps == 0


def test_standby_is_skipped_if_the_final_device_check_crosses_midnight(store, device):
    device.now = datetime(2026, 10, 20, 16, 59, 59, tzinfo=timezone.utc)
    device.sleep_delay_seconds = 2
    store.add_interval("com.thegate.tv", "Asia/Bangkok",
                       device.now - timedelta(seconds=2400), device.now)
    monitor = Monitor(store, device)
    monitor.inspect_once()
    assert device.sleeps == 0
    assert monitor.standby_status == "not_requested"


def test_failed_standby_attempts_are_rate_limited(store, device):
    store.add_interval("com.thegate.tv", "Asia/Bangkok",
                       device.now - timedelta(seconds=2400), device.now)
    device.fail_sleep = True
    monitor = Monitor(store, device)
    with pytest.raises(DeviceError):
        monitor.inspect_once()
    device.now += timedelta(seconds=5)
    device.monotonic += 5
    monitor.inspect_once()
    assert device.sleep_checks == 1
    device.now += timedelta(seconds=10)
    device.monotonic += 10
    with pytest.raises(DeviceError):
        monitor.inspect_once()
    assert device.sleep_checks == 2
    assert monitor.standby_status == "not_requested"
