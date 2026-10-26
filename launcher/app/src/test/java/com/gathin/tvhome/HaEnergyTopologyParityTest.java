package com.gathin.tvhome;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;

import static org.junit.Assert.*;

public class HaEnergyTopologyParityTest {
    @Test public void completeTopologyMatchesTheSharedBackendContract() throws Exception {
        JSONObject fixtures;
        try (InputStream input = getClass().getResourceAsStream("/energy_topology.json")) {
            assertNotNull("Shared topology fixture must be on the test classpath", input);
            fixtures = new JSONObject(new String(input.readAllBytes(), StandardCharsets.UTF_8));
        }
        JSONArray cases = fixtures.getJSONArray("cases");
        assertEquals(18, cases.length());
        for (int i = 0; i < cases.length(); i++) {
            JSONObject item = cases.getJSONObject(i);
            JSONObject bindings = merge(fixtures.getJSONObject("bindings"), item.optJSONObject("bindings"));
            JSONObject readings = merge(fixtures.getJSONObject("readings"), item.optJSONObject("readings"));
            JSONArray missing = item.optJSONArray("missing");
            if (missing != null) for (int j = 0; j < missing.length(); j++) {
                readings.remove(missing.getString(j));
            }
            JSONObject states = new JSONObject();
            for (Iterator<String> keys = readings.keys(); keys.hasNext();) {
                String entity = keys.next();
                JSONArray reading = readings.getJSONArray(entity);
                states.put(entity, HaTestData.state(entity, reading.getString(0), reading.getString(1))
                        .put("attributes", new JSONObject().put("unit_of_measurement", reading.get(1))
                                .put("device_class", reading.get(2))));
            }
            JSONObject expected = item.getJSONObject("expected");
            JSONObject actual = HaEnergyTopology.fromStates(bindings, states);
            assertJson(item.getString("name"), expected, actual);
        }
    }

    private static void assertJson(String path, Object expected, Object actual) throws Exception {
        if (expected instanceof JSONObject) {
            assertTrue(path, actual instanceof JSONObject);
            JSONObject left = (JSONObject) expected, right = (JSONObject) actual;
            assertEquals(path + " field count", left.length(), right.length());
            for (Iterator<String> keys = left.keys(); keys.hasNext();) {
                String key = keys.next();
                assertTrue(path + "." + key + " is missing", right.has(key));
                assertJson(path + "." + key, left.get(key), right.get(key));
            }
        } else if (expected instanceof JSONArray) {
            assertTrue(path, actual instanceof JSONArray);
            JSONArray left = (JSONArray) expected, right = (JSONArray) actual;
            assertEquals(path + " length", left.length(), right.length());
            for (int i = 0; i < left.length(); i++) {
                assertJson(path + "[" + i + "]", left.get(i), right.get(i));
            }
        } else if (expected instanceof Number) {
            assertTrue(path, actual instanceof Number);
            assertEquals(path, ((Number) expected).doubleValue(), ((Number) actual).doubleValue(), 0);
        } else {
            assertEquals(path, expected, actual);
        }
    }

    private static JSONObject merge(JSONObject base, JSONObject overrides) throws Exception {
        JSONObject result = new JSONObject(base.toString());
        if (overrides != null) for (Iterator<String> keys = overrides.keys(); keys.hasNext();) {
            String key = keys.next();
            result.put(key, overrides.get(key));
        }
        return result;
    }
}
