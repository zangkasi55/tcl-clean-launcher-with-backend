package com.gathin.tvhome;

import org.junit.Test;
import static org.junit.Assert.*;

public class RemoteKeyPolicyTest {
    private final RemoteKeyPolicy tcl = new RemoteKeyPolicy(new int[0], new int[]{83});

    @Test public void handlesStandardAndTclSettingsKeys() {
        assertTrue(tcl.isSettingsKey(176, 0));
        assertTrue(tcl.isSettingsKey(83, 240));
        assertTrue(tcl.isSettingsKey(83, 0));
    }

    @Test public void sharedTclScancodeDoesNotStealOtherButtons() {
        for (int code = 0; code <= 304; code++) {
            if (code != 83 && code != 176) assertFalse("Key " + code, tcl.isSettingsKey(code, 240));
        }
    }

    @Test public void leavesNavigationPowerVolumeMediaInputsVoiceAndAppKeysAlone() {
        int[] buttons = {3, 4, 19, 20, 21, 22, 23, 24, 25, 26, 66, 79, 82, 84,
                85, 86, 87, 88, 89, 90, 91, 126, 127, 130, 164, 165, 166, 167,
                170, 171, 172, 173, 174, 175, 177, 178, 183, 184, 185, 186,
                219, 222, 223, 224, 231, 284, 289, 290, 291, 292};
        for (int code : buttons) assertFalse("Key " + code, tcl.isSettingsKey(code, 240));
    }

    @Test public void configuredScancodesRemainSupportedWithoutMutatingTheConfiguration() {
        int[] scancodes = {384};
        int[] keycodes = {83};
        RemoteKeyPolicy policy = new RemoteKeyPolicy(scancodes, keycodes);
        scancodes[0] = 240;
        keycodes[0] = 82;
        assertTrue(policy.isSettingsKey(0, 384));
        assertTrue(policy.isSettingsKey(83, 0));
        assertFalse(policy.isSettingsKey(82, 240));
    }

    @Test public void opensOnlyOnUncanceledReleaseAndNeverOnLongPressRepeats() {
        assertFalse(RemoteKeyPolicy.opensOnRelease(0, false));
        assertFalse(RemoteKeyPolicy.opensOnRelease(0, true));
        assertTrue(RemoteKeyPolicy.opensOnRelease(1, false));
        assertFalse(RemoteKeyPolicy.opensOnRelease(1, true));
        assertFalse(RemoteKeyPolicy.opensOnRelease(2, false));
        assertTrue(RemoteKeyPolicy.isKeyAction(0));
        assertTrue(RemoteKeyPolicy.isKeyAction(1));
        assertFalse(RemoteKeyPolicy.isKeyAction(2));
    }
}
