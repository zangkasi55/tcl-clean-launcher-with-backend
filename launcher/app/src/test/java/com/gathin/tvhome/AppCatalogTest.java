package com.gathin.tvhome;

import org.junit.Test;
import java.util.Arrays;
import java.util.Collections;
import static org.junit.Assert.*;

public class AppCatalogTest {
    @Test public void dimensionlessBannersFallBackToTheOriginalIcon() {
        assertFalse(AppCatalog.usableBanner(-1, -1));
        assertFalse(AppCatalog.usableBanner(0, 180));
        assertFalse(AppCatalog.usableBanner(320, 0));
        assertTrue(AppCatalog.usableBanner(320, 180));
    }

    @Test public void shortAndFullComponentsHaveOneIdentity() {
        assertEquals("com.thegate.tv/com.thegate.tv.MainActivity",
                AppCatalog.canonicalComponent("com.thegate.tv/.MainActivity"));
    }

    @Test public void ytAndYoutubeRemainDistinct() {
        String yt = "com.thegate.tv/com.thegate.tv.MainActivity";
        String youtube = "com.google.android.youtube.tv/com.google.android.apps.youtube.tv.activity.ShellActivity";
        assertEquals(0, AppCatalog.rank(Arrays.asList(yt, youtube), yt));
        assertEquals(1, AppCatalog.rank(Arrays.asList(yt, youtube), youtube));
    }

    @Test public void unconfiguredAppsAreNotRemoved() {
        assertEquals(Integer.MAX_VALUE,
                AppCatalog.rank(Collections.emptyList(), "com.netflix.ninja/.MainActivity"));
    }

    @Test public void onlyContentAddressedImagesAreAccepted() {
        String digest = String.join("", Collections.nCopies(64, "a"));
        assertTrue(AppCatalog.safeAsset("artwork-" + digest + ".png"));
        assertTrue(AppCatalog.safeAsset("wallpaper-" + digest + ".jpg"));
        assertFalse(AppCatalog.safeAsset("../wallpaper.jpg"));
        assertFalse(AppCatalog.safeAsset("/sdcard/logo.png"));
        assertFalse(AppCatalog.safeAsset("artwork-" + digest + ".svg"));
    }

    @Test public void invalidActivityIsRejected() {
        assertThrows(IllegalArgumentException.class,
                () -> AppCatalog.canonicalComponent("com.example/.Main;reboot"));
    }
}
