package com.gathin.tvhome;

import java.net.URI;
import java.net.URISyntaxException;

final class HaOrigin {
    private HaOrigin() {}

    static String validate(String value) {
        if (value == null || value.length() > 2048) {
            throw new IllegalArgumentException("Enter a Home Assistant HTTPS origin.");
        }
        try {
            URI uri = new URI(value);
            if (!"https".equals(uri.getScheme()) || uri.getHost() == null
                    || uri.getRawUserInfo() != null || uri.getRawQuery() != null
                    || uri.getRawFragment() != null || uri.getPort() == 0
                    || uri.getPort() > 65535 || uri.getRawAuthority().endsWith(":")
                    || !(uri.getRawPath().isEmpty() || "/".equals(uri.getRawPath()))) {
                throw new IllegalArgumentException("Use an HTTPS Home Assistant origin without API paths or credentials.");
            }
            return value.endsWith("/") ? value.substring(0, value.length() - 1) : value;
        } catch (URISyntaxException e) {
            throw new IllegalArgumentException("The Home Assistant HTTPS address is invalid.");
        }
    }

    static URI websocket(String origin) {
        return URI.create("wss" + validate(origin).substring(5) + "/api/websocket");
    }

    static String token(String value) {
        if (value == null || value.isEmpty() || value.length() > 8192) {
            throw new IllegalArgumentException("Enter a valid Home Assistant access token.");
        }
        for (int i = 0; i < value.length(); i++) {
            if (value.charAt(i) <= 32 || value.charAt(i) >= 127) {
                throw new IllegalArgumentException("The Home Assistant token contains invalid characters.");
            }
        }
        return value;
    }
}
