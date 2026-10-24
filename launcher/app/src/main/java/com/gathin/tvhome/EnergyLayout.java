package com.gathin.tvhome;

import java.util.Arrays;

final class EnergyLayout {
    static final float HEIGHT = 128f;
    static final float HOME_BOTTOM = 18f;

    private EnergyLayout() {}

    static float homeTopPadding(float availableHeight, float tileHeight, boolean energy) {
        float dockHeight = tileHeight + 16f + 18f;
        float panelHeight = energy ? HEIGHT + 12f : 0f;
        return Math.max(0f, availableHeight - dockHeight - panelHeight - HOME_BOTTOM);
    }

    static float width(float available, float screenWidth) {
        return Math.min(available, screenWidth * 0.3f);
    }

    static String visualKey(String state, boolean current, double[] metrics, double charge) {
        return state + ":" + current + ":" + Arrays.toString(metrics) + ":" + charge;
    }
}
