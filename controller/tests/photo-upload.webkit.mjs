import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {after, before, beforeEach, test} from "node:test";

import {devices, webkit} from "playwright-core";

const base = process.env.TCL_PHOTO_TEST_URL || "http://127.0.0.1:18796";
let browser, context, page, original;

async function api(path, body) {
  return page.evaluate(async ({path, body}) => {
    const session = await (await fetch("/api/session")).json();
    const response = await fetch(`/api/${path}`, {
      method: body === undefined ? "GET" : "PUT",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": session.csrf_token},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`Test API ${path} returned ${response.status}`);
    return response.json();
  }, {path, body});
}

async function ready() {
  await page.goto(base);
  await page.locator("#dashboard:not([hidden])").waitFor();
  assert.equal((await api("status")).sandbox?.real_device_access, false,
    "Photo tests must never write to a real controller");
}

async function portrait({large = false} = {}) {
  const data = await page.evaluate(async () => {
    const canvas = document.createElement("canvas");
    canvas.width = 600; canvas.height = 1200;
    const context = canvas.getContext("2d");
    for (const [color, y] of [["#ef3020", 0], ["#20d050", 400], ["#3040e0", 800]]) {
      context.fillStyle = color; context.fillRect(0, y, 600, 400);
    }
    const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
    return Array.from(new Uint8Array(await blob.arrayBuffer()));
  });
  const buffer = large ? Buffer.concat([Buffer.from(data), Buffer.alloc(9 * 1024 * 1024)]) : Buffer.from(data);
  return {name: "same-photo.png", mimeType: "image/png", buffer};
}

async function choose(photo) {
  await page.locator("#wallpaper-upload").setInputFiles(photo);
  await page.locator("#wallpaper-crop:not([hidden])").waitFor();
  await page.waitForFunction(() => !document.querySelector("#theme-controls").disabled);
  assert.equal(await page.locator("#wallpaper-upload").inputValue(), "");
}

async function range(id, value) {
  await page.locator(`#${id}`).evaluate((input, value) => {
    input.value = String(value);
    input.dispatchEvent(new Event("input", {bubbles: true}));
  }, value);
}

async function save(selector = "#crop-save") {
  const saved = page.waitForResponse(response => response.url() === `${base}/api/theme`
    && response.request().method() === "PUT" && response.status() === 200);
  await page.locator(selector).click();
  await saved;
  await page.locator("#wallpaper-crop").waitFor({state: "hidden"});
  await page.waitForFunction(() => !document.querySelector("#theme-controls").disabled);
  return api("status");
}

async function previewPixel() {
  return page.locator("#wallpaper-canvas").evaluate(canvas =>
    Array.from(canvas.getContext("2d").getImageData(480, 270, 1, 1).data));
}

function isColor(pixel, expected) {
  assert.ok(expected.every((value, index) => Math.abs(pixel[index] - value) < 25),
    `Expected ${expected}, received ${pixel}`);
}

before(async () => {
  browser = await webkit.launch();
  context = await browser.newContext({...devices["iPhone 14 Pro"]});
  page = await context.newPage();
  page.setDefaultTimeout(30000);
  await ready();
  original = (await api("status")).theme;
});

beforeEach(ready);

after(async () => {
  try {
    if (original && page && !page.isClosed()) {
      assert.equal((await api("status")).sandbox?.real_device_access, false);
      await api("theme", original);
    }
  } finally {
    await browser?.close();
  }
});

test("portrait crop uploads and persists twice, without applying to the TV", async () => {
  const photo = await portrait();
  const initial = await api("status");
  const uploads = [];
  const listener = request => {
    if (request.url().endsWith("/api/images/wallpaper")) uploads.push(request);
  };
  page.on("request", listener);
  try {
    await choose(photo);
    assert.equal(uploads.length, 0, "Choosing a crop must not upload or save prematurely");
    isColor(await previewPixel(), [32, 208, 80]);
    await range("crop-y", 0);
    isColor(await previewPixel(), [239, 48, 32]);
    const first = await save();
    assert.notEqual(first.theme.wallpaper_file, initial.theme.wallpaper_file);
    assert.deepEqual(first.applied_theme, initial.applied_theme);
    assert.equal(uploads.length, 1);
    assert.ok(uploads[0].postDataBuffer().includes(Buffer.from("Content-Type: image/jpeg")));
    await ready();
    assert.equal((await api("status")).theme.wallpaper_file, first.theme.wallpaper_file);
    assert.match(await page.locator("#wallpaper-state").innerText(), /Saved to system/);
    await choose(photo);
    await range("crop-y", 100);
    isColor(await previewPixel(), [48, 64, 224]);
    const second = await save();
    assert.notEqual(second.theme.wallpaper_file, first.theme.wallpaper_file);
    assert.deepEqual(second.applied_theme, initial.applied_theme);
    await ready();
    assert.equal((await api("status")).theme.wallpaper_file, second.theme.wallpaper_file);
  } finally {
    page.off("request", listener);
  }
});

test("a source photo above 8 MiB is resized before it reaches the upload guard", async () => {
  const photo = await portrait({large: true});
  assert.ok(photo.buffer.length > 8 * 1024 * 1024);
  await choose(photo);
  const upload = page.waitForRequest(request => request.url().endsWith("/api/images/wallpaper"));
  await save();
  assert.ok((await upload).postDataBuffer().length < 8 * 1024 * 1024);
});

test("EXIF-rotated iPhone JPEG opens upright before cropping", async () => {
  const photo = await readFile(new URL("./fixtures/iphone-orientation.jpg", import.meta.url));
  await choose({name: "iPhone.JPG", mimeType: "image/jpeg", buffer: photo});
  await range("crop-y", 0);
  isColor(await previewPixel(), [239, 48, 32]);
  await range("crop-y", 100);
  isColor(await previewPixel(), [48, 64, 224]);
  await page.locator("#crop-cancel").click();
});

test("real HEIC and HEIF convert locally when native browser support is unavailable", {timeout: 90000}, async () => {
  const photo = await readFile(new URL("./fixtures/portrait.heic", import.meta.url));
  const origins = new Set();
  const listener = request => origins.add(new URL(request.url()).origin);
  page.on("request", listener);
  await page.evaluate(() => {
    const create = URL.createObjectURL;
    URL.createObjectURL = blob => create.call(URL,
      ["image/heic", "image/heif"].includes(blob.type) ? new Blob(["native HEIF unavailable"]) : blob);
  });
  try {
    for (const type of ["heic", "heif"]) {
      await choose({name: `portrait.${type}`, mimeType: `image/${type}`, buffer: photo});
      await range("crop-y", 0);
      isColor(await previewPixel(), [239, 48, 32]);
      const state = await save();
      assert.match(state.theme.wallpaper_file, /^wallpaper-[a-f0-9]+\.jpg$/);
    }
    assert.deepEqual([...origins], [new URL(base).origin], "No external conversion service may receive the photo");
  } finally {
    page.off("request", listener);
  }
});

test("failed upload and failed save retain the crop and allow retry", async () => {
  await choose(await portrait());
  await range("crop-y", 100);
  const initial = (await api("status")).theme;
  await page.route("**/api/images/wallpaper", route => route.fulfill({
    status: 422, json: {detail: "Upload rejected for test"},
  }));
  await page.locator("#crop-save").click();
  await page.waitForFunction(() => document.querySelector("#wallpaper-feedback").textContent.includes("Upload rejected"));
  assert.equal(await page.locator("#wallpaper-crop").isVisible(), true);
  assert.deepEqual((await api("status")).theme, initial);
  await page.unroute("**/api/images/wallpaper");
  await page.route("**/api/theme", async route => {
    if (route.request().method() === "PUT") await route.fulfill({status: 503, json: {detail: "Save failed for test"}});
    else await route.continue();
  });
  await page.locator("#crop-save").click();
  await page.waitForFunction(() => document.querySelector("#wallpaper-feedback").textContent.includes("Save failed"));
  assert.equal(await page.locator("#wallpaper-crop").isVisible(), true);
  assert.deepEqual((await api("status")).theme, initial);
  await page.unroute("**/api/theme");
  const saved = await save("#save-theme");
  assert.notEqual(saved.theme.wallpaper_file, initial.wallpaper_file);
});

test("crop dragging, zoom, cancel and mobile geometry remain usable", async () => {
  const initial = (await api("status")).theme;
  await choose(await portrait());
  const canvas = page.locator("#wallpaper-canvas");
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height * 0.8, {steps: 5});
  await page.mouse.up();
  assert.ok(Number(await page.locator("#crop-y").inputValue()) < 50);
  await range("crop-zoom", 2);
  assert.equal(await page.locator("#crop-x").isEnabled(), true);
  assert.equal(await page.locator("#crop-zoom-value").innerText(), "200%");
  await page.locator("#crop-reset").click();
  assert.equal(await page.locator("#crop-y").inputValue(), "50");
  for (const viewport of [{width: 393, height: 852}, {width: 852, height: 393}]) {
    await page.setViewportSize(viewport);
    const geometry = await canvas.evaluate(canvas => ({
      width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
      touchAction: getComputedStyle(canvas).touchAction,
      ratio: canvas.getBoundingClientRect().width / canvas.getBoundingClientRect().height,
    }));
    assert.ok(geometry.scrollWidth <= geometry.width + 1);
    assert.equal(geometry.touchAction, "none");
    assert.ok(Math.abs(geometry.ratio - 16 / 9) < 0.01);
    await page.locator("#crop-save").scrollIntoViewIfNeeded();
    assert.ok((await page.locator("#crop-save").boundingBox()).height >= 44);
  }
  await page.locator("#crop-cancel").click();
  assert.deepEqual((await api("status")).theme, initial);
  await page.setViewportSize({width: 393, height: 852});
});

test("invalid HEIC conversion is an explicit local error, not a saved success", async () => {
  const initial = (await api("status")).theme;
  await page.locator("#wallpaper-upload").setInputFiles({
    name: "broken.heic", mimeType: "image/heic", buffer: Buffer.from("not a HEIC photo"),
  });
  await page.waitForFunction(() => {
    const feedback = document.querySelector("#wallpaper-feedback");
    return feedback.classList.contains("upload-error") && feedback.textContent.includes("conversion failed");
  });
  assert.deepEqual((await api("status")).theme, initial);
  assert.equal(await page.locator("#theme-controls").isEnabled(), true);
});
