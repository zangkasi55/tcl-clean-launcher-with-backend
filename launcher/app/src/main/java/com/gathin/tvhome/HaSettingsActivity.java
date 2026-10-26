package com.gathin.tvhome;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Bundle;
import android.util.Log;
import android.view.WindowManager;
import android.widget.EditText;
import android.widget.TextView;

import org.json.JSONException;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.security.ProviderException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class HaSettingsActivity extends Activity {
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private EditText origin, token;
    private TextView status;
    private HaCredentials credentials;
    private boolean saving;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        setContentView(R.layout.activity_ha_settings);
        credentials = new HaCredentials(this);
        origin = findViewById(R.id.haOrigin);
        token = findViewById(R.id.haToken);
        status = findViewById(R.id.haStatus);
        status.setText(credentials.direct() ? R.string.ha_direct_selected : R.string.ha_backend_selected);
        findViewById(R.id.haSave).setOnClickListener(v -> save());
        findViewById(R.id.haBackend).setOnClickListener(v -> changeSource(false));
        findViewById(R.id.haForget).setOnClickListener(v ->
                new AlertDialog.Builder(this).setMessage(R.string.ha_forget_warning)
                        .setPositiveButton(android.R.string.ok, (dialog, which) -> changeSource(true))
                        .setNegativeButton(android.R.string.cancel, null).show());
        origin.requestFocus();
    }

    private void save() {
        if (saving) return;
        final String address, accessToken;
        try {
            address = HaOrigin.validate(origin.getText().toString());
            accessToken = HaOrigin.token(token.getText().toString());
        } catch (IllegalArgumentException e) {
            status.setText(R.string.ha_invalid_input);
            return;
        }
        begin();
        token.setText("");
        worker.execute(() -> {
            try {
                credentials.save(address, accessToken);
                complete();
            } catch (GeneralSecurityException | IOException | JSONException
                     | ProviderException | SecurityException e) {
                failed();
            }
        });
    }

    private void changeSource(boolean forget) {
        if (saving) return;
        begin();
        token.setText("");
        worker.execute(() -> {
            try {
                if (forget) credentials.forget();
                else credentials.useBackend();
                complete();
            } catch (GeneralSecurityException | IOException | ProviderException | SecurityException e) {
                failed();
            }
        });
    }

    private void begin() {
        saving = true;
        status.setText(R.string.ha_saving);
        for (int id : new int[]{R.id.haSave, R.id.haBackend, R.id.haForget}) {
            findViewById(id).setEnabled(false);
        }
    }

    private void complete() {
        runOnUiThread(() -> {
            if (isDestroyed()) return;
            setResult(RESULT_OK);
            finish();
        });
    }

    private void failed() {
        Log.w("HaSettings", "credential_write_failed");
        runOnUiThread(() -> {
            if (isDestroyed()) return;
            saving = false;
            status.setText(R.string.ha_save_failed);
            for (int id : new int[]{R.id.haSave, R.id.haBackend, R.id.haForget}) {
                findViewById(id).setEnabled(true);
            }
            findViewById(R.id.haSave).requestFocus();
        });
    }

    @Override public void onBackPressed() {
        if (!saving) super.onBackPressed();
    }

    @Override protected void onSaveInstanceState(Bundle state) {
        // Neither user-entered credential is eligible for saved UI state.
        super.onSaveInstanceState(state);
        state.clear();
    }

    @Override protected void onDestroy() {
        token.setText("");
        worker.shutdown();
        super.onDestroy();
    }
}
