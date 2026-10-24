import {copyFile, readFile, writeFile} from "node:fs/promises";
import {fileURLToPath} from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.join(root, "controller", "node_modules", "heic-to");
const target = path.join(root, "controller", "tvcontrol", "static");
const metadata = JSON.parse(await readFile(path.join(source, "package.json"), "utf8"));
if (metadata.version !== "1.6.6") throw new Error("Expected the pinned heic-to 1.6.6 release");
await copyFile(path.join(source, "dist", "csp", "heic-to.js"), path.join(target, "heic-to.js"));
await copyFile(path.join(source, "LICENSE"), path.join(target, "heic-to-LICENSE.txt"));
await writeFile(path.join(target, "heic-to-NOTICE.txt"), [
  "heic-to 1.6.6, by Hopper Gee, LGPL-3.0",
  "The CSP-safe library is distributed unmodified as a separately replaceable ES module.",
  "Application photo-tools.js loads it only when native HEIC/HEIF decoding fails.",
  "Source and build instructions: https://github.com/hoppergee/heic-to/tree/v1.6.6",
  "Corresponding source: https://github.com/hoppergee/heic-to/archive/refs/tags/v1.6.6.tar.gz",
  "Includes libheif 1.23.6 (LGPL-3.0) and libde265 1.0.16 (LGPL-3.0).",
  "libheif source: https://github.com/strukturag/libheif/archive/refs/tags/v1.23.6.tar.gz",
  "libde265 source: https://github.com/strukturag/libde265/archive/refs/tags/v1.0.16.tar.gz",
  "See heic-to-LICENSE.txt for the LGPL terms. No photo is sent to these websites.",
  "",
].join("\n"), "utf8");
console.log("Vendored the pinned CSP-safe HEIC decoder and license notices.");
