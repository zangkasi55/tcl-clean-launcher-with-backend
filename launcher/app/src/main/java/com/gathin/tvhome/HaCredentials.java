package com.gathin.tvhome;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.KeyStore;
import java.util.UUID;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class HaCredentials {
    private static final String NAME = "ha_connection_v1";
    private static final String ALIAS = "tvhome.ha.v1";
    private final SharedPreferences preferences;
    private final Keys keys;

    interface Keys {
        SecretKey key(boolean create) throws GeneralSecurityException, IOException;
        void delete() throws GeneralSecurityException, IOException;
    }

    static final class Connection {
        final String origin, token;

        Connection(String origin, String token) {
            this.origin = HaOrigin.validate(origin);
            this.token = HaOrigin.token(token);
        }
    }

    HaCredentials(Context context) {
        this(context.getSharedPreferences(NAME, Context.MODE_PRIVATE), new AndroidKeys());
    }

    HaCredentials(SharedPreferences preferences, Keys keys) {
        this.preferences = preferences;
        this.keys = keys;
    }

    boolean direct() {
        // A damaged selection must not silently expose backend snapshots.
        return preferences.contains("direct")
                && !Boolean.FALSE.equals(preferences.getAll().get("direct"));
    }

    String revision() {
        Object value = preferences.getAll().get("revision");
        return value instanceof String ? (String) value : "";
    }

    Connection load() throws GeneralSecurityException, IOException, JSONException {
        if (!Boolean.TRUE.equals(preferences.getAll().get("direct"))) {
            throw new GeneralSecurityException("Direct connection is not configured");
        }
        Object stored = preferences.getAll().get("record");
        if (!(stored instanceof String) || ((String) stored).length() > 32768) {
            throw new GeneralSecurityException("Missing encrypted HA credentials");
        }
        JSONObject record = new JSONObject((String) stored);
        String origin = HaOrigin.validate(record.getString("origin"));
        try {
            byte[] iv = Base64.decode(record.getString("iv"), Base64.NO_WRAP);
            if (iv.length != 12) throw new GeneralSecurityException("Invalid credential IV");
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, keys.key(false), new GCMParameterSpec(128, iv));
            cipher.updateAAD(origin.getBytes(StandardCharsets.UTF_8));
            String token = new String(cipher.doFinal(Base64.decode(
                    record.getString("ciphertext"), Base64.NO_WRAP)), StandardCharsets.UTF_8);
            return new Connection(origin, token);
        } catch (IllegalArgumentException e) {
            throw new GeneralSecurityException("Invalid encrypted HA credentials");
        }
    }

    void save(String address, String accessToken)
            throws GeneralSecurityException, IOException, JSONException {
        Connection connection = new Connection(address, accessToken);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, keys.key(true));
        cipher.updateAAD(connection.origin.getBytes(StandardCharsets.UTF_8));
        JSONObject record = new JSONObject().put("origin", connection.origin)
                .put("iv", Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP))
                .put("ciphertext", Base64.encodeToString(cipher.doFinal(
                        connection.token.getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP));
        if (!preferences.edit().putString("record", record.toString())
                .putBoolean("direct", true).putString("revision", UUID.randomUUID().toString())
                .commit()) {
            throw new IOException("Could not persist HA credentials");
        }
    }

    void useBackend() throws IOException {
        if (!preferences.edit().putBoolean("direct", false)
                .putString("revision", UUID.randomUUID().toString()).commit()) {
            throw new IOException("Could not persist the card source");
        }
    }

    void forget() throws IOException, GeneralSecurityException {
        keys.delete();
        if (!preferences.edit().clear().putBoolean("direct", false)
                .putString("revision", UUID.randomUUID().toString()).commit()) {
            throw new IOException("Could not erase HA credentials");
        }
    }

    private static final class AndroidKeys implements Keys {
        @Override public SecretKey key(boolean create) throws GeneralSecurityException, IOException {
            KeyStore store = store();
            if (store.containsAlias(ALIAS)) {
                java.security.Key key = store.getKey(ALIAS, null);
                if (key instanceof SecretKey) return (SecretKey) key;
                throw new GeneralSecurityException("Invalid HA credential key");
            }
            if (!create) throw new GeneralSecurityException("HA credential key is unavailable");
            KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,
                    "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setKeySize(256).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
            return generator.generateKey();
        }

        @Override public void delete() throws GeneralSecurityException, IOException {
            store().deleteEntry(ALIAS);
        }
    }

    private static KeyStore store() throws GeneralSecurityException, IOException {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        return store;
    }
}
