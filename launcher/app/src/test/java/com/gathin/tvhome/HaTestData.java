package com.gathin.tvhome;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

final class HaTestData {
    static final long NOW = 1800000000000L;

    private HaTestData() {}

    static JSONObject mapping() throws JSONException {
        return new JSONObject().put("schema_version", 1)
                .put("energy", new JSONObject()).put("weather", weather())
                .put("vehicle", vehicle());
    }

    static JSONObject vehicle() throws JSONException {
        return new JSONObject().put("enabled", true).put("transport", "websocket")
                .put("battery_soc_entity", "sensor.test_soc")
                .put("charging_entity", "binary_sensor.test_charging");
    }

    static JSONObject weather() throws JSONException {
        return new JSONObject().put("enabled", true).put("transport", "websocket")
                .put("entity_id", "weather.test");
    }

    static JSONObject basicEnergy() throws JSONException {
        return new JSONObject().put("enabled", true).put("transport", "websocket")
                .put("solar_entity", "sensor.solar").put("home_entity", "sensor.home")
                .put("grid_entity", "sensor.grid").put("battery_entity", "sensor.battery")
                .put("battery_soc_entity", "sensor.test_soc");
    }

    static JSONObject state(String entity, String value, String unit) throws JSONException {
        return new JSONObject().put("entity_id", entity).put("state", value)
                .put("attributes", new JSONObject().put("unit_of_measurement", unit));
    }

    static JSONObject vehicleStates() throws JSONException {
        return new JSONObject()
                .put("sensor.test_soc", state("sensor.test_soc", "47", "%"))
                .put("binary_sensor.test_charging", state("binary_sensor.test_charging", "on", null));
    }

    static JSONObject weatherStates() throws JSONException {
        return new JSONObject().put("weather.test", new JSONObject().put("entity_id", "weather.test")
                .put("state", "sunny").put("attributes", new JSONObject()
                        .put("friendly_name", "Test weather").put("temperature", 25)
                        .put("temperature_unit", "\u00b0C")));
    }

    static JSONArray forecast() throws JSONException {
        return new JSONArray().put(new JSONObject().put("datetime", "2027-01-15T12:00:00+07:00")
                .put("condition", "sunny").put("temperature", 28).put("templow", 20));
    }

    static JSONObject additions(JSONObject states) throws JSONException {
        JSONObject additions = new JSONObject();
        for (java.util.Iterator<String> keys = states.keys(); keys.hasNext();) {
            String entity = keys.next();
            JSONObject state = states.getJSONObject(entity);
            additions.put(entity, new JSONObject().put("s", state.get("state"))
                    .put("a", state.get("attributes")));
        }
        return new JSONObject().put("a", additions);
    }
}
