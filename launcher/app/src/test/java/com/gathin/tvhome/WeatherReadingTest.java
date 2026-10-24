package com.gathin.tvhome;

import java.util.Locale;
import org.junit.Test;
import static org.junit.Assert.*;

public class WeatherReadingTest {
    @Test public void forecastBecomesStaleAfterItsDeclaredFifteenMinuteWindow() {
        long at = 1800000000000L;
        assertTrue(WeatherReading.current("live", at, at + 900000, 900000));
        assertFalse(WeatherReading.current("live", at, at + 900001, 900000));
        assertFalse(WeatherReading.current("live", at, at - 5001, 900000));
        assertFalse(WeatherReading.current("stale", at, at, 900000));
        assertFalse(WeatherReading.current("unavailable", 0, at, 900000));
        assertTrue(WeatherReading.current("simulated", 0, at, 900000));
    }

    @Test public void invalidNumbersNeverBecomeDisplayedTemperatures() {
        assertFalse(WeatherReading.validTemperature(Double.NaN));
        assertFalse(WeatherReading.validTemperature(Double.POSITIVE_INFINITY));
        assertFalse(WeatherReading.validTemperature(-151));
        assertFalse(WeatherReading.validTemperature(301));
        assertTrue(WeatherReading.validTemperature(25.6));
    }

    @Test public void temperaturesKeepSourcePrecisionAndMissingLowsAreNotZero() {
        assertEquals("25.6°C", WeatherReading.temperature(25.6, "°C", Locale.US));
        assertEquals("32.5 / 25.6", WeatherReading.range(32.5, 25.6, Locale.US));
        assertEquals("32.5 / --", WeatherReading.range(32.5, null, Locale.US));
        assertEquals("Partly cloudy", WeatherReading.condition("partlycloudy"));
        assertEquals("Clear night", WeatherReading.condition("clear-night"));
    }
}
