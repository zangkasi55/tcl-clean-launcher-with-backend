import json
from pathlib import Path

import pytest

from tvcontrol.energy_topology import EnergyBindings, topology_from_states

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "energy_topology.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", FIXTURES["cases"], ids=lambda case: case["name"])
def test_complete_topology_matches_shared_contract(case):
    bindings = EnergyBindings.model_validate({**FIXTURES["bindings"], **case.get("bindings", {})})
    readings = {**FIXTURES["readings"], **case.get("readings", {})}
    states = {
        entity: {
            "entity_id": entity,
            "state": reading[0],
            "attributes": {"unit_of_measurement": reading[1], "device_class": reading[2]},
        }
        for entity, reading in readings.items()
        if entity not in case.get("missing", [])
    }
    assert topology_from_states(bindings, states).model_dump(mode="json") == case["expected"]
