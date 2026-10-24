"use strict";

// Invoked only in an already-authorized HA page by a local browser-automation host.
globalThis.installTclHaEnergyBridge = async function (callbackName, entityIds) {
  const root = document.querySelector("home-assistant");
  const hass = root?.hass;
  if (!hass?.connected || typeof globalThis[callbackName] !== "function") {
    throw new Error("A connected Home Assistant page and a local publisher are required.");
  }
  if (globalThis.tclHaEnergyBridge) await globalThis.tclHaEnergyBridge.stop();
  const allowed = new Set(entityIds), cache = {};
  const connection = hass.connection;
  const stats = {source: "home_assistant", sent: 0, changes: 0, last_sent: null, error: null};
  let pending = null, flushing = null, stopped = false;
  for (const id of allowed) {
    const state = hass.states[id];
    if (state) cache[id] = {entity_id: id, state: state.state,
      attributes: {unit_of_measurement: state.attributes.unit_of_measurement ?? null}};
  }
  async function send(connected = !!root.hass?.connected) {
    pending = {connected, states: structuredClone(cache)};
    if (flushing) return flushing;
    flushing = (async () => {
      while (pending) {
        const packet = pending; pending = null;
        try {
          const result = await globalThis[callbackName](packet);
          if (!result.ok) throw new Error(result.error || `Energy receiver returned ${result.status}`);
          stats.sent += 1; stats.last_sent = new Date().toISOString(); stats.error = null;
        } catch (error) { stats.error = String(error.message || error); }
      }
    })().finally(() => { flushing = null; });
    return flushing;
  }
  const unsubscribe = await connection.subscribeMessage(update => {
    if (stopped) return;
    for (const [id, value] of Object.entries(update.a || {})) {
      if (allowed.has(id)) cache[id] = {entity_id: id, state: value.s,
        attributes: {unit_of_measurement: value.a?.unit_of_measurement ?? null}};
    }
    for (const id of update.r || []) delete cache[id];
    for (const [id, change] of Object.entries(update.c || {})) {
      if (!allowed.has(id) || !cache[id]) continue;
      const plus = change["+"] || {};
      if ("s" in plus) cache[id].state = plus.s;
      if (plus.a && "unit_of_measurement" in plus.a) cache[id].attributes.unit_of_measurement = plus.a.unit_of_measurement;
      if (change["-"]?.a?.includes("unit_of_measurement")) cache[id].attributes.unit_of_measurement = null;
    }
    stats.changes += 1;
    void send();
  }, {type: "subscribe_entities", entity_ids: [...allowed]});
  const disconnected = () => { void send(false); };
  connection.addEventListener("disconnected", disconnected);
  const interval = setInterval(() => { if (!stopped) void send(); }, 5000);
  globalThis.tclHaEnergyBridge = {
    stats,
    stop: async () => {
      stopped = true; clearInterval(interval);
      connection.removeEventListener("disconnected", disconnected);
      await unsubscribe();
      await send(false);
      globalThis.tclHaEnergyBridge = null;
    },
  };
  await send();
  if (stats.error) throw new Error(stats.error);
  return {...stats, entity_count: allowed.size};
};

globalThis.installTclDashboardHeartbeat = function (readCallbackName) {
  if (globalThis.tclDashboardHeartbeat) globalThis.tclDashboardHeartbeat.stop();
  const stats = {sent: 0, last_sent: null, error: null};
  let busy = false;
  async function tick() {
    if (busy) return;
    busy = true;
    try {
      const packet = await globalThis[readCallbackName]();
      const session = await (await fetch("api/session", {credentials: "same-origin"})).json();
      const response = await fetch("api/sandbox/energy/live", {
        method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": session.csrf_token},
        body: JSON.stringify(packet),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || result.message || "Live heartbeat unavailable");
      stats.sent += 1; stats.last_sent = new Date().toISOString(); stats.error = null;
    } catch (error) { stats.error = String(error.message || error); }
    finally { busy = false; }
  }
  const interval = setInterval(() => { void tick(); }, 5000);
  globalThis.tclDashboardHeartbeat = {stats, stop: () => {
    clearInterval(interval); globalThis.tclDashboardHeartbeat = null;
  }};
  void tick();
  return stats;
};
