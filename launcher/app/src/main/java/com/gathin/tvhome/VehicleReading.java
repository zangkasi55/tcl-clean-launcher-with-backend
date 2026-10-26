package com.gathin.tvhome;

import java.util.Locale;

final class VehicleReading {
    private VehicleReading() {}

    static float width(float available, float screenWidth) {
        return Math.min(available, screenWidth * .1f);
    }

    static boolean validSoc(double value) {
        return !Double.isNaN(value) && !Double.isInfinite(value) && value >= 0 && value <= 100;
    }

    static long timestamp(Object value) {
        if (!(value instanceof Long || value instanceof Integer) || ((Number) value).longValue() <= 0) {
            throw new IllegalArgumentException("Invalid vehicle observation timestamp");
        }
        return ((Number) value).longValue();
    }

    static long freshness(Object value) {
        if (!(value instanceof Long || value instanceof Integer)
                || ((Number) value).longValue() < 1000 || ((Number) value).longValue() > 900000) {
            throw new IllegalArgumentException("Invalid vehicle freshness");
        }
        return ((Number) value).longValue();
    }

    static boolean current(String state, long receivedAt, long now, long maxAge) {
        if ("simulated".equals(state)) return true;
        return "live".equals(state) && receivedAt > 0 && maxAge >= 1000 && maxAge <= 900000
                && now - receivedAt >= -5000 && now - receivedAt <= maxAge;
    }

    static String percentage(Double value, Locale locale) {
        return value != null && validSoc(value) ? String.format(locale, "%.0f%%", value) : "--%";
    }
}
