package com.gathin.tvhome;

import org.junit.Test;
import static org.junit.Assert.*;

public class HomeOverridePolicyTest {
    @Test public void redirectsOnlyTheStockHomeWindow() {
        assertTrue(HomeOverridePolicy.isStockHome("com.google.android.apps.tv.launcherx",
                "com.google.android.apps.tv.launcherx.home.HomeActivity"));
        assertTrue(HomeOverridePolicy.isStockHome("com.google.android.apps.tv.launcherx",
                ".home.HomeActivity"));
    }

    @Test public void leavesSettingsAccountsAndAppsAlone() {
        assertFalse(HomeOverridePolicy.isStockHome("com.google.android.apps.tv.launcherx",
                "com.google.android.apps.tv.launcherx.profile.core.AccountVerificationActivity"));
        assertFalse(HomeOverridePolicy.isStockHome("com.thegate.tv", ".MainActivity"));
        assertFalse(HomeOverridePolicy.isStockHome("com.android.tv.settings", ".MainSettings"));
        assertFalse(HomeOverridePolicy.isStockHome("com.gathin.tvhome", ".MainActivity"));
        assertFalse(HomeOverridePolicy.isStockHome(null, null));
    }
}
