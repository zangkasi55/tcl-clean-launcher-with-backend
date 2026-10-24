package com.gathin.tvhome;

import java.util.Locale;

final class EnergyReading {
    private EnergyReading() {}

    static String power(double watts, Locale locale) {
        if (!Double.isFinite(watts)) throw new IllegalArgumentException("Non-finite power");
        double magnitude = Math.abs(watts);
        return magnitude >= 1000
                ? String.format(locale, "%.2f kW", magnitude / 1000)
                : String.format(locale, "%.0f W", magnitude);
    }

    static int direction(double watts) {
        if (!Double.isFinite(watts)) throw new IllegalArgumentException("Non-finite power");
        return Math.abs(watts) <= 1 ? 0 : watts > 0 ? 1 : -1;
    }

    static boolean stale(long sampledAtMillis, long nowMillis) {
        return stale(sampledAtMillis, nowMillis, 120000);
    }

    static boolean stale(long sampledAtMillis, long nowMillis, long maxAgeMillis) {
        return sampledAtMillis <= 0 || maxAgeMillis <= 0
                || sampledAtMillis < nowMillis - maxAgeMillis
                || sampledAtMillis > nowMillis + 30000;
    }

    static boolean current(String state, long receivedAt, long now, long maxAge) {
        return "simulated".equals(state) || "live".equals(state)
                && !stale(receivedAt, now, maxAge);
    }
}
