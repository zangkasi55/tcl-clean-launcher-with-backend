package com.gathin.tvhome;

import org.json.JSONException;
import org.json.JSONObject;
import org.junit.Test;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

import static org.junit.Assert.*;

public class HaConnectionTest {
    private static final class Clock implements HaConnection.Clock {
        long time;
        @Override public long wall() { return HaTestData.NOW + time; }
        @Override public long elapsed() { return time; }
    }

    private static final class Transport implements HaConnection.Transport {
        final HaWebSocket.Listener listener;
        final List<JSONObject> sent = new ArrayList<>();
        int connects, closes;
        boolean closeFails, sendFails;

        Transport(HaWebSocket.Listener listener) { this.listener = listener; }
        @Override public void connect() { connects++; }
        @Override public void send(String message) {
            if (sendFails) throw new IllegalStateException("Synthetic private transport details");
            try {
                sent.add(new JSONObject(message));
            } catch (JSONException e) {
                throw new AssertionError("Client sent invalid JSON", e);
            }
        }
        @Override public void close() throws IOException {
            closes++;
            if (closeFails) throw new IOException("Synthetic private transport details");
        }
        void frame(JSONObject message) { listener.message(message.toString()); }
        int id(String type) throws JSONException {
            for (int i = sent.size() - 1; i >= 0; i--) {
                if (type.equals(sent.get(i).optString("type"))) return sent.get(i).getInt("id");
            }
            throw new AssertionError("Missing command " + type);
        }
    }

    private static final class Owner implements HaConnection.Listener {
        final Clock clock = new Clock();
        final List<Transport> transports = new ArrayList<>();
        final List<JSONObject> snapshots = new ArrayList<>();
        final List<String> errors = new ArrayList<>();
        final List<Runnable> queued = new ArrayList<>();
        final HaConnection connection;

        Owner(boolean queue) throws Exception {
            connection = new HaConnection("vehicle", HaTestData.vehicle(),
                    "https://ha.example.test", "synthetic-test-token", (origin, listener) -> {
                        Transport transport = new Transport(listener);
                        transports.add(transport);
                        return transport;
                    }, clock, task -> { if (queue) queued.add(task); else task.run(); }, this);
        }
        Owner() throws Exception { this(false); }
        @Override public void snapshot(JSONObject snapshot) { snapshots.add(snapshot); }
        @Override public void error(String code) { errors.add(code); }
        Transport current() { return transports.get(transports.size() - 1); }
        JSONObject latest() { return snapshots.get(snapshots.size() - 1); }
        void tick(long time) { clock.time = time; connection.tick(); }
        void drain() {
            List<Runnable> pending = new ArrayList<>(queued);
            queued.clear();
            for (Runnable task : pending) task.run();
        }
        void live() throws Exception {
            current().frame(new JSONObject().put("type", "auth_required"));
            drain();
            current().frame(new JSONObject().put("type", "auth_ok"));
            drain();
            int id = current().id("subscribe_entities");
            current().frame(new JSONObject().put("type", "result").put("id", id)
                    .put("success", true));
            current().frame(new JSONObject().put("type", "event").put("id", id)
                    .put("event", HaTestData.additions(HaTestData.vehicleStates())));
            drain();
            assertEquals("live", latest().getString("status"));
        }
    }

    @Test public void retriesBackOffFromOneSecondToOneMinuteWithoutBusyLoop() throws Exception {
        Owner owner = new Owner();
        owner.tick(0);
        long time = 0;
        for (long delay : new long[] {1000, 2000, 4000, 8000, 16000, 32000, 60000, 60000}) {
            Transport failed = owner.current();
            failed.listener.disconnected();
            assertEquals(1, failed.closes);
            int count = owner.transports.size();
            time += delay;
            owner.tick(time - 1);
            assertEquals(count, owner.transports.size());
            owner.tick(time);
            assertEquals(count + 1, owner.transports.size());
        }
        owner.connection.stop();
    }

    @Test public void onlyLiveDataResetsBackoffAndStaleRetainsItsOriginalObservation() throws Exception {
        Owner owner = new Owner();
        owner.tick(0);
        owner.current().listener.disconnected();
        owner.tick(1000);
        owner.current().listener.disconnected();
        owner.tick(3000);
        owner.live();
        long observation = owner.latest().getLong("received_at_ms");
        owner.current().listener.disconnected();
        assertEquals("stale", owner.latest().getString("status"));
        assertEquals(observation, owner.latest().getLong("received_at_ms"));
        assertEquals(47, owner.latest().getJSONObject("data").getInt("battery_soc"));
        owner.tick(3999);
        assertEquals(3, owner.transports.size());
        owner.tick(4000);
        assertEquals(4, owner.transports.size());
        owner.connection.stop();
    }

    @Test public void authenticationAloneDoesNotResetBackoff() throws Exception {
        Owner owner = new Owner();
        owner.tick(0);
        owner.current().listener.disconnected();
        owner.tick(1000);
        owner.current().frame(new JSONObject().put("type", "auth_required"));
        owner.current().frame(new JSONObject().put("type", "auth_ok"));
        owner.current().listener.disconnected();
        owner.tick(2999);
        assertEquals(2, owner.transports.size());
        owner.tick(3000);
        assertEquals(3, owner.transports.size());
        owner.connection.stop();
    }

    @Test public void rejectedAuthorizationClearsDataAndNeverRetries() throws Exception {
        Owner owner = new Owner();
        owner.tick(0);
        owner.live();
        owner.current().frame(new JSONObject().put("type", "auth_invalid")
                .put("message", "Synthetic private server message"));
        assertEquals("unavailable", owner.latest().getString("status"));
        assertTrue(owner.latest().isNull("data"));
        assertTrue(owner.latest().isNull("received_at_ms"));
        owner.tick(1000000);
        assertEquals(1, owner.transports.size());
        assertEquals("authorization_rejected", owner.errors.get(owner.errors.size() - 1));
        assertFalse(owner.latest().toString().contains("private"));
    }

    @Test public void oldAndQueuedCallbacksCannotRepopulateStoppedOrReplacedOwners() throws Exception {
        Owner owner = new Owner(true);
        owner.tick(0);
        owner.live();
        Transport first = owner.current();
        first.listener.disconnected();
        owner.drain();
        owner.tick(1000);
        int count = owner.snapshots.size();
        first.frame(new JSONObject().put("type", "auth_invalid"));
        first.listener.disconnected();
        owner.drain();
        assertEquals(count, owner.snapshots.size());
        owner.current().frame(new JSONObject().put("type", "auth_required"));
        owner.connection.stop();
        owner.connection.stop();
        owner.drain();
        owner.tick(1000000);
        assertEquals(2, owner.transports.size());
        assertEquals(1, first.closes);
        assertEquals(1, owner.current().closes);
        assertTrue(owner.current().sent.isEmpty());
        assertEquals(count, owner.snapshots.size());
    }

    @Test public void cleanupFailureClearsDataAndQuarantinesReplacement() throws Exception {
        Owner owner = new Owner();
        owner.tick(0);
        owner.live();
        owner.current().closeFails = true;
        owner.current().listener.disconnected();
        assertTrue(owner.errors.contains("transport_cleanup_failed"));
        assertEquals("unavailable", owner.latest().getString("status"));
        assertEquals("transport_cleanup_failed", owner.latest().getString("message"));
        assertTrue(owner.latest().isNull("data"));
        owner.tick(1000000);
        assertEquals(1, owner.transports.size());
        owner.connection.stop();
        assertEquals(1, owner.current().closes);
    }

    @Test public void invalidFramesDoNotRetainLiveValuesDuringRetry() throws Exception {
        Owner owner = new Owner();
        owner.tick(0);
        owner.live();
        owner.current().listener.message("{invalid");
        assertEquals("unavailable", owner.latest().getString("status"));
        assertTrue(owner.latest().isNull("data"));
        assertEquals(1, owner.current().closes);
        owner.tick(1000);
        assertEquals(2, owner.transports.size());
        owner.connection.stop();
    }

    @Test public void connectionTimeoutCleansExactlyOnceAndSchedulesRetry() throws Exception {
        Owner owner = new Owner();
        owner.tick(0);
        owner.tick(10000);
        assertEquals("connection_timeout", owner.latest().getString("message"));
        assertEquals(1, owner.current().closes);
        owner.tick(10999);
        assertEquals(1, owner.transports.size());
        owner.tick(11000);
        assertEquals(2, owner.transports.size());
        owner.connection.stop();
    }

    @Test public void sendFailureDoesNotEscapeOrExposeTransportDetails() throws Exception {
        Owner owner = new Owner();
        owner.tick(0);
        owner.current().sendFails = true;
        owner.current().frame(new JSONObject().put("type", "auth_required"));
        assertEquals("send_failed", owner.latest().getString("message"));
        assertFalse(owner.latest().toString().contains("private"));
        assertEquals(1, owner.current().closes);
        owner.connection.stop();
    }

    @Test public void factoryAndPlatformFailuresAreExplicitAndCleanupUncertaintyDoesNotRetry()
            throws Exception {
        for (String failure : new String[] {"io", "permission", "cleanup"}) {
            Clock clock = new Clock();
            List<String> errors = new ArrayList<>();
            int[] creates = {0};
            HaConnection connection = new HaConnection("vehicle", HaTestData.vehicle(),
                    "https://ha.example.test", "synthetic-test-token", (origin, listener) -> {
                        creates[0]++;
                        if ("permission".equals(failure)) throw new SecurityException("private");
                        if ("cleanup".equals(failure)) {
                            throw new HaWebSocket.CleanupException(new IOException("private"));
                        }
                        throw new IOException("private");
                    }, clock, Runnable::run, new HaConnection.Listener() {
                        @Override public void snapshot(JSONObject snapshot) {
                            assertEquals("unavailable", snapshot.optString("status"));
                            assertFalse(snapshot.toString().contains("private"));
                        }
                        @Override public void error(String code) { errors.add(code); }
                    });
            connection.tick();
            clock.time = 1000;
            connection.tick();
            assertEquals("io".equals(failure) ? 2 : 1, creates[0]);
            assertTrue(errors.contains("cleanup".equals(failure) ? "transport_cleanup_failed"
                    : "permission".equals(failure) ? "connection_not_permitted" : "connection_failed"));
            connection.stop();
        }
    }
}
