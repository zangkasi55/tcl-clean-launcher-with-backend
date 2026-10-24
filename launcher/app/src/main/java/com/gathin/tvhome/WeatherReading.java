package com.gathin.tvhome;

import java.util.Locale;

final class WeatherReading {
    private WeatherReading() {}

    static boolean current(String state, long received, long now, long maxAge) {
        return "simulated".equals(state) || "live".equals(state)
                && received > 0 && maxAge > 0 && received <= now + 5000
                && received >= now - maxAge;
    }

    static boolean validTemperature(double value) {
        return Double.isFinite(value) && value >= -150 && value <= 300;
    }

    static String temperature(double value, String unit, Locale locale) {
        return String.format(locale, "%.1f%s", value, unit);
    }

    static String range(double high, Double low, Locale locale) {
        return String.format(locale, "%.1f / ", high)
                + (low == null ? "--" : String.format(locale, "%.1f", low));
    }

    static String condition(String value) {
        if ("partlycloudy".equals(value)) return "Partly cloudy";
        if ("clear-night".equals(value)) return "Clear night";
        if (value.isEmpty()) return "";
        String words = value.replace('-', ' ');
        return words.substring(0, 1).toUpperCase(Locale.ROOT) + words.substring(1);
    }
}
