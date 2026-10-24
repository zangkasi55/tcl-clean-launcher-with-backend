package com.gathin.tvhome;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.DashPathEffect;
import android.util.AttributeSet;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Locale;

public final class EnergyFlowView extends LocalSnapshotView {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private String state = "unconfigured";
    private long receivedAt;
    private long maxAgeMillis = 120000;
    private double[] metrics;
    private double batterySoc;
    private JSONObject topology;
    private Reading cached;
    private JSONObject parsedSource;
    private boolean parsedFailure;
    private String visualKey = "";
    private final String[] labels = {"--", "--", "--", "--"};
    private String chargeLabel = "--%";
    private String displayedStatus = "";
    private boolean live;
    private final Path arrow = new Path();
    private static final int SOLAR = 0xFFFFAD00, GRID = 0xFF399BD2, BATTERY = 0xFFFF4F92;

    public EnergyFlowView(Context context, AttributeSet attrs) {
        super(context, attrs);
    }

    @Override protected String snapshotName() {
        return "energy";
    }

    @Override protected void applySnapshot(JSONObject snapshot, boolean failed) {
        if (cached == null || parsedSource != snapshot || parsedFailure != failed) {
            parsedSource = snapshot;
            parsedFailure = failed;
            cached = parseSnapshot(snapshot, failed);
        }
        applyReading(cached);
    }

    private static final class Reading {
        final String state;
        final long receivedAt, maxAge;
        final double[] metrics;
        final double charge;
        final JSONObject topology;

        Reading(String state, long receivedAt, long maxAge, double[] metrics, double charge) {
            this(state, receivedAt, maxAge, metrics, charge, null);
        }

        Reading(String state, long receivedAt, long maxAge, double[] metrics,
                double charge, JSONObject topology) {
            this.state = state;
            this.receivedAt = receivedAt;
            this.maxAge = maxAge;
            this.metrics = metrics;
            this.charge = charge;
            this.topology = topology;
        }
    }

    private Reading parseSnapshot(JSONObject snapshot, boolean failed) {
        if (snapshot == null) {
            return new Reading(failed ? "unavailable" : "unconfigured", 0, 15000, null, 0);
        }
        try {
            String state = snapshot.getString("status");
            long sampledAt = snapshot.optLong("sampled_at_ms", 0);
            long receivedAt = snapshot.optLong("received_at_ms", sampledAt);
            long maxAgeMillis = snapshot.optLong("delivery_stale_after_ms",
                    snapshot.optLong("stale_after_ms", 120000));
            if (maxAgeMillis < 1000 || maxAgeMillis > 120000) {
                throw new JSONException("Invalid energy freshness window");
            }
            JSONObject topology = snapshot.optJSONObject("topology");
            if (topology != null) {
                validateTopology(topology);
                return new Reading(state, receivedAt, maxAgeMillis, null, 0, topology);
            }
            JSONObject values = snapshot.optJSONObject("metrics");
            double[] metrics = null;
            double batterySoc = 0;
            if (values != null) {
                double[] candidate = {values.getDouble("solar_w"), values.getDouble("home_w"),
                        values.getDouble("grid_w"), values.getDouble("battery_w")};
                for (double value : candidate) {
                    if (!Double.isFinite(value)) throw new JSONException("Non-finite energy metric");
                }
                batterySoc = values.getDouble("battery_soc");
                if (candidate[0] < 0 || candidate[1] < 0 || !Double.isFinite(batterySoc)
                        || batterySoc < 0 || batterySoc > 100) {
                    throw new JSONException("Invalid energy metric range");
                }
                metrics = candidate;
            }
            return new Reading(state, receivedAt, maxAgeMillis, metrics, batterySoc);
        } catch (JSONException e) {
            Log.w("TvHome.Energy", "Energy snapshot unavailable: " + e.getMessage());
            return new Reading("unavailable", 0, 15000, null, 0);
        }
    }

    private void applyReading(Reading reading) {
        state = reading.state;
        receivedAt = reading.receivedAt;
        maxAgeMillis = reading.maxAge;
        metrics = reading.metrics;
        batterySoc = reading.charge;
        topology = reading.topology;
        live = (metrics != null || topology != null)
                && EnergyReading.current(state, receivedAt, System.currentTimeMillis(), maxAgeMillis);
        String key = EnergyLayout.visualKey(state, live, metrics, batterySoc)
                + (topology == null ? "" : topology.toString());
        if (key.equals(visualKey)) return;
        visualKey = key;
        displayedStatus = statusLabel();
        for (int i = 0; i < labels.length; i++) {
            labels[i] = metrics == null ? "--"
                    : EnergyReading.power(metrics[i], Locale.getDefault());
        }
        chargeLabel = metrics == null ? "--%"
                : String.format(Locale.getDefault(), "%.0f%%", batterySoc);
        if (topology != null) {
            setContentDescription(getContext().getString(R.string.energy_title) + ", "
                    + (live ? topology.optString("status_text") : displayedStatus)
                    + ", Enphase " + topologyPower(optional(topology, "enphase_monitor"), true)
                    + ", Home " + topologyPower(optional(topology, "home_display"), false)
                    + (topology.optBoolean("home_fallback_active") ? ", CT fallback" : ""));
        } else setContentDescription(getContext().getString(R.string.energy_title) + ", " + displayedStatus
                + ", " + getContext().getString(R.string.energy_solar) + " " + labels[0]
                + ", " + getContext().getString(R.string.energy_home) + " " + labels[1]
                + ", " + getContext().getString(R.string.energy_grid) + " " + labels[2]
                + ", " + getContext().getString(R.string.energy_battery) + " " + labels[3]);
        invalidate();
    }

    private static Double optional(JSONObject object, String key) {
        Object value = object.opt(key);
        return value instanceof Number ? ((Number) value).doubleValue() : null;
    }

    private void validateTopology(JSONObject value) throws JSONException {
        JSONObject values = value.getJSONObject("values");
        for (String key : new String[]{"enphase", "ess", "home", "gridImport", "gridExport",
                "charge", "discharge"}) {
            if (!values.isNull(key) && (optional(values, key) == null
                    || !Double.isFinite(values.getDouble(key)) || values.getDouble(key) < 0)) {
                throw new JSONException("Invalid topology power");
            }
        }
        for (String key : new String[]{"enphase_monitor", "home_display", "solar_input", "soc"}) {
            if (!value.isNull(key) && (optional(value, key) == null
                    || !Double.isFinite(value.getDouble(key)))) {
                throw new JSONException("Invalid topology reading");
            }
        }
        if (!value.isNull("soc") && (value.getDouble("soc") < 0 || value.getDouble("soc") > 100)) {
            throw new JSONException("Invalid topology battery percentage");
        }
        if (value.getString("status_text").length() > 96) {
            throw new JSONException("Invalid topology status");
        }
        JSONArray edges = value.getJSONArray("edges");
        if (edges.length() != 5) throw new JSONException("Invalid topology edge count");
        for (int i = 0; i < edges.length(); i++) {
            JSONObject edge = edges.getJSONObject(i);
            int direction = edge.getInt("direction");
            if (direction < -1 || direction > 1) throw new JSONException("Invalid flow direction");
            if (!edge.isNull("watts") && (!Double.isFinite(edge.getDouble("watts"))
                    || edge.getDouble("watts") < 0)) throw new JSONException("Invalid flow power");
        }
    }

    private String topologyPower(Double value, boolean signed) {
        return value == null ? "n/a" : (signed && value < 0 ? "-" : "")
                + EnergyReading.power(value, Locale.getDefault());
    }

    private String statusLabel() {
        if ("simulated".equals(state)) return getContext().getString(R.string.energy_simulated);
        if ("live".equals(state) && !EnergyReading.stale(receivedAt, System.currentTimeMillis(),
                                                       maxAgeMillis)) {
            return getContext().getString(R.string.energy_live);
        }
        if ("live".equals(state) || "stale".equals(state)) {
            return getContext().getString(R.string.energy_stale);
        }
        return getContext().getString(R.string.energy_unavailable);
    }

    private boolean current() {
        return live;
    }

    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        canvas.save();
        canvas.scale(density, density);
        float width = getWidth() / density, height = getHeight() / density;
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(0xE620363C);
        canvas.drawRoundRect(0, 0, width, height, 12, 12, paint);
        if (topology != null) {
            drawTopology(canvas, width);
            canvas.restore();
            return;
        }
        text(canvas, getContext().getString(R.string.energy_title), 14, 21,
                15, 0xFFE2E9EB, Paint.Align.LEFT);
        text(canvas, displayedStatus, width - 14, 21, 11, 0xFFB0C4C7, Paint.Align.RIGHT);
        float x = width / 2, y = height * 0.56f, r = 20;
        float solarY = 42, batteryY = height - 25, gridX = width * 0.16f, homeX = width * 0.84f;
        flow(canvas, x, solarY + r, x, y - 6, SOLAR, metric(0));
        flow(canvas, gridX + r, y, x - 6, y, GRID, metric(2));
        flow(canvas, x + 6, y, homeX - r, y, SOLAR, metric(1));
        flow(canvas, x, batteryY - r, x, y + 6, BATTERY, metric(3));
        node(canvas, x, solarY, r, SOLAR, power(0), null);
        node(canvas, gridX, y, r, GRID, power(2), null);
        node(canvas, homeX, y, r, SOLAR, power(1), null);
        node(canvas, x, batteryY, r, BATTERY, power(3),
                chargeLabel);
        text(canvas, getContext().getString(R.string.energy_solar), x + 42, solarY + 4,
                12, 0xFFB7C8CC, Paint.Align.LEFT);
        text(canvas, getContext().getString(R.string.energy_grid), gridX, y + 32,
                12, 0xFFB7C8CC, Paint.Align.CENTER);
        text(canvas, getContext().getString(R.string.energy_home), homeX, y + 32,
                12, 0xFFB7C8CC, Paint.Align.CENTER);
        text(canvas, getContext().getString(R.string.energy_battery), x + 42, height - 3,
                11, 0xFFB7C8CC, Paint.Align.LEFT);
        canvas.restore();
    }

    private void drawTopology(Canvas canvas, float width) {
        float scale = width / 288f;
        canvas.save();
        canvas.scale(scale, 1);
        text(canvas, getContext().getString(R.string.energy_title), 14, 20,
                13.5f, 0xFFE2E9EB, Paint.Align.LEFT);
        text(canvas, live ? topology.optString("status_text") : displayedStatus, 274, 20,
                8.5f, "ok".equals(topology.optString("status")) && live
                        ? 0xFFA4E7C4 : 0xFFFFCC80, Paint.Align.RIGHT);
        JSONArray edges = topology.optJSONArray("edges");
        for (int i = 0; i < edges.length(); i++) {
            JSONObject edge = edges.optJSONObject(i);
            String id = edge.optString("id");
            float x1 = 144, y1 = 78, x2 = 144, y2 = 86;
            int color = SOLAR;
            if ("enphase-inverter".equals(id)) { x1 = 57; y1 = 63; y2 = 68; }
            else if ("ess-inverter-pv".equals(id)) { x1 = 231; y1 = 63; y2 = 68; }
            else if ("inverter-grid".equals(id)) { x2 = 57; color = GRID; }
            else if ("inverter-battery".equals(id)) color = BATTERY;
            else if ("inverter-home".equals(id)) x2 = 231;
            Double watts = optional(edge, "watts");
            if (edge.optBoolean("planned")) paint.setPathEffect(new DashPathEffect(new float[]{3, 3}, 0));
            flow(canvas, x1, y1, x2, y2, color,
                    live && edge.optBoolean("active") && watts != null
                            ? watts * edge.optInt("direction") : 0);
            paint.setPathEffect(null);
        }
        paint.setStyle(Paint.Style.FILL);
        for (float[] box : new float[][]{{14,29,100,64}, {188,29,274,64}, {118,58,170,78},
                {14,85,100,124}, {101,85,187,124}, {188,85,274,124}}) {
            paint.setColor(0xFF20363C);
            canvas.drawRoundRect(box[0], box[1], box[2], box[3], 5, 5, paint);
        }
        JSONObject values = topology.optJSONObject("values");
        Double enphase = optional(topology, "enphase_monitor"), soc = optional(topology, "soc");
        text(canvas, "Enphase Solar", 57, 39, 9, 0xFFE2E9EB, Paint.Align.CENTER);
        text(canvas, topologyPower(enphase, true), 57, 52, 12, SOLAR, Paint.Align.CENTER);
        text(canvas, enphase == null ? "Unavailable" : enphase < 0 ? "Standby draw" : "Live monitor",
                57, 61, 7, 0xFFB7C8CC, Paint.Align.CENTER);
        text(canvas, "ESS Solar", 231, 39, 9, 0xFFE2E9EB, Paint.Align.CENTER);
        text(canvas, topologyPower(optional(values, "ess"), false), 231, 52, 12, SOLAR, Paint.Align.CENTER);
        text(canvas, topology.optBoolean("planned") ? "Not installed" : "Measured",
                231, 61, 7, 0xFFB7C8CC, Paint.Align.CENTER);
        text(canvas, "ESS inverter", 144, 66, 7, 0xFFE2E9EB, Paint.Align.CENTER);
        text(canvas, topologyPower(optional(topology, "solar_input"), false),
                144, 75, 8, 0xFFE2E9EB, Paint.Align.CENTER);
        text(canvas, "Grid", 57, 95, 10, 0xFFE2E9EB, Paint.Align.CENTER);
        text(canvas, "In " + topologyPower(optional(values, "gridImport"), false),
                57, 107, 9, GRID, Paint.Align.CENTER);
        text(canvas, "Out " + topologyPower(optional(values, "gridExport"), false),
                57, 118, 9, GRID, Paint.Align.CENTER);
        text(canvas, "Battery " + (soc == null ? "n/a" : Math.round(soc) + "%"),
                144, 95, 9, 0xFFE2E9EB, Paint.Align.CENTER);
        text(canvas, "Chg " + topologyPower(optional(values, "charge"), false),
                144, 107, 9, BATTERY, Paint.Align.CENTER);
        text(canvas, "Dis " + topologyPower(optional(values, "discharge"), false),
                144, 118, 9, BATTERY, Paint.Align.CENTER);
        text(canvas, "Home", 231, 95, 10, 0xFFE2E9EB, Paint.Align.CENTER);
        text(canvas, topologyPower(optional(topology, "home_display"), false),
                231, 108, 12, SOLAR, Paint.Align.CENTER);
        text(canvas, topology.optBoolean("home_fallback_active") ? "CT fallback" : "ESS meter",
                231, 120, 8, 0xFFB7C8CC, Paint.Align.CENTER);
        canvas.restore();
    }

    private double metric(int index) {
        return metrics == null || !current() ? 0 : metrics[index];
    }

    private String power(int index) {
        return labels[index];
    }

    private void node(Canvas canvas, float x, float y, float r, int color,
                      String power, String extra) {
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(0xFF20363C);
        canvas.drawCircle(x, y, r, paint);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(1.7f);
        paint.setColor(current() ? color : 0xFF789397);
        canvas.drawCircle(x, y, r, paint);
        text(canvas, power, x, y + (extra == null ? 4 : 12), 11.5f,
                current() ? color : 0xFFB0C0C4, Paint.Align.CENTER);
        if (extra != null) text(canvas, extra, x, y - 7, 11,
                current() ? color : 0xFFB0C0C4, Paint.Align.CENTER);
    }

    private void flow(Canvas canvas, float x1, float y1, float x2, float y2,
                      int color, double power) {
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(1.2f);
        int direction = EnergyReading.direction(power);
        paint.setColor(direction == 0 ? 0x55789397 : color);
        canvas.drawLine(x1, y1, x2, y2, paint);
        if (direction == 0) return;
        if (direction < 0) {
            float swap = x1; x1 = x2; x2 = swap;
            swap = y1; y1 = y2; y2 = swap;
        }
        double length = Math.hypot(x2 - x1, y2 - y1);
        if (length < 1) return;
        float dx = (float) ((x2 - x1) / length), dy = (float) ((y2 - y1) / length);
        arrow.reset();
        arrow.moveTo(x2, y2);
        arrow.lineTo(x2 - dx * 6 + dy * 3, y2 - dy * 6 - dx * 3);
        arrow.lineTo(x2 - dx * 6 - dy * 3, y2 - dy * 6 + dx * 3);
        arrow.close();
        paint.setStyle(Paint.Style.FILL);
        canvas.drawPath(arrow, paint);
    }

    private void text(Canvas canvas, String value, float x, float y, float size,
                      int color, Paint.Align align) {
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(color);
        paint.setTextSize(size);
        paint.setTextAlign(align);
        canvas.drawText(value, x, y, paint);
    }
}
