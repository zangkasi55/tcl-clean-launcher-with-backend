import asyncio
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from .models import Observation, Rule
from .store import Store

logger = logging.getLogger(__name__)


class DeviceError(RuntimeError):
    pass


class ObservedDevice(Protocol):
    def observe(self) -> Observation: ...
    def sleep_if_foreground(self, package: str, *, valid_from: datetime | None = None,
                            valid_until: datetime | None = None) -> bool: ...


class Monitor:
    def __init__(self, store: Store, device: ObservedDevice, poll_seconds: float = 5,
                 clock: Callable[[], datetime] | None = None):
        self.store = store
        self.device = device
        self.poll_seconds = poll_seconds
        self.now = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        self.previous: Observation | None = None
        self.previous_rule: Rule | None = None
        self.health = "starting"
        self.error: str | None = None
        self.last_sample: Observation | None = None
        self.last_attempt_monotonic = float("-inf")
        self.standby_status = "not_requested"

    def forget_interval(self) -> None:
        self.previous = None
        self.previous_rule = None

    def record(self, sample: Observation) -> bool:
        rule = self.store.rule()
        self.last_sample = sample
        self.health, self.error = "online", None
        previous = self.previous
        if previous is not None and self.previous_rule == rule and rule.enabled:
            elapsed = sample.monotonic - previous.monotonic
            wall = (sample.at - previous.at).total_seconds()
            if (elapsed < 0 or elapsed > self.poll_seconds * 3 or abs(wall - elapsed) > 2
                    or (wall <= 0 and elapsed > 0)):
                self.health = "gap"
                self.error = "Unobserved interval or clock change; that interval was not charged."
            elif (sample.awake and previous.awake
                  and sample.package == previous.package == rule.package):
                self.store.add_interval(rule.package, rule.timezone, previous.at, sample.at,
                                        elapsed_seconds=elapsed)
        self.previous, self.previous_rule = sample, rule
        if not sample.awake:
            if self.standby_status == "requested":
                self.standby_status = "confirmed"
            self.last_attempt_monotonic = float("-inf")
        used = self.store.usage(rule.package, rule.timezone, sample.at)
        return (rule.enabled and self.health == "online" and sample.awake
                and sample.package == rule.package and used >= rule.minutes * 60
                and sample.monotonic - self.last_attempt_monotonic >= 15)

    def inspect_once(self) -> None:
        with self.lock:
            self._inspect_once()

    def _inspect_once(self) -> None:
        sample = self.device.observe()
        if self.record(sample):
            rule = self.store.rule()
            tz = ZoneInfo(rule.timezone)
            next_day = sample.at.astimezone(tz).date() + timedelta(days=1)
            midnight = datetime.combine(next_day, datetime.min.time(), tz).astimezone(timezone.utc)
            deadline = min(midnight, sample.at + timedelta(seconds=self.poll_seconds * 3))
            self.last_attempt_monotonic = sample.monotonic
            # Recheck under the device lock, without carrying yesterday's budget across midnight.
            if self.device.sleep_if_foreground(rule.package, valid_from=sample.at,
                                               valid_until=deadline):
                self.standby_status = "requested"
                self.store.event("standby_requested", "Daily app allowance exhausted; sleep requested.")
            else:
                self.forget_interval()
                self.store.event("standby_skipped",
                                 "App, power state or daily enforcement window changed.")

    def poll_once(self) -> None:
        with self.lock:
            try:
                self.inspect_once()
            except DeviceError as exc:
                if self.error != str(exc):
                    logger.warning("TV monitor unavailable: %s", exc)
                    self.store.event("monitor_unavailable", str(exc))
                self.health, self.error = "unavailable", str(exc)
                self.forget_interval()

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            started = asyncio.get_running_loop().time()
            await asyncio.to_thread(self.poll_once)
            try:
                elapsed = asyncio.get_running_loop().time() - started
                await asyncio.wait_for(stop.wait(), timeout=max(0.01, self.poll_seconds - elapsed))
            except TimeoutError:
                continue

    def status(self, now: datetime | None = None) -> dict:
        rule = self.store.rule()
        now = now or self.now()
        used = self.store.usage(rule.package, rule.timezone, now)
        return {
            "rule": rule.model_dump(), "used_seconds": round(used, 3),
            "remaining_seconds": max(0, rule.minutes * 60 - used),
            "health": self.health, "error": self.error,
            "last_sample": self.last_sample.model_dump(mode="json") if self.last_sample else None,
            "standby": self.standby_status,
            "limitation": "NAS and authorized ADB must be online. This is not a tamper-proof lock.",
        }
