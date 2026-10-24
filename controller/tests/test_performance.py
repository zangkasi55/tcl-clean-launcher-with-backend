import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tvcontrol.device import Device
from tvcontrol.energy import ESS_LINK_CONFIG, EnergyMetrics, EnergyService, EnergySnapshot
from tvcontrol.models import LAUNCHER
from tvcontrol.monitor import DeviceError
from tvcontrol.store import Store


class Publisher:
    def __init__(self):
        self.frames = []
        self.busy = False

    def publish_energy(self, snapshot):
        if self.busy:
            return False
        self.frames.append(snapshot)
        return True


@pytest.fixture
def energy(tmp_path):
    store = Store(tmp_path / "performance.sqlite3")
    publisher = Publisher()
    now = datetime(2026, 10, 20, 8, tzinfo=timezone.utc)
    service = EnergyService(store, publisher, tmp_path, lambda: now, sandbox=False)
    service.monotonic = lambda: 0.0
    store.put("energy_config", ESS_LINK_CONFIG.model_dump())
    store.put("inventory", {"installed": [LAUNCHER]})
    yield service, publisher, store, now
    store.close()


def frame(at, watts=1200):
    return EnergySnapshot(source="home_assistant", status="live", sampled_at=at, received_at=at,
                          metrics=EnergyMetrics(solar_w=watts, home_w=800, grid_w=0,
                                                battery_w=-400, battery_soc=40))


def test_transient_heartbeats_do_not_write_sqlite_or_publish_synchronously(energy):
    service, publisher, store, now = energy
    statements = []
    store.db.set_trace_callback(statements.append)
    for _ in range(60):
        service._commit(frame(now))
    store.db.set_trace_callback(None)
    assert sum(sql.upper() == "COMMIT" for sql in statements) == 0
    assert publisher.frames == []
    assert service.deliver_pending()
    assert len(publisher.frames) == 1


def test_only_latest_energy_frame_is_delivered_and_changes_are_rate_limited(energy):
    service, publisher, _, now = energy
    for watts in range(100, 160):
        service._commit(frame(now, watts))
    assert service.deliver_pending()
    assert publisher.frames[-1]["metrics"]["solar_w"] == 159
    service._commit(frame(now + timedelta(seconds=1), 180))
    assert not service.deliver_pending()
    service.monotonic = lambda: 1.0
    assert service.deliver_pending()
    assert publisher.frames[-1]["metrics"]["solar_w"] == 180


def test_unchanged_values_keep_freshness_without_republishing_every_heartbeat(energy):
    service, publisher, _, now = energy
    service._commit(frame(now))
    service.deliver_pending()
    service.monotonic = lambda: 5.0
    service._commit(frame(now + timedelta(seconds=5)))
    assert not service.deliver_pending()
    service.monotonic = lambda: 10.0
    service._commit(frame(now + timedelta(seconds=10)))
    assert service.deliver_pending()
    assert len(publisher.frames) == 2
    assert publisher.frames[-1]["received_at_ms"] > publisher.frames[0]["received_at_ms"]


def test_busy_device_defers_energy_but_does_not_block_ingesting_new_values(energy):
    service, publisher, _, now = energy
    publisher.busy = True
    service._commit(frame(now))
    assert not service.deliver_pending()
    assert service.delivery_error is not None
    publisher.busy = False
    service._commit(frame(now + timedelta(seconds=1), 2200))
    service.monotonic = lambda: 1.0
    assert service.deliver_pending()
    assert publisher.frames[-1]["metrics"]["solar_w"] == 2200
    assert service.delivery_error is None


def test_slow_energy_delivery_does_not_hold_the_live_source_lock(energy):
    service, publisher, _, now = energy
    started, release = threading.Event(), threading.Event()
    errors = []

    def slow_publish(snapshot):
        started.set()
        if not release.wait(2):
            raise AssertionError("Synthetic publisher was not released")
        publisher.frames.append(snapshot)
        return True

    publisher.publish_energy = slow_publish
    service._commit(frame(now))

    def deliver():
        try:
            service.deliver_pending()
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=deliver)
    thread.start()
    try:
        assert started.wait(1)
        assert service.lock.acquire(timeout=0.2)
        try:
            service._commit(frame(now, 1800))
        finally:
            service.lock.release()
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    assert errors == []
    publisher.publish_energy = lambda snapshot: publisher.frames.append(snapshot) or True
    service.monotonic = lambda: 1.0
    assert service.deliver_pending()
    assert publisher.frames[-1]["metrics"]["solar_w"] == 1800


def test_graceful_checkpoint_restores_last_reading_as_stale_not_fake_live(tmp_path):
    path = tmp_path / "restart.sqlite3"
    now = datetime(2026, 10, 20, 8, tzinfo=timezone.utc)
    first = Store(path)
    first.put("energy_config", ESS_LINK_CONFIG.model_dump())
    service = EnergyService(first, Publisher(), tmp_path, lambda: now, sandbox=False)
    service._commit(frame(now))
    assert first.get("energy_snapshot") is None
    service.checkpoint()
    first.close()
    restored = Store(path)
    try:
        restarted = EnergyService(restored, Publisher(), tmp_path, lambda: now, sandbox=False)
        assert restarted.snapshot().status == "stale"
        assert restarted.snapshot().metrics.solar_w == 1200
        assert "restart" in restarted.snapshot().message
    finally:
        restored.close()


def test_telemetry_is_one_bounded_shell_command_without_a_file_push(tmp_path, monkeypatch):
    device = Device("192.168.1.148", tmp_path)
    device.connection = SimpleNamespace(available=True)
    calls = []
    monkeypatch.setattr(device, "_shell",
                        lambda command, **kwargs: calls.append((command, kwargs)) or "")
    assert device.publish_energy(frame(datetime.now(timezone.utc)).wire())
    assert len(calls) == 1
    command, options = calls[0]
    assert "base64 -d" in command and "sha256sum" in command and " && mv " in command
    assert options["timeout_s"] <= 2
    assert options["read_timeout_s"] <= 2
    assert not list(tmp_path.glob("energy-cache.json"))


def test_energy_yields_to_a_waiting_timer_check(tmp_path, monkeypatch):
    device = Device("192.168.1.148", tmp_path)
    device.connection = SimpleNamespace(available=True)
    announced, finished = threading.Event(), threading.Event()
    calls = []
    monkeypatch.setattr(device, "_shell", lambda command, **kwargs: calls.append(command) or "")

    def timer():
        with device.priority_io():
            announced.set()
            finished.wait(1)

    thread = threading.Thread(target=timer)
    thread.start()
    try:
        assert announced.wait(1)
        assert not device.publish_energy(frame(datetime.now(timezone.utc)).wire())
        assert calls == []
    finally:
        finished.set()
        thread.join(2)


def test_energy_does_not_open_a_new_adb_connection_or_hide_failed_delivery(tmp_path, monkeypatch):
    device = Device("192.168.1.148", tmp_path)
    monkeypatch.setattr(device, "_connect",
                        lambda: pytest.fail("Background telemetry attempted ADB authorization"))
    assert not device.publish_energy(frame(datetime.now(timezone.utc)).wire())
    device.connection = SimpleNamespace(available=True)

    def failure(command, **kwargs):
        raise DeviceError("Synthetic checksum failure")

    monkeypatch.setattr(device, "_shell", failure)
    with pytest.raises(DeviceError, match="checksum"):
        device.publish_energy(frame(datetime.now(timezone.utc)).wire())


def test_artwork_can_be_privately_cached_without_caching_control_responses(client):
    from io import BytesIO

    from PIL import Image

    image = BytesIO()
    Image.new("RGB", (100, 100), "#335577").save(image, "PNG")
    name = client.post("/api/images/artwork", files={
        "file": ("artwork.png", image.getvalue(), "image/png")}).json()["filename"]
    response = client.get("/api/assets/" + name)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert client.get("/api/status").headers["cache-control"] == "no-store"
    assert json.loads(client.get("/api/status").content)["theme"]["energy_panel"] is True
