package com.gathin.tvhome;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.text.TextUtils;
import android.text.TextPaint;
import android.util.AttributeSet;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

public final class WeatherView extends LocalSnapshotView {
    private final TextPaint paint = new TextPaint(Paint.ANTI_ALIAS_FLAG);
    private JSONObject parsedSource;
    private String state = "unconfigured", unit = "", condition = "", temperature = "";
    private long receivedAt, maxAge = 900000;
    private final String[] days = new String[3], ranges = new String[3], conditions = new String[3];
    private int forecastCount;
    private String visualKey = "", displayedStatus = "";

    public WeatherView(Context context, AttributeSet attrs) {
        super(context, attrs);
    }

    @Override protected String snapshotName() {
        return "weather";
    }

    @Override protected void applySnapshot(JSONObject snapshot, boolean failed) {
        if (parsedSource != snapshot || failed) {
            parsedSource = snapshot;
            parseSnapshot(snapshot, failed);
        }
        boolean current = WeatherReading.current(state, receivedAt, System.currentTimeMillis(), maxAge);
        String effective = current ? state : "live".equals(state) ? "stale" : state;
        String next = effective + ":" + temperature + ":" + condition
                + ":" + java.util.Arrays.toString(days) + ":" + java.util.Arrays.toString(ranges)
                + ":" + java.util.Arrays.toString(conditions);
        if (next.equals(visualKey)) return;
        visualKey = next;
        displayedStatus = getContext().getString("simulated".equals(effective)
                ? R.string.energy_simulated : "live".equals(effective) ? R.string.energy_live
                : "stale".equals(effective) ? R.string.energy_stale : R.string.weather_unavailable);
        setContentDescription(getContext().getString(R.string.weather_title) + ", "
                + displayedStatus + ", " + temperature + ", " + condition);
        invalidate();
    }

    private void parseSnapshot(JSONObject snapshot, boolean failed) {
        forecastCount = 0;
        temperature = "";
        condition = "";
        java.util.Arrays.fill(days, null);
        java.util.Arrays.fill(ranges, null);
        java.util.Arrays.fill(conditions, null);
        state = failed ? "unavailable" : "unconfigured";
        if (snapshot == null) return;
        try {
            state = snapshot.getString("status");
            receivedAt = snapshot.optLong("received_at_ms", 0);
            maxAge = snapshot.optLong("stale_after_ms", 900000);
            if (maxAge < 1000 || maxAge > 900000) throw new JSONException("Invalid weather freshness");
            JSONObject data = snapshot.optJSONObject("data");
            if (data == null) {
                if ("live".equals(state) || "simulated".equals(state)) {
                    throw new JSONException("Live weather snapshot has no forecast");
                }
                return;
            }
            unit = data.getString("temperature_unit");
            double degrees = data.getDouble("temperature");
            if (!("°C".equals(unit) || "°F".equals(unit))
                    || !WeatherReading.validTemperature(degrees)) {
                throw new JSONException("Invalid weather temperature or unit");
            }
            JSONArray forecast = data.getJSONArray("forecast");
            if (forecast.length() < 1 || forecast.length() > 3) {
                throw new JSONException("Invalid forecast length");
            }
            SimpleDateFormat format = new SimpleDateFormat("EEE", Locale.getDefault());
            long previous = 0;
            for (int i = 0; i < forecast.length(); i++) {
                JSONObject day = forecast.getJSONObject(i);
                long at = day.getLong("time_ms");
                double high = day.getDouble("temperature");
                Double low = day.isNull("templow") ? null : day.getDouble("templow");
                if (at <= previous || !WeatherReading.validTemperature(high)
                        || low != null && !WeatherReading.validTemperature(low)) {
                    throw new JSONException("Invalid forecast date or temperature");
                }
                previous = at;
                days[i] = format.format(new Date(at));
                ranges[i] = WeatherReading.range(high, low, Locale.getDefault());
                conditions[i] = day.getString("condition");
            }
            forecastCount = forecast.length();
            temperature = WeatherReading.temperature(degrees, unit, Locale.getDefault());
            condition = WeatherReading.condition(data.getString("condition"));
        } catch (JSONException e) {
            Log.w("TvHome.Weather", "Weather snapshot unavailable: " + e.getMessage());
            state = "unavailable";
            forecastCount = 0;
            temperature = "";
            condition = "";
        }
    }

    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        canvas.save();
        canvas.scale(density, density);
        float width = getWidth() / density, height = getHeight() / density;
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(0xE620303C);
        canvas.drawRoundRect(0, 0, width, height, 12, 12, paint);
        text(canvas, getContext().getString(R.string.weather_title), 14, 21,
                15, 0xFFE2E9EB, Paint.Align.LEFT, width * .6f);
        text(canvas, displayedStatus, width - 14, 21,
                11, 0xFFB0C4C7, Paint.Align.RIGHT, width * .35f);
        if (forecastCount == 0) {
            text(canvas, getContext().getString(R.string.weather_waiting), 14, 65,
                    12, 0xFFB7C8CC, Paint.Align.LEFT, width - 28);
        } else {
            text(canvas, temperature, 14, 58, 25, 0xFFE8F3FF, Paint.Align.LEFT, width * .5f);
            text(canvas, condition, width - 14, 55,
                    11, 0xFFB7C8CC, Paint.Align.RIGHT, width * .45f);
            float column = (width - 28) / forecastCount;
            for (int i = 0; i < forecastCount; i++) {
                float x = 14 + column * (i + .5f);
                text(canvas, days[i], x, 82, 11, 0xFFCFDDE7, Paint.Align.CENTER, column - 4);
                conditionIcon(canvas, conditions[i], x, 95);
                text(canvas, ranges[i], x, 119, 11, 0xFFE8F3FF, Paint.Align.CENTER, column - 4);
            }
        }
        canvas.restore();
    }

    private void conditionIcon(Canvas canvas, String value, float x, float y) {
        paint.setStyle(Paint.Style.FILL);
        if ("sunny".equals(value) || "clear-night".equals(value)) {
            paint.setColor(0xFFFFD071);
            canvas.drawCircle(x, y, 5, paint);
        } else {
            paint.setColor(0xFFC5D9E9);
            canvas.drawRoundRect(x - 9, y - 2, x + 9, y + 4, 3, 3, paint);
            canvas.drawCircle(x - 3, y - 3, 5, paint);
            canvas.drawCircle(x + 3, y - 2, 4, paint);
            if (value.contains("rain") || "pouring".equals(value)) {
                paint.setColor(0xFF73BCEC);
                paint.setStrokeWidth(1.5f);
                canvas.drawLine(x - 4, y + 6, x - 6, y + 9, paint);
                canvas.drawLine(x + 3, y + 6, x + 1, y + 9, paint);
            }
        }
    }

    private void text(Canvas canvas, String value, float x, float y, float size,
                      int color, Paint.Align align, float width) {
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(color);
        paint.setTextSize(size);
        paint.setTextAlign(align);
        canvas.drawText(TextUtils.ellipsize(value, paint, Math.max(0, width),
                TextUtils.TruncateAt.END).toString(), x, y, paint);
    }
}
