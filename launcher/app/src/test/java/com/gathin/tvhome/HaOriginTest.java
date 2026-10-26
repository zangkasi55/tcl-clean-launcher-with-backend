package com.gathin.tvhome;

import org.junit.Test;

import static org.junit.Assert.*;

public class HaOriginTest {
    @Test public void acceptsOnlyAnHttpsOriginAndBuildsTheFixedWebsocketRoute() {
        assertEquals("https://ha.example.test", HaOrigin.validate("https://ha.example.test/"));
        assertEquals("wss://ha.example.test:8443/api/websocket",
                HaOrigin.websocket("https://ha.example.test:8443").toString());
        assertEquals("wss://[::1]:8443/api/websocket",
                HaOrigin.websocket("https://[::1]:8443/").toString());
        assertEquals("https://192.0.2.1", HaOrigin.validate("https://192.0.2.1"));
        for (String value : new String[] {null, "", "http://ha.example.test",
                "HTTPS://ha.example.test", "https://user@ha.example.test", "https://ha.example.test/api",
                "https://ha.example.test?", "https://ha.example.test/#",
                "https://ha.example.test:0", "https://ha.example.test:65536", "https://ha.example.test:",
                "https://ha.example.test:-2", "https://", " https://ha.example.test",
                "https://ha.example.test\\path", "https://ha.example.test/" + "a".repeat(2048)}) {
            assertThrows(IllegalArgumentException.class, () -> HaOrigin.validate(value));
        }
    }

    @Test public void rejectsTokenWhitespaceControlsNonAsciiAndOversizeWithoutExposingInput() {
        assertEquals("synthetic.test-token_1", HaOrigin.token("synthetic.test-token_1"));
        assertEquals(8192, HaOrigin.token("x".repeat(8192)).length());
        for (String value : new String[] {null, "", "secret value", "secret\n", "\tsecret", "secret\u007f",
                "secret\u00e9", "secret" + "x".repeat(8192)}) {
            IllegalArgumentException error = assertThrows(IllegalArgumentException.class,
                    () -> HaOrigin.token(value));
            assertFalse(error.getMessage().contains("secret"));
        }
    }
}
