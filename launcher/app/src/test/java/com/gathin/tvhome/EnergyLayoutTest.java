package com.gathin.tvhome;

import org.junit.Test;
import static org.junit.Assert.*;

public class EnergyLayoutTest {
    @Test public void tvCardUsesThirtyPercentOfTheScreenNotOfTheContentRow() {
        assertEquals(288f, EnergyLayout.width(848f, 960f), 0.01f);
        assertEquals(384f, EnergyLayout.width(1168f, 1280f), 0.01f);
        assertEquals(0.3f, EnergyLayout.width(848f, 960f) / 960f, 0.001f);
    }

    @Test public void smallScreensNeverClipTheCard() {
        assertEquals(240f, EnergyLayout.width(240f, 960f), 0.01f);
        assertEquals(192f, EnergyLayout.width(528f, 640f), 0.01f);
    }

    @Test public void identicalHeartbeatsNeedNoRedraw() {
        double[] values = {1200, 800, 0, -400};
        String first = EnergyLayout.visualKey("live", true, values, 40);
        assertEquals(first, EnergyLayout.visualKey("live", true, values.clone(), 40));
        assertNotEquals(first, EnergyLayout.visualKey("live", false, values, 40));
        assertNotEquals(first, EnergyLayout.visualKey("live", true, values, 41));
    }

    @Test public void photoHomePlacesTheDockInTheLowerHalfAndEnergyNearTheBottom() {
        float screenHeight = 540, available = 450, tileHeight = 80;
        float padding = EnergyLayout.homeTopPadding(available, tileHeight, true);
        float dockTop = screenHeight - available + padding + 14;
        float panelTop = screenHeight - available + padding + tileHeight + 34 + 4;
        assertTrue(dockTop >= screenHeight * 0.5f);
        assertTrue(panelTop >= screenHeight * 0.7f);
        assertTrue(panelTop + EnergyLayout.HEIGHT <= screenHeight - 26);
        assertEquals(0f, EnergyLayout.homeTopPadding(100, 80, true), 0.01f);
    }

    @Test public void hidingEnergyKeepsTheDockLowWithoutReservingAnEmptyRow() {
        float withEnergy = EnergyLayout.homeTopPadding(450, 80, true);
        float withoutEnergy = EnergyLayout.homeTopPadding(450, 80, false);
        assertEquals(EnergyLayout.HEIGHT + 12f, withoutEnergy - withEnergy, 0.01f);
    }
}
