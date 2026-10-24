# Technology stack

[Documentation hub](README.md) | [Architecture](ARCHITECTURE.md) | [Build](BUILD.md)

## Versions and sources of truth

| Item | Published baseline | Source of truth |
|---|---|---|
| Controller/deployment bundle | 2.6.23 | [Compose image tag](../docker-compose.yaml), static asset cache versions and [release](https://github.com/zangkasi55/tcl-clean-launcher-with-backend/releases/tag/v2.6.23) |
| Native APK | 2.6.18-tcl, Android version code 17 | [app Gradle configuration](../launcher/app/build.gradle) |
| Python distribution metadata | `tcl-tv-control` 1.0.0 | [pyproject.toml](../controller/pyproject.toml) |
| Locked runtime dependencies | Exact versions for Linux CPython 3.12 | [requirements.lock](../controller/requirements.lock) |
| Browser development dependencies | Exact versions | [package-lock.json](../controller/package-lock.json) |

These are not a single synchronized version. A controller-only release does
not automatically require a new APK. Package metadata 1.0.0 does not mean the
deployed UI is older than bundle 2.6.23.

## Native launcher

| Technology | Version / choice | Purpose |
|---|---|---|
| Language | Java 17 | Native Android activities, views and policies |
| Android API | Minimum 26; compile/target 34 | Android 8+ application API floor; not a promise of every firmware's HOME behavior |
| UI | Android Views and XML layouts | Small, remote-first native UI; not Compose or a WebView |
| Android Gradle Plugin | 8.5.2 | Build and shrink resources/code |
| Gradle wrapper | 8.9 | Reproducible build-tool version |
| RecyclerView | 1.3.2 | App rows and full catalog |
| DynamicAnimation | 1.0.0 | Focus/highlight animation |
| R8 | Release build enabled | Code shrinking, optimization and resource shrinking |
| Tests | JUnit 4.13.2 | JVM navigation, layout and data-policy tests |
| Font | Outfit | Bundled with its SIL OFL notice |

The package ID remains `com.gathin.tvhome` for continuity with the upstream
launcher and the signed installation. The native app has no `INTERNET`
permission. Existing network/Wi-Fi state and fine-location declarations are
used for the network label; accessibility services are explicit compatibility
features, not a general network or background sync client.

## Backend

Python **3.12 or newer** is required by the package. The Docker image uses
`python:3.12-slim`; development has also been performed with Python 3.14.
Use the lock file, not this table, for exact install inputs.

| Library / service | Locked version or mechanism | Role |
|---|---|---|
| FastAPI | 0.143.0 | HTTP routes and request validation |
| Starlette | 1.7.0 | HTTP/static files and streaming responses |
| Uvicorn | 0.54.0 | ASGI server |
| Pydantic | 2.14.1 | Strict configuration and request models |
| SQLite | Python standard library | Settings, usage, sessions and events; WAL journaling |
| adb-shell | 0.4.4 | Authorized private-network TV transport |
| cryptography / rsa | 50.0.2 / 4.9.1 | Cryptographic and ADB authentication support |
| Pillow | 12.3.0 | Server image validation, EXIF orientation and normalization |
| python-multipart | 0.0.32 | Image upload parsing |
| websockets | 16.1.1 | Persistent HA subscriptions |
| tzdata / zoneinfo | 2026.5 / Python standard library | Local-midnight accounting and IANA zones |

The image installs the locked runtime graph, then the controller with
`--no-deps`. No Python HEIC decoder is needed in production because the
browser prepares a supported JPEG/PNG before upload.

## Web UI and photo processing

| Technology | Choice | Role |
|---|---|---|
| HTML/CSS | Responsive, locally served static files | Desktop, mobile and HA iframe management |
| JavaScript | Native ES modules, no frontend bundler | UI, requests, pointer/keyboard crop interaction |
| Canvas | Browser API | Preview, crop and JPEG/PNG preparation |
| heic-to | 1.6.6, vendored CSP-safe module | Local fallback HEIC/HEIF decoding |
| libheif / libde265 | 1.23.6 / 1.0.16 inside converter | HEIF container and HEVC decoding |
| Web Worker | Same-origin blob worker | Fallback decoding on the user's device |
| Live energy | Server-sent events | Browser updates without waiting for full status refresh |
| Browser regression tests | playwright-core 1.64.1 and installed WebKit | iPhone-oriented emulation and real HEIC fixture testing |

Native browser image decoding is preferred. The approximately 3.15 MB decoder
is lazy-loaded only if native HEIC/HEIF decoding fails. It is served from the
controller, not from a CDN. Node.js is for development/tests and vendoring,
not a NAS runtime dependency. Node 24 was used for the published validation;
the project does not currently declare a formal Node `engines` range.

## Deployment and access boundary

- Synology DSM Container Manager / Docker Compose v2.
- A local image build, not a pre-published Docker Hub or GHCR image.
- Container runs as UID/GID **10001**, with no added Linux capabilities.
- Port **8787** binds to host **127.0.0.1**, behind a separate HTTPS proxy.
- Dedicated persistent volume, read-only container filesystem and private tmpfs.
- Secure, HttpOnly, SameSite=Strict sessions; same-origin and CSRF write checks.
- Exact HTTPS frame origins for compatible same-site HA embedding.
- CSP keeps scripts self-only and permits the local HEIC worker through
  `worker-src 'self' blob:`; no `unsafe-eval` is needed.
- No cloud account, analytics integration or external photo-conversion service
  is required by this application's runtime.

Installed streaming apps and optional HA integrations have their own independent
network behavior; this project's design does not change or audit those services.

## Resource and input limits

| Area | Current enforced limit / behavior |
|---|---|
| Source photo in browser | 1 byte through 40 MiB; up to 60,000,000 decoded pixels |
| Crop | Fixed 16:9; zoom 1-4; horizontal/vertical position 0-100% |
| Prepared wallpaper upload | 1920x1080 JPEG; below the 8 MiB client guard |
| Prepared artwork upload | Fitted 640x360 PNG, without stretching |
| Server image upload | 8 MiB; at most 20 million decoded pixels |
| Final server wallpaper | At most 300 KiB |
| Native panel snapshot | At most 16,384 bytes per JSON file |
| Favorites and columns | 1-7 distinct favorites; 4-7 columns; favorites must fit |
| App-order list | At most 256 distinct activity components |
| Daily allowance | 1-1,440 minutes; starts disabled |
| Real-device sampling | About 5 seconds; not a precise playback-duration meter |
| Container memory | 512 MiB in supplied Compose |
| Container temporary storage | 32 MiB tmpfs in supplied Compose |
| CI APK size ceiling | Strictly less than 409,600 bytes |

Boundary validation is not a guarantee that every phone can decode a maximum
size photo within its memory budget. A smaller JPEG is the practical recovery
option if the device cannot prepare a large source.

## Test and release tooling

The repository contains pytest/httpx backend tests, Ruff checks, Node unit
tests, WebKit regressions, JVM tests and upstream Python unittest checks.
The current [GitHub workflow](../.github/workflows/ci.yml) automatically runs
the launcher build/lint/JVM tests, APK size check and upstream lists/tools/docs
tests. Backend and photo suites must still be run separately by contributors;
they are **not automatically covered by that workflow**.

See [build and testing](BUILD.md) for exact commands and
[contributing](../CONTRIBUTING.md) for the review matrix.
