package com.gathin.tvhome;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

final class HaEnergyTopology {
    static final String[] ENTITIES = {"enphase_solar_entity", "ess_solar_entity", "home_entity",
            "grid_import_entity", "grid_export_entity", "battery_charge_entity",
            "battery_discharge_entity", "battery_soc_entity", "enphase_monitor_entity",
            "home_fallback_entity"};
    private final JSONObject bindings, states;
    private final JSONArray issues = new JSONArray(), notes = new JSONArray();
    private final boolean offline;

    private HaEnergyTopology(JSONObject bindings, JSONObject states) throws JSONException {
        this.bindings = bindings;
        this.states = states;
        offline = HaCardConfig.bool(bindings, "ess_cloud_offline", false);
    }

    static JSONObject bindings(JSONObject dashboard, String viewName) throws JSONException {
        List<JSONObject> matches = new ArrayList<>();
        JSONArray views = dashboard.getJSONArray("views");
        int[] visited = {0};
        for (int i = 0; i < views.length(); i++) {
            JSONObject view = views.getJSONObject(i);
            if (viewName.equals(view.opt("path")) || viewName.equals(view.opt("title"))) {
                walk(view, matches, 0, visited);
            }
        }
        if (matches.size() != 1) throw new JSONException("Select one unambiguous HA Energy Distribution card");
        JSONObject source = matches.get(0), result = new JSONObject();
        for (int i = 0; i < ENTITIES.length; i++) {
            result.put(ENTITIES[i], HaCardConfig.entity(source, ENTITIES[i], "sensor", i < 8));
        }
        result.put("ess_solar_planned", HaCardConfig.boolRequired(source, "ess_solar_planned"));
        result.put("ess_cloud_offline", HaCardConfig.bool(source, "ess_cloud_offline", false));
        Object tolerance = source.get("balance_tolerance_w");
        if (!(tolerance instanceof Number) || !Double.isFinite(((Number) tolerance).doubleValue())
                || ((Number) tolerance).doubleValue() < 0 || ((Number) tolerance).doubleValue() > 1000) {
            throw new JSONException("Invalid HA energy balance tolerance");
        }
        return result.put("balance_tolerance_w", tolerance);
    }

    private static void walk(JSONObject card, List<JSONObject> matches, int depth, int[] visited)
            throws JSONException {
        if (depth > 32 || ++visited[0] > 4096) throw new JSONException("HA dashboard is too complex");
        JSONObject variables = card.optJSONObject("variables");
        if ("custom:button-card".equals(card.opt("type")) && variables != null
                && variables.has("enphase_solar_entity")) matches.add(variables);
        for (String field : new String[]{"cards", "sections"}) {
            JSONArray children = card.optJSONArray(field);
            if (children != null) for (int i = 0; i < children.length(); i++) {
                JSONObject child = children.optJSONObject(i);
                if (child != null) walk(child, matches, depth + 1, visited);
            }
        }
        JSONObject child = card.optJSONObject("card");
        if (child != null) walk(child, matches, depth + 1, visited);
    }

    static JSONObject fromStates(JSONObject bindings, JSONObject states) throws JSONException {
        return new HaEnergyTopology(bindings, states).calculate();
    }

    private JSONObject calculate() throws JSONException {
        if (offline) issues.put("ESS cloud is offline; its measurements are unavailable");
        String[] fields = {"enphase_solar_entity", "ess_solar_entity", "home_entity",
                "grid_import_entity", "grid_export_entity", "battery_charge_entity",
                "battery_discharge_entity"};
        String[] labels = {"Enphase Solar", "ESS Solar", "Home", "Grid import",
                "Grid export", "Battery charge", "Battery discharge"};
        String[] names = {"enphase", "ess", "home", "gridImport", "gridExport", "charge", "discharge"};
        Double[] values = new Double[fields.length];
        JSONObject encoded = new JSONObject();
        for (int i = 0; i < fields.length; i++) {
            values[i] = power(fields[i], labels[i], true, false);
            encoded.put(names[i], nullable(values[i]));
        }
        Double monitor = power("enphase_monitor_entity", "Enphase monitor", false, true);
        Double fallback = values[2] == null ? power("home_fallback_entity", "Enphase CT load", false, false) : null;
        boolean fallbackActive = values[2] == null && fallback != null;
        Double home = fallbackActive ? fallback : values[2];
        if (fallbackActive) notes.put("Home displays an Enphase CT fallback; the ESS total is unverified");
        boolean planned = HaCardConfig.boolRequired(bindings, "ess_solar_planned");
        if (planned && values[1] != null && values[1] != 0) {
            issues.put("Planned ESS PV is nonzero; commissioning is required");
        }
        Double soc = null;
        if (!offline) {
            try {
                soc = HaCardData.percentage(HaCardData.state(states, bindings, "battery_soc_entity"));
            } catch (JSONException e) {
                // An optional SOC must not invalidate independently measured power.
            }
        }
        if (soc == null) notes.put("Battery state of charge is unavailable");
        boolean available = issues.length() == 0;
        Double imbalance = available ? HaCardData.finite(values[0] + values[1] + values[3] + values[6]
                - values[2] - values[4] - values[5]) : null;
        boolean balanced = available && Math.abs(imbalance) <= bindings.getDouble("balance_tolerance_w");
        Double battery = difference(values[5], values[6]), grid = difference(values[4], values[3]);
        Double solar = values[0] == null || values[1] == null ? null : HaCardData.finite(values[0] + values[1]);
        JSONArray edges = new JSONArray()
                .put(edge("enphase-inverter", monitor, false, true, balanced, null))
                .put(edge("ess-inverter-pv", values[1], planned, false, balanced, null).put("direction", 1))
                .put(edge("inverter-grid", grid, false, false, balanced, null))
                .put(edge("inverter-battery", battery, false, false, balanced, null))
                .put(edge("inverter-home", home, false, false, balanced,
                        fallbackActive ? "ct-only" : home == null ? "unavailable" : "measured"));
        String status = !available ? "error" : !balanced || notes.length() > 0 ? "warning" : "ok";
        String text = offline ? "ESS cloud offline" : !available ? "ESS data unavailable"
                : !balanced ? "Check meter balance" : notes.length() > 0 ? "Live / check details" : "Live / balanced";
        return new JSONObject().put("values", encoded).put("planned", planned).put("soc", nullable(soc))
                .put("enphase_monitor", nullable(monitor)).put("home_display", nullable(home))
                .put("home_fallback_active", fallbackActive).put("ess_offline", offline)
                .put("solar_input", nullable(solar)).put("imbalance", nullable(imbalance))
                .put("balanced", balanced).put("status", status).put("status_text", text)
                .put("issues", issues).put("notes", notes).put("edges", edges);
    }

    private Double power(String field, String label, boolean required, boolean signed) throws JSONException {
        if (offline && !"ess_solar_entity".equals(field)
                && !"enphase_monitor_entity".equals(field) && !"home_fallback_entity".equals(field)) return null;
        Double value = null;
        JSONObject state = states.optJSONObject(bindings.optString(field, ""));
        if (state != null) {
            try {
                JSONObject attributes = state.getJSONObject("attributes");
                if ("power".equals(attributes.opt("device_class"))
                        && ("W".equals(attributes.opt("unit_of_measurement"))
                        || "kW".equals(attributes.opt("unit_of_measurement")))) {
                    value = HaCardData.numericState(state)
                            * ("kW".equals(attributes.opt("unit_of_measurement")) ? 1000 : 1);
                }
            } catch (JSONException e) {
                // Missing/invalid power is represented in topology quality, never as zero.
            }
        }
        if (value == null) {
            (required ? issues : notes).put(label + " power is unavailable or has invalid units");
        } else if (!Double.isFinite(value) || value < 0 && !signed) {
            (required ? issues : notes).put(label + " requires a nonnegative directional reading");
            return null;
        }
        return value;
    }

    private static Object nullable(Double value) {
        return value == null ? JSONObject.NULL : value;
    }

    private static Double difference(Double positive, Double negative) throws JSONException {
        return positive == null || negative == null ? null : HaCardData.finite(positive - negative);
    }

    private static JSONObject edge(String id, Double value, boolean planned, boolean partial,
                                   boolean balanced, String quality) throws JSONException {
        double watts = value == null ? 0 : Math.abs(value);
        return new JSONObject().put("id", id).put("watts", value == null ? JSONObject.NULL : watts)
                .put("direction", value == null || value == 0 ? 0 : value < 0 ? -1 : 1)
                .put("active", (balanced || partial) && !planned && value != null && watts > 1)
                .put("planned", planned).put("quality", quality != null ? quality
                        : value == null ? "unavailable" : planned ? "planned" : "measured");
    }
}
