package com.gathin.tvhome;

import org.java_websocket.WebSocket;
import org.java_websocket.handshake.ClientHandshake;
import org.java_websocket.server.DefaultSSLWebSocketServerFactory;
import org.java_websocket.server.WebSocketServer;
import org.junit.BeforeClass;
import org.junit.ClassRule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.IOException;
import java.io.InputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManagerFactory;

import static org.junit.Assert.*;

public class HaWebSocketTest {
    @ClassRule public static final TemporaryFolder TEMP = new TemporaryFolder();
    private static SSLContext matchingServer, wrongHostServer, trustedClient;

    @BeforeClass public static void certificates() throws Exception {
        String password = UUID.randomUUID().toString();
        KeyStore matching = certificate("matching", "dns:localhost,ip:127.0.0.1", password);
        KeyStore wrongHost = certificate("wrong-host", "dns:other.example.test", password);
        matchingServer = serverContext(matching, password);
        wrongHostServer = serverContext(wrongHost, password);
        KeyStore trust = KeyStore.getInstance("PKCS12");
        trust.load(null, null);
        trust.setCertificateEntry("matching", matching.getCertificate("test"));
        trust.setCertificateEntry("wrong-host", wrongHost.getCertificate("test"));
        TrustManagerFactory managers = TrustManagerFactory.getInstance(
                TrustManagerFactory.getDefaultAlgorithm());
        managers.init(trust);
        trustedClient = SSLContext.getInstance("TLS");
        trustedClient.init(null, managers.getTrustManagers(), null);
    }

    private static KeyStore certificate(String name, String san, String password) throws Exception {
        Path file = TEMP.getRoot().toPath().resolve(name + ".p12");
        String keytool = Path.of(System.getProperty("java.home"), "bin",
                System.getProperty("os.name").startsWith("Windows") ? "keytool.exe" : "keytool")
                .toString();
        Process process = new ProcessBuilder(keytool, "-genkeypair", "-alias", "test",
                "-keyalg", "RSA", "-keysize", "2048", "-validity", "2",
                "-dname", "CN=Local HA TLS Test", "-ext", "SAN=" + san,
                "-storetype", "PKCS12", "-keystore", file.toString(),
                "-storepass", password, "-keypass", password, "-noprompt")
                .redirectErrorStream(true).start();
        if (!process.waitFor(20, TimeUnit.SECONDS)) {
            process.destroyForcibly();
            throw new AssertionError("Test certificate generation timed out");
        }
        assertEquals("Test certificate generation failed", 0, process.exitValue());
        KeyStore store = KeyStore.getInstance("PKCS12");
        try (InputStream input = Files.newInputStream(file)) { store.load(input, password.toCharArray()); }
        return store;
    }

    private static SSLContext serverContext(KeyStore store, String password) throws Exception {
        KeyManagerFactory managers = KeyManagerFactory.getInstance(
                KeyManagerFactory.getDefaultAlgorithm());
        managers.init(store, password.toCharArray());
        SSLContext context = SSLContext.getInstance("TLS");
        context.init(managers.getKeyManagers(), null, null);
        return context;
    }

    private static final class Capture implements HaWebSocket.Listener {
        final CountDownLatch frame = new CountDownLatch(1), disconnected = new CountDownLatch(1);
        final AtomicInteger disconnects = new AtomicInteger(), messages = new AtomicInteger();
        volatile String text;
        @Override public void message(String message) {
            text = message;
            messages.incrementAndGet();
            frame.countDown();
        }
        @Override public void disconnected() {
            disconnects.incrementAndGet();
            disconnected.countDown();
        }
    }

    private static final class Server extends WebSocketServer implements AutoCloseable {
        final CountDownLatch ready = new CountDownLatch(1), received = new CountDownLatch(1);
        final AtomicReference<Exception> error = new AtomicReference<>();
        volatile String path, message;
        Server(SSLContext tls) throws InterruptedException {
            super(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 1);
            setWebSocketFactory(new DefaultSSLWebSocketServerFactory(tls));
            setDaemon(true);
            start();
            assertTrue("Local TLS server did not start", ready.await(5, TimeUnit.SECONDS));
        }
        @Override public void onStart() { ready.countDown(); }
        @Override public void onOpen(WebSocket socket, ClientHandshake handshake) {
            path = handshake.getResourceDescriptor();
            socket.send("{\"type\":\"auth_required\"}");
        }
        @Override public void onMessage(WebSocket socket, String text) {
            message = text;
            received.countDown();
        }
        @Override public void onClose(WebSocket socket, int code, String reason, boolean remote) {}
        @Override public void onError(WebSocket socket, Exception failure) { error.set(failure); }
        String origin() { return "https://localhost:" + getPort(); }
        @Override public void close() throws InterruptedException { stop(2000); }
    }

    @Test(timeout = 15000) public void trustedTlsUsesFixedWebSocketPathAndClosesIdempotently()
            throws Exception {
        try (Server server = new Server(matchingServer)) {
            Capture capture = new Capture();
            HaWebSocket client = new HaWebSocket(server.origin(), capture, trustedClient.getSocketFactory());
            try {
                client.connect();
                assertTrue(capture.frame.await(5, TimeUnit.SECONDS));
                assertEquals("{\"type\":\"auth_required\"}", capture.text);
                assertEquals("/api/websocket", server.path);
                client.send("{\"type\":\"ping\",\"id\":1}");
                assertTrue(server.received.await(5, TimeUnit.SECONDS));
                assertEquals("{\"type\":\"ping\",\"id\":1}", server.message);
                assertNull("Trusted TLS server failed", server.error.get());
            } finally { client.close(); }
            client.close();
            assertThrows(IllegalStateException.class, client::connect);
            assertThrows(IllegalStateException.class, () -> client.send("{}"));
            assertEquals(0, capture.disconnects.get());
        }
    }

    @Test(timeout = 15000) public void defaultTrustRejectsAnUntrustedLocalCertificate() throws Exception {
        try (Server server = new Server(matchingServer)) {
            Capture capture = new Capture();
            HaWebSocket client = new HaWebSocket(server.origin(), capture);
            try {
                client.connect();
                assertTrue(capture.disconnected.await(5, TimeUnit.SECONDS));
                assertEquals(0, capture.messages.get());
                assertEquals(1, capture.disconnects.get());
            } finally { client.close(); }
        }
    }

    @Test(timeout = 15000) public void trustedCertificateForAnotherHostIsStillRejected() throws Exception {
        try (Server server = new Server(wrongHostServer)) {
            Capture capture = new Capture();
            HaWebSocket client = new HaWebSocket(server.origin(), capture, trustedClient.getSocketFactory());
            try {
                client.connect();
                assertTrue(capture.disconnected.await(5, TimeUnit.SECONDS));
                assertEquals(0, capture.messages.get());
                assertEquals(1, capture.disconnects.get());
            } finally { client.close(); }
        }
    }

    @Test(timeout = 15000) public void closeCancelsAStalledTlsHandshakeWithoutLateCallbacks()
            throws Exception {
        try (ServerSocket server = new ServerSocket(0, 1, InetAddress.getLoopbackAddress())) {
            CountDownLatch started = new CountDownLatch(1);
            AtomicReference<IOException> failure = new AtomicReference<>();
            Thread peer = new Thread(() -> {
                try (Socket socket = server.accept()) {
                    socket.setSoTimeout(5000);
                    InputStream input = socket.getInputStream();
                    assertTrue(input.read() >= 0);
                    started.countDown();
                    while (input.read() >= 0) {}
                } catch (IOException e) { failure.set(e); }
            }, "ha-test-stalled-peer");
            peer.setDaemon(true);
            peer.start();
            Capture capture = new Capture();
            HaWebSocket client = new HaWebSocket("https://localhost:" + server.getLocalPort(),
                    capture, trustedClient.getSocketFactory());
            try {
                client.connect();
                assertTrue("TLS handshake did not start", started.await(5, TimeUnit.SECONDS));
            } finally { client.close(); }
            peer.join(2000);
            assertFalse("Closing transport left the peer waiting", peer.isAlive());
            assertNull(failure.get());
            assertEquals(0, capture.messages.get());
            assertEquals(0, capture.disconnects.get());
        }
    }
}
