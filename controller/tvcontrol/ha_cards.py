import json

from pydantic import Field, StrictInt, model_validator

from .energy import EnergyConfig
from .models import Model
from .vehicle import VehicleConfig
from .weather import WeatherConfig


class DirectHaCards(Model):
    """Non-secret native mappings, independent of the legacy backend feeds."""

    schema_version: StrictInt = Field(ge=1, le=1)
    energy: EnergyConfig
    weather: WeatherConfig
    vehicle: VehicleConfig

    @model_validator(mode="after")
    def native_contract(self):
        if any(card.transport != "websocket"
               for card in (self.energy, self.weather, self.vehicle)):
            raise ValueError("Direct TV cards require real Home Assistant WebSocket mappings")
        if (self.energy.enabled and self.energy.topology_dashboard is None
                and any(value is None for value in self.energy.mappings().values())):
            raise ValueError("Map all required energy sensors or select an HA energy dashboard")
        encoded = json.dumps(self.model_dump(), ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
        if len(encoded) > 16384:
            raise ValueError("Direct TV card mappings exceed the native 16 KiB limit")
        return self
