package com.gathin.tvhome;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.IOException;
import java.security.GeneralSecurityException;
import java.security.ProviderException;
import java.util.HashMap;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

/** Main-thread view ownership; credentials, protocol and sockets stay on one serial worker. */
final class HaCards {
    private static volatile boolean cleanupBlocked;
    // Recreated activities must finish closing old sockets before new owners start.
    private static final ScheduledExecutorService SHARED_WORKER =
            Executors.newSingleThreadScheduledExecutor();
    private final Context context;
    private final HaCredentials storage;
    private final boolean direct;
    private final String credentialRevision;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Map<LocalSnapshotView, String> views = new IdentityHashMap<>();
    private final ScheduledExecutorService worker;
    private final ScheduledFuture<?> ticker;
    private final HaConnection.Factory transportFactory;
    private final HaConnection.Clock clock;
    private final Map<String, HaConnection> connections = new HashMap<>();
    private final Map<String, JSONObject> snapshots = new HashMap<>();
    private Set<String> wanted = new HashSet<>();
    private HaCredentials.Connection credentials;
    private HaCardConfig mappings;
    private String mappingText = "", errorCode = "";
    private long mappingAt, viewEpoch, deliveryEpoch;
    private boolean mappingChecked;
    private boolean foreground;
    private volatile boolean closed;
    HaCards(Context context) {
        this(context, new HaCredentials(context), HaWebSocket::new, new HaConnection.Clock() {
            @Override public long wall() { return System.currentTimeMillis(); }
            @Override public long elapsed() { return SystemClock.elapsedRealtime(); }
        }, SHARED_WORKER);
    }

    HaCards(Context context, HaCredentials storage, HaConnection.Factory transportFactory,
            HaConnection.Clock clock, ScheduledExecutorService worker) {
        this.context = context.getApplicationContext();
        this.storage = storage;
        this.transportFactory = transportFactory;
        this.clock = clock;
        this.worker = worker;
        direct = storage.direct();
        credentialRevision = storage.revision();
        if (direct) {
            dispatch(() -> {
                try {
                    credentials = storage.load();
                } catch (GeneralSecurityException | IOException | JSONException
                         | IllegalArgumentException | ProviderException | SecurityException e) {
                    error("credential_storage_unavailable");
                }
            });
            ticker = worker.scheduleWithFixedDelay(() -> runWorker(this::tick),
                    0, 1, TimeUnit.SECONDS);
        } else ticker = null;
    }

    boolean configurationChanged() {
        return direct != storage.direct() || !credentialRevision.equals(storage.revision());
    }

    void attach(LocalSnapshotView view) {
        if (!direct || closed) return;
        views.put(view, view.snapshotName());
        update();
    }

    void detach(LocalSnapshotView view) {
        views.remove(view);
        update();
    }

    void foreground(boolean value) {
        foreground = value;
        update();
    }

    void update() {
        if (!direct || closed) return;
        final long epoch = ++viewEpoch;
        Set<String> visible = new HashSet<>();
        if (foreground) {
            for (Map.Entry<LocalSnapshotView, String> entry : views.entrySet()) {
                if (entry.getKey().directVisible()) visible.add(entry.getValue());
            }
        }
        dispatch(() -> {
            deliveryEpoch = epoch;
            wanted = visible;
            tick();
            for (String kind : wanted) {
                JSONObject snapshot = snapshots.get(kind);
                if (snapshot != null) deliver(kind, snapshot);
            }
        });
    }

    void close() {
        if (closed) return;
        closed = true;
        views.clear();
        if (ticker != null) ticker.cancel(false);
        if (direct) worker.execute(() -> runWorker(this::stopAll));
    }

    private void dispatch(Runnable action) {
        if (closed) return;
        try {
            worker.execute(() -> runWorker(action));
        } catch (RejectedExecutionException e) {
            if (!closed) {
                cleanupBlocked = true;
                Log.e("HaCards", "worker_rejected");
            }
        }
    }

    private void runWorker(Runnable action) {
        try {
            action.run();
        } catch (RuntimeException e) {
            cleanupBlocked = true;
            Log.e("HaCards", "coordinator_failed (" + e.getClass().getSimpleName() + ")");
            stopAll();
            unavailable("coordinator_failed");
        }
    }

    private void tick() {
        if (closed) return;
        for (String kind : new HashSet<>(connections.keySet())) {
            if (!wanted.contains(kind)) {
                connections.remove(kind).stop();
                snapshots.remove(kind);
            }
        }
        if (wanted.isEmpty()) return;
        if (cleanupBlocked) {
            stopAll();
            unavailable("transport_cleanup_failed");
            return;
        }
        if (credentials == null) {
            unavailable("credential_storage_unavailable");
            return;
        }
        long now = clock.elapsed();
        if (!mappingChecked || now - mappingAt >= 2000) {
            mappingAt = now;
            mappingChecked = true;
            try {
                File directory = Config.dir(context);
                if (directory == null) throw new IOException("No HA mapping directory");
                String text = HaCardConfig.read(new File(directory, HaCardConfig.FILE_NAME));
                if (mappings == null || !mappingText.equals(text)) {
                    HaCardConfig next = new HaCardConfig(new JSONObject(text));
                    stopAll();
                    if (cleanupBlocked) {
                        unavailable("transport_cleanup_failed");
                        return;
                    }
                    mappings = next;
                    mappingText = text;
                    errorCode = "";
                    for (String kind : wanted) publish(kind, HaCardData.empty(kind, "unavailable"));
                }
            } catch (IOException | JSONException | SecurityException e) {
                mappings = null;
                stopAll();
                unavailable("invalid_or_missing_mappings");
                return;
            }
        }
        if (mappings == null) {
            unavailable("invalid_or_missing_mappings");
            return;
        }
        for (String kind : wanted) {
            try {
                JSONObject config = mappings.card(kind);
                if (!HaCardConfig.enabled(config)) {
                    publish(kind, HaCardData.empty(kind, "disabled"));
                    continue;
                }
                HaConnection connection = connections.get(kind);
                if (connection == null) {
                    publish(kind, HaCardData.empty(kind, "unavailable"));
                    connection = new HaConnection(kind, config, credentials.origin, credentials.token,
                            transportFactory, clock, this::dispatch, new HaConnection.Listener() {
                        @Override public void snapshot(JSONObject snapshot) { publish(kind, snapshot); }
                        @Override public void error(String code) { HaCards.this.error(code); }
                    });
                    connections.put(kind, connection);
                }
                connection.tick();
                if (cleanupBlocked) {
                    stopAll();
                    unavailable("transport_cleanup_failed");
                    return;
                }
            } catch (JSONException e) {
                error("invalid_mappings");
                try {
                    publish(kind, HaCardData.empty(kind, "unavailable")
                            .put("message", "invalid_mappings"));
                } catch (JSONException encoding) {
                    throw new IllegalStateException("Could not encode HA status", encoding);
                }
            }
        }
    }

    private void unavailable(String code) {
        error(code);
        for (String kind : wanted) {
            try {
                publish(kind, HaCardData.empty(kind, "unavailable").put("message", code));
            } catch (JSONException e) {
                throw new IllegalStateException("Could not encode HA status", e);
            }
        }
    }

    private void publish(String kind, JSONObject snapshot) {
        snapshots.put(kind, snapshot);
        deliver(kind, snapshot);
    }

    private void deliver(String kind, JSONObject snapshot) {
        long epoch = deliveryEpoch;
        main.post(() -> {
            if (closed || !foreground || epoch != viewEpoch) return;
            for (Map.Entry<LocalSnapshotView, String> entry : views.entrySet()) {
                if (kind.equals(entry.getValue()) && entry.getKey().directVisible()) {
                    entry.getKey().directSnapshot(snapshot);
                }
            }
        });
    }

    private void error(String code) {
        if ("transport_cleanup_failed".equals(code)) cleanupBlocked = true;
        if (!code.equals(errorCode)) Log.w("HaCards", code);
        errorCode = code;
    }

    private void stopAll() {
        for (HaConnection connection : connections.values()) {
            try {
                connection.stop();
            } catch (RuntimeException e) {
                cleanupBlocked = true;
                Log.e("HaCards", "transport_cleanup_failed (" + e.getClass().getSimpleName() + ")");
            }
        }
        connections.clear();
        snapshots.clear();
    }
}
