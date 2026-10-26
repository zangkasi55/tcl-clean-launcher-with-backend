package com.gathin.tvhome;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;

final class HaCardData {
    private HaCardData() {}

    static JSONObject empty(String kind, String status) throws JSONException {
        return new JSONObject().put("source", "home_assistant").put("transport", "direct")
                .put("status", status).put("received_at_ms", JSONObject.NULL)
                .put("data", JSONObject.NULL)
                .put("stale_after_ms", "energy".equals(kind) ? 15000 : 900000);
    }

    static JSONObject stale(JSONObject snapshot) throws JSONException {
        return new JSONObject(snapshot.toString()).put("status",
                "live".equals(snapshot.optString("status")) ? "stale" : snapshot.getString("status"));
    }

    static JSONObject create(String kind, JSONObject config, JSONObject states,
                             JSONArray forecast, long observedAt) throws JSONException {
        JSONObject snapshot = empty(kind, "live").put("received_at_ms", observedAt);
        if ("energy".equals(kind)) {
            snapshot.put("sampled_at_ms", observedAt).put("delivery_stale_after_ms", 30000);
            if (config.has("bindings")) {
                return snapshot.put("topology", HaEnergyTopology.fromStates(
                        config.getJSONObject("bindings"), states));
            }
            double grid = signed(config, states, "grid_entity", "grid_import_entity", "grid_export_entity");
            double battery = signed(config, states, "battery_entity",
                    "battery_discharge_entity", "battery_charge_entity");
            if (!HaCardConfig.bool(config, "grid_import_positive", true)) grid = -grid;
            if (!HaCardConfig.bool(config, "battery_discharge_positive", true)) battery = -battery;
            return snapshot.put("metrics", new JSONObject()
                    .put("solar_w", power(state(states, config, "solar_entity"), false))
                    .put("home_w", power(state(states, config, "home_entity"), false))
                    .put("grid_w", finite(grid)).put("battery_w", finite(battery))
                    .put("battery_soc", percentage(state(states, config, "battery_soc_entity"))));
        }
        if ("vehicle".equals(kind)) {
            JSONObject soc = state(states, config, "battery_soc_entity");
            String raw = text(soc, "state", 80);
            if (!raw.trim().matches("[+-]?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)")) {
                throw new JSONException("Invalid vehicle percentage");
            }
            String charging = text(state(states, config, "charging_entity"), "state", 16);
            if (!"on".equals(charging) && !"off".equals(charging)) {
                throw new JSONException("Vehicle charging state is unavailable, not assumed off");
            }
            return snapshot.put("data", new JSONObject()
                    .put("battery_soc_entity", config.getString("battery_soc_entity"))
                    .put("charging_entity", config.getString("charging_entity"))
                    .put("battery_soc", percentage(soc)).put("charging", "on".equals(charging)));
        }
        JSONObject weather = state(states, config, "entity_id");
        JSONObject attributes = weather.getJSONObject("attributes");
        String condition = text(weather, "state", 40);
        if ("unknown".equals(condition) || "unavailable".equals(condition)) {
            throw new JSONException("Weather entity is unavailable");
        }
        String unit = text(attributes, "temperature_unit", 2);
        if (!"\u00b0C".equals(unit) && !"\u00b0F".equals(unit)) {
            throw new JSONException("Unsupported weather temperature unit");
        }
        if (forecast == null || forecast.length() == 0) throw new JSONException("No daily HA forecast");
        JSONArray days = new JSONArray();
        long previous = 0;
        for (int i = 0; i < Math.min(3, forecast.length()); i++) {
            JSONObject item = forecast.getJSONObject(i);
            long at;
            try {
                at = OffsetDateTime.parse(text(item, "datetime", 80)).toInstant().toEpochMilli();
            } catch (DateTimeParseException e) {
                throw new JSONException("Daily forecast requires timezone-aware dates");
            }
            if (at <= previous) throw new JSONException("Daily forecast dates are not increasing");
            previous = at;
            JSONObject day = new JSONObject().put("time_ms", at)
                    .put("condition", text(item, "condition", 40))
                    .put("temperature", temperature(item.get("temperature")));
            day.put("templow", item.isNull("templow") ? JSONObject.NULL
                    : temperature(item.get("templow")));
            days.put(day);
        }
        String name = HaCardConfig.optional(attributes, "friendly_name");
        if (name == null) name = config.getString("entity_id");
        if (name.length() > 80) throw new JSONException("Weather name is too long");
        return snapshot.put("data", new JSONObject().put("entity_id", config.getString("entity_id"))
                .put("name", name).put("condition", condition).put("temperature_unit", unit)
                .put("temperature", temperature(attributes.get("temperature"))).put("forecast", days));
    }

    static JSONObject state(JSONObject states, JSONObject config, String key) throws JSONException {
        String entity = config.getString(key);
        JSONObject state = states.getJSONObject(entity);
        if (!entity.equals(state.get("entity_id"))) throw new JSONException("HA entity identity mismatch");
        return state;
    }

    static String text(JSONObject object, String key, int maximum) throws JSONException {
        Object value = object.get(key);
        if (!(value instanceof String) || ((String) value).isEmpty()
                || ((String) value).length() > maximum) throw new JSONException("Invalid HA text field");
        return (String) value;
    }

    static double numericState(JSONObject state) throws JSONException {
        String raw = text(state, "state", 80).trim();
        if (!raw.matches("[+-]?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)(?:[eE][+-]?[0-9]+)?")) {
            throw new JSONException("HA sensor is unavailable or not numeric");
        }
        try {
            return finite(Double.parseDouble(raw));
        } catch (NumberFormatException e) {
            throw new JSONException("Invalid HA sensor number");
        }
    }

    static double finite(double value) throws JSONException {
        if (!Double.isFinite(value)) throw new JSONException("Non-finite HA measurement");
        return value;
    }

    static double power(JSONObject state, boolean signed) throws JSONException {
        double watts = numericState(state);
        String unit = text(state.getJSONObject("attributes"), "unit_of_measurement", 8);
        if ("kW".equals(unit)) watts *= 1000;
        else if (!"W".equals(unit)) throw new JSONException("Expected W or kW, not energy totals");
        if (!signed && watts < 0) throw new JSONException("Expected nonnegative directional power");
        return finite(watts);
    }

    static double percentage(JSONObject state) throws JSONException {
        double value = numericState(state);
        if (!"%".equals(state.getJSONObject("attributes").get("unit_of_measurement"))
                || value < 0 || value > 100) throw new JSONException("Invalid HA battery percentage");
        return value;
    }

    private static double temperature(Object value) throws JSONException {
        if (!(value instanceof Number) || !WeatherReading.validTemperature(((Number) value).doubleValue())) {
            throw new JSONException("Invalid HA weather temperature");
        }
        return ((Number) value).doubleValue();
    }

    private static double signed(JSONObject config, JSONObject states, String signed,
                                 String positive, String negative) throws JSONException {
        return HaCardConfig.optional(config, signed) != null
                ? power(state(states, config, signed), true)
                : finite(power(state(states, config, positive), false)
                        - power(state(states, config, negative), false));
    }
}
