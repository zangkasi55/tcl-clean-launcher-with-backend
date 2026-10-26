package com.gathin.tvhome;

import java.util.Locale;
import org.junit.Test;
import static org.junit.Assert.*;

public class VehicleReadingTest {
    @Test public void cardIsTenPercentOfTheWholeDisplayNotTheRemainingRow() {
        for (int screen : new int[] {960, 1280, 1920, 3840}) {
            assertEquals(screen / 10f, VehicleReading.width(screen * .6f, screen), .001f);
            assertEquals(screen / 10f, VehicleReading.width(screen, screen), .001f);
        }
        assertEquals(50f, VehicleReading.width(50, 1920), .001f);
    }

    @Test public void percentageRejectsMissingNonfiniteAndOutOfRangeReadings() {
        assertEquals("--%", VehicleReading.percentage(null, Locale.US));
        for (double value : new double[] {Double.NaN, Double.POSITIVE_INFINITY, -1, 100.1}) {
            assertFalse(VehicleReading.validSoc(value));
            assertEquals("--%", VehicleReading.percentage(value, Locale.US));
        }
        assertEquals("0%", VehicleReading.percentage(0.0, Locale.US));
        assertEquals("47%", VehicleReading.percentage(47.0, Locale.US));
        assertEquals("100%", VehicleReading.percentage(100.0, Locale.US));
    }

    @Test public void historicalActivityNeverClaimsToBeCurrent() {
        long at = 1800000000000L;
        assertTrue(VehicleReading.current("live", at, at + 900000, 900000));
        assertFalse(VehicleReading.current("live", at, at + 900001, 900000));
        assertFalse(VehicleReading.current("live", at, at - 5001, 900000));
        assertFalse(VehicleReading.current("live", 0, at, 900000));
        assertFalse(VehicleReading.current("live", at, at, 900001));
        assertFalse(VehicleReading.current("stale", at, at, 900000));
        assertFalse(VehicleReading.current("unavailable", 0, at, 900000));
        assertTrue(VehicleReading.current("simulated", at, at, 900000));
    }

    @Test public void malformedTimestampsAndFreshnessCannotBeCoercedIntoLiveReadings() {
        assertEquals(1800000000000L, VehicleReading.timestamp(1800000000000L));
        assertEquals(900000L, VehicleReading.freshness(900000));
        for (Object value : new Object[] {null, "1800000000000", true, 1.5, 0, -1}) {
            assertThrows(IllegalArgumentException.class, () -> VehicleReading.timestamp(value));
        }
        for (Object value : new Object[] {null, "900000", true, 900000.0, 999, 900001}) {
            assertThrows(IllegalArgumentException.class, () -> VehicleReading.freshness(value));
        }
    }
}
