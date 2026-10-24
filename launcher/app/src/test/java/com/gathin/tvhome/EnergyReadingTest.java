package com.gathin.tvhome;

import org.junit.Test;
import java.util.Locale;
import static org.junit.Assert.*;

public class EnergyReadingTest {
    @Test public void screenshotValuesKeepTheirUnits() {
        assertEquals("3.47 kW", EnergyReading.power(3470, Locale.US));
        assertEquals("880 W", EnergyReading.power(880, Locale.US));
        assertEquals("2.59 kW", EnergyReading.power(-2590, Locale.US));
        assertEquals("0 W", EnergyReading.power(0, Locale.US));
    }

    @Test public void chargingAndDischargingAreOppositeDirections() {
        assertEquals(-1, EnergyReading.direction(-2590));
        assertEquals(1, EnergyReading.direction(1800));
        assertEquals(0, EnergyReading.direction(0));
    }

    @Test public void nonFiniteValuesAreNeverDisplayedAsLivePower() {
        assertThrows(IllegalArgumentException.class,
                () -> EnergyReading.power(Double.NaN, Locale.US));
        assertThrows(IllegalArgumentException.class,
                () -> EnergyReading.direction(Double.POSITIVE_INFINITY));
    }

    @Test public void liveReadingsExpireAndFutureClockSkewIsVisible() {
        long now = 1800000000000L;
        assertFalse(EnergyReading.stale(now - 120000, now));
        assertTrue(EnergyReading.stale(now - 120001, now));
        assertFalse(EnergyReading.stale(now + 30000, now));
        assertTrue(EnergyReading.stale(now + 30001, now));
        assertTrue(EnergyReading.stale(0, now));
    }

    @Test public void formattingFollowsTheLocale() {
        assertEquals("3,47 kW", EnergyReading.power(3470, Locale.GERMANY));
    }

    @Test public void realtimeFeedUsesItsShortHeartbeatFreshnessWindow() {
        long now = 1800000000000L;
        assertFalse(EnergyReading.stale(now - 15000, now, 15000));
        assertTrue(EnergyReading.stale(now - 15001, now, 15000));
        assertTrue(EnergyReading.stale(now, now, 0));
    }

    @Test public void tvDeliveryWindowToleratesOneDelayedHeartbeatButStillExpires() {
        long received = 1800000000000L;
        assertTrue(EnergyReading.stale(received, received + 16000, 15000));
        assertTrue(EnergyReading.current("live", received, received + 16000, 30000));
        assertTrue(EnergyReading.current("live", received, received + 30000, 30000));
        assertFalse(EnergyReading.current("live", received, received + 30001, 30000));
    }

    @Test public void upstreamStaleOrUnavailableNeverUsesTheDeliveryGracePeriod() {
        long now = 1800000000000L;
        for (String state : new String[]{"stale", "unavailable", "unconfigured", "disabled"}) {
            assertFalse(EnergyReading.current(state, now, now, 30000));
        }
        assertTrue(EnergyReading.current("simulated", 0, now, 30000));
    }
}
