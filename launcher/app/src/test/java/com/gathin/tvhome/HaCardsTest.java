package com.gathin.tvhome;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Looper;
import android.view.View;
import android.widget.LinearLayout;

import org.json.JSONObject;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.Shadows;
import org.robolectric.annotation.Config;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.shadows.ShadowLog;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;

import static org.junit.Assert.*;

@RunWith(RobolectricTestRunner.class)
@Config(manifest = Config.NONE, sdk = 34)
public class HaCardsTest {
    private ActivityController<Activity> activityController;
    private Activity activity;
    private LinearLayout row;
    private HaCredentials storage;
    private final HaStorageTestSupport.Keys keys = new HaStorageTestSupport.Keys();
    private final HaTestWorker worker = new HaTestWorker();
    private final List<HaCards> owners = new ArrayList<>();
    private final List<Transport> transports = new ArrayList<>();
    private final List<String> events = new ArrayList<>();
    private long elapsed;
    private boolean clockFails;
    private File mappingsFile, legacyFile;

    private final HaConnection.Clock clock = new HaConnection.Clock() {
        @Override public long wall() { return HaTestData.NOW + elapsed; }
        @Override public long elapsed() {
            if (clockFails) throw new IllegalArgumentException("Synthetic private failure");
            return elapsed;
        }
    };

    private static final class Card extends LocalSnapshotView {
        final String kind;
        JSONObject latest;
        int deliveries;
        Card(Context context, String kind) { super(context, null); this.kind = kind; }
        @Override protected String snapshotName() { return kind; }
        @Override protected void applySnapshot(JSONObject snapshot, boolean failed) {
            latest = snapshot;
            deliveries++;
        }
    }

    private final class Transport implements HaConnection.Transport {
        final HaWebSocket.Listener listener;
        final int number;
        final List<JSONObject> sent = new ArrayList<>();
        boolean closeFails;
        int closes;
        Transport(HaWebSocket.Listener listener, int number) {
            this.listener = listener;
            this.number = number;
        }
        @Override public void connect() { events.add("open:" + number); }
        @Override public void send(String text) {
            try {
                sent.add(new JSONObject(text));
            } catch (org.json.JSONException e) {
                throw new AssertionError(e);
            }
        }
        @Override public void close() throws IOException {
            events.add("close:" + number);
            closes++;
            if (closeFails) throw new IOException("Synthetic private failure");
        }
    }

    @Before public void setUp() throws Exception {
        quarantine(false);
        activityController = Robolectric.buildActivity(Activity.class).setup();
        activity = activityController.get();
        row = new LinearLayout(activity);
        activity.setContentView(row);
        activityController.visible();
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        SharedPreferences preferences = activity.getSharedPreferences(
                "ha_connection_v1", Context.MODE_PRIVATE);
        assertTrue(preferences.edit().clear().commit());
        storage = new HaCredentials(preferences, keys);
        storage.save(HaStorageTestSupport.ORIGIN, HaStorageTestSupport.TOKEN);
        mappingsFile = new File(com.gathin.tvhome.Config.dir(activity), HaCardConfig.FILE_NAME);
        legacyFile = new File(mappingsFile.getParentFile(), "vehicle.json");
        write(mappingsFile, HaTestData.mapping().toString());
    }

    @After public void tearDown() throws Exception {
        try {
            for (HaCards owner : owners) owner.close();
            worker.drain();
            activityController.pause().stop().destroy();
            assertTrue(!mappingsFile.exists() || mappingsFile.delete());
            assertTrue(!legacyFile.exists() || legacyFile.delete());
        } finally {
            quarantine(false);
        }
    }

    private static void write(File file, String text) throws IOException {
        Files.write(file.toPath(), text.getBytes(StandardCharsets.UTF_8));
    }

    private static void quarantine(boolean value) throws Exception {
        java.lang.reflect.Field field = HaCards.class.getDeclaredField("cleanupBlocked");
        field.setAccessible(true);
        field.setBoolean(null, value);
    }

    private HaCards owner() {
        HaCards owner = new HaCards(activity, storage, (origin, listener) -> {
            assertEquals(HaStorageTestSupport.ORIGIN, origin);
            Transport transport = new Transport(listener, transports.size());
            transports.add(transport);
            return transport;
        }, clock, worker);
        owners.add(owner);
        owner.foreground(true);
        return owner;
    }

    private Card attach(HaCards owner, String kind) {
        Card card = new Card(activity, kind);
        card.bindHaCards(owner);
        row.addView(card);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertTrue("Card must be attached to the activity window", card.isAttachedToWindow());
        assertTrue("Card and its ancestors must be shown", card.isShown());
        assertEquals("Card window must be visible", View.VISIBLE, card.getWindowVisibility());
        assertTrue(card.directVisible());
        return card;
    }

    private void drain() {
        worker.drain();
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        worker.drain();
        Shadows.shadowOf(Looper.getMainLooper()).idle();
    }

    private void live(Transport transport) throws Exception {
        transport.listener.message("{\"type\":\"auth_required\"}");
        drain();
        transport.listener.message("{\"type\":\"auth_ok\"}");
        drain();
        int id = transport.sent.stream()
                .filter(command -> "subscribe_entities".equals(command.optString("type")))
                .findFirst().orElseThrow().getInt("id");
        transport.listener.message(new JSONObject().put("type", "result").put("id", id)
                .put("success", true).toString());
        transport.listener.message(new JSONObject().put("type", "event").put("id", id)
                .put("event", HaTestData.additions(HaTestData.vehicleStates())).toString());
        drain();
    }

    @Test public void onlyVisibleCardKindsOwnConnectionsAndSharedViewsReuseOne() throws Exception {
        HaCards owner = owner();
        drain();
        assertTrue(transports.isEmpty());
        Card first = attach(owner, "vehicle");
        Card second = attach(owner, "vehicle");
        Card weather = attach(owner, "weather");
        Card disabled = attach(owner, "energy");
        drain();
        assertEquals(2, transports.size());
        assertEquals("disabled", disabled.latest.getString("status"));
        row.removeView(first);
        drain();
        assertEquals(0, transports.stream().mapToInt(value -> value.closes).sum());
        row.removeView(second);
        drain();
        assertEquals(1, transports.stream().mapToInt(value -> value.closes).sum());
        weather.setVisibility(View.GONE);
        drain();
        assertEquals(2, transports.stream().mapToInt(value -> value.closes).sum());
    }

    @Test public void pauseAndAllAppsVisibilityCloseAndResumeFreshConnections() throws Exception {
        HaCards owner = owner();
        Card card = attach(owner, "vehicle");
        drain();
        live(transports.get(0));
        assertEquals("live", card.latest.getString("status"));
        owner.foreground(false);
        drain();
        assertEquals(1, transports.get(0).closes);
        owner.foreground(true);
        drain();
        assertEquals(2, transports.size());
        assertEquals("unavailable", card.latest.getString("status"));
        row.setVisibility(View.GONE);
        drain();
        assertEquals(1, transports.get(1).closes);
        row.setVisibility(View.VISIBLE);
        drain();
        assertEquals(3, transports.size());
    }

    @Test public void rebindAndRecreationCloseOldOwnersBeforeOpeningNewSockets() throws Exception {
        HaCards first = owner();
        Card card = attach(first, "vehicle");
        drain();
        live(transports.get(0));
        first.close();
        HaCards second = owner();
        card.bindHaCards(second);
        drain();
        assertEquals(List.of("open:0", "close:0", "open:1"), events);
        assertEquals("unavailable", card.latest.getString("status"));
        transports.get(0).listener.message("{\"type\":\"auth_ok\"}");
        drain();
        assertEquals("unavailable", card.latest.getString("status"));
        assertEquals(2, transports.size());
    }

    @Test public void mappingChangesCloseOldSubscriptionsAndInvalidFilesNeverUseLegacyData()
            throws Exception {
        HaCards owner = owner();
        Card card = attach(owner, "vehicle");
        drain();
        live(transports.get(0));
        JSONObject mapping = HaTestData.mapping();
        mapping.getJSONObject("vehicle").put("charging_entity", "binary_sensor.replaced");
        write(mappingsFile, mapping.toString());
        elapsed = 2000;
        worker.tick();
        drain();
        assertEquals(List.of("open:0", "close:0", "open:1"), events);
        write(legacyFile,
                "{\"status\":\"live\",\"data\":{\"battery_soc\":99}}");
        write(mappingsFile, "malformed");
        elapsed = 4000;
        worker.tick();
        drain();
        assertEquals(1, transports.get(1).closes);
        assertEquals("unavailable", card.latest.getString("status"));
        assertTrue(card.latest.isNull("data"));
        assertNull(loader(card));
    }

    private static Object loader(Card card) throws Exception {
        java.lang.reflect.Field field = LocalSnapshotView.class.getDeclaredField("loader");
        field.setAccessible(true);
        return field.get(card);
    }

    @Test public void queuedDeliveryCannotReachADetachedOrPausedView() throws Exception {
        HaCards owner = owner();
        Card card = attach(owner, "vehicle");
        worker.drain();
        row.removeView(card);
        int deliveries = card.deliveries;
        drain();
        assertEquals(deliveries, card.deliveries);
        row.addView(card);
        drain();
        owner.update();
        worker.drain();
        owner.foreground(false);
        deliveries = card.deliveries;
        drain();
        assertEquals(deliveries, card.deliveries);
    }

    @Test public void sourceAndCredentialRevisionsAreDetectedWithoutChangingMappings()
            throws Exception {
        HaCards direct = owner();
        assertFalse(direct.configurationChanged());
        storage.save(HaStorageTestSupport.ORIGIN, "synthetic-replacement-token");
        assertTrue(direct.configurationChanged());
        HaCards replacement = owner();
        assertFalse(replacement.configurationChanged());
        storage.useBackend();
        assertTrue(replacement.configurationChanged());
        HaCards backend = owner();
        assertFalse(backend.configurationChanged());
        storage.save(HaStorageTestSupport.ORIGIN, HaStorageTestSupport.TOKEN);
        assertTrue(backend.configurationChanged());
    }

    @Test public void cleanupFailureQuarantinesAllKindsAndReplacementOwners() throws Exception {
        HaCards first = owner();
        Card card = attach(first, "vehicle");
        drain();
        transports.get(0).closeFails = true;
        first.foreground(false);
        drain();
        first.close();
        HaCards second = owner();
        card.bindHaCards(second);
        drain();
        assertEquals(1, transports.size());
        assertEquals("transport_cleanup_failed", card.latest.getString("message"));
        assertTrue(card.latest.isNull("data"));
    }

    @Test public void unexpectedWorkerFailureIsSanitizedFailsClosedAndDoesNotKillTheTicker()
            throws Exception {
        HaCards owner = owner();
        Card card = attach(owner, "vehicle");
        drain();
        clockFails = true;
        worker.tick();
        drain();
        assertEquals(1, transports.get(0).closes);
        assertEquals("coordinator_failed", card.latest.getString("message"));
        assertTrue(ShadowLog.getLogsForTag("HaCards").stream()
                .anyMatch(log -> log.msg.startsWith("coordinator_failed")));
        assertTrue(ShadowLog.getLogsForTag("HaCards").stream()
                .noneMatch(log -> log.msg.contains("private")));
        clockFails = false;
        worker.tick();
        drain();
        assertEquals("transport_cleanup_failed", card.latest.getString("message"));
        assertEquals(1, transports.size());
    }
}
