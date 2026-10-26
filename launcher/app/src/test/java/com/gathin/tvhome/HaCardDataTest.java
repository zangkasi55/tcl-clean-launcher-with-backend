package com.gathin.tvhome;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import org.junit.Test;

import static org.junit.Assert.*;

public class HaCardDataTest {
    private static JSONObject energyStates() throws JSONException {
        return new JSONObject()
                .put("sensor.solar", HaTestData.state("sensor.solar", "2.5", "kW"))
                .put("sensor.home", HaTestData.state("sensor.home", "800", "W"))
                .put("sensor.grid", HaTestData.state("sensor.grid", "-1.2", "kW"))
                .put("sensor.battery", HaTestData.state("sensor.battery", "-500", "W"))
                .put("sensor.test_soc", HaTestData.state("sensor.test_soc", "63.5", "%"));
    }

    @Test public void basicPowerKeepsExistingSignsAndConvertsKilowatts() throws Exception {
        JSONObject snapshot = HaCardData.create("energy", HaTestData.basicEnergy(),
                energyStates(), null, HaTestData.NOW);
        JSONObject metrics = snapshot.getJSONObject("metrics");
        assertEquals(2500, metrics.getDouble("solar_w"), 0);
        assertEquals(800, metrics.getDouble("home_w"), 0);
        assertEquals(-1200, metrics.getDouble("grid_w"), 0);
        assertEquals(-500, metrics.getDouble("battery_w"), 0);
        assertEquals(63.5, metrics.getDouble("battery_soc"), 0);
        assertEquals("home_assistant", snapshot.getString("source"));
        assertEquals("direct", snapshot.getString("transport"));
        assertEquals("live", snapshot.getString("status"));
        assertEquals(HaTestData.NOW, snapshot.getLong("received_at_ms"));
        assertEquals(HaTestData.NOW, snapshot.getLong("sampled_at_ms"));
        assertEquals(15000, snapshot.getLong("stale_after_ms"));
        assertEquals(30000, snapshot.getLong("delivery_stale_after_ms"));
        assertFalse(snapshot.has("topology"));
    }

    @Test public void directionalPairsAndExplicitSignFlagsAreIndependent() throws Exception {
        JSONObject config = HaTestData.basicEnergy();
        config.remove("grid_entity");
        config.remove("battery_entity");
        config.put("grid_import_entity", "sensor.import").put("grid_export_entity", "sensor.export")
                .put("battery_discharge_entity", "sensor.discharge")
                .put("battery_charge_entity", "sensor.charge");
        JSONObject states = energyStates()
                .put("sensor.import", HaTestData.state("sensor.import", "0.2", "kW"))
                .put("sensor.export", HaTestData.state("sensor.export", "1000", "W"))
                .put("sensor.discharge", HaTestData.state("sensor.discharge", "10", "W"))
                .put("sensor.charge", HaTestData.state("sensor.charge", "0.5", "kW"));
        JSONObject metrics = HaCardData.create("energy", config, states, null, HaTestData.NOW)
                .getJSONObject("metrics");
        assertEquals(-800, metrics.getDouble("grid_w"), 0);
        assertEquals(-490, metrics.getDouble("battery_w"), 0);
        config.put("grid_import_positive", false).put("battery_discharge_positive", false);
        metrics = HaCardData.create("energy", config, states, null, HaTestData.NOW)
                .getJSONObject("metrics");
        assertEquals(800, metrics.getDouble("grid_w"), 0);
        assertEquals(490, metrics.getDouble("battery_w"), 0);
        states.getJSONObject("sensor.import").put("state", "-1");
        assertThrows(JSONException.class,
                () -> HaCardData.create("energy", config, states, null, HaTestData.NOW));
    }

    @Test public void invalidEnergyNeverBecomesZeroOrChangesEntityIdentity() throws Exception {
        for (String raw : new String[] {"unknown", "unavailable", "NaN", "Infinity", "1e999", ""}) {
            JSONObject states = energyStates();
            states.getJSONObject("sensor.solar").put("state", raw);
            assertThrows(raw, JSONException.class, () -> HaCardData.create(
                    "energy", HaTestData.basicEnergy(), states, null, HaTestData.NOW));
        }
        for (String unit : new String[] {"kWh", "Wh", "%", ""}) {
            JSONObject states = energyStates();
            states.getJSONObject("sensor.solar").getJSONObject("attributes")
                    .put("unit_of_measurement", unit);
            assertThrows(unit, JSONException.class, () -> HaCardData.create(
                    "energy", HaTestData.basicEnergy(), states, null, HaTestData.NOW));
        }
        JSONObject states = energyStates();
        states.getJSONObject("sensor.solar").put("entity_id", "sensor.unrelated");
        assertThrows(JSONException.class, () -> HaCardData.create(
                "energy", HaTestData.basicEnergy(), states, null, HaTestData.NOW));
    }

    @Test public void vehicleKeepsDecimalPercentageAndExactChargingState() throws Exception {
        for (String charging : new String[] {"on", "off"}) {
            JSONObject states = HaTestData.vehicleStates();
            states.getJSONObject("sensor.test_soc").put("state", " 47.25 ");
            states.getJSONObject("binary_sensor.test_charging").put("state", charging);
            JSONObject snapshot = HaCardData.create(
                    "vehicle", HaTestData.vehicle(), states, null, HaTestData.NOW);
            JSONObject data = snapshot.getJSONObject("data");
            assertEquals(47.25, data.getDouble("battery_soc"), 0);
            assertEquals("on".equals(charging), data.getBoolean("charging"));
            assertEquals("sensor.test_soc", data.getString("battery_soc_entity"));
            assertEquals("binary_sensor.test_charging", data.getString("charging_entity"));
            assertEquals(900000, snapshot.getLong("stale_after_ms"));
        }
    }

    @Test public void missingInvalidVehicleReadingsNeverMeanNotCharging() throws Exception {
        for (String raw : new String[] {"1e2", "NaN", "-1", "100.01", "unknown", ""}) {
            JSONObject states = HaTestData.vehicleStates();
            states.getJSONObject("sensor.test_soc").put("state", raw);
            assertThrows(raw, JSONException.class, () -> HaCardData.create(
                    "vehicle", HaTestData.vehicle(), states, null, HaTestData.NOW));
        }
        for (Object raw : new Object[] {"unknown", "unavailable", "OFF", "false", false, 0}) {
            JSONObject states = HaTestData.vehicleStates();
            states.getJSONObject("binary_sensor.test_charging").put("state", raw);
            assertThrows(JSONException.class, () -> HaCardData.create(
                    "vehicle", HaTestData.vehicle(), states, null, HaTestData.NOW));
        }
    }

    @Test public void weatherPreservesUnitPrecisionMissingLowAndThreeDayLimit() throws Exception {
        JSONObject states = HaTestData.weatherStates();
        states.getJSONObject("weather.test").getJSONObject("attributes")
                .put("temperature", 72.25).put("temperature_unit", "\u00b0F");
        JSONArray forecast = new JSONArray();
        for (int i = 15; i < 19; i++) {
            forecast.put(new JSONObject().put("datetime", "2027-01-" + i + "T12:00:00+07:00")
                    .put("condition", "sunny").put("temperature", 80.5));
        }
        JSONObject snapshot = HaCardData.create(
                "weather", HaTestData.weather(), states, forecast, HaTestData.NOW);
        JSONObject data = snapshot.getJSONObject("data");
        assertEquals("weather.test", data.getString("entity_id"));
        assertEquals("Test weather", data.getString("name"));
        assertEquals("\u00b0F", data.getString("temperature_unit"));
        assertEquals(72.25, data.getDouble("temperature"), 0);
        assertEquals(3, data.getJSONArray("forecast").length());
        assertEquals(80.5, data.getJSONArray("forecast").getJSONObject(0)
                .getDouble("temperature"), 0);
        assertTrue(data.getJSONArray("forecast").getJSONObject(0).isNull("templow"));
        assertEquals(900000, snapshot.getLong("stale_after_ms"));
    }

    @Test public void invalidForecastDatesTypesAndTemperaturesFailExplicitly() throws Exception {
        for (String date : new String[] {"2027-01-15T12:00:00", "not-a-date"}) {
            JSONArray forecast = HaTestData.forecast();
            forecast.getJSONObject(0).put("datetime", date);
            assertThrows(JSONException.class, () -> HaCardData.create(
                    "weather", HaTestData.weather(), HaTestData.weatherStates(),
                    forecast, HaTestData.NOW));
        }
        JSONArray repeated = HaTestData.forecast();
        repeated.put(new JSONObject(repeated.getJSONObject(0).toString()));
        assertThrows(JSONException.class, () -> HaCardData.create(
                "weather", HaTestData.weather(), HaTestData.weatherStates(),
                repeated, HaTestData.NOW));
        for (Object temperature : new Object[] {"25", true, -151, 301, JSONObject.NULL}) {
            JSONArray forecast = HaTestData.forecast();
            forecast.getJSONObject(0).put("temperature", temperature);
            assertThrows(JSONException.class, () -> HaCardData.create(
                    "weather", HaTestData.weather(), HaTestData.weatherStates(),
                    forecast, HaTestData.NOW));
        }
    }

    @Test public void staleIsAnIsolatedCopyAndEmptyHasNoObservationOrValues() throws Exception {
        JSONObject live = HaCardData.create("vehicle", HaTestData.vehicle(),
                HaTestData.vehicleStates(), null, HaTestData.NOW);
        JSONObject stale = HaCardData.stale(live);
        assertEquals("live", live.getString("status"));
        assertEquals("stale", stale.getString("status"));
        assertEquals(live.getLong("received_at_ms"), stale.getLong("received_at_ms"));
        stale.getJSONObject("data").put("battery_soc", 1);
        assertEquals(47, live.getJSONObject("data").getInt("battery_soc"));
        for (String kind : HaCardConfig.KINDS) {
            JSONObject empty = HaCardData.empty(kind, "unavailable");
            assertTrue(empty.isNull("received_at_ms"));
            assertTrue(empty.isNull("data"));
            assertFalse(empty.has("metrics"));
            assertFalse(empty.has("topology"));
        }
    }
}
