package com.gathin.tvhome;

import android.accessibilityservice.AccessibilityService;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.os.SystemClock;
import android.provider.Settings;
import android.util.Log;
import android.view.accessibility.AccessibilityEvent;

public final class HomeOverrideService extends AccessibilityService {
    private long lastRedirect = Long.MIN_VALUE / 2;

    @Override protected void onServiceConnected() {
        super.onServiceConnected();
        int boot = Settings.Global.getInt(getContentResolver(), Settings.Global.BOOT_COUNT, -1);
        if (boot < 0) {
            Log.w("TvHome.HomeOverride", "Cannot identify the TV boot; Home window handling remains active");
            return;
        }
        android.content.SharedPreferences saved = getSharedPreferences("home_override_boot", MODE_PRIVATE);
        if (saved.getInt("last_boot", -1) == boot) return;
        if (!saved.edit().putInt("last_boot", boot).commit()) {
            Log.w("TvHome.HomeOverride", "Cannot persist the Home compatibility boot state");
        }
        openCleanHome();
    }

    @Override public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event.getEventType() != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
                || !HomeOverridePolicy.isStockHome(
                        String.valueOf(event.getPackageName()),
                        String.valueOf(event.getClassName()))) return;
        long now = SystemClock.elapsedRealtime();
        if (now - lastRedirect < 500) return;
        lastRedirect = now;
        openCleanHome();
    }

    private void openCleanHome() {
        Intent home = new Intent(this, MainActivity.class)
                .addCategory(Intent.CATEGORY_HOME)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
        try {
            startActivity(home);
        } catch (ActivityNotFoundException | SecurityException e) {
            Log.e("TvHome.HomeOverride", "Cannot open the clean Home", e);
        }
    }

    @Override public void onInterrupt() {}
}
