package com.gathin.tvhome;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import org.junit.Test;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import static org.junit.Assert.*;

public class HaSessionTest {
    private static final class Capture implements HaSession.Listener {
        final List<JSONObject> sent = new ArrayList<>(), snapshots = new ArrayList<>();
        final List<String> errors = new ArrayList<>();
        HaSession session;
        Runnable authenticated = () -> {}, snapshot = () -> {};
        boolean rejectSend;
        boolean retry;

        @Override public void send(JSONObject message) {
            if (rejectSend) throw new IllegalStateException("Transport closed");
            sent.add(message);
        }
        @Override public void snapshot(JSONObject value) {
            snapshots.add(value);
            snapshot.run();
        }
        @Override public void authenticated() { authenticated.run(); }
        @Override public void failure(String code, boolean retryable) {
            errors.add(code);
            retry = retryable;
        }
        void start(String kind, JSONObject config) throws JSONException {
            session = new HaSession(kind, config, "synthetic-test-token", 0, this);
            frame(new JSONObject().put("type", "auth_required"), 0);
            frame(new JSONObject().put("type", "auth_ok"), 1);
        }
        void frame(JSONObject value, long elapsed) {
            session.message(value.toString(), HaTestData.NOW + elapsed, elapsed);
        }
        int id(String type) throws JSONException {
            for (int i = sent.size() - 1; i >= 0; i--) {
                if (type.equals(sent.get(i).getString("type"))) return sent.get(i).getInt("id");
            }
            throw new AssertionError("Missing command " + type);
        }
        void ack(int id, long elapsed) throws JSONException {
            frame(new JSONObject().put("type", "result").put("id", id).put("success", true), elapsed);
        }
        void event(int id, JSONObject event, long elapsed) throws JSONException {
            frame(new JSONObject().put("type", "event").put("id", id).put("event", event), elapsed);
        }
        JSONObject latest() { return snapshots.get(snapshots.size() - 1); }
        void tick(long elapsed) { session.tick(HaTestData.NOW + elapsed, elapsed); }
    }

    @Test public void acknowledgedSubscriptionTimesOutWithNoOutstandingCommands() throws Exception {
        Capture c = new Capture();
        c.start("vehicle", HaTestData.vehicle());
        c.ack(c.id("subscribe_entities"), 2);
        c.tick(10001);
        c.frame(new JSONObject().put("type", "pong").put("id", c.id("ping")), 10001);
        c.tick(10002);
        c.tick(30000);
        assertEquals(Arrays.asList("initial_data_timeout"), c.errors);
        assertTrue(c.retry);
    }

    @Test public void weatherEntityAndForecastInitialDeadlinesAreIndependent() throws Exception {
        for (boolean forecastFirst : new boolean[] {false, true}) {
            Capture c = new Capture();
            c.start("weather", HaTestData.weather());
            int entities = c.id("subscribe_entities"), forecast = c.id("weather/subscribe_forecast");
            c.ack(entities, 2);
            c.ack(forecast, 2);
            c.event(forecastFirst ? forecast : entities, forecastFirst
                    ? new JSONObject().put("type", "daily").put("forecast", HaTestData.forecast())
                    : HaTestData.additions(HaTestData.weatherStates()), 3);
            c.tick(10001);
            c.frame(new JSONObject().put("type", "pong").put("id", c.id("ping")), 10001);
            c.tick(10002);
            assertEquals(Arrays.asList("initial_data_timeout"), c.errors);
        }
    }

    @Test public void initialEventBeforeAcknowledgementDoesNotStartAnotherDeadline() throws Exception {
        Capture c = new Capture();
        c.start("vehicle", HaTestData.vehicle());
        int entities = c.id("subscribe_entities");
        c.event(entities, HaTestData.additions(HaTestData.vehicleStates()), 2);
        c.ack(entities, 3);
        c.tick(10004);
        assertTrue(c.errors.isEmpty());
        assertEquals("live", c.latest().getString("status"));
    }

    @Test public void renewalIgnoresOldEventsAndAppliesCompactChangesAndRemovals() throws Exception {
        Capture c = new Capture();
        c.start("vehicle", HaTestData.vehicle());
        int old = c.id("subscribe_entities");
        c.ack(old, 2);
        c.event(old, HaTestData.additions(HaTestData.vehicleStates()), 3);
        c.event(old, new JSONObject().put("c", new JSONObject()
                .put("binary_sensor.test_charging", new JSONObject()
                        .put("+", new JSONObject().put("s", "off")))), 4);
        assertFalse(c.latest().getJSONObject("data").getBoolean("charging"));
        c.tick(60001);
        int renewed = c.id("subscribe_entities");
        assertNotEquals(old, renewed);
        int count = c.snapshots.size();
        c.event(old, HaTestData.additions(HaTestData.vehicleStates()), 60002);
        assertEquals(count, c.snapshots.size());
        c.ack(renewed, 60002);
        c.event(renewed, HaTestData.additions(HaTestData.vehicleStates()), 60003);
        c.event(renewed, new JSONObject().put("c", new JSONObject()
                .put("sensor.test_soc", new JSONObject().put("-", new JSONObject()
                        .put("a", new JSONArray().put("unit_of_measurement"))))), 60004);
        assertEquals("unavailable", c.latest().getString("status"));
        c.event(renewed, new JSONObject().put("r", new JSONArray().put("sensor.test_soc")), 60005);
        assertTrue(c.latest().isNull("data"));
    }

    @Test public void heartbeatNeverRefreshesObservationTime() throws Exception {
        Capture c = new Capture();
        c.start("vehicle", HaTestData.vehicle());
        int entities = c.id("subscribe_entities");
        c.ack(entities, 2);
        c.event(entities, HaTestData.additions(HaTestData.vehicleStates()), 3);
        long observed = c.latest().getLong("received_at_ms");
        c.tick(10001);
        c.frame(new JSONObject().put("type", "pong").put("id", c.id("ping")), 10002);
        assertEquals(observed, c.latest().getLong("received_at_ms"));
    }

    @Test public void closingDuringCallbacksPreventsFurtherCommands() throws Exception {
        Capture auth = new Capture();
        auth.authenticated = () -> auth.session.stop();
        auth.start("vehicle", HaTestData.vehicle());
        assertEquals(1, auth.sent.size());

        Capture snapshot = new Capture();
        snapshot.start("vehicle", HaTestData.vehicle());
        int entities = snapshot.id("subscribe_entities");
        snapshot.ack(entities, 2);
        snapshot.snapshot = () -> snapshot.session.stop();
        snapshot.event(entities, HaTestData.additions(HaTestData.vehicleStates()), 3);
        snapshot.tick(1000000);
        assertEquals(2, snapshot.sent.size());
        assertTrue(snapshot.errors.isEmpty());
    }

    @Test public void malformedAndOversizedMessagesClearValuesAndFailOnlyOnce() throws Exception {
        for (String bad : new String[] {null, "{invalid", "x".repeat(1048577)}) {
            Capture c = new Capture();
            c.start("vehicle", HaTestData.vehicle());
            int entities = c.id("subscribe_entities");
            c.ack(entities, 2);
            c.event(entities, HaTestData.additions(HaTestData.vehicleStates()), 3);
            c.session.message(bad, HaTestData.NOW + 4, 4);
            c.session.disconnected();
            assertEquals(1, c.errors.size());
            assertEquals("unavailable", c.latest().getString("status"));
            assertTrue(c.latest().isNull("data"));
        }
    }

    @Test public void sendFailureIsSanitizedAndStopsHandshake() throws Exception {
        Capture c = new Capture();
        c.rejectSend = true;
        c.start("vehicle", HaTestData.vehicle());
        assertEquals(Arrays.asList("send_failed"), c.errors);
        assertTrue(c.sent.isEmpty());
    }

    @Test public void onlyReadOnlyCommandsCanBeEmitted() throws Exception {
        Capture c = new Capture();
        c.start("weather", HaTestData.weather());
        int entityId = c.id("subscribe_entities"), forecastId = c.id("weather/subscribe_forecast");
        c.ack(entityId, 2);
        c.ack(forecastId, 2);
        c.event(entityId, HaTestData.additions(HaTestData.weatherStates()), 3);
        c.event(forecastId, new JSONObject().put("type", "daily")
                .put("forecast", HaTestData.forecast()), 4);
        c.tick(60001);
        for (JSONObject message : c.sent) {
            assertTrue(Arrays.asList("auth", "subscribe_entities", "weather/subscribe_forecast",
                    "unsubscribe_events", "ping").contains(message.getString("type")));
        }
        assertEquals("direct", c.latest().getString("transport"));
        assertEquals("live", c.latest().getString("status"));
    }
}
