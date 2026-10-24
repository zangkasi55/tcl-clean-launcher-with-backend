"use strict";
import {cropRectangle, drawPhoto, openPhoto, preparePhotoUpload} from "./photo-tools.js?v=2.6.23";

const $ = (id) => document.getElementById(id);
const YOUTUBE = "com.google.android.youtube.tv/com.google.android.apps.youtube.tv.activity.ShellActivity";
let csrf = "", status = null, theme = null, dirty = false, polling = false, initialized = false;
let loginRequired = true, sessionRefresh = null;
let screenUrl = null;
let energyEvents = null;
let previewKey = "", energyKey = "", factsKey = "", eventsKey = "";
let previewAppsPage = false;
let weatherKey = "";
let wallpaperCrop = null, cropDrag = null;
const themeColors = [["background-color", "background_color"], ["gradient-color", "gradient_color"], ["clock-color", "clock_color"], ["date-color", "date_color"], ["input-color", "input_color"], ["wifi-color", "wifi_color"], ["settings-color", "settings_color"]];
const energyBindings = [["solar", "solar_entity"], ["home", "home_entity"], ["grid", "grid_entity"], ["battery", "battery_entity"], ["soc", "battery_soc_entity"], ["grid-import", "grid_import_entity"], ["grid-export", "grid_export_entity"], ["battery-discharge", "battery_discharge_entity"], ["battery-charge", "battery_charge_entity"]];

class ApiError extends Error {
  constructor(message, code) { super(message); this.code = code; }
}
async function api(path, options = {}, renewSession = true) {
  const headers = {...options.headers};
  if (csrf) headers["X-CSRF-Token"] = csrf;
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(`api/${path}`, {...options, headers, credentials: "same-origin"});
  if (!response.headers.get("Content-Type")?.includes("application/json")) {
    throw new ApiError(`The controller returned an unexpected response (${response.status}). Check the connection and retry.`, response.status);
  }
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path === "session") {
      loginRequired = true; showLogin();
    } else if (response.status === 401 && path !== "login") {
      if (!loginRequired && renewSession) {
        await bootstrapSession();
        return api(path, options, false);
      }
      if (loginRequired) showLogin();
    }
    const detail = Array.isArray(result.detail) ? result.detail.map(e => e.msg).join("; ") : result.detail;
    throw new ApiError(detail || `Request failed (${response.status})`, response.status);
  }
  return result;
}
function bootstrapSession() {
  if (!sessionRefresh) sessionRefresh = api("session", {}, false).then(result => {
    csrf = result.csrf_token; loginRequired = result.login_required;
    return result;
  }).finally(() => { sessionRefresh = null; });
  return sessionRefresh;
}
function message(text, error = false, feedback = null) {
  $(error ? "error" : "notice").textContent = text;
  $(error ? "error" : "notice").hidden = false;
  $(error ? "notice" : "error").hidden = true;
  if (feedback) {
    feedback.textContent = text; feedback.hidden = false;
    feedback.classList.toggle("upload-error", error);
  }
}
async function action(button, work, feedback = null) {
  if (button) button.disabled = true;
  $("error").hidden = true;
  try { await work(); }
  catch (error) { message(error.message, true, feedback); }
  finally { if (button) button.disabled = false; }
}
async function themeAction(button, work, feedback = null) {
  const controls = $("theme-controls");
  if (controls.disabled) return;
  controls.disabled = true;
  controls.setAttribute("aria-busy", "true");
  document.querySelectorAll("[data-theme-feedback]").forEach(node => { node.hidden = true; });
  try { await action(button, work, feedback); }
  finally { controls.disabled = false; controls.setAttribute("aria-busy", "false"); }
}
function showLogin() {
  csrf = ""; initialized = false;
  $("boot-panel").hidden = true;
  $("login-panel").hidden = false; $("dashboard").hidden = true;
  if (energyEvents) energyEvents.close();
  energyEvents = null;
}
function appLabel(packageName) {
  return status?.apps.find(a => a.package === packageName)?.label || packageName || "Unknown";
}
function uniqueApps() {
  return [...new Map(status.apps.map(a => [a.package, a])).values()];
}
function option(value, text) {
  const node = document.createElement("option"); node.value = value; node.textContent = text; return node;
}
function markDirty() {
  dirty = true; $("theme-dirty").textContent = "Preview changed / not applied to the TV";
  renderPreview();
}
function previewTile(app, fallbackLabel) {
  const tile = document.createElement("div"); tile.className = "preview-tile";
  if (app) tile.dataset.component = app.component;
  const artwork = app && theme.tile_artwork[app.component];
  if (artwork) {
    const image = document.createElement("img"); image.src = `api/assets/${encodeURIComponent(artwork)}`;
    image.alt = app.label; tile.append(image);
  } else tile.textContent = app?.label || fallbackLabel;
  return tile;
}
function renderImageSelections() {
  for (const kind of ["wallpaper", "artwork"]) {
    const selected = kind === "wallpaper" ? theme.wallpaper_file : theme.tile_artwork[YOUTUBE] || null;
    const saved = kind === "wallpaper" ? status.theme.wallpaper_file : status.theme.tile_artwork[YOUTUBE] || null;
    const applied = status.applied_theme && (kind === "wallpaper" ? status.applied_theme.wallpaper_file : status.applied_theme.tile_artwork[YOUTUBE] || null);
    const state = selected !== saved ? "Not saved. Choose Save to system or Apply to TV."
      : !status.applied_theme || selected !== applied ? "Saved to system. Not yet applied to TV."
      : "Saved to system and applied to TV.";
    const description = selected ? "Image uploaded." : kind === "wallpaper" ? "Plain background." : "Original YouTube artwork.";
    $(`${kind}-state`).textContent = kind === "wallpaper" && wallpaperCrop
      ? "New photo awaiting crop. Choose Upload & save to system to keep it."
      : `${description} ${state}`;
    const thumbnail = $(`${kind}-thumbnail`);
    thumbnail.hidden = !selected;
    if (selected) {
      const src = `api/assets/${encodeURIComponent(selected)}`;
      if (thumbnail.getAttribute("src") !== src) thumbnail.src = src;
    } else thumbnail.removeAttribute("src");
  }
}
function renderPreview() {
  renderImageSelections();
  const nextKey = JSON.stringify([theme, status.apps]);
  if (nextKey === previewKey) { renderEnergy(); return; }
  previewKey = nextKey;
  const catalog = status.apps.filter(app => app.package !== "com.gathin.tvhome");
  $("preview-count").textContent = `${theme.dock_apps.length} favorites / ${catalog.length} apps preserved`;
  const preview = $("tv-preview");
  preview.style.backgroundColor = theme.background_color;
  preview.style.backgroundImage = theme.wallpaper_file ? `url("api/assets/${encodeURIComponent(theme.wallpaper_file)}")` : "none";
  $("preview-gradient").style.background = `linear-gradient(180deg, ${theme.gradient_color}00, ${theme.gradient_color}${Math.round(theme.gradient_opacity * 255 / 100).toString(16).padStart(2, "0")})`;
  $("gradient-strength").textContent = `${theme.gradient_opacity}%`;
  $("ambient-glow-state").textContent = theme.ambient_glow !== false
    ? "The TV adds a glow from the focused app artwork; it can tint faces. This TV-only effect is separate from the gradient preview."
    : "App-colored tint is disabled on the TV. Focus highlights and app names remain visible; your gradient setting is unchanged.";
  $("preview-clock").textContent = new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
  $("preview-date").textContent = new Date().toLocaleDateString([], {weekday: "short", day: "numeric", month: "short"});
  $("preview-clock").style.color = theme.clock_color;
  $("preview-date").style.color = `${theme.date_color}a6`;
  for (const key of ["input", "wifi", "settings"]) $(`preview-${key}`).style.color = theme[`${key}_color`];
  const dockApps = theme.dock_apps.map(packageName => status.apps.find(a => a.package === packageName));
  $("preview-dock").replaceChildren(...dockApps.map((app, index) => previewTile(app, appLabel(theme.dock_apps[index]))));
  const appOrder = new Map(theme.app_order.map((component, index) => [component, index]));
  const allApps = [...catalog].sort((a, b) =>
    (appOrder.get(a.component) ?? Number.MAX_SAFE_INTEGER) - (appOrder.get(b.component) ?? Number.MAX_SAFE_INTEGER)
    || a.label.localeCompare(b.label));
  $("preview-app-grid").style.setProperty("--app-columns", theme.columns);
  $("preview-app-grid").replaceChildren(...allApps.map(app => previewTile(app, app.label)));
  $("favorites").replaceChildren(...theme.dock_apps.map((packageName, index) => {
    const item = document.createElement("li"); item.className = "favorite-item";
    const number = document.createElement("span"); number.className = "favorite-number"; number.textContent = String(index + 1).padStart(2, "0");
    const name = document.createElement("span"); name.className = "favorite-name"; name.textContent = appLabel(packageName);
    item.append(number, name);
    for (const [label, delta] of [["Up", -1], ["Down", 1]]) {
      const button = document.createElement("button"); button.textContent = label;
      button.setAttribute("aria-label", `Move ${name.textContent} ${label.toLowerCase()}`);
      button.disabled = index + delta < 0 || index + delta >= theme.dock_apps.length;
      button.onclick = () => {
        [theme.dock_apps[index], theme.dock_apps[index + delta]] = [theme.dock_apps[index + delta], theme.dock_apps[index]];
        markDirty();
      };
      item.append(button);
    }
    const remove = document.createElement("button"); remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove ${name.textContent} from favorites`);
    remove.disabled = theme.dock_apps.length === 1;
    remove.onclick = () => { theme.dock_apps.splice(index, 1); markDirty(); };
    item.append(remove); return item;
  }));
  $("favorite-add").replaceChildren(...uniqueApps().filter(a => !theme.dock_apps.includes(a.package)).map(a => option(a.package, a.label)));
  renderEnergy();
  renderWeather();
}
function showPreviewPage(allApps) {
  previewAppsPage = allApps;
  $("preview-home").hidden = allApps;
  $("preview-apps").hidden = !allApps;
  $("tv-preview").classList.toggle("all-apps-page", allApps);
  $("preview-open-apps").textContent = allApps ? "Preview Home" : "Preview All apps";
  (allApps ? $("preview-app-grid") : $("preview-open-apps")).focus();
}
function powerText(watts) {
  if (!Number.isFinite(watts)) return "--";
  const value = Math.abs(watts);
  return value >= 1000 ? `${(value / 1000).toFixed(2)} kW` : `${Math.round(value)} W`;
}
function renderEnergy() {
  const reading = status.energy;
  const nextKey = JSON.stringify([theme.energy_panel, reading?.source, reading?.status,
    reading?.metrics, reading?.topology, reading?.message, status.energy_delivery_error]);
  if (nextKey === energyKey) return;
  energyKey = nextKey;
  $("energy-panel").hidden = theme.energy_panel === false;
  $("preview-middle").hidden = theme.energy_panel === false && theme.weather_panel === false;
  $("tv-preview").classList.toggle("has-energy", theme.energy_panel !== false);
  const metrics = reading?.metrics;
  const current = reading && ["live", "simulated"].includes(reading.status);
  $("energy-panel").classList.toggle("is-stale", !current);
  $("energy-panel").classList.toggle("topology-mode", !!reading?.topology);
  $("energy-diagram").hidden = !!reading?.topology;
  $("energy-topology").hidden = !reading?.topology;
  if (reading?.topology) {
    renderEnergyTopology(reading.topology, !!current);
    return;
  }
  delete $("energy-panel").dataset.quality;
  $("energy-panel").classList.toggle("is-stale", !current);
  $("energy-status").textContent = reading?.source === "sandbox" && current ? "Simulated" : reading?.status || "Not configured";
  for (const [node, key] of [["solar", "solar_w"], ["home", "home_w"], ["grid", "grid_w"], ["battery", "battery_w"]]) {
    $(`energy-${node}-value`).textContent = powerText(metrics?.[key]);
  }
  $("energy-battery-soc").textContent = Number.isFinite(metrics?.battery_soc) ? `${metrics.battery_soc.toFixed(0)}%` : "--%";
  for (const [node, value, color] of [["solar", metrics?.solar_w, "solar"], ["home", metrics?.home_w, "solar"], ["grid", metrics?.grid_w, "grid"], ["battery", metrics?.battery_w, "battery"]]) {
    const path = $(`energy-${node}-flow`);
    path.classList.toggle("active", !!current && Number.isFinite(value) && Math.abs(value) > 1);
    path.removeAttribute("marker-start"); path.removeAttribute("marker-end");
    if (current && Number.isFinite(value) && Math.abs(value) > 1) path.setAttribute(value < 0 ? "marker-start" : "marker-end", `url(#energy-arrow-${color})`);
  }
  const parts = [];
  if (reading?.message) parts.push(reading.message);
  if (reading?.source === "sandbox") parts.push("Simulated values; no Home Assistant connection.");
  if (metrics && current) {
    parts.push(metrics.battery_w < -1 ? "Battery charging." : metrics.battery_w > 1 ? "Battery discharging." : "Battery idle.");
    parts.push(metrics.grid_w < -1 ? "Exporting to grid." : metrics.grid_w > 1 ? "Importing from grid." : "Grid idle.");
  }
  if (status.energy_delivery_error) parts.push(status.energy_delivery_error);
  $("energy-caption").textContent = parts.join(" ") || "Readings are not configured. No live values are assumed.";
  $("energy-details").textContent = $("energy-caption").textContent;
  $("energy-diagram").setAttribute("aria-label", `Energy distribution: solar ${powerText(metrics?.solar_w)}, home ${powerText(metrics?.home_w)}, grid ${powerText(metrics?.grid_w)}, battery ${powerText(metrics?.battery_w)}. ${parts.join(" ")}`);
}
function renderEnergyTopology(topology, current) {
  const svg = $("energy-topology"), ns = "http://www.w3.org/2000/svg";
  const element = (tag, attributes, text) => {
    const node = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const text = (value, x, y, size = 10, color = "#dce7ec") => element("text", {x, y, "font-size": size, "text-anchor": "middle", fill: color}, value);
  const power = value => value === null ? "n/a" : powerText(value);
  const values = topology.values, nodes = [];
  const marker = element("marker", {id: "topology-arrow", viewBox: "0 0 8 8", refX: 7, refY: 4, markerWidth: 5, markerHeight: 5, orient: "auto-start-reverse"});
  marker.append(element("path", {d: "M0 0L8 4 0 8Z", fill: "context-stroke"}));
  const defs = element("defs", {}); defs.append(marker); nodes.push(defs);
  const paths = {"enphase-inverter": "M57 63L144 68", "ess-inverter-pv": "M231 63L144 68", "inverter-grid": "M144 78L57 86", "inverter-battery": "M144 78V86", "inverter-home": "M144 78L231 86"};
  for (const edge of topology.edges) {
    const active = current && edge.active;
    const path = element("path", {d: paths[edge.id], class: `energy-flow ${active ? "active" : ""}`, stroke: edge.planned || edge.quality === "unavailable" || !current ? "#789397" : edge.id.includes("grid") ? "#399bd2" : edge.id.includes("battery") ? "#ff4f92" : "#ffad00", fill: "none"});
    if (edge.direction <= 0) path.setAttribute("marker-start", "url(#topology-arrow)");
    if (edge.direction >= 0) path.setAttribute("marker-end", "url(#topology-arrow)");
    if (edge.planned) path.setAttribute("stroke-dasharray", "3 3");
    if (edge.direction < 0) path.style.animationDirection = "reverse";
    nodes.push(path);
  }
  for (const [x, y, width, height] of [[14, 29, 86, 35], [188, 29, 86, 35], [118, 58, 52, 20], [14, 85, 86, 39], [101, 85, 86, 39], [188, 85, 86, 39]]) {
    nodes.push(element("rect", {x, y, width, height, rx: 5, fill: "#20363c", stroke: "#516870"}));
  }
  nodes.push(text("Enphase Solar", 57, 39, 9), text(`${topology.enphase_monitor < 0 ? "-" : ""}${power(topology.enphase_monitor)}`, 57, 52, 12, "#ffad00"), text(topology.enphase_monitor === null ? "Unavailable" : topology.enphase_monitor < 0 ? "Standby draw" : "Live monitor", 57, 61, 7));
  nodes.push(text("ESS Solar", 231, 39, 9), text(power(values.ess), 231, 52, 12, "#ffad00"), text(topology.planned ? "Not installed" : "Measured", 231, 61, 7));
  nodes.push(text("ESS inverter", 144, 66, 7), text(power(topology.solar_input), 144, 75, 8));
  nodes.push(text("Grid", 57, 95), text(`In ${power(values.gridImport)}`, 57, 107, 9), text(`Out ${power(values.gridExport)}`, 57, 118, 9));
  nodes.push(text(`Battery ${topology.soc === null ? "--" : Math.round(topology.soc)}%`, 144, 95, 9), text(`Chg ${power(values.charge)}`, 144, 107, 9), text(`Dis ${power(values.discharge)}`, 144, 118, 9));
  nodes.push(text("Home", 231, 95), text(power(topology.home_display), 231, 108, 12), text(topology.home_fallback_active ? "CT fallback" : "ESS meter", 231, 120, 8));
  svg.replaceChildren(...nodes);
  $("energy-status").textContent = current ? topology.status_text : "Stale readings";
  $("energy-panel").dataset.quality = topology.status;
  const details = [...topology.issues, ...topology.notes];
  if (!current) {
    if (status.energy?.message) details.unshift(status.energy.message);
    if (Number.isFinite(status.energy?.received_at_ms)) {
      details.push(`Last received: ${new Date(status.energy.received_at_ms).toLocaleString()}. These are historical readings, not current power.`);
    }
  }
  if (topology.imbalance !== null && !topology.balanced) details.push(`Meter balance differs by ${Math.round(topology.imbalance)} W.`);
  if (status.energy_delivery_error) details.push(status.energy_delivery_error);
  $("energy-details").textContent = details.join(" ") || topology.status_text;
  svg.setAttribute("aria-label", `Energy Distribution. ${current ? topology.status_text : "Stale readings"}. Enphase ${power(topology.enphase_monitor)}, ESS Solar ${power(values.ess)}${topology.planned ? ", not installed" : ""}, grid import ${power(values.gridImport)}, export ${power(values.gridExport)}, home ${power(topology.home_display)}${topology.home_fallback_active ? ", CT fallback" : ""}. ${details.join(" ")}`);
}
function renderWeather() {
  const reading = status.weather;
  $("weather-panel").hidden = theme.weather_panel === false;
  $("preview-middle").hidden = theme.energy_panel === false && theme.weather_panel === false;
  let state = reading?.status || "unconfigured";
  if (state === "live" && (!Number.isFinite(reading.received_at_ms)
      || Date.now() - reading.received_at_ms < -5000
      || Date.now() - reading.received_at_ms > (reading.stale_after_ms || 900000))) state = "stale";
  const key = JSON.stringify([theme.weather_panel, state, reading?.data, reading?.message, status.weather_delivery_error]);
  if (key === weatherKey) return;
  weatherKey = key;
  const data = reading?.data;
  $("weather-status").textContent = state === "simulated" ? "Simulated" : state === "live" ? "Live" : state === "stale" ? "Stale" : "No forecast";
  $("weather-panel").classList.toggle("is-stale", !["live", "simulated"].includes(state));
  const temperature = value => Number.isFinite(value) ? value.toFixed(1) : "--";
  $("weather-temperature").textContent = data ? `${temperature(data.temperature)}${data.temperature_unit}` : "--";
  $("weather-condition").textContent = data ? data.condition.replace("partlycloudy", "partly cloudy").replaceAll("-", " ") : "Waiting for Home Assistant";
  $("weather-forecast").replaceChildren(...(data?.forecast || []).map(day => {
    const column = document.createElement("div"), date = document.createElement("time"), condition = document.createElement("span"), range = document.createElement("strong");
    date.dateTime = day.datetime;
    date.textContent = new Date(day.time_ms).toLocaleDateString([], {weekday: "short"});
    condition.textContent = day.condition.replace("partlycloudy", "partly cloudy").replaceAll("-", " ");
    range.textContent = `${temperature(day.temperature)} / ${temperature(day.templow)}`;
    column.append(date, condition, range); return column;
  }));
  $("weather-details").textContent = [reading?.message, data?.name, status.weather_delivery_error].filter(Boolean).join(" ");
}
function renderStatus() {
  const monitor = status.monitor, rule = monitor.rule, sample = monitor.last_sample;
  const online = monitor.health === "online";
  $("logout").hidden = !status.login_required;
  $("lan-access-warning").hidden = status.login_required;
  $("connection-label").textContent = status.simulation ? "Simulator / no real TV" : online ? "TV connected" : monitor.health === "starting" ? "Connecting" : "Needs attention";
  $("connection-dot").classList.toggle("offline", !online);
  $("monitor-warning").hidden = online && !status.simulation;
  $("monitor-warning").textContent = status.simulation ? `TV SANDBOX: no real TV or NAS changes. ${monitor.error || "The app-use clock is manual."} Energy source: ${status.energy?.source === "home_assistant" ? "Home Assistant live feed" : "explicit simulation"}.` : monitor.error || "Waiting for an authorized ADB sample. The timer is not assumed to be enforcing.";
  const nativeHome = status.native_home;
  const homeWarning = status.home_recovery_error || nativeHome?.error
    || (nativeHome?.phase === "fallback" ? `Google TV was restored automatically. ${nativeHome.reason}` : "");
  $("home-warning").hidden = !homeWarning;
  $("home-warning").textContent = homeWarning ? `Home-button recovery: ${homeWarning}` : "";
  $("native-home").disabled = !!status.simulation || !!nativeHome?.armed;
  const approvedDisabled = status.approved_disabled_packages ?? [];
  $("preservation-note").textContent = approvedDisabled.length
    ? `${approvedDisabled.length} approved vendor packages disabled. No apps uninstalled or data cleared.`
    : "All your apps stay. No uninstalling. No lost logins.";
  const privacyPending = approvedDisabled.filter(packageName => !status.inventory?.disabled.includes(packageName));
  $("privacy-warning").hidden = privacyPending.length === 0;
  $("privacy-warning").textContent = privacyPending.length
    ? `Not confirmed disabled at the last inspection: ${privacyPending.join(", ")}. Inspect the TV to verify your privacy policy.` : "";
  renderSandbox();
  renderEnergy();
  renderWeather();
  if (status.sandbox && status.energy_transport === "browser_bridge"
      && typeof globalThis.__tclReadAuthorizedEnergy === "function"
      && !globalThis.tclDashboardHeartbeat) {
    globalThis.installTclDashboardHeartbeat("__tclReadAuthorizedEnergy");
  } else if (status.energy_transport !== "browser_bridge" && globalThis.tclDashboardHeartbeat) {
    globalThis.tclDashboardHeartbeat.stop();
  }
  const used = Math.floor(monitor.used_seconds);
  $("used-time").textContent = `${String(Math.floor(used / 60)).padStart(2, "0")}:${String(used % 60).padStart(2, "0")}`;
  $("limit-time").textContent = `${rule.minutes} min`;
  $("usage-progress").max = rule.minutes * 60; $("usage-progress").value = used;
  $("remaining-time").textContent = `${Math.ceil(monitor.remaining_seconds / 60)} minutes remaining`;
  $("reset-description").textContent = `${rule.enabled ? "Rule enabled" : "Rule disabled"} / resets at midnight in ${rule.timezone}.`;
  $("current-app").textContent = sample ? `${online ? "" : "Last seen: "}${appLabel(sample.package)}` : "Not observed yet";
  $("power-status").textContent = sample ? `${online ? "" : "Last seen: "}${sample.awake ? "Awake" : "Standby / not awake"}` : "Unknown";
  $("standby-status").textContent = monitor.standby.replaceAll("_", " ");
  $("install").disabled = !status.apk_ready;
  const nextFacts = JSON.stringify([status.inventory, status.baseline_counts, approvedDisabled, nativeHome?.armed, online]);
  if (nextFacts !== factsKey) {
    factsKey = nextFacts;
    const facts = $("device-facts"); facts.replaceChildren();
    if (status.inventory) {
    const current = status.inventory;
    for (const text of [
      `Firmware: ${current.properties["ro.software.version_id"] || current.properties["ro.build.display.id"]}`,
      `Android ${current.properties["ro.build.version.release"]} / ${current.installed.length} installed / ${current.enabled.length} enabled / ${current.disabled.length} disabled`,
      `${online ? "Home" : "Last verified Home"}: ${current.home}`,
      `Home button: ${!online ? "Not verified while the TV/ADB is unavailable" : nativeHome?.armed ? "Clean Home is the actual Android default; NAS failback armed" : current.home_override_enabled ? "Clean Home compatibility override (stock launcher stays enabled)" : "Android default Home"}`,
      ...(nativeHome?.armed ? [online ? "Recovery: Google TV and setup remain installed, disabled until restore or failback." : "Recovery: NAS failback is configured but cannot act until the TV/ADB reconnects."] : []),
      `Protected baseline: ${status.baseline_counts?.installed ?? 0} apps`,
      ...(approvedDisabled.length ? [`Approved privacy exceptions: ${approvedDisabled.join(", ")}`] : []),
    ]) { const line = document.createElement("div"); line.textContent = text; facts.append(line); }
    } else facts.textContent = "Inspect the TV to capture its current apps and original Home.";
  }
  const nextEvents = JSON.stringify(status.events);
  if (nextEvents !== eventsKey) {
    eventsKey = nextEvents;
    $("events").replaceChildren(...status.events.map(event => {
    const row = document.createElement("li"), time = document.createElement("time");
    time.textContent = new Date(event.at).toLocaleString(); row.append(time, document.createTextNode(event.message)); return row;
    }));
  }
}
function renderSandbox(force = false) {
  const sandbox = status.sandbox;
  $("sandbox-panel").hidden = !sandbox;
  if (!sandbox) return;
  $("sandbox-energy-controls").hidden = status.energy_transport !== "simulation";
  $("sandbox-clock").textContent = new Date(sandbox.at).toLocaleString("en-GB", {timeZone: status.monitor.rule.timezone, dateStyle: "medium", timeStyle: "medium"});
  $("sandbox-standby-count").textContent = `${sandbox.standby_requests} simulated standby request(s) / sample app catalog`;
  if (!$("sandbox-app").options.length) $("sandbox-app").replaceChildren(...uniqueApps().map(app => option(app.package, app.label)));
  if (force || !$("sandbox-state-form").contains(document.activeElement)) {
    $("sandbox-app").value = sandbox.package;
    $("sandbox-awake").checked = sandbox.awake;
    $("sandbox-online").checked = sandbox.online;
  }
  const metrics = status.energy?.metrics;
  if (metrics && (force || !$("sandbox-energy-form").contains(document.activeElement))) {
    for (const [id, key] of [["solar", "solar_w"], ["home", "home_w"], ["grid", "grid_w"], ["battery", "battery_w"], ["soc", "battery_soc"]]) $(`sandbox-${id}`).value = metrics[key];
  }
  $("tv-host").disabled = true;
  $("target-form").querySelector("button").disabled = true;
  $("install").textContent = "1. Simulate launcher install";
  $("clean-home").textContent = "2. Select simulated clean Home";
  $("original-home").textContent = "Restore simulated original Home";
  $("standby-now").textContent = "Put simulated TV in standby";
  document.querySelectorAll("[data-apply-theme]").forEach(button => { button.textContent = "Apply to simulated TV"; });
  $("lan-access-warning").textContent = "This loopback-only sandbox has no real TV access. Its settings and usage are separate from the production controller.";
}
async function sandboxAction(path, body) {
  await api(`sandbox/${path}`, {method: ["state", "energy"].includes(path) ? "PUT" : "POST", body: JSON.stringify(body)});
  await refresh();
  renderSandbox(true);
  if (!$("sandbox-screen").hidden) await loadSandboxScreen();
  message("Sandbox updated. No real device was contacted.");
}
async function loadSandboxScreen() {
  const response = await fetch("api/screenshot", {credentials: "same-origin"});
  if (!response.ok) {
    const result = await response.json(); throw new ApiError(result.detail, response.status);
  }
  if (screenUrl) URL.revokeObjectURL(screenUrl);
  screenUrl = URL.createObjectURL(await response.blob());
  $("sandbox-screen").src = screenUrl; $("sandbox-screen").hidden = false;
}
async function refresh() {
  if (!csrf || polling) return;
  polling = true;
  try {
    status = await api("status");
    if (!theme || (!dirty && !$("theme-controls").disabled)) {
      theme = structuredClone(status.theme);
      for (const [id, key] of themeColors) $(id).value = theme[key];
      $("gradient-opacity").value = theme.gradient_opacity;
      $("columns").value = theme.columns; $("animations").checked = theme.animations;
      $("ambient-glow").checked = theme.ambient_glow !== false;
      $("energy-panel-enabled").checked = theme.energy_panel !== false;
      $("weather-panel-enabled").checked = theme.weather_panel !== false;
      renderPreview();
    }
    if (!initialized) {
      $("rule-app").replaceChildren(...uniqueApps().map(a => option(a.package, `${a.label} / ${a.package}`)));
      $("rule-app").value = status.monitor.rule.package;
      $("rule-minutes").value = status.monitor.rule.minutes;
      $("rule-timezone").value = status.monitor.rule.timezone;
      $("rule-enabled").checked = status.monitor.rule.enabled;
      $("tv-host").value = status.host;
      const settings = await api("energy/config");
      const config = settings.config;
      $("energy-feed-enabled").checked = config.enabled;
      for (const [id, key] of energyBindings) $(`energy-entity-${id}`).value = config[key] || "";
      const modes = [["websocket", "Live HA WebSocket (backend only)"]];
      if (status.sandbox) modes.push(["browser_bridge", "Live HA browser bridge (development)"], ["simulation", "Explicit simulated test data"]);
      $("energy-transport").replaceChildren(...modes.map(([value, text]) => option(value, text)));
      $("energy-transport").value = config.transport;
      $("energy-grid-sign").checked = config.grid_import_positive;
      $("energy-battery-sign").checked = config.battery_discharge_positive;
      $("energy-ha-card").checked = !!config.topology_dashboard;
      $("energy-ha-dashboard").value = config.topology_dashboard || "";
      $("energy-ha-view").value = config.topology_view;
      updateEnergySourceFields();
      $("energy-setup-status").textContent = config.transport === "browser_bridge" ? "Live development feed uses the already-authorized Home Assistant browser page. Keep that page connected; credentials never leave it or reach the TV." : config.transport === "simulation" ? "Explicit simulation selected. These are test values, not live energy readings." : settings.token_configured ? "Backend WebSocket authorization is configured. The TV displays automatically without login." : "Configure the backend-only HA token file for a persistent live WebSocket feed. The TV never logs in.";
      const weatherSettings = await api("weather/config");
      $("weather-feed-enabled").checked = weatherSettings.config.enabled;
      $("weather-entity").value = weatherSettings.config.entity_id || "";
      $("weather-transport").replaceChildren(option("websocket", "Live Home Assistant daily forecast"));
      if (status.sandbox) $("weather-transport").append(option("simulation", "Explicit simulated weather"));
      $("weather-transport").value = weatherSettings.config.transport;
      $("weather-setup-status").textContent = weatherSettings.token_configured ? "Backend authorization is configured. The forecast stays local to the controller and TV." : "Use the backend HA URL/token file for live forecasts. No credentials go on the TV.";
      initialized = true;
    }
    renderStatus();
  } finally { polling = false; }
}
async function saveTheme() {
  if (wallpaperCrop) {
    const feedback = $("wallpaper-feedback");
    message("Preparing your cropped wallpaper...", false, feedback);
    const file = await preparePhotoUpload(wallpaperCrop, "wallpaper", cropSelection());
    message("Uploading and saving your cropped wallpaper...", false, feedback);
    const result = await uploadImage(file, "wallpaper");
    theme.wallpaper_file = result.filename;
    markDirty();
  }
  const favoriteComponents = theme.dock_apps.map(p => status.apps.find(a => a.package === p)?.component).filter(Boolean);
  theme.app_order = [...new Set([...favoriteComponents, ...theme.app_order])];
  const saved = await api("theme", {method: "PUT", body: JSON.stringify(theme)});
  status.theme = saved; theme = structuredClone(saved);
  dirty = false; $("theme-dirty").textContent = "Preview saved / apply when ready";
  closeWallpaperCrop();
  renderPreview();
}
function cropSelection() {
  return {zoom: Number($("crop-zoom").value), x: Number($("crop-x").value), y: Number($("crop-y").value)};
}
function renderWallpaperCrop() {
  if (!wallpaperCrop) return;
  const selection = cropSelection();
  drawPhoto($("wallpaper-canvas"), wallpaperCrop, "wallpaper", selection);
  $("crop-zoom-value").textContent = `${Math.round(selection.zoom * 100)}%`;
  const crop = cropRectangle(wallpaperCrop.width, wallpaperCrop.height, selection);
  $("crop-x").disabled = wallpaperCrop.width - crop.width < 0.01;
  $("crop-y").disabled = wallpaperCrop.height - crop.height < 0.01;
}
function closeWallpaperCrop() {
  const canvas = $("wallpaper-canvas");
  if (cropDrag && canvas.hasPointerCapture(cropDrag.id)) canvas.releasePointerCapture(cropDrag.id);
  cropDrag = null;
  if (wallpaperCrop) wallpaperCrop.release();
  wallpaperCrop = null;
  $("wallpaper-crop").hidden = true;
}
async function chooseWallpaper(input) {
  const file = input.files[0];
  if (!file) return;
  message(`Opening ${file.name} for cropping...`, false, $("wallpaper-feedback"));
  try {
    const photo = await openPhoto(file, text => message(text, false, $("wallpaper-feedback")));
    closeWallpaperCrop();
    wallpaperCrop = photo;
    $("crop-zoom").value = "1"; $("crop-x").value = "50"; $("crop-y").value = "50";
    $("wallpaper-crop").hidden = false;
    renderWallpaperCrop();
    renderImageSelections();
    message("Photo ready. Position the landscape crop, then choose Upload & save to system. The TV will not change.", false, $("wallpaper-feedback"));
    $("wallpaper-crop").scrollIntoView({block: "nearest"});
  } finally { input.value = ""; }
}
async function uploadImage(file, kind) {
  const form = new FormData(); form.append("file", file);
  return api(`images/${kind}`, {method: "POST", body: form});
}
async function upload(input, kind) {
  const file = input.files[0];
  if (!file) return;
  const feedback = $(`${kind}-feedback`);
  message(`Preparing ${file.name}...`, false, feedback);
  let photo;
  try {
    photo = await openPhoto(file, text => message(text, false, feedback));
    const prepared = await preparePhotoUpload(photo, kind);
    message(`Uploading ${prepared.name}...`, false, feedback);
    const result = await uploadImage(prepared, kind);
    theme.tile_artwork[YOUTUBE] = result.filename;
    markDirty(); message("Image uploaded for preview. Choose Save to system or Apply to TV.", false, feedback);
  } finally {
    if (photo) photo.release();
    input.value = "";
  }
}
async function connectDashboard() {
  $("boot-panel").hidden = true;
  $("login-panel").hidden = true; $("dashboard").hidden = false;
  await refresh();
  connectEnergyEvents();
}
function connectEnergyEvents() {
  if (energyEvents) energyEvents.close();
  energyEvents = new EventSource("api/energy/events");
  energyEvents.onmessage = event => {
    try {
      const reading = JSON.parse(event.data);
      if (status) { status.energy = reading; renderEnergy(); }
    } catch (error) { message(`Energy stream error: ${error.message}`, true); }
  };
}
async function resumeDashboard() {
  if (document.hidden || !csrf || $("dashboard").hidden) return;
  try {
    await refresh();
    if (!energyEvents || energyEvents.readyState === EventSource.CLOSED) connectEnergyEvents();
  } catch (error) { message(error.message, true); }
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (energyEvents) energyEvents.close();
    energyEvents = null;
  } else resumeDashboard();
});
window.addEventListener("pageshow", event => { if (event.persisted) resumeDashboard(); });
window.addEventListener("online", resumeDashboard);
$("login-form").onsubmit = (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    try {
      const result = await api("login", {method: "POST", body: JSON.stringify({password: $("password").value})});
      csrf = result.csrf_token; $("password").value = ""; $("login-error").textContent = "";
      await connectDashboard();
    } catch (error) { $("login-error").textContent = error.message; }
  });
};
$("logout").onclick = () => action($("logout"), async () => { await api("logout", {method: "POST"}); showLogin(); });
document.querySelectorAll("[data-tab]").forEach(button => button.onclick = () => {
  document.querySelectorAll("[data-tab]").forEach(node => { node.classList.toggle("active", node === button); node.setAttribute("aria-selected", String(node === button)); });
  document.querySelectorAll(".tab").forEach(node => { node.hidden = node.id !== `tab-${button.dataset.tab}`; });
  $("page-title").textContent = {home: "Make room for what matters.", allowance: "A healthy stopping point.", device: "Your TV. Safely managed."}[button.dataset.tab];
});
$("preview-open-apps").onclick = () => showPreviewPage(!previewAppsPage);
$("preview-back-home").onclick = () => showPreviewPage(false);
$("tv-preview").addEventListener("keydown", event => {
  if (!previewAppsPage && event.key === "ArrowDown") {
    event.preventDefault(); showPreviewPage(true);
  } else if (previewAppsPage && (["Escape", "Backspace", "Home"].includes(event.key)
      || event.key === "ArrowUp" && $("preview-app-grid").scrollTop === 0)) {
    event.preventDefault(); showPreviewPage(false);
  }
});
for (const [id, key] of themeColors) $(id).oninput = () => { theme[key] = $(id).value.toUpperCase(); markDirty(); };
$("gradient-opacity").oninput = () => { theme.gradient_opacity = Number($("gradient-opacity").value); markDirty(); };
$("weather-panel-enabled").onchange = () => { theme.weather_panel = $("weather-panel-enabled").checked; markDirty(); };
$("columns").onchange = () => { theme.columns = Number($("columns").value); markDirty(); };
$("animations").onchange = () => { theme.animations = $("animations").checked; markDirty(); };
$("ambient-glow").onchange = () => { theme.ambient_glow = $("ambient-glow").checked; markDirty(); };
$("energy-panel-enabled").onchange = () => { theme.energy_panel = $("energy-panel-enabled").checked; markDirty(); };
$("wallpaper-upload").onchange = () => themeAction(null, () => chooseWallpaper($("wallpaper-upload")), $("wallpaper-feedback"));
$("artwork-upload").onchange = () => themeAction(null, () => upload($("artwork-upload"), "artwork"), $("artwork-feedback"));
$("wallpaper-reset").onclick = () => { closeWallpaperCrop(); theme.wallpaper_file = null; $("wallpaper-upload").value = ""; $("wallpaper-feedback").hidden = true; markDirty(); };
$("artwork-reset").onclick = () => { delete theme.tile_artwork[YOUTUBE]; $("artwork-upload").value = ""; $("artwork-feedback").hidden = true; markDirty(); };
for (const id of ["crop-zoom", "crop-x", "crop-y"]) $(id).oninput = renderWallpaperCrop;
$("crop-reset").onclick = () => {
  $("crop-zoom").value = "1"; $("crop-x").value = "50"; $("crop-y").value = "50";
  renderWallpaperCrop();
};
$("crop-cancel").onclick = () => {
  closeWallpaperCrop(); renderImageSelections();
  message("New photo cancelled. Your saved wallpaper and TV are unchanged.", false, $("wallpaper-feedback"));
};
$("wallpaper-canvas").onpointerdown = event => {
  if (!wallpaperCrop || $("theme-controls").disabled || !event.isPrimary || event.button !== 0) return;
  cropDrag = {id: event.pointerId, clientX: event.clientX, clientY: event.clientY, selection: cropSelection()};
  event.currentTarget.setPointerCapture(event.pointerId);
};
$("wallpaper-canvas").onpointermove = event => {
  if (!wallpaperCrop || !cropDrag || cropDrag.id !== event.pointerId || $("theme-controls").disabled) return;
  const crop = cropRectangle(wallpaperCrop.width, wallpaperCrop.height, cropDrag.selection);
  const box = event.currentTarget.getBoundingClientRect();
  if (!box.width || !box.height) return;
  const travelX = wallpaperCrop.width - crop.width, travelY = wallpaperCrop.height - crop.height;
  const clamp = value => Math.max(0, Math.min(100, value));
  $("crop-x").value = travelX > 0 ? clamp(cropDrag.selection.x - (event.clientX - cropDrag.clientX) * crop.width / box.width / travelX * 100) : 50;
  $("crop-y").value = travelY > 0 ? clamp(cropDrag.selection.y - (event.clientY - cropDrag.clientY) * crop.height / box.height / travelY * 100) : 50;
  renderWallpaperCrop();
};
for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) {
  $("wallpaper-canvas").addEventListener(event, () => { cropDrag = null; });
}
$("wallpaper-canvas").onkeydown = event => {
  if (!wallpaperCrop || $("theme-controls").disabled) return;
  const movement = {ArrowLeft: ["crop-x", 1], ArrowRight: ["crop-x", -1],
    ArrowUp: ["crop-y", 1], ArrowDown: ["crop-y", -1]}[event.key];
  if (!movement) return;
  event.preventDefault();
  const input = $(movement[0]);
  input.value = Math.max(0, Math.min(100, Number(input.value) + movement[1] * (event.shiftKey ? 10 : 2)));
  renderWallpaperCrop();
};
$("add-favorite").onclick = () => {
  if (theme.dock_apps.length >= 7) return message("The dock supports at most seven favorites.", true);
  const value = $("favorite-add").value;
  if (value && !theme.dock_apps.includes(value)) { theme.dock_apps.push(value); theme.columns = Math.max(theme.columns, theme.dock_apps.length); $("columns").value = theme.columns; markDirty(); }
};
document.querySelectorAll("[data-save-theme]").forEach(button => button.onclick = () => {
  const feedback = button.closest("[data-image-kind]")?.querySelector("[data-theme-feedback]");
  return themeAction(button, async () => {
    await saveTheme(); message("Design saved to system. It will remain after reload. The TV has not changed yet.", false, feedback);
  }, feedback);
});
document.querySelectorAll("[data-apply-theme]").forEach(button => button.onclick = () => {
  const feedback = button.closest("[data-image-kind]")?.querySelector("[data-theme-feedback]");
  return themeAction(button, async () => {
    if (!confirm("Apply this design and open the clean launcher on the TV? Existing apps will not be removed.")) return;
    await saveTheme(); await api("theme/apply", {method: "POST"}); await refresh(); renderPreview();
    $("theme-dirty").textContent = "Design applied to TV";
    message("Design applied. All existing apps were preserved.", false, feedback);
  }, feedback);
});
$("rule-form").onsubmit = event => {
  event.preventDefault();
  action(event.submitter, async () => {
    const body = {enabled: $("rule-enabled").checked, package: $("rule-app").value,
      minutes: Number($("rule-minutes").value), timezone: $("rule-timezone").value, confirm_exhausted: false};
    try { await api("rule", {method: "PUT", body: JSON.stringify(body)}); }
    catch (error) {
      if (error.code !== 409 || !confirm(`${error.message}\nPut the TV into standby when this exhausted app is next observed?`)) throw error;
      body.confirm_exhausted = true; await api("rule", {method: "PUT", body: JSON.stringify(body)});
    }
    message("Daily allowance saved. Existing consumed time is retained."); await refresh();
  });
};
$("target-form").onsubmit = event => {
  event.preventDefault(); action(event.submitter, async () => { await api("target", {method: "PUT", body: JSON.stringify({host: $("tv-host").value.trim()})}); message("TV address saved. Approve this controller on the TV when prompted."); });
};
$("inspect").onclick = () => action($("inspect"), async () => {
  message("Reading the TV's apps and original Home. This may take a moment.");
  await api("inventory", {method: "POST"}); initialized = false; await refresh(); message("App inventory captured. Existing app states are preserved.");
});
$("install").onclick = () => action($("install"), async () => {
  if (!confirm("Install and open the new launcher? The current Home choice and existing package states will be preserved.")) return;
  await api("install", {method: "POST", body: JSON.stringify({confirmation: "INSTALL_LAUNCHER"})});
  message("Launcher installed and opened. Test it before making it Home."); await refresh();
});
$("native-home").onclick = () => action($("native-home"), async () => {
  if (!confirm("Make Clean Home the actual Android default? This disables only Google TV Home and its competing setup-wizard package, not uninstalling them. Saved Wi-Fi must not belong to the wizard. The NAS will restore Google TV if Clean Home disappears, crashes repeatedly, or Android reports an ANR. Automatic failback needs this NAS and authorized ADB. Restore original Home reverses the change.")) return;
  const result = await api("home", {method: "POST", body: JSON.stringify({
    target: "clean", confirmation: "CHANGE_HOME", true_default: true,
  })});
  if (result.mode !== "native_guarded") throw new Error("The controller did not confirm guarded native Home.");
  message("Clean Home is the actual Android default. Google TV is retained for recovery; NAS failback is armed.");
  await refresh();
}).finally(() => {
  $("native-home").disabled = !!status?.simulation || !!status?.native_home?.armed;
});
for (const [id, target] of [["clean-home", "clean"], ["original-home", "original"]]) $(id).onclick = () => action($(id), async () => {
  if (!confirm(`Switch the TV to ${target === "clean" ? "compatibility clean" : "its original"} Home now? Any native-Home backup packages will be restored first. No new packages will be disabled.`)) return;
  const body = {target, confirmation: "CHANGE_HOME", allow_home_override: false};
  let result;
  try { result = await api("home", {method: "POST", body: JSON.stringify(body)}); }
  catch (error) {
    if (target !== "clean" || !error.message.includes("Home compatibility override")) throw error;
    if (!confirm("This TCL protects its stock Home preference. Enable the minimal compatibility service to open clean Home when Google TV Home appears? It cannot read screen content or capture keys, and leaves all apps enabled. Restore original Home turns it off.")) throw error;
    body.allow_home_override = true;
    result = await api("home", {method: "POST", body: JSON.stringify(body)});
  }
  message(result.mode === "accessibility_override" ? "Clean Home compatibility override enabled. Google TV remains installed and enabled." : "Home changed and app preservation verified."); await refresh();
});
$("undo-theme").onclick = () => action($("undo-theme"), async () => {
  if (!confirm("Restore the previous applied design on the TV?")) return;
  await api("theme/undo", {method: "POST"}); dirty = false; await refresh(); message("Previous design restored.");
});
$("standby-now").onclick = () => action($("standby-now"), async () => {
  if (!confirm("Put the TV into standby RIGHT NOW? This does not toggle power or wake an asleep TV.")) return;
  const result = await api("standby", {method: "POST", body: JSON.stringify({confirmation: "STANDBY_NOW"})});
  message(result.standby_requested ? "Standby requested. Device confirmation may follow." : "The TV was already asleep.");
});
$("sandbox-state-form").onsubmit = event => {
  event.preventDefault();
  action(event.submitter, () => sandboxAction("state", {package: $("sandbox-app").value, awake: $("sandbox-awake").checked, online: $("sandbox-online").checked}));
};
$("sandbox-advance-form").onsubmit = event => {
  event.preventDefault();
  action(event.submitter, () => sandboxAction("advance", {seconds: Number($("sandbox-seconds").value)}));
};
document.querySelectorAll("[data-sandbox-seconds]").forEach(button => button.onclick = () =>
  action(button, () => sandboxAction("advance", {seconds: Number(button.dataset.sandboxSeconds)})));
document.querySelectorAll("[data-sandbox-jump]").forEach(button => button.onclick = () =>
  action(button, () => sandboxAction("jump", {target: button.dataset.sandboxJump})));
$("sandbox-screen-button").onclick = () => action($("sandbox-screen-button"), loadSandboxScreen);
$("sandbox-energy-form").onsubmit = event => {
  event.preventDefault();
  const body = {};
  for (const [id, key] of [["solar", "solar_w"], ["home", "home_w"], ["grid", "grid_w"], ["battery", "battery_w"], ["soc", "battery_soc"]]) body[key] = Number($(`sandbox-${id}`).value);
  action(event.submitter, () => sandboxAction("energy", body));
};
const energyPresets = {
  solar: {solar_w: 3470, home_w: 880, grid_w: 0, battery_w: -2590, battery_soc: 24},
  battery: {solar_w: 0, home_w: 1800, grid_w: 0, battery_w: 1800, battery_soc: 73},
  export: {solar_w: 4500, home_w: 800, grid_w: -3700, battery_w: 0, battery_soc: 95},
};
document.querySelectorAll("[data-energy-preset]").forEach(button => button.onclick = () =>
  action(button, () => sandboxAction("energy", energyPresets[button.dataset.energyPreset])));
$("energy-config-form").onsubmit = event => {
  event.preventDefault();
  const body = {enabled: $("energy-feed-enabled").checked, transport: $("energy-transport").value, grid_import_positive: $("energy-grid-sign").checked, battery_discharge_positive: $("energy-battery-sign").checked, topology_dashboard: $("energy-ha-card").checked ? $("energy-ha-dashboard").value.trim() : null, topology_view: $("energy-ha-view").value.trim()};
  for (const [id, key] of energyBindings) body[key] = $(`energy-entity-${id}`).value.trim() || null;
  action(event.submitter, async () => { await api("energy/config", {method: "PUT", body: JSON.stringify(body)}); initialized = false; await refresh(); message("Energy source saved. Live feeds never substitute simulated data."); });
};
function updateEnergySourceFields() {
  const automatic = $("energy-ha-card").checked;
  for (const [id] of energyBindings) $(`energy-entity-${id}`).disabled = automatic;
  $("energy-grid-sign").disabled = automatic;
  $("energy-battery-sign").disabled = automatic;
}
$("energy-ha-card").onchange = updateEnergySourceFields;
$("energy-refresh").onclick = () => action($("energy-refresh"), async () => {
  await api("energy/refresh", {method: "POST"}); await refresh(); message("Energy status refreshed.");
});
$("weather-config-form").onsubmit = event => {
  event.preventDefault();
  action(event.submitter, async () => {
    await api("weather/config", {method: "PUT", body: JSON.stringify({enabled: $("weather-feed-enabled").checked, entity_id: $("weather-entity").value.trim() || null, transport: $("weather-transport").value})});
    initialized = false; await refresh(); message("Weather source saved. Live weather never substitutes simulated data.", false, $("weather-setup-status"));
  }, $("weather-setup-status"));
};
setInterval(() => { if (csrf && !document.hidden) refresh().catch(error => message(error.message, true)); }, 5000);
setInterval(() => {
  const reading = status?.energy;
  const contact = reading?.received_at_ms ?? reading?.sampled_at_ms;
  if (reading?.status === "live" && Number.isFinite(contact) && Date.now() - contact > (reading.stale_after_ms || 15000)) {
    status.energy = {...reading, status: "stale", message: "Live energy updates stopped; showing the last system reading."};
    renderEnergy();
  }
  if (theme && status?.weather) renderWeather();
}, 1000);
$("retry-connection").onclick = () => location.reload();
(async () => {
  try { await bootstrapSession(); await connectDashboard(); }
  catch (error) {
    if (error.code === 401) showLogin();
    else {
      $("login-panel").hidden = true; $("dashboard").hidden = true;
      $("boot-panel").hidden = false; $("boot-message").textContent = error.message;
      $("retry-connection").hidden = false;
    }
  }
})();
