package com.gathin.tvhome;

import org.java_websocket.client.WebSocketClient;
import org.java_websocket.drafts.Draft_6455;
import org.java_websocket.exceptions.WebsocketNotConnectedException;
import org.java_websocket.handshake.ServerHandshake;

import java.io.IOException;
import java.net.URI;
import java.nio.ByteBuffer;
import java.util.Collections;
import java.util.concurrent.atomic.AtomicBoolean;

import javax.net.ssl.SSLParameters;
import javax.net.ssl.SNIHostName;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;

final class HaWebSocket implements HaConnection.Transport {
    static final class CleanupException extends IOException {
        CleanupException(Exception cause) {
            super("HA transport cleanup could not be confirmed", cause);
        }
    }

    interface Listener {
        void message(String message);
        void disconnected();
    }

    private final WebSocketClient client;
    private final SSLSocket socket;
    private final AtomicBoolean closed = new AtomicBoolean(), notified = new AtomicBoolean();

    HaWebSocket(String origin, Listener listener) throws IOException {
        this(origin, listener, (SSLSocketFactory) SSLSocketFactory.getDefault());
    }

    HaWebSocket(String origin, Listener listener, SSLSocketFactory factory) throws IOException {
        URI uri = HaOrigin.websocket(origin);
        socket = (SSLSocket) factory.createSocket();
        try {
            SSLParameters parameters = socket.getSSLParameters();
            parameters.setEndpointIdentificationAlgorithm("HTTPS");
            String host = uri.getHost();
            if (host.indexOf(':') < 0 && !host.matches("[0-9.]+")) {
                parameters.setServerNames(Collections.singletonList(new SNIHostName(host)));
            }
            socket.setSSLParameters(parameters);
            socket.setSoTimeout(15000);
            client = new WebSocketClient(uri,
                new Draft_6455(Collections.emptyList(), 1048576), null, 10000) {
            @Override public void onOpen(ServerHandshake handshake) {}
            @Override public void onMessage(String message) {
                if (!closed.get() && !notified.get()) listener.message(message);
            }
            @Override public void onMessage(ByteBuffer message) {
                disconnected();
            }
            @Override public void onClose(int code, String reason, boolean remote) {
                disconnected();
            }
            @Override public void onError(Exception error) {
                disconnected();
            }
            private void disconnected() {
                if (!closed.get() && notified.compareAndSet(false, true)) listener.disconnected();
            }
        };
            // Owning an actual TLS socket permits cancellation before the library's writer exists.
            client.setSocket(socket);
            client.setConnectionLostTimeout(0);
            client.setTcpNoDelay(true);
            client.setDaemon(true);
        } catch (IOException | RuntimeException e) {
            try {
                socket.close();
            } catch (IOException cleanup) {
                e.addSuppressed(cleanup);
                throw new CleanupException(e);
            }
            throw e;
        }
    }

    @Override public void connect() {
        if (closed.get()) throw new IllegalStateException("HA connection is closed");
        client.connect();
    }

    @Override public void send(String message) {
        if (closed.get()) throw new IllegalStateException("HA connection is closed");
        try {
            client.send(message);
        } catch (WebsocketNotConnectedException e) {
            throw new IllegalStateException("HA connection is not open");
        }
    }

    @Override public void close() throws IOException {
        if (!closed.compareAndSet(false, true)) return;
        try {
            socket.close();
        } finally {
            client.closeConnection(1000, "");
        }
    }
}
