package com.gathin.tvhome;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.text.TextUtils;
import android.text.TextPaint;
import android.util.AttributeSet;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Arrays;
import java.util.Locale;

public final class VehicleView extends LocalSnapshotView {
    private final TextPaint paint = new TextPaint(Paint.ANTI_ALIAS_FLAG);
    private JSONObject parsedSource;
    private String state = "unconfigured", visualKey = "", activity = "", displayedStatus = "";
    private Double soc;
    private Boolean charging;
    private long receivedAt, maxAge = 900000;
    private boolean current;

    public VehicleView(Context context, AttributeSet attrs) {
        super(context, attrs);
    }

    @Override protected String snapshotName() {
        return "vehicle";
    }

    @Override protected float panelWidth(float available, float screenWidth) {
        return VehicleReading.width(available, screenWidth);
    }

    @Override protected void applySnapshot(JSONObject snapshot, boolean failed) {
        if (parsedSource != snapshot || failed) {
            parsedSource = snapshot;
            parseSnapshot(snapshot, failed);
        }
        current = VehicleReading.current(state, receivedAt, System.currentTimeMillis(), maxAge);
        String effective = current ? state : "live".equals(state) ? "stale" : state;
        String next = effective + ":" + soc + ":" + charging;
        if (next.equals(visualKey)) return;
        visualKey = next;
        displayedStatus = getContext().getString("simulated".equals(effective)
                ? R.string.energy_simulated : "live".equals(effective) ? R.string.energy_live
                : "stale".equals(effective) ? R.string.energy_stale : R.string.vehicle_unavailable);
        activity = getContext().getString(charging == null ? R.string.vehicle_activity_unknown
                : charging ? R.string.vehicle_charging : R.string.vehicle_not_charging);
        if (!current && charging != null) {
            activity = getContext().getString(R.string.vehicle_last_activity, activity);
        }
        setContentDescription(getContext().getString(R.string.vehicle_description,
                VehicleReading.percentage(soc, Locale.getDefault()), activity, displayedStatus));
        invalidate();
    }

    private void parseSnapshot(JSONObject snapshot, boolean failed) {
        state = failed ? "unavailable" : "unconfigured";
        soc = null;
        charging = null;
        receivedAt = 0;
        if (snapshot == null) return;
        try {
            if (!(snapshot.get("status") instanceof String)
                    || !(snapshot.get("source") instanceof String)) {
                throw new JSONException("Invalid vehicle status or source type");
            }
            state = snapshot.getString("status");
            if (!Arrays.asList("live", "simulated", "stale", "unavailable", "unconfigured",
                    "disabled").contains(state)) throw new JSONException("Invalid vehicle status");
            String source = snapshot.getString("source");
            if (!Arrays.asList("home_assistant", "sandbox").contains(source)
                    || "live".equals(state) && !"home_assistant".equals(source)
                    || "simulated".equals(state) && !"sandbox".equals(source)) {
                throw new JSONException("Invalid vehicle source");
            }
            if (!snapshot.isNull("received_at_ms")) {
                receivedAt = VehicleReading.timestamp(snapshot.get("received_at_ms"));
            }
            maxAge = VehicleReading.freshness(snapshot.get("stale_after_ms"));
            if (("live".equals(state) || "simulated".equals(state)) && receivedAt == 0) {
                throw new JSONException("Live vehicle snapshot has no observation timestamp");
            }
            if (!snapshot.isNull("data") && !(snapshot.get("data") instanceof JSONObject)) {
                throw new JSONException("Invalid vehicle reading type");
            }
            JSONObject data = snapshot.optJSONObject("data");
            if (data == null) {
                if ("live".equals(state) || "simulated".equals(state)) {
                    throw new JSONException("Live vehicle snapshot has no readings");
                }
                return;
            }
            Object percentage = data.get("battery_soc"), active = data.get("charging");
            if (!(percentage instanceof Number) || !(active instanceof Boolean)
                    || !VehicleReading.validSoc(((Number) percentage).doubleValue())) {
                throw new JSONException("Invalid vehicle battery or charging state");
            }
            soc = ((Number) percentage).doubleValue();
            charging = (Boolean) active;
        } catch (JSONException | IllegalArgumentException e) {
            Log.w("TvHome.Vehicle", "Vehicle snapshot unavailable: " + e.getMessage());
            state = "unavailable";
            soc = null;
            charging = null;
        }
    }

    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        canvas.save();
        canvas.scale(density, density);
        float width = getWidth() / density, height = getHeight() / density;
        int color = current ? Boolean.TRUE.equals(charging) ? 0xFFA4E7C4 : 0xFFE8F3FF
                : 0xFFB0C4C7;
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(0xE620303C);
        canvas.drawRoundRect(0, 0, width, height, 12, 12, paint);
        text(canvas, getContext().getString(R.string.vehicle_title), width, 22, 16, 0xFFE8F3FF);
        float left = width * .2f, right = width * .77f;
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(1.5f);
        paint.setColor(color);
        canvas.drawRoundRect(left, 36, right, 54, 3, 3, paint);
        paint.setStyle(Paint.Style.FILL);
        canvas.drawRoundRect(right + 1, 41, right + 4, 49, 1, 1, paint);
        if (soc != null) {
            canvas.drawRoundRect(left + 3, 39, left + 3 + (right - left - 6) * soc.floatValue() / 100,
                    51, 1, 1, paint);
        }
        text(canvas, VehicleReading.percentage(soc, Locale.getDefault()), width, 85, 27, color);
        text(canvas, activity, width, 105, 10, color);
        text(canvas, displayedStatus, width, height - 9, 9, 0xFFB0C4C7);
        canvas.restore();
    }

    private void text(Canvas canvas, String value, float width, float y, float size, int color) {
        paint.setStyle(Paint.Style.FILL);
        paint.setTextAlign(Paint.Align.CENTER);
        paint.setTextSize(size);
        paint.setColor(color);
        canvas.drawText(TextUtils.ellipsize(value, paint, Math.max(0, width - 12),
                TextUtils.TruncateAt.END).toString(), width / 2, y, paint);
    }
}
