package com.gathin.tvhome;

import android.accessibilityservice.AccessibilityService;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.provider.Settings;
import android.util.Log;
import android.view.KeyEvent;
import android.view.accessibility.AccessibilityEvent;
import android.widget.Toast;

/**
 * Sends the remote's Home button to this launcher on remotes that emit KEYCODE_ALL_APPS for Home
 * (Xiaomi sticks and boxes). Android TV 9 routes that key only to the system-image launcher, so once
 * the stock launcher is disabled the button does nothing. Every other key passes through untouched.
 *
 * Enable over adb:
 *   settings put secure enabled_accessibility_services com.gathin.tvhome/.HomeKeyService
 *   settings put secure accessibility_enabled 1
 */
public class HomeKeyService extends AccessibilityService {

    private RemoteKeyPolicy keys;

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        keys = new RemoteKeyPolicy(getResources().getIntArray(R.array.settings_key_scancodes),
                getResources().getIntArray(R.array.settings_key_keycodes));
    }

    @Override
    protected boolean onKeyEvent(KeyEvent event) {
        if (keys == null || !RemoteKeyPolicy.isKeyAction(event.getAction())) return false;
        if (keys.isSettingsKey(event.getKeyCode(), event.getScanCode())) {
            if (RemoteKeyPolicy.opensOnRelease(event.getAction(), event.isCanceled())) {
                open(new Intent(Settings.ACTION_SETTINGS));
            }
            return true;
        }
        if (event.getKeyCode() != KeyEvent.KEYCODE_ALL_APPS) return false;
        if (RemoteKeyPolicy.opensOnRelease(event.getAction(), event.isCanceled())) {
            Intent home = new Intent(Intent.ACTION_MAIN)
                    .addCategory(Intent.CATEGORY_HOME)
                    .setPackage(getPackageName())
                    .addFlags(Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
            open(home);
        }
        return true;
    }

    private void open(Intent intent) {
        try {
            startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        } catch (ActivityNotFoundException | SecurityException error) {
            Log.e("TvHome.RemoteKeys", "Cannot open the requested remote action", error);
            Toast.makeText(this, R.string.not_available, Toast.LENGTH_SHORT).show();
        }
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
    }

    @Override
    public void onInterrupt() {
    }
}
