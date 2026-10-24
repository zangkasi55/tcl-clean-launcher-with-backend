package com.gathin.tvhome;

final class HomeOverridePolicy {
    private HomeOverridePolicy() {}

    static boolean isStockHome(String packageName, String className) {
        return "com.google.android.apps.tv.launcherx".equals(packageName)
                && ("com.google.android.apps.tv.launcherx.home.HomeActivity".equals(className)
                || ".home.HomeActivity".equals(className));
    }
}
