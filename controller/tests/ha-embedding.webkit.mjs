import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {after, before, beforeEach, afterEach, test} from "node:test";

import {devices, webkit} from "playwright-core";

const base = process.env.TCL_HA_TEST_URL || "http://127.0.0.1:18837";
const cardSource = await readFile(new URL("../home-assistant/tcl-control-card.js", import.meta.url));
const origin = "https://ha.example.com";
const ingress = "/api/hassio_ingress/synthetic-app/";
let browser, context, page, controllerFrame, failedHealth, appState, calls;

before(async () => { browser = await webkit.launch(); });
after(async () => { await browser?.close(); });

beforeEach(async () => {
  context = await browser.newContext({...devices["iPhone 14 Pro"]});
  const bootstrap = await context.request.get(`${base}/api/session`);
  assert.equal(bootstrap.status(), 200);
  const status = await (await context.request.get(`${base}/api/status`)).json();
  assert.equal(status.sandbox?.real_device_access, false,
    "Embedding tests require the isolated simulated controller");
  page = await context.newPage();
  page.setDefaultTimeout(15000);
  failedHealth = false;
  appState = "started";
  calls = [];
  await page.exposeFunction("syntheticSupervisor", request => {
    calls.push(request.endpoint);
    if (request.endpoint.endsWith("/info")) {
      return {state: appState, ingress_url: ingress};
    }
    if (request.endpoint === "/ingress/session") return {session: "synthetic-ingress-session"};
    if (request.endpoint === "/ingress/validate_session") return {};
    throw new Error(`Unexpected synthetic Supervisor request: ${request.endpoint}`);
  });
  await page.route(`${origin}/tcl-control-card.js`, route => route.fulfill({
    contentType: "text/javascript", body: cardSource,
  }));
  await page.route(`${origin}${ingress}**`, async route => {
    const request = route.request();
    const url = new URL(request.url());
    const relative = url.pathname.slice(ingress.length) + url.search;
    if (failedHealth && relative === "healthz") {
      return route.fulfill({status: 502, contentType: "text/plain", body: "Synthetic LAN failure"});
    }
    assert.ok(["GET", "HEAD"].includes(request.method()),
      "These embedding tests must not save settings or operate even the simulated TV");
    if (relative === "api/energy/events") {
      return route.fulfill({contentType: "text/event-stream", body: ": synthetic stream\n\n"});
    }
    const response = await context.request.get(`${base}/${relative}`);
    await route.fulfill({response});
  });
  await page.route(`${origin}/test`, route => route.fulfill({
    contentType: "text/html",
    body: `<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">
      <style>body{margin:0;font-family:sans-serif}#below{height:1000px}</style>
      <script type="module">
        import "/tcl-control-card.js";
        const card = document.createElement("tcl-control-card");
        window.card = card;
        window.fakeHass = {connected:true,user:{id:"synthetic-owner"},
          callWS:request=>window.syntheticSupervisor(request)};
        card.setConfig({type:"custom:tcl-control-card",addon:"synthetic_tcl_control",height:720});
        card.hass = window.fakeHass;
        document.body.append(card);
        const below = document.createElement("div");
        below.id = "below";
        below.textContent = "Existing Home cards remain below";
        document.body.append(below);
      </script>`,
  }));
});

afterEach(async () => {
  await page?.unrouteAll({behavior: "wait"});
  await context?.close();
});

async function ready() {
  await page.goto(`${origin}/test`);
  await page.locator("tcl-control-card iframe:not([hidden])").waitFor();
  controllerFrame = page.frameLocator("tcl-control-card iframe");
  await controllerFrame.locator("#dashboard:not([hidden])").waitFor();
  assert.equal(page.frames().length, 2);
}

async function innerScroll(y) {
  await controllerFrame.locator("body").evaluate((body, y) => body.ownerDocument.defaultView.scrollTo(0, y), y);
}

test("iPhone WebKit exposes all lower forms and outer scrolling without a gesture wrapper",
  async () => {
    await ready();
    assert.equal(await page.locator("custom-button-card,button-card").count(), 0);
    assert.equal(await page.locator("tcl-control-card").evaluate(element =>
      getComputedStyle(element).touchAction), "auto");
    const frame = page.locator("tcl-control-card iframe");
    const box = await frame.boundingBox();
    assert.ok(box.width <= 393 && box.height >= 360 && box.height < 720);
    const dimensions = await controllerFrame.locator("body").evaluate(body => ({
      width: body.ownerDocument.documentElement.clientWidth,
      scrollWidth: body.ownerDocument.documentElement.scrollWidth,
      height: body.ownerDocument.defaultView.innerHeight,
      scrollHeight: body.ownerDocument.documentElement.scrollHeight,
    }));
    assert.ok(dimensions.scrollHeight > dimensions.height * 2, "Real lower forms must exist");
    assert.ok(dimensions.scrollWidth <= dimensions.width + 1, "No sideways clipping");
    await innerScroll(650);
    await page.waitForFunction(() => {
      const frame = document.querySelector("tcl-control-card").shadowRoot.querySelector("iframe");
      return frame.contentWindow.scrollY > 100;
    });
    await innerScroll(100000);
    const finalScroll = await controllerFrame.locator("body").evaluate(body =>
      body.ownerDocument.defaultView.scrollY);
    assert.ok(finalScroll > 1000, "The lower controls are reachable");
    await page.locator("#below").scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(() => scrollY) > 0, "The outer Home view still scrolls");
    assert.match(await page.locator("tcl-control-card [role=status]").innerText(), /Scroll inside/);
  });

test("all three controller sections and portrait crop are usable inside the iframe",
  async () => {
    await ready();
    for (const tab of ["allowance", "device", "home"]) {
      await innerScroll(0);
      await controllerFrame.locator(`[data-tab="${tab}"]`).click();
      await controllerFrame.locator(`#tab-${tab}:not([hidden])`).waitFor();
    }
    const data = await page.evaluate(() => {
      const canvas = document.createElement("canvas");
      canvas.width = 300; canvas.height = 600;
      const paint = canvas.getContext("2d");
      paint.fillStyle = "#276b57"; paint.fillRect(0, 0, 300, 600);
      return canvas.toDataURL("image/png").split(",")[1];
    });
    await controllerFrame.locator("#wallpaper-upload").setInputFiles({
      name: "synthetic-portrait.png", mimeType: "image/png", buffer: Buffer.from(data, "base64"),
    });
    const crop = controllerFrame.locator("#wallpaper-crop:not([hidden])");
    await crop.waitFor();
    await crop.scrollIntoViewIfNeeded();
    const size = await controllerFrame.locator("#wallpaper-canvas").evaluate(canvas => ({
      width: canvas.width, height: canvas.height,
    }));
    assert.equal(size.width * 9, size.height * 16);
    await controllerFrame.locator("#crop-save").scrollIntoViewIfNeeded();
    assert.equal(await controllerFrame.locator("#crop-save").isVisible(), true);
    await controllerFrame.locator("#crop-cancel").click();
    await crop.waitFor({state: "hidden"});
  });

test("frequent HA state updates and ingress renewal preserve the existing iframe",
  async () => {
    await page.clock.install();
    await ready();
    const previous = await page.locator("tcl-control-card iframe").getAttribute("src");
    await controllerFrame.locator("body").evaluate(body => { body.dataset.syntheticDraft = "keep"; });
    await page.evaluate(() => {
      for (let index = 0; index < 30; index++) {
        window.card.hass = {...window.fakeHass, syntheticState: index};
      }
    });
    await page.clock.fastForward(60001);
    await page.waitForFunction(() => window.card._keepAlive !== null);
    assert.equal(await page.locator("tcl-control-card iframe").getAttribute("src"), previous);
    assert.equal(await controllerFrame.locator("body").getAttribute("data-synthetic-draft"), "keep");
    assert.equal(calls.filter(path => path.endsWith("/info")).length, 1);
    assert.ok(calls.includes("/ingress/validate_session"));
    await page.evaluate(() => window.card.remove());
    assert.equal(await page.evaluate(() => window.card._keepAlive), null);
  });

test("connection failure is visible, does not create a blank frame, and has an explicit retry",
  async () => {
    failedHealth = true;
    await page.goto(`${origin}/test`);
    const status = page.locator("tcl-control-card [role=status]");
    await status.filter({hasText: "HTTP 502"}).waitFor();
    assert.equal(await page.locator("tcl-control-card iframe").getAttribute("src"), null);
    assert.equal(await page.locator("tcl-control-card iframe").isVisible(), false);
    failedHealth = false;
    await page.getByRole("button", {name: "Retry connection"}).click();
    await page.locator("tcl-control-card iframe:not([hidden])").waitFor();
    await page.frameLocator("tcl-control-card iframe").locator("#dashboard:not([hidden])").waitFor();
    assert.match(await status.innerText(), /Scroll inside/);
  });

test("a stopped app is reported without silently starting services or changing access",
  async () => {
    appState = "stopped";
    await page.goto(`${origin}/test`);
    await page.locator("tcl-control-card [role=status]").filter({hasText: "Start the TCL"}).waitFor();
    assert.deepEqual(calls, ["/addons/synthetic_tcl_control/info"]);
    assert.equal(await page.locator("tcl-control-card iframe").getAttribute("src"), null);
  });
