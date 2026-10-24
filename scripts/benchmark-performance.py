"""Repeatable local work-count benchmark; never connects to HA, ADB or a real TV."""
import argparse
import json
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tvcontrol.energy import ESS_LINK_CONFIG, EnergyService
from tvcontrol.models import LAUNCHER
from tvcontrol.store import Store


def benchmark(interval: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="tcl-performance-") as directory:
        root = Path(directory)
        store = Store(root / "benchmark.sqlite3")
        wall = datetime(2026, 10, 20, 8, tzinfo=UTC)
        monotonic = 0.0

        class Publisher:
            def __init__(self):
                self.calls = 0

            def publish_energy(self, snapshot):
                self.calls += 1
                return True

        publisher = Publisher()
        try:
            service = EnergyService(store, publisher, root, lambda: wall, sandbox=False)
            service.monotonic = lambda: monotonic
            store.put("energy_config", ESS_LINK_CONFIG.model_dump())
            store.put("inventory", {"installed": [LAUNCHER]})
            values = {"solar_w": "1.2", "home_w": "0.8", "grid_import_w": "0",
                      "grid_export_w": "0", "battery_discharge_w": "0",
                      "battery_charge_w": "0.4", "battery_soc": "40"}
            states = {entity: {"entity_id": entity, "state": values[metric],
                              "attributes": {"unit_of_measurement":
                                             "%" if metric == "battery_soc" else "kW"}}
                      for metric, entity in ESS_LINK_CONFIG.mappings().items()}
            statements, durations = [], []
            store.db.set_trace_callback(statements.append)
            queued = callable(getattr(service, "deliver_pending", None))
            for _ in range(60):
                start = time.perf_counter()
                service.accept_states(states)
                if queued:
                    service.deliver_pending()
                durations.append((time.perf_counter() - start) * 1000)
                wall += timedelta(seconds=interval)
                monotonic += interval
            store.db.set_trace_callback(None)
            return {
                "scenario": "steady_5s_heartbeats" if interval else "60_update_burst",
                "updates": 60, "publications": publisher.calls,
                "sqlite_commits": sum(sql.upper() == "COMMIT" for sql in statements),
                "mean_ms_local": round(sum(durations) / len(durations), 3),
                "p95_ms_local": round(sorted(durations)[56], 3),
                "delivery": "coalesced" if queued else "synchronous_baseline",
            }
        finally:
            store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--assert-optimized", action="store_true")
    args = parser.parse_args()
    results = [benchmark(0), benchmark(5)]
    if args.assert_optimized:
        assert all(result["sqlite_commits"] == 0 for result in results)
        assert results[0]["publications"] == 1
        assert results[1]["publications"] <= 30
    report = json.dumps({"network_access": False, "real_tv_benchmark": False,
                         "results": results}, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    print(report, end="")
