package com.gathin.tvhome;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ActivityInfo;
import android.os.Bundle;
import android.os.Looper;
import android.text.method.PasswordTransformationMethod;
import android.view.View;
import android.view.WindowManager;
import android.widget.EditText;
import android.widget.TextView;

import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.Shadows;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.LooperMode;
import org.robolectric.shadows.ShadowActivity;
import org.robolectric.util.ReflectionHelpers;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.TimeUnit;

import static org.junit.Assert.*;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34, qualifiers = "land")
@LooperMode(LooperMode.Mode.PAUSED)
public class HaActivityTest {
    private SharedPreferences preferences;
    private HaCredentials credentials;

    @Before public void setUp() {
        Context context = RuntimeEnvironment.getApplication();
        preferences = context.getSharedPreferences("ha_connection_v1", Context.MODE_PRIVATE);
        assertTrue(preferences.edit().clear().commit());
        assertTrue(context.getSharedPreferences(com.gathin.tvhome.Config.SETTINGS_NAME, Context.MODE_PRIVATE)
                .edit().clear().putBoolean("animations", false).commit());
        credentials = new HaCredentials(preferences, new HaStorageTestSupport.Keys());
    }

    private void saveCredentials() throws Exception {
        credentials.save(HaStorageTestSupport.ORIGIN, HaStorageTestSupport.TOKEN);
    }

    private static HaCards cards(MainActivity activity) {
        return ReflectionHelpers.getField(activity, "haCards");
    }

    private static void finishWorker(HaSettingsActivity activity) throws Exception {
        ExecutorService worker = ReflectionHelpers.getField(activity, "worker");
        worker.submit(() -> {}).get(5, TimeUnit.SECONDS);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
    }

    @Test public void mainResumeRecreatesSourceOwnersAndDestroyClosesThem() throws Exception {
        try (ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup()) {
            HaCards first = cards(controller.get());
            assertFalse(ReflectionHelpers.<Boolean>getField(first, "direct"));
            assertTrue(ReflectionHelpers.<Boolean>getField(first, "foreground"));
            controller.pause();
            assertFalse(ReflectionHelpers.<Boolean>getField(first, "foreground"));
            saveCredentials();
            controller.resume();
            // Paused Robolectric queues Activity.recreate() on the main looper.
            Shadows.shadowOf(Looper.getMainLooper()).idle();
            HaCards direct = cards(controller.get());
            assertNotSame(first, direct);
            assertTrue(ReflectionHelpers.<Boolean>getField(first, "closed"));
            assertTrue(ReflectionHelpers.<Boolean>getField(direct, "direct"));
            assertTrue(ReflectionHelpers.<Boolean>getField(direct, "foreground"));
            controller.pause();
            credentials.useBackend();
            controller.resume();
            Shadows.shadowOf(Looper.getMainLooper()).idle();
            HaCards backend = cards(controller.get());
            assertNotSame(direct, backend);
            assertTrue(ReflectionHelpers.<Boolean>getField(direct, "closed"));
            assertFalse(ReflectionHelpers.<Boolean>getField(backend, "direct"));
            assertTrue(ReflectionHelpers.<Boolean>getField(backend, "foreground"));
            controller.pause().stop().destroy();
            assertTrue(ReflectionHelpers.<Boolean>getField(backend, "closed"));
        }
    }

    @Test public void homeIntentKeepsTheSourceAndReturnsFromAllApps() {
        try (ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup()) {
            HaCards initial = cards(controller.get());
            ReflectionHelpers.setStaticField(MainActivity.class, "allAppsPage", true);
            controller.newIntent(new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME));
            assertSame(initial, cards(controller.get()));
            assertFalse(ReflectionHelpers.<Boolean>getStaticField(MainActivity.class, "allAppsPage"));
            assertFalse(initial.configurationChanged());
        }
    }

    @Test public void credentialScreenIsPrivateMaskedAndDoesNotRestoreInput() throws Exception {
        try (ActivityController<HaSettingsActivity> controller =
                     Robolectric.buildActivity(HaSettingsActivity.class).setup()) {
            HaSettingsActivity activity = controller.get();
            EditText origin = activity.findViewById(R.id.haOrigin);
            EditText token = activity.findViewById(R.id.haToken);
            assertTrue((activity.getWindow().getAttributes().flags & WindowManager.LayoutParams.FLAG_SECURE) != 0);
            ActivityInfo info = activity.getPackageManager().getActivityInfo(activity.getComponentName(), 0);
            assertFalse(info.exported);
            assertEquals(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE, info.screenOrientation);
            assertTrue(token.getTransformationMethod() instanceof PasswordTransformationMethod);
            assertEquals(activity.getString(R.string.ha_origin_hint), origin.getHint().toString());
            assertEquals(activity.getString(R.string.ha_token_hint), token.getHint().toString());
            assertNull(origin.getContentDescription());
            assertNull(token.getContentDescription());
            assertFalse(origin.isSaveEnabled());
            assertFalse(token.isSaveEnabled());
            assertEquals(View.IMPORTANT_FOR_AUTOFILL_NO, token.getImportantForAutofill());
            assertEquals("", token.getText().toString());
            assertTrue(origin.hasFocus());
            for (int id : new int[]{R.id.haSave, R.id.haBackend, R.id.haForget}) {
                assertTrue(activity.findViewById(id).isFocusable());
                assertTrue(activity.findViewById(id).isClickable());
            }
            origin.setText(HaStorageTestSupport.ORIGIN);
            token.setText(HaStorageTestSupport.TOKEN);
            Bundle state = new Bundle();
            controller.saveInstanceState(state);
            assertTrue(state.isEmpty());
            controller.recreate();
            assertEquals("", ((EditText) controller.get().findViewById(R.id.haOrigin)).getText().toString());
            assertEquals("", ((EditText) controller.get().findViewById(R.id.haToken)).getText().toString());
        }
    }

    @Test public void invalidSetupStaysVisibleAndDoesNotSelectDirectMode() {
        try (ActivityController<HaSettingsActivity> controller =
                     Robolectric.buildActivity(HaSettingsActivity.class).setup()) {
            HaSettingsActivity activity = controller.get();
            ((EditText) activity.findViewById(R.id.haOrigin)).setText("http://ha.example.test");
            ((EditText) activity.findViewById(R.id.haToken)).setText(HaStorageTestSupport.TOKEN);
            activity.findViewById(R.id.haSave).performClick();
            assertEquals(activity.getString(R.string.ha_invalid_input),
                    ((TextView) activity.findViewById(R.id.haStatus)).getText().toString());
            assertFalse(activity.isFinishing());
            assertFalse(credentials.direct());
            assertFalse(preferences.contains("record"));
        }
    }

    @Test public void privateSavePersistsBeforeReturningSuccessAndClearsToken() throws Exception {
        try (ActivityController<HaSettingsActivity> controller =
                     Robolectric.buildActivity(HaSettingsActivity.class).setup()) {
            HaSettingsActivity activity = controller.get();
            ReflectionHelpers.setField(activity, "credentials", credentials);
            ((EditText) activity.findViewById(R.id.haOrigin)).setText(HaStorageTestSupport.ORIGIN);
            EditText token = activity.findViewById(R.id.haToken);
            token.setText(HaStorageTestSupport.TOKEN);
            activity.findViewById(R.id.haSave).performClick();
            assertEquals("", token.getText().toString());
            finishWorker(activity);
            assertEquals(Activity.RESULT_OK, Shadows.shadowOf(activity).getResultCode());
            assertTrue(activity.isFinishing());
            assertTrue(credentials.direct());
            assertEquals(HaStorageTestSupport.TOKEN, credentials.load().token);
            assertFalse(preferences.getAll().toString().contains(HaStorageTestSupport.TOKEN));
        }
    }

    @Test public void backendRollbackRetainsCiphertextAndSettingsPropagatesResult() throws Exception {
        saveCredentials();
        String encrypted = preferences.getString("record", "");
        try (ActivityController<LauncherSettingsActivity> settings =
                     Robolectric.buildActivity(LauncherSettingsActivity.class).setup();
             ActivityController<HaSettingsActivity> setup =
                     Robolectric.buildActivity(HaSettingsActivity.class).setup()) {
            settings.get().findViewById(R.id.haSettingsChoice).performClick();
            ShadowActivity.IntentForResult launched =
                    Shadows.shadowOf(settings.get()).getNextStartedActivityForResult();
            assertNotNull(launched);
            assertEquals(HaSettingsActivity.class.getName(), launched.intent.getComponent().getClassName());
            setup.get().findViewById(R.id.haBackend).performClick();
            finishWorker(setup.get());
            assertFalse(credentials.direct());
            assertEquals(encrypted, preferences.getString("record", ""));
            assertEquals(Activity.RESULT_OK, Shadows.shadowOf(setup.get()).getResultCode());
            Shadows.shadowOf(settings.get()).callOnActivityResult(
                    launched.requestCode, Activity.RESULT_OK, null);
            assertEquals(Activity.RESULT_OK, Shadows.shadowOf(settings.get()).getResultCode());
        }
    }
}
