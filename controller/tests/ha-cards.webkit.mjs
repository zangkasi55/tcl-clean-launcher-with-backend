import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {after, before, beforeEach, test} from "node:test";

import {devices, webkit} from "playwright-core";

const base = process.env.TCL_HA_TEST_URL || "http://127.0.0.1:18827";
let browser, context, page;

async function api(path) {
  const response = await context.request.get(`${base}/api/${path}`);
  assert.equal(response.status(), 200, `Sandbox API ${path}`);
  return response.json();
}

async function ready() {
  await page.goto(base);
  await page.locator("#dashboard:not([hidden])").waitFor();
  await page.waitForFunction(() =>
    !document.querySelector("#ha-cards-status").textContent.startsWith("Checking"));
  assert.equal((await api("status")).sandbox?.real_device_access, false,
    "Direct HA browser tests must never write to a real controller");
}

async function syntheticForms() {
  await page.locator('[data-tab="device"]').click();
  await page.locator("#energy-ha-card").uncheck();
  await page.locator("#energy-feed-enabled").check();
  await page.locator("#energy-transport").selectOption("websocket");
  for (const id of ["solar", "home", "grid", "battery", "soc", "grid-import",
    "grid-export", "battery-charge", "battery-discharge"]) {
    await page.locator(`#energy-entity-${id}`).fill("");
  }
  await page.locator("#energy-ha-card").check();
  await page.locator("#energy-ha-dashboard").fill("synthetic-energy");
  await page.locator("#energy-ha-view").fill("Test view");
  await page.locator('[data-tab="home"]').click();
  await page.locator("#weather-feed-enabled").check();
  await page.locator("#weather-entity").fill("weather.synthetic");
  await page.locator("#weather-transport").selectOption("websocket");
  await page.locator("#vehicle-feed-enabled").check();
  await page.locator("#vehicle-soc-entity").fill("sensor.synthetic_soc");
  await page.locator("#vehicle-charging-entity").fill("binary_sensor.synthetic_charging");
  await page.locator("#vehicle-transport").selectOption("websocket");
}

function responseFor(path, method) {
  return page.waitForResponse(response => response.url() === `${base}/api/${path}`
    && response.request().method() === method);
}

async function save() {
  const response = responseFor("ha/cards", "PUT");
  await page.locator("#ha-cards-save").click();
  return response;
}

before(async () => {
  browser = await webkit.launch();
  context = await browser.newContext({...devices["iPhone 14 Pro"], acceptDownloads: true});
  await api("session");
  assert.equal((await api("status")).sandbox?.real_device_access, false,
    "Refusing a non-sandbox server before browser mutations");
  page = await context.newPage();
  page.setDefaultTimeout(15000);
});

beforeEach(ready);
after(async () => { await browser?.close(); });

test("mobile direct save, dirty polling, saved-only send, load and download stay separate from backend",
  async () => {
    const backend = await Promise.all(["energy", "weather", "vehicle"]
      .map(kind => api(`${kind}/config`)));
    const initial = await api("status");
    const mutations = [];
    const record = request => {
      if (!["GET", "HEAD"].includes(request.method())) mutations.push(request.url());
    };
    page.on("request", record);
    try {
      await syntheticForms();
      assert.equal((await save()).status(), 200);
      const saved = (await api("ha/cards")).config;
      assert.equal(saved.schema_version, 1);
      assert.equal(saved.energy.topology_dashboard, "synthetic-energy");
      assert.equal(saved.weather.entity_id, "weather.synthetic");
      assert.equal(saved.vehicle.battery_soc_entity, "sensor.synthetic_soc");
      assert.equal(saved.vehicle.charging_entity, "binary_sensor.synthetic_charging");
      assert.deepEqual(mutations, [`${base}/api/ha/cards`]);
      assert.deepEqual(await Promise.all(["energy", "weather", "vehicle"]
        .map(kind => api(`${kind}/config`))), backend);
      const current = await api("status");
      for (const key of ["theme", "applied_theme", "inventory", "host"]) {
        assert.deepEqual(current[key], initial[key], key);
      }
      assert.deepEqual(current.monitor.rule, initial.monitor.rule);

      await page.locator("#vehicle-soc-entity").fill("sensor.unsaved_soc");
      await responseFor("status", "GET");
      assert.equal(await page.locator("#vehicle-soc-entity").inputValue(), "sensor.unsaved_soc");
      const simulated = responseFor("ha/cards/send", "POST");
      await page.locator("#ha-cards-send").click();
      const rejected = await simulated;
      assert.equal(rejected.status(), 409);
      assert.equal(rejected.request().postData(), null, "Send must not submit unsaved forms");
      await page.locator("#ha-cards-status.upload-error").waitFor();
      assert.match(await page.locator("#ha-cards-status").innerText(), /simulated TV/i);
      assert.deepEqual((await api("ha/cards")).config, saved);

      await page.route("**/api/ha/cards/send", route => route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({sent: true, credentials_transferred: false, source_changed: false}),
      }));
      try {
        const sent = responseFor("ha/cards/send", "POST");
        await page.locator("#ha-cards-send").click();
        assert.equal((await sent).request().postData(), null);
        await page.waitForFunction(() =>
          document.querySelector("#ha-cards-status").textContent.startsWith("Saved mappings sent"));
        assert.match(await page.locator("#ha-cards-status").innerText(), /previews still show backend/);
      } finally { await page.unroute("**/api/ha/cards/send"); }

      const downloadReady = page.waitForEvent("download");
      await page.locator("#ha-cards-export").click();
      const download = await downloadReady;
      assert.equal(download.suggestedFilename(), "ha-cards.json");
      assert.equal(await download.failure(), null);
      assert.deepEqual(JSON.parse(await readFile(await download.path(), "utf8")), saved);
      const loaded = responseFor("ha/cards", "GET");
      await page.locator("#ha-cards-load").click();
      await loaded;
      await page.waitForFunction(() =>
        document.querySelector("#vehicle-soc-entity").value === "sensor.synthetic_soc");
      assert.equal(await page.locator("#weather-entity").inputValue(), "weather.synthetic");
      await page.reload();
      await page.waitForFunction(() =>
        document.querySelector("#ha-cards-status").textContent.startsWith("Direct mappings are saved"));
      assert.deepEqual((await api("ha/cards")).config, saved);
      assert.ok(mutations.every(url => url === `${base}/api/ha/cards`
        || url === `${base}/api/ha/cards/send`), "No backend, installation, HOME or theme mutations");
    } finally { page.off("request", record); }
  });

test("invalid transport has visible recoverable feedback and preserves saved mappings", async () => {
  const original = (await api("ha/cards")).config;
  await syntheticForms();
  await page.locator("#weather-transport").selectOption("simulation");
  assert.equal((await save()).status(), 422);
  await page.locator("#ha-cards-status.upload-error").waitFor();
  assert.match(await page.locator("#ha-cards-status").innerText(), /websocket|direct/i);
  assert.equal(await page.locator("#ha-cards-save").isEnabled(), true);
  assert.deepEqual((await api("ha/cards")).config, original);
  await page.locator("#weather-transport").selectOption("websocket");
  assert.equal((await save()).status(), 200);
});

for (const kind of ["energy", "weather", "vehicle"]) {
  test(`saving backend ${kind} must not erase direct edits in the forms`, async () => {
    await syntheticForms();
    await page.locator("#vehicle-soc-entity").fill("sensor.keep_unsaved");
    await page.route(`**/api/${kind}/config`, async route => {
      if (route.request().method() === "PUT") {
        await route.fulfill({status: 200, contentType: "application/json", body: "{}"});
      } else await route.continue();
    });
    try {
      await page.locator(`[data-tab="${kind === "energy" ? "device" : "home"}"]`).click();
      await page.locator(`#${kind}-config-form button[type=submit]`).click();
      await page.waitForFunction(name =>
        document.querySelector("#notice").textContent.startsWith(
          `${name[0].toUpperCase()}${name.slice(1)} source saved`), kind);
      await page.locator('[data-tab="home"]').click();
      assert.equal(await page.locator("#vehicle-soc-entity").inputValue(), "sensor.keep_unsaved");
      assert.equal(await page.locator("#weather-entity").inputValue(), "weather.synthetic");
      await page.locator('[data-tab="device"]').click();
      assert.equal(await page.locator("#energy-ha-dashboard").inputValue(), "synthetic-energy");
    } finally { await page.unroute(`**/api/${kind}/config`); }
  });
}

test("inventory refresh updates app choices without resetting card or allowance edits", async () => {
  await syntheticForms();
  await page.locator('[data-tab="device"]').click();
  const initial = await api("status");
  const added = {package: "com.example.new_app", component: "com.example.new_app/.Main",
    label: "Synthetic new app"};
  await page.route("**/api/inventory", route => route.fulfill({
    status: 200, contentType: "application/json", body: "{}",
  }));
  await page.route("**/api/status", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({...initial, apps: [...initial.apps, added]}),
  }));
  try {
    await page.locator("#inspect").click();
    await page.waitForFunction(() => document.querySelector(
      '#rule-app option[value="com.example.new_app"]') !== null);
    assert.equal(await page.locator("#energy-ha-dashboard").inputValue(), "synthetic-energy");
    await page.locator('[data-tab="home"]').click();
    assert.equal(await page.locator("#vehicle-soc-entity").inputValue(), "sensor.synthetic_soc");
    await page.locator('[data-tab="allowance"]').click();
    await page.locator("#rule-app").selectOption("com.example.new_app");
    await page.locator("#rule-minutes").fill("37");
    await responseFor("status", "GET");
    assert.equal(await page.locator("#rule-app").inputValue(), "com.example.new_app");
    assert.equal(await page.locator("#rule-minutes").inputValue(), "37");
  } finally {
    await page.unroute("**/api/inventory");
    await page.unroute("**/api/status");
  }
});
