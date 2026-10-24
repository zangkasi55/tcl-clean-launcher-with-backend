import math
import re

from pydantic import Field, StrictBool, field_validator

from .models import Model


class EnergyBindings(Model):
    enphase_solar_entity: str
    ess_solar_entity: str
    home_entity: str
    grid_import_entity: str
    grid_export_entity: str
    battery_charge_entity: str
    battery_discharge_entity: str
    battery_soc_entity: str
    enphase_monitor_entity: str | None = None
    home_fallback_entity: str | None = None
    ess_solar_planned: StrictBool
    ess_cloud_offline: StrictBool = False
    balance_tolerance_w: float = Field(ge=0, le=1000, allow_inf_nan=False)

    @field_validator("*", mode="before")
    @classmethod
    def entity_names(cls, value, info):
        if info.field_name.endswith("_entity") and value is not None:
            if not isinstance(value, str) or not re.fullmatch(r"sensor\.[a-z0-9_]+", value):
                raise ValueError("HA energy-card bindings must contain exact sensor entity IDs")
        if info.field_name == "balance_tolerance_w" and (
                isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError("The power-balance tolerance must be numeric")
        return value

    def entities(self) -> set[str]:
        return {value for key, value in self.model_dump().items()
                if key.endswith("_entity") and value is not None}


class EnergyEdge(Model):
    id: str
    watts: float | None
    direction: int
    active: bool
    planned: bool = False
    quality: str


class EnergyTopology(Model):
    values: dict[str, float | None]
    planned: bool
    soc: float | None
    enphase_monitor: float | None
    home_display: float | None
    home_fallback_active: bool
    ess_offline: bool
    solar_input: float | None
    imbalance: float | None
    balanced: bool
    status: str
    status_text: str
    issues: list[str]
    notes: list[str]
    edges: list[EnergyEdge]


def bindings_from_dashboard(config: dict, view_name: str) -> EnergyBindings:
    def candidates(cards):
        if not isinstance(cards, list):
            return
        for card in cards:
            if not isinstance(card, dict):
                continue
            if (card.get("type") == "custom:button-card"
                    and isinstance(card.get("variables"), dict)
                    and "enphase_solar_entity" in card["variables"]):
                yield card["variables"]
            yield from candidates(card.get("cards", []))
            yield from candidates(card.get("sections", []))
            if isinstance(card.get("card"), dict):
                yield from candidates([card["card"]])

    selected = [view for view in config.get("views", [])
                if view.get("path") == view_name or view.get("title") == view_name]
    matches = list(candidates(selected))
    if len(matches) != 1:
        raise ValueError("Select one unambiguous HA Energy Distribution card and view")
    return EnergyBindings.model_validate({key: value for key, value in matches[0].items()
                                         if key in EnergyBindings.model_fields})


def topology_from_states(bindings: EnergyBindings, states: dict[str, dict]) -> EnergyTopology:
    issues, notes = [], []
    offline = bindings.ess_cloud_offline
    if offline:
        issues.append("ESS cloud is offline; its measurements are unavailable")

    def power(key, label, *, required=True, signed=False):
        if offline and key in {
                "enphase_solar_entity", "home_entity", "grid_import_entity",
                "grid_export_entity", "battery_charge_entity", "battery_discharge_entity"}:
            return None
        state = states.get(getattr(bindings, key), {})
        raw, attributes = state.get("state"), state.get("attributes", {})
        if not isinstance(attributes, dict):
            attributes = {}
        try:
            value = float(raw) if isinstance(raw, str) and raw.strip() else float("nan")
        except ValueError:
            value = float("nan")
        if (not math.isfinite(value) or attributes.get("unit_of_measurement") not in {"W", "kW"}
                or attributes.get("device_class") != "power"):
            (issues if required else notes).append(
                f"{label} power is unavailable or has invalid units")
            return None
        watts = value * (1000 if attributes["unit_of_measurement"] == "kW" else 1)
        if not math.isfinite(watts) or watts < 0 and not signed:
            (issues if required else notes).append(
                f"{label} requires a nonnegative directional reading")
            return None
        return watts

    values = {
        "enphase": power("enphase_solar_entity", "Enphase Solar"),
        "ess": power("ess_solar_entity", "ESS Solar"),
        "home": power("home_entity", "Home"),
        "gridImport": power("grid_import_entity", "Grid import"),
        "gridExport": power("grid_export_entity", "Grid export"),
        "charge": power("battery_charge_entity", "Battery charge"),
        "discharge": power("battery_discharge_entity", "Battery discharge"),
    }
    monitor = power("enphase_monitor_entity", "Enphase monitor", required=False, signed=True)
    fallback = (power("home_fallback_entity", "Enphase CT load", required=False)
                if values["home"] is None else None)
    fallback_active = values["home"] is None and fallback is not None
    home = fallback if fallback_active else values["home"]
    if fallback_active:
        notes.append("Home displays an Enphase CT fallback; the ESS total is unverified")
    if bindings.ess_solar_planned and values["ess"] is not None and values["ess"] != 0:
        issues.append("Planned ESS PV is nonzero; commissioning is required")
    soc_state = {} if offline else states.get(bindings.battery_soc_entity, {})
    raw_soc = soc_state.get("state")
    try:
        soc = float(raw_soc) if isinstance(raw_soc, str) and raw_soc.strip() else float("nan")
    except ValueError:
        soc = float("nan")
    soc_attributes = soc_state.get("attributes")
    if (not math.isfinite(soc) or not 0 <= soc <= 100
            or not isinstance(soc_attributes, dict)
            or soc_attributes.get("unit_of_measurement") != "%"):
        soc = None
        notes.append("Battery state of charge is unavailable")
    available = not issues
    imbalance = (values["enphase"] + values["ess"] + values["gridImport"] + values["discharge"]
                 - values["home"] - values["gridExport"] - values["charge"]) if available else None
    balanced = available and abs(imbalance) <= bindings.balance_tolerance_w
    battery_net = (values["charge"] - values["discharge"]
                   if values["charge"] is not None and values["discharge"] is not None else None)
    grid_net = (values["gridExport"] - values["gridImport"]
                if values["gridExport"] is not None and values["gridImport"] is not None else None)
    solar = (values["enphase"] + values["ess"]
             if values["enphase"] is not None and values["ess"] is not None else None)

    def edge(name, watts, direction=1, planned=False, partial=False, quality=None):
        return EnergyEdge(id=name, watts=watts, direction=direction, planned=planned,
            active=(balanced or partial) and not planned and watts is not None and watts > 1,
            quality=quality or ("unavailable" if watts is None else
                               "planned" if planned else "measured"))

    def direction(value):
        return 0 if value is None or value == 0 else -1 if value < 0 else 1
    return EnergyTopology(values=values, planned=bindings.ess_solar_planned, soc=soc,
        enphase_monitor=monitor, home_display=home, home_fallback_active=fallback_active,
        ess_offline=offline, solar_input=solar, imbalance=imbalance, balanced=balanced,
        status="error" if not available else "warning" if not balanced or notes else "ok",
        status_text="ESS cloud offline" if offline else "ESS data unavailable" if not available
        else "Check meter balance" if not balanced else "Live / check details" if notes
        else "Live / balanced", issues=issues, notes=notes,
        edges=[
            edge("enphase-inverter", abs(monitor) if monitor is not None else None,
                 direction(monitor), partial=True),
            edge("ess-inverter-pv", values["ess"], planned=bindings.ess_solar_planned),
            edge("inverter-grid", abs(grid_net) if grid_net is not None else None, direction(grid_net)),
            edge("inverter-battery", abs(battery_net) if battery_net is not None else None,
                 direction(battery_net)),
            edge("inverter-home", home, direction(home),
                 quality="ct-only" if fallback_active else "unavailable" if home is None
                 else "measured"),
        ])
