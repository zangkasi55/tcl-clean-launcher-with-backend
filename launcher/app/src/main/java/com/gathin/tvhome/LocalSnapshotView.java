package com.gathin.tvhome;

import android.content.Context;
import android.util.AttributeSet;
import android.util.Log;
import android.view.View;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

abstract class LocalSnapshotView extends View {
    protected final float density;
    private ExecutorService loader;
    private int generation;
    private String stamp = "";
    private Snapshot cached = new Snapshot(null, false);

    private static final class Snapshot {
        final JSONObject data;
        final boolean failed;

        Snapshot(JSONObject data, boolean failed) {
            this.data = data;
            this.failed = failed;
        }
    }

    private final Runnable refresh = new Runnable() {
        @Override public void run() {
            if (loader == null || loader.isShutdown() || !isShown()) return;
            final int version = generation;
            loader.execute(() -> {
                Snapshot result = readSnapshot();
                post(() -> {
                    if (version != generation || !isAttachedToWindow()) return;
                    applySnapshot(result.data, result.failed);
                    if (isShown() && getWindowVisibility() == VISIBLE) {
                        postDelayed(refresh, 1000);
                    }
                });
            });
        }
    };

    LocalSnapshotView(Context context, AttributeSet attrs) {
        super(context, attrs);
        density = getResources().getDisplayMetrics().density;
        setFocusable(false);
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
    }

    protected abstract String snapshotName();
    protected abstract void applySnapshot(JSONObject snapshot, boolean failed);

    @Override protected void onAttachedToWindow() {
        super.onAttachedToWindow();
        loader = Executors.newSingleThreadExecutor();
        restartRefresh();
    }

    @Override protected void onDetachedFromWindow() {
        removeCallbacks(refresh);
        generation++;
        if (loader != null) loader.shutdownNow();
        loader = null;
        super.onDetachedFromWindow();
    }

    @Override protected void onWindowVisibilityChanged(int visibility) {
        super.onWindowVisibilityChanged(visibility);
        restartRefresh();
    }

    @Override protected void onVisibilityChanged(View changedView, int visibility) {
        super.onVisibilityChanged(changedView, visibility);
        restartRefresh();
    }

    private void restartRefresh() {
        if (refresh == null) return;
        generation++;
        removeCallbacks(refresh);
        if (loader != null && isAttachedToWindow() && isShown()
                && getWindowVisibility() == VISIBLE) post(refresh);
    }

    @Override protected void onMeasure(int widthSpec, int heightSpec) {
        int available = MeasureSpec.getSize(widthSpec);
        float screenWidth = getResources().getDisplayMetrics().widthPixels / density;
        int width = Math.round(EnergyLayout.width(available / density, screenWidth) * density);
        setMeasuredDimension(resolveSize(width, widthSpec),
                resolveSize(Math.round(EnergyLayout.HEIGHT * density), heightSpec));
    }

    private synchronized Snapshot readSnapshot() {
        File dir = Config.dir(getContext());
        File file = dir == null ? null : new File(dir, snapshotName() + ".json");
        if (file == null || !file.isFile()) {
            stamp = "";
            cached = new Snapshot(null, false);
            return cached;
        }
        String next = file.length() + "@" + file.lastModified();
        if (next.equals(stamp)) return cached;
        stamp = next;
        try {
            if (file.length() > 16384) throw new IOException("Panel snapshot exceeds 16 KB");
            byte[] bytes = new byte[(int) file.length()];
            try (FileInputStream input = new FileInputStream(file)) {
                int count = 0, n;
                while (count < bytes.length && (n = input.read(bytes, count, bytes.length - count)) > 0) {
                    count += n;
                }
                if (count != bytes.length) throw new IOException("Incomplete panel snapshot");
            }
            cached = new Snapshot(new JSONObject(new String(bytes, StandardCharsets.UTF_8)), false);
        } catch (IOException | JSONException e) {
            Log.w("TvHome.Panels", snapshotName() + " snapshot unavailable: " + e.getMessage());
            cached = new Snapshot(null, true);
        }
        return cached;
    }
}
