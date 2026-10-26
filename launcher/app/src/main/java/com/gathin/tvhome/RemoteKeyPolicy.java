package com.gathin.tvhome;

final class RemoteKeyPolicy {
    private static final int KEYCODE_SETTINGS = 176;
    private final int[] settingsScancodes;
    private final int[] settingsKeycodes;

    RemoteKeyPolicy(int[] scancodes, int[] keycodes) {
        settingsScancodes = scancodes.clone();
        settingsKeycodes = keycodes.clone();
    }

    boolean isSettingsKey(int keycode, int scancode) {
        if (keycode == KEYCODE_SETTINGS) return true;
        for (int code : settingsScancodes) if (scancode == code) return true;
        for (int code : settingsKeycodes) if (keycode == code) return true;
        return false;
    }

    static boolean isKeyAction(int action) {
        return action == 0 || action == 1;
    }

    static boolean opensOnRelease(int action, boolean canceled) {
        return action == 1 && !canceled;
    }
}
