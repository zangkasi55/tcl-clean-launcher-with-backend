import assert from "node:assert/strict";
import test from "node:test";

import {cropRectangle, openPhoto} from "../tvcontrol/static/photo-tools.js";

test("portrait crop stays 16:9 and reaches the top, center and bottom", () => {
  for (const [position, top] of [[0, 0], [50, 1262.25], [100, 2524.5]]) {
    const crop = cropRectangle(2016, 3658.5, {y: position});
    assert.equal(crop.width, 2016);
    assert.equal(crop.height, 1134);
    assert.equal(crop.top, top);
    assert.equal(crop.left, 0);
    assert.equal(crop.width / crop.height, 16 / 9);
  }
});

test("landscape crop and zoom never go outside the original photo", () => {
  for (const [width, height] of [[8064, 6048], [1080, 1920], [1920, 1080], [4000, 1000]]) {
    for (const zoom of [1, 1.5, 4]) {
      for (const x of [0, 50, 100]) {
        for (const y of [0, 50, 100]) {
          const crop = cropRectangle(width, height, {zoom, x, y});
          assert.ok(crop.left >= 0 && crop.top >= 0);
          assert.ok(crop.left + crop.width <= width + 1e-6);
          assert.ok(crop.top + crop.height <= height + 1e-6);
          assert.ok(Math.abs(crop.width / crop.height - 16 / 9) < 1e-12);
        }
      }
    }
  }
});

test("invalid crop coordinates fail explicitly", () => {
  for (const args of [[0, 100], [100, -1], [Infinity, 100], [100, 100, {zoom: 0.9}],
    [100, 100, {zoom: 4.1}], [100, 100, {x: -1}], [100, 100, {y: 101}],
    [100, 100, {zoom: NaN}]]) {
    assert.throws(() => cropRectangle(...args), /crop position are invalid/);
  }
});

test("empty, oversized and unsupported source photos fail before decoding", async () => {
  for (const file of [
    new File([], "empty.jpg", {type: "image/jpeg"}),
    {size: 40 * 1024 * 1024 + 1, type: "image/jpeg", name: "too-large.jpg"},
    new File(["not a photo"], "document.svg", {type: "image/svg+xml"}),
    new File(["not a photo"], "document.txt", {type: ""}),
  ]) {
    await assert.rejects(openPhoto(file), /Choose a photo|Choose a JPEG/);
  }
});

test("the exact 40 MiB and 60 megapixel source boundaries are enforced", async t => {
  let height = 6000, released = 0;
  const original = globalThis.Image;
  globalThis.Image = class {
    naturalWidth = 10000;
    naturalHeight = height;
    set src(value) { queueMicrotask(() => this.onload()); }
  };
  t.mock.method(URL, "createObjectURL", () => "blob:photo-limit-test");
  t.mock.method(URL, "revokeObjectURL", () => { released++; });
  try {
    const file = {size: 40 * 1024 * 1024, type: "image/jpeg", name: "large.jpg"};
    const photo = await openPhoto(file);
    assert.equal(photo.width * photo.height, 60_000_000);
    photo.release();
    height = 6001;
    await assert.rejects(openPhoto(file), /at most 60 megapixels/);
    assert.equal(released, 2, "Both success and rejection must release the source image");
  } finally {
    if (original === undefined) delete globalThis.Image;
    else globalThis.Image = original;
  }
});
