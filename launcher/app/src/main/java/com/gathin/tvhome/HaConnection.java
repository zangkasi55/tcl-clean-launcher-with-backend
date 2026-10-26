package com.gathin.tvhome;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.security.ProviderException;
import java.util.function.Consumer;

/** One card connection; state and protocol callbacks run on the owner's serialized worker. */
final class HaConnection {
    interface Transport {
        void connect();
        void send(String message);
        void close() throws IOException;
    }

    interface Factory {
        Transport create(String origin, HaWebSocket.Listener listener) throws IOException;
    }

    interface Clock {
        long wall();
        long elapsed();
    }

    interface Listener {
        void snapshot(JSONObject snapshot);
        void error(String code);
    }

    private final String kind, origin, token;
    private final JSONObject config;
    private final Factory factory;
    private final Clock clock;
    private final Consumer<Runnable> dispatch;
    private final Listener listener;
    private Transport transport;
    private HaSession session;
    private JSONObject latest;
    private long generation, retryAt, backoff = 1000;
    private boolean stopped, blocked;

    HaConnection(String kind, JSONObject config, String origin, String token, Factory factory,
                 Clock clock, Consumer<Runnable> dispatch, Listener listener) throws JSONException {
        this.kind = kind;
        this.config = new JSONObject(config.toString());
        this.origin = HaOrigin.validate(origin);
        this.token = HaOrigin.token(token);
        this.factory = factory;
        this.clock = clock;
        this.dispatch = dispatch;
        this.listener = listener;
    }

    void tick() {
        if (stopped || blocked) return;
        if (session != null) {
            session.tick(clock.wall(), clock.elapsed());
        } else if (clock.elapsed() >= retryAt) {
            connect();
        }
    }

    private void connect() {
        final long version = ++generation;
        try {
            session = new HaSession(kind, config, token, clock.elapsed(), new HaSession.Listener() {
                @Override public void send(JSONObject message) {
                    transport.send(message.toString());
                }
                @Override public void authenticated() {}
                @Override public void snapshot(JSONObject snapshot) {
                    if (stopped || generation != version) return;
                    latest = snapshot;
                    if ("live".equals(snapshot.optString("status"))) backoff = 1000;
                    listener.snapshot(snapshot);
                }
                @Override public void failure(String code, boolean retry) {
                    if (stopped || generation != version) return;
                    failed(code, retry);
                }
            });
            transport = factory.create(origin, new HaWebSocket.Listener() {
                @Override public void message(String message) {
                    dispatch.accept(() -> {
                        if (!stopped && generation == version && session != null) {
                            session.message(message, clock.wall(), clock.elapsed());
                        }
                    });
                }
                @Override public void disconnected() {
                    dispatch.accept(() -> {
                        if (!stopped && generation == version && session != null) session.disconnected();
                    });
                }
            });
            transport.connect();
        } catch (HaWebSocket.CleanupException e) {
            listener.error("transport_cleanup_failed");
            failed("transport_cleanup_failed", false);
        } catch (SecurityException | ProviderException e) {
            failed("connection_not_permitted", false);
        } catch (IOException | IllegalStateException e) {
            failed("connection_failed", true);
        } catch (JSONException | IllegalArgumentException e) {
            failed("invalid_configuration", false);
        }
    }

    private void failed(String code, boolean retry) {
        boolean cleaned = disconnect();
        if (!cleaned) code = "transport_cleanup_failed";
        blocked = !retry || !cleaned;
        retryAt = clock.elapsed() + backoff;
        backoff = Math.min(60000, backoff * 2);
        try {
            latest = retry && cleaned && latest != null ? HaCardData.stale(latest)
                    : HaCardData.empty(kind, "unavailable");
            latest.put("message", code);
            listener.snapshot(latest);
        } catch (JSONException e) {
            throw new IllegalStateException("Cannot encode HA connection status", e);
        }
        listener.error(code);
    }

    void stop() {
        if (stopped) return;
        stopped = true;
        disconnect();
        latest = null;
    }

    private boolean disconnect() {
        generation++;
        if (session != null) session.stop();
        session = null;
        Transport previous = transport;
        transport = null;
        if (previous != null) {
            try {
                previous.close();
            } catch (IOException | IllegalStateException | SecurityException | ProviderException e) {
                listener.error("transport_cleanup_failed");
                return false;
            }
        }
        return true;
    }
}
