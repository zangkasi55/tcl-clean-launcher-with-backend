package com.gathin.tvhome;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

import java.io.IOException;
import java.security.GeneralSecurityException;

import static org.junit.Assert.*;

@RunWith(RobolectricTestRunner.class)
@Config(manifest = Config.NONE, sdk = 34)
public class HaCredentialsTest {
    private SharedPreferences preferences;
    private HaStorageTestSupport.Keys keys;
    private HaCredentials storage;

    @Before public void setUp() {
        preferences = RuntimeEnvironment.getApplication()
                .getSharedPreferences("ha_connection_v1", Context.MODE_PRIVATE);
        assertTrue(preferences.edit().clear().commit());
        keys = new HaStorageTestSupport.Keys();
        storage = new HaCredentials(preferences, keys);
    }

    private void save() throws Exception {
        storage.save(HaStorageTestSupport.ORIGIN, HaStorageTestSupport.TOKEN);
    }

    @Test public void savesAuthenticatedCiphertextAndLoadsItAfterRecreation() throws Exception {
        assertFalse(storage.direct());
        save();
        assertTrue(storage.direct());
        assertFalse(storage.revision().isEmpty());
        assertFalse(preferences.getAll().toString().contains(HaStorageTestSupport.TOKEN));
        HaCredentials restored = new HaCredentials(preferences, keys);
        assertEquals(HaStorageTestSupport.ORIGIN, restored.load().origin);
        assertEquals(HaStorageTestSupport.TOKEN, restored.load().token);
        assertEquals(1, keys.creations);
    }

    @Test public void rollbackIsExplicitAndRetainsEncryptedCredentials() throws Exception {
        save();
        String firstRevision = storage.revision();
        String record = preferences.getString("record", "");
        storage.useBackend();
        assertFalse(storage.direct());
        assertNotEquals(firstRevision, storage.revision());
        assertEquals(record, preferences.getString("record", ""));
        assertThrows(GeneralSecurityException.class, storage::load);
        assertTrue(preferences.edit().putBoolean("direct", true).commit());
        assertEquals(HaStorageTestSupport.TOKEN, storage.load().token);
    }

    @Test public void missingKeyDoesNotRegenerateOrFallBackToBackend() throws Exception {
        save();
        keys.value = null;
        assertThrows(GeneralSecurityException.class, storage::load);
        assertEquals(1, keys.creations);
        assertTrue(storage.direct());
    }

    @Test public void changedOriginOrCiphertextFailsAuthentication() throws Exception {
        save();
        String original = preferences.getString("record", "");
        JSONObject record = new JSONObject(original).put("origin", "https://different.example.test");
        assertTrue(preferences.edit().putString("record", record.toString()).commit());
        assertThrows(GeneralSecurityException.class, storage::load);
        record = new JSONObject(original).put("ciphertext", "AAAA");
        assertTrue(preferences.edit().putString("record", record.toString()).commit());
        assertThrows(GeneralSecurityException.class, storage::load);
        assertTrue(storage.direct());
    }

    @Test public void malformedSelectionAndRecordFailClosed() throws Exception {
        assertTrue(preferences.edit().putString("direct", "damaged").commit());
        assertTrue(storage.direct());
        assertThrows(GeneralSecurityException.class, storage::load);
        assertTrue(preferences.edit().putBoolean("direct", true)
                .putString("record", "x".repeat(32769)).commit());
        assertThrows(GeneralSecurityException.class, storage::load);
        assertTrue(preferences.edit().putString("record", "{}").commit());
        assertThrows(org.json.JSONException.class, storage::load);
    }

    @Test public void persistenceFailuresAreNotReportedAsSuccess() throws Exception {
        HaCredentials failed = new HaCredentials(
                HaStorageTestSupport.failingCommits(preferences), keys);
        assertThrows(IOException.class,
                () -> failed.save(HaStorageTestSupport.ORIGIN, HaStorageTestSupport.TOKEN));
        assertFalse(storage.direct());
        save();
        String revision = storage.revision();
        assertThrows(IOException.class, failed::useBackend);
        assertTrue(storage.direct());
        assertEquals(revision, storage.revision());
    }

    @Test public void keyCreationFailurePreservesExistingSelection() throws Exception {
        keys.failCreate = true;
        assertThrows(IOException.class, this::save);
        assertFalse(storage.direct());
        assertFalse(preferences.contains("record"));
    }

    @Test public void forgetErasesTheKeyRecordAndSourceWithANewRevision() throws Exception {
        save();
        String revision = storage.revision();
        storage.forget();
        assertNull(keys.value);
        assertFalse(preferences.contains("record"));
        assertFalse(storage.direct());
        assertNotEquals(revision, storage.revision());
    }

    @Test public void failedKeyDeletionDoesNotClearTheRecordOrChangeSource() throws Exception {
        save();
        String revision = storage.revision();
        keys.failDelete = true;
        assertThrows(IOException.class, storage::forget);
        assertTrue(storage.direct());
        assertEquals(revision, storage.revision());
        assertEquals(HaStorageTestSupport.TOKEN, storage.load().token);
    }

    @Test public void failedForgetCommitLeavesCredentialsUndecryptableAndReportsFailure()
            throws Exception {
        save();
        HaCredentials failed = new HaCredentials(
                HaStorageTestSupport.failingCommits(preferences), keys);
        assertThrows(IOException.class, failed::forget);
        assertNull(keys.value);
        assertTrue(storage.direct());
        assertThrows(GeneralSecurityException.class, storage::load);
        assertEquals(1, keys.creations);
    }
}
