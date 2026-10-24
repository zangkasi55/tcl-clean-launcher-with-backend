# TCL Home Control

A separate Synology Container Manager project and a native Android TV launcher.
It does not modify the Home Assistant container, flash firmware, clear app data,
or provide package-disabling controls. Explicitly approved maintenance exceptions
can be recorded separately from the original app-state lists.

## Included

- Five distinct favorites: **YT, Netflix, GodTV, Monomax, YouTube**.
  YT is `com.thegate.tv`; official YouTube is `com.google.android.youtube.tv`.
- Full app catalog, including ordinary launcher entries and distinct aliases.
- Wallpaper and official YouTube artwork upload/reset, without modifying APKs.
- A LAN-restricted dashboard with configurable target app, minutes and timezone.
- Persistent daily allowance, initially **YT / 40 minutes / Asia/Bangkok**.
- Discrete standby, never a power toggle, after the daily allowance is exhausted.
- Original Home/app inventory capture and staged install/restore controls.
- Lower-screen **Energy Distribution** and daily weather cards, each 30% of screen width.
- An isolated sandbox with a manual clock, simulated TV and energy scenarios.

### Saving a wallpaper or logo

Choose a wallpaper in **Home design** to open a **16:9 crop preview**. Drag the photo
with a finger or mouse, or use Horizontal position, Vertical position and Zoom.
The arrow keys also move the photo; Reset crop returns to the centered, unzoomed
selection. This lets you choose which part of a portrait photo fills the landscape
TV without stretching it. Cancel new photo leaves the saved wallpaper unchanged.
Your original photo is never modified.

Tap **Upload & save to system** to upload the selected crop and save the current
design in one action. Wait for the saved confirmation beside the image. The crop
stays open on failure so you can retry; a failed upload/save is never reported as
saved. The regular Save to system and Apply to TV buttons also include a pending
crop, so they cannot accidentally save the old wallpaper instead.

Source photos up to **40 MiB / 60 megapixels** are prepared locally before uploading.
JPEG, PNG and WebP work in supported browsers. **HEIC/HEIF converts automatically
on the user's device**: native decoding is used when available, otherwise the
locally served CSP-safe HEIC decoder prepares its pixels in a local worker before
the crop opens. The selected crop is then encoded as JPEG when saving.
No photo is sent to an external service, and the original HEIC/HEIF is not sent
to the NAS. Conversion failures appear beside the picker rather than being
reported as successful uploads. Every wallpaper is encoded as a 1920x1080 JPEG;
logos become a fitted 640x360 PNG without stretching.
This avoids rejecting large iPhone photos before they can be resized and keeps
HEIC data from being sent to a server decoder that does not support it.
The NAS's **8 MiB upload, 20-million-decoded-pixel and 300 KiB final wallpaper**
limits remain enforced. File selections are cleared after processing so choosing
the same photo again works. Saved thumbnails and status remain after reload.

- **Save to system** keeps the entire current design, including the image, on the NAS
  so it survives a page reload or controller restart. It does not change the TV.
- **Apply to TV** saves the current design and sends it, including its images, to the
  TV after confirmation. You do not need to save separately first.

Choosing a wallpaper alone does not upload or save it: confirm the crop first.
Replacement YouTube logos still upload for preview immediately and require Save
to system or Apply to TV. Design controls are disabled while preparing or saving
an image. Upload, save and apply failures appear beside the image as well as in
the page error banner. Neither saving a wallpaper nor cropping restarts the TV.
Use **Use plain background** or **Reset to original YouTube artwork**, then save or
apply, to keep an image removal. No APK rebuild is needed.

The Synology deployment opens **without an additional app password**, as requested.
Keep the reverse proxy restricted to the home LAN. Any allowed LAN device can edit
the rules. CSRF/origin validation and secure browser-session cookies remain enabled.
The standalone service defaults to password protection unless
`TVCONTROL_REQUIRE_LOGIN=false` is explicitly set; the supplied Compose profile sets it.

### Gradient and header colors

Home design includes a gradient color and 0-100% strength, plus individual colors
for the clock, date, Inputs, Wi-Fi and Settings. The shortcut colors apply to both
text and icons; network/VPN warning badges keep their semantic status colors.
The date retains its existing softer opacity. **Save to system** persists the
colors; **Apply to TV** saves and displays them without another APK rebuild.
The gradient defaults to **0%** so an existing photo is not unexpectedly tinted.
Raise its strength to make the chosen overlay color visible.

**Focused app color glow on the TV** is a separate layer sampled from the focused
app artwork. For example, a red app can cast a red wash over a face even with
gradient strength at 0%. Turn this switch off and **Apply to TV** to remove that
layer while retaining the glass focus highlight, app labels, selected gradient,
header colors and photo. It defaults to enabled for older saved themes; disabling
it is persisted with the design. The browser labels it TV-only because its static
preview does not sample native app artwork.

### Weather forecast

The weather card sits directly beside Energy Distribution; each card occupies
**30% of the entire TV screen width**, with the remaining area left clear.
It shows current temperature/conditions and three daily forecast entries, with
high/low temperatures in the source's Celsius or Fahrenheit units.

Enable the weather feed and select an exact HA weather entity in Home design.
The verified home source is **`weather.forecast_home`**, using HA's
`weather/subscribe_forecast` daily subscription plus its current-state subscription.
Both use the existing backend HA URL/token-file authorization. No HA token, network
permission, login page, or web browser is added to the TV.

Weather follows the same bounded, checksum-verified local-file delivery as energy.
The two cards share the lifecycle-bound JSON reader and stop reading while hidden.
Unchanged forecasts refresh the TV's contact timestamp at most once a minute.
Loss of HA contact is marked stale after 15 minutes; missing forecasts/units or
unsupported daily forecasts are explicit errors, never fabricated zero temperatures
or silently substituted simulated data. Old real forecasts remain labeled stale.
Simulation is available only as an explicitly selected sandbox source.

For password-free viewing with parent-only settings, optionally set
`TVCONTROL_MANAGEMENT_NETWORKS` to a comma-separated allowlist of parent device
addresses/CIDRs (for example `192.168.1.20/32`). Other allowed clients can view the
dashboard but cannot change rules, install apps, select Home, or send standby.
The check uses the server's resolved client address, never an arbitrary browser
`X-Forwarded-For` header. If DSM proxies requests, configure Uvicorn's trusted
forwarding addresses for only the actual proxy and verify the parent address
before enabling this allowlist. Do not trust every proxy or include the whole
child/guest network. No parent IP was guessed or applied to the live NAS; an empty
allowlist preserves the existing LAN-only trust model. The loopback sandbox
ignores production management-network settings.

The allowance starts **disabled** so installation cannot unexpectedly turn off a TV.
Enable it explicitly after confirming the app, limit, timezone and device authorization.
Menus and paused video count while the selected app is foreground and the TV is awake.
App switches and TV/controller restarts do not erase the day's stored consumption.
Reopening an exhausted app triggers standby again. Other apps are not restricted.
Changing the limit preserves consumption; enabling an already-exhausted rule requires confirmation.

This is a NAS-based convenience timer, not a tamper-proof parental lock.
The NAS and authorized ADB connection must be available. Unobserved gaps are not charged.
Sampling is every five seconds: short transitions can be missed, and enforcement follows
the first valid sample after 2,400 counted seconds, not a millisecond-accurate playback meter.
It applies to the TV/app regardless of the viewer; it does not identify a child account.
The final power check also expires at local midnight (or after a stale observation),
so yesterday's exhausted allowance cannot put the TV into standby after a delayed ADB response.
Failed standby attempts are rate-limited and are never reported as confirmed standby.

## Local development (Windows)

From the repository root:

```powershell
python -m venv .venv
$env:PIP_NO_DEPS = 'false'
.\.venv\Scripts\python.exe -m pip install -e '.\controller[test]'
.\.venv\Scripts\python.exe -m pytest controller\tests -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check controller
node --check controller\tvcontrol\static\app.js
```

The browser-only HEIC converter is pinned in
[`package-lock.json`](package-lock.json). To reproduce its unmodified CSP-safe
asset and run the crop geometry checks:

```powershell
npm --prefix .\controller ci --ignore-scripts
npm --prefix .\controller run vendor:photos
npm --prefix .\controller run test:photos
```

`heic-to.js` is lazy-loaded from the controller, not a CDN. Its LGPL license and
exact upstream source/build links are shipped beside it. Conversion uses a local
blob worker; CSP explicitly permits that worker while keeping script sources
self-only (no unsafe-eval), exact HA frame origins, Secure/HttpOnly/Strict
sessions, CSRF checks and all server image limits.

For the real WebKit photo-selection/save/reload and automatic HEIC/HEIF regression,
start a separate `--sandbox` instance with `TVCONTROL_SANDBOX_PORT=18796` and an
isolated `TVCONTROL_SANDBOX_DATA_DIR`, then run:

```powershell
npm --prefix .\controller exec -- playwright-core install webkit
npm --prefix .\controller run test:photos:webkit
```

The browser test refuses to write unless the server explicitly reports
`sandbox.real_device_access=false`. Its HEIC and EXIF fixtures are original
synthetic red/green/blue test patterns, not user photos. It covers real local
HEIC/HEIF conversion with native support disabled, repeated persistence, large
source preparation, crop geometry/dragging, failure/retry, and mobile orientation.

### Sandbox: the current development mode

No NAS, TV, Android SDK, APK, or pytest import is needed to run the sandbox once
the controller's runtime dependencies are installed:

```powershell
.\.venv\Scripts\python.exe -m tvcontrol --sandbox
```

Open **http://127.0.0.1:8790/**. No password is required.
The server binds to loopback even if a production listen address is configured.
`scripts\preview.py` remains a compatibility entry point.

Sandbox data is stored separately in `%LOCALAPPDATA%\TclHomeControl-sandbox`
(or `TVCONTROL_SANDBOX_DATA_DIR`). A runtime-mode marker prevents reusing either
the real controller database in the sandbox or sandbox data in real-TV mode.
The simulated device has no ADB transport. Its energy source is separate from TV
simulation: the development entry point defaults to **live Home Assistant data**.
If no authorized live feed is available it shows waiting/unavailable, never screenshot values.
Explicit simulated energy remains available only as a selectable test mode.

Use **Sandbox controls** to choose the foreground app, wake/sleep the simulated TV,
take the connection offline, advance time, and skip to a new day.
The simulated clock is manual: waiting in the browser does not consume app time.
Rules, usage, time, power state and applied design survive a sandbox restart.
The TV catalog is a clearly identified sample containing the current favorites and
the other apps seen in the home row, not a claim that a live TV was inspected.

For an exact timer test:

1. Enable **YT / 40 minutes / Asia/Bangkok** in Daily allowance.
2. Set the simulated foreground app to YT and the TV awake/online.
3. Advance **2,399 seconds**: no standby should occur.
4. Advance **1 second**: usage reaches 2,400 seconds and standby is confirmed.
5. Wake the exhausted app: it should immediately return to standby.
6. Select **Next day**, then wake it: the new allowance is available.

Day jumps skip unobserved time rather than charging it. Normal Advance steps use
the same monitor/accounting/enforcement logic as real mode.
The simulated TV image is illustrative, not an Android emulator or a real screenshot.
Actual firmware, D-pad navigation and real standby will be validated after returning home.

### Optional real controller: use only when deployment is resumed

Run the real controller on loopback, using private storage outside OneDrive:

```powershell
$env:TVCONTROL_DATA_DIR = Join-Path $env:LOCALAPPDATA 'TclHomeControl'
$env:TVCONTROL_SECURE_COOKIE = 'false' # loopback development only
.\.venv\Scripts\python.exe -m tvcontrol
```

Open `http://127.0.0.1:8787`. For passwordless loopback development set
`TVCONTROL_REQUIRE_LOGIN=false`. If password protection is enabled, a random initial
parent password is written to
`initial-admin-password.txt` in the configured data directory. It is not printed,
stored in the frontend, or included in deployment packages.
Alternatively set `TVCONTROL_ADMIN_PASSWORD` to at least 12 characters before startup.
Never use HTTP for a remotely accessible deployment.

## Build the native APK

Use JDK 17 and Android SDK platform 34/build-tools 34.0.0. The Windows build helper
also recognizes portable tools under `%LOCALAPPDATA%\TclTvBuild`.

```powershell
.\scripts\build-launcher.ps1
```

It runs the JVM suite, builds and verifies `dist\tvhome-release.apk`, and creates/reuses
a private signing identity under `%LOCALAPPDATA%\TclTvBuild\signing`.
Build outputs and the project cache are kept under a project-specific
`%LOCALAPPDATA%\TclTvBuild\builds` directory to avoid OneDrive incremental-resource locks.
`dist\native-test-results.json` records the validated JVM test totals.
Keep both the keystore and its password file backed up securely outside source control
and cloud-synced project folders; losing them prevents signed in-place updates.
The controller release property uses this identity; the original upstream build path
retains its debug-signing behavior for backward compatibility.

## Synology Container Manager

Generate a pinned runtime lock and minimal deployment archive:

```powershell
.\.venv\Scripts\python.exe scripts\lock-runtime.py
.\.venv\Scripts\python.exe scripts\package-synology.py
```

1. Upload/extract `dist\tcl-home-control-synology.zip` to a **new dedicated project
   directory** on the NAS. Do not overwrite another project.
2. In Container Manager, create a new project named `tcl-home-control`, using that
   directory and its `docker-compose.yaml`. The image builds from the included APK
   and controller; no Android compiler or SDK runs on the NAS.
3. Keep the default localhost-only binding. Configure a separate DSM HTTPS reverse
   proxy to `http://127.0.0.1:8787`, without replacing an existing DSM/HA rule.
   Set `TVCONTROL_PUBLIC_ORIGIN` to the actual HTTPS origin, including a non-default
   port if used. Do not invent DNS/certificate entries or expose the raw API publicly.
4. The named volume `tvcontrol-data` contains configuration, usage, sessions, images
   and this controller's private ADB key. It persists across rebuilds. Do not delete
   it when recreating the container.
5. Open the secured management URL directly; the provided LAN-only profile does not
   ask for an app password. If you later enable `TVCONTROL_REQUIRE_LOGIN=true`, read
   `/data/initial-admin-password.txt` in the container terminal for initial login,
   or supply a strong password through the supported environment setting.
6. Enable TV USB/network debugging, then approve **this container's** key on the TV.
   A PC's previous authorization does not automatically authorize the NAS.
7. Inspect the TV and download the saved app baseline. Install/open the launcher,
   test the five apps and remote, then choose the clean Home deliberately.
8. Upload your wallpaper and custom logo, apply the design, and explicitly enable
   the daily allowance after testing a standby action.

The app runs as UID/GID 10001, without elevated capabilities or Docker socket access,
with a read-only root filesystem and a dedicated persistent volume.
If using a bind mount instead, provision that one data directory for UID/GID 10001;
do not broadly change NAS permissions or use world-writable folders.

### Updating an existing NAS installation

Version **2.6.23** adds touch-friendly portrait-to-landscape wallpaper cropping and
an explicit upload-and-save action, with browser-side preparation for large
iPhone photos and automatic local HEIC/HEIF conversion. It does not require a launcher APK
update. Version **2.6.20** restores live energy after HA moves its renderer into a shared
button-card template, and supports exact multiple HA frame origins for iPhone app
embedding. It also adds mobile touch targets and refresh on returning to the app.
This controller-only update does not require a new launcher APK.
The existing launcher adds a switch to remove the focused-app wallpaper color glow.
It also separates HA-source and TV-delivery freshness so a delayed local
heartbeat does not falsely mark a healthy source stale. It includes gradient/header colors, a 30%-width weather forecast, and
HA Energy Distribution topology/source-quality matching. It also includes the
lower favorites row, separate All apps screen, and high-resolution image resizing.
Extract the updated deployment archive into the existing TCL project's build
directory, preserving its private environment settings and persistent data.
In Container Manager, rebuild the existing `tcl-home-control` project from that
directory. Keep the same project name and `tvcontrol-data` volume; do not delete
or recreate the data volume. Reload the TCL page after the rebuild (use a hard
refresh if it still shows the old controls).

After rebuilding the controller, use **Install/update launcher** in Device & recovery
to install the signed launcher update in place. No other apps, saved logins, theme
images, default-Home choice, or Home Assistant configuration need to change.

## Home Assistant link/embedding

Home Assistant is optional. Link to the secured management URL.
Embedding requires compatible HTTPS/same-site browser-session cookies and
an explicit allowlist of exact HA HTTPS origins. The original
`TVCONTROL_FRAME_ORIGIN` remains supported. For both home server names use:

```yaml
TVCONTROL_FRAME_ORIGINS: "https://nitarnhome.synology.me,https://home.nitarnhome.synology.me"
```

A frame does not inherit HA login. Passwordless mode still needs a same-site
CSRF session, not a login. Frame permission does not grant cross-origin API write
access; secure HttpOnly SameSite=Strict cookies and the existing CSRF check stay enabled.
When sites are not same-site, use a link rather than weakening cookie security.

### Home Assistant Companion app on iPhone

The reusable [TCL TV view](home-assistant/tcl-tv-view.json) uses the built-in
button card and the already-installed `custom:button-card`. Replace only the
existing `tcl-tv` view with this object in the dashboard's raw configuration;
do not replace the whole dashboard or its shared energy templates. JSON is valid
YAML for this purpose. The Synology build archive includes this optional view.

The view always provides **Open TCL controls**, which opens the controller as a
separate page rather than inside HA's iframe. It embeds the controller only at
the two explicitly allowed HTTPS HA origins above. At a local HTTP/IP address
or any other origin, it shows the direct action and address-setup guidance
instead of requesting a frame that the controller must block. This does not
change HA's global internal/external URLs, controller security settings, or the
TV's saved configuration. If the controller is unreachable or its embedded
session fails even at a permitted origin, the direct action remains available.

Use the **Nitarn Home / TCL TV** view while connected to the home LAN or the same
authorized VPN. For inline controls, open **Settings > Companion app**, select
this HA server, and set its **Internal URL** to one of the exact HTTPS origins
above. Check its external URL too if the app switches addresses away from home.
An IP address, alternate hostname, or HTTP URL is a different origin and is not
implicitly allowed. Both HA and the TCL controller address must be reachable by
the phone; a working HA connection alone does not prove the TCL hostname is
reachable. Do not expose ADB to the phone or internet.

The portal supports portrait/landscape scrolling, touch-sized actions, photo file
selection, and Save to system / Apply to TV beside the image picker. Choose a
wallpaper, position its 16:9 crop, then tap Upload & save to system. JPEG, PNG,
WebP and HEIC/HEIF photos up to 40 MiB / 60 megapixels are prepared locally below
the server's 8 MiB upload limit. HEIC/HEIF conversion automatically falls back to
the locally served decoder when native browser support is unavailable.
Returning from a suspended mobile WebView refreshes status and reconnects the
energy stream without discarding unsaved design changes.

If the phone uses a different-site HA address or its WebView blocks storage,
open the HTTPS TCL controller directly rather than disabling cookie protections.
Safari/WebKit emulation can check the portal, but a physical Companion-app smoke
test is still required for a particular iPhone/iOS installation.

## Energy distribution

The **Energy Distribution** panel follows the configured Home Assistant card:
solar/home in orange, grid in blue, and battery in pink, with directional flow indicators.
It stays below the app dock and does not remove any apps.
Home shows only the favorites row and compact information cards near the bottom,
leaving the upper photograph unobstructed by app rows. All apps live on a separate
screen, reached by pressing Down from favorites.
Apps with dimensionless placeholder banners use their original app icon instead of a blank tile.
It is **left-aligned** and occupies **30% of the entire screen width**, not 30% of
the padded app row. The native panel is 128 dp high and retains its text sizes.
The browser preview uses the same 30% screen-width rule, including on narrow screens.
Turning off one card leaves the other in the same information row. Turning off both
removes the row and keeps favorites anchored lower down.

### Matching the updated HA Energy Distribution card

Device & recovery can bind directly to a dashboard URL path and view title/path.
The verified home card is on **`nitarn-home` / `Home`**. The controller reads only
its declared sensor bindings and quality flags, independently of an inline or
shared-template renderer; it **never executes dashboard JavaScript**. Cards in
sections, stacks, and single-card wrappers are supported. Ambiguous cards and
invalid bindings fail explicitly instead of selecting an arbitrary source.
It subscribes only to those entities and rechecks the declarations
once a minute, so changes to commissioning/offline flags are reflected automatically.
Each dashboard refresh uses a new WebSocket request ID, as required by HA; repeated
refreshes do not reuse the subscription or earlier configuration-command IDs.

The compact TV/preview topology includes **Enphase Solar, ESS Solar, the ESS inverter,
grid import/export, ESS Battery charge/discharge/SOC, and Home**. The Enphase node
uses the live Envoy production monitor, including negative standby draw.
Planned ESS PV remains labeled **Not installed** with an inactive dashed edge.
An old `-1 W` nighttime sample is not a daytime production reading: a broken feed
is shown stale, with the connection error and last-received timestamp in the portal.
Negative measured standby draw is preserved rather than silently clamped to zero.

When the HA card marks **ESS cloud offline**, even cached ESS sensor states with
plausible numbers are unavailable, not zero power. Home may display the verified
live **Enphase CT fallback**, explicitly
labeled as such; grid/battery/SOC remain `n/a`. Partial Enphase readings may animate
their verified path, but an unverified whole-system balance or CT-only Home value
does not imply a verified energy flow. Units must be W/kW with device class `power`;
the configured balance tolerance (currently 50 W) and directional signs follow HA.

The older individual-sensor mapping remains available when automatic HA-card binding
is turned off. The sensor table below documents that legacy ESS-Link mapping.

The **TV does not authenticate to Home Assistant**. The panel appears automatically
with the clean home screen. No HA login page, token, or password is placed on the TV.
Authentication to HA stays in the backend, or in the already-authorized HA browser
used temporarily during sandbox development.

The default development entry point now uses live data, not the example screenshot.
For deliberate fixture testing only, select **Explicit simulated test data** under
the energy connection settings. That mode offers the screenshot's 3.47 kW / 880 W /
24% / 2.59 kW example plus charging/discharging/export presets. It is always marked
Simulated and is never a fallback for a failed live feed.
Power signs are:

- Grid positive: import; negative: export.
- Battery positive: discharge; negative: charge.

### Verified ESS-Link Portal sensor mapping

The actual `ESS-Link Portal Flow` and `ESS-Link Portal Balance` cards were inspected
through the user's authorized HA page. Their exact mapping is:

| Meaning | Home Assistant entity |
|---|---|
| Solar | `sensor.ess_link_pv_power` |
| Home load | `sensor.ess_link_load_power` |
| Grid into home | `sensor.ess_link_grid_export_power` |
| Grid out of home | `sensor.ess_link_grid_import_power` |
| Battery discharge | `sensor.ess_link_battery_discharge_power` |
| Battery charge | `sensor.ess_link_battery_charge_power` |
| Battery charge % | `sensor.ess_link_battery_soc` |

The raw grid names are intentionally reversed in the real Portal Balance card:
its `grid_in` uses the raw `grid_export_power` sensor. The preset follows that
verified configuration, not a guess from the entity name. A nonzero live balance
was checked: 0.73 kW solar + 0.63 kW grid + 0.82 kW battery = 2.18 kW home.

The supplied NAS profile sets `TVCONTROL_HA_PROFILE=ess_link`, which enables this
mapping automatically on a new installation. Existing saved mappings are preserved.
Other installations can configure either signed sensors or separate import/export
and charge/discharge pairs under Device & recovery:

- Solar instantaneous power.
- Home instantaneous power.
- Grid instantaneous signed power.
- Battery instantaneous signed power.
- Battery charge percentage.

The backend accepts **W or kW** and normalizes to watts. Separate pairs are combined
as import minus export and discharge minus charge. It rejects kWh totals,
unknown/unavailable/non-finite sensor states, and battery percentages outside 0–100.
Use the two sign toggles if your sensors use the reverse convention.
Flow arrows represent reported import/export/charge/discharge, not inferred allocation
between individual sources.

Set `TVCONTROL_HA_URL` to the HA origin, such as `https://home.nitarnhome.synology.me`,
and `TVCONTROL_HA_TOKEN_FILE` to a **container-local read-only token file**. For example,
a later deployment override can mount a dedicated secret:

```yaml
services:
  tvcontrol:
    environment:
      TVCONTROL_HA_URL: "https://home.nitarnhome.synology.me"
      TVCONTROL_HA_TOKEN_FILE: "/run/secrets/ha-token"
    volumes:
      - "./secrets/ha-token:/run/secrets/ha-token:ro"
```

Do not commit that token, paste it into chat, put it in a theme, or send it to the TV.
Use an appropriate dedicated Home Assistant account/token and preserve TLS verification.
Redirects are refused rather than forwarding the authorization header elsewhere.
Only read-only state requests are made; no HA service calls modify your energy system.

The persistent backend uses HA's **WebSocket entity subscription**, filtered to the
configured sensors, not 30-second REST polling. Reported state changes are delivered
immediately, with connection heartbeats while values are unchanged. HA controls the
underlying sensor sampling rate; the display cannot invent faster hardware measurements.

The backend delivers a small, checksum-verified local `energy.json` snapshot over
the existing authorized ADB channel after launcher installation. The native launcher
checks the local file every **one second** while visible. It needs no network
permission, embedded HA page, login token, or additional background service.
The browser uses a server-sent event stream instead of waiting for dashboard refresh.
Loss of HA contact is labeled stale after 15 seconds. The TV receives that explicit
stale/unavailable status without a grace period. Its separate local-delivery timeout
is 30 seconds, allowing one delayed ten-second heartbeat during priority TV-control
work without falsely labeling a healthy HA source stale. If the controller/ADB link
stops delivering frames, the TV still expires the last live frame after 30 seconds.
Last values are never replaced with simulated readings.

## Performance and responsiveness

Home and All apps are separate screens. Favorites sit low, with Energy Distribution
and weather below them on the left and a clear area to their right. Down from favorites opens the
complete installed-app catalog, including favorites. Up from its first row or Back
returns Home; the remote Home button returns to the first favorite. Each page keeps
its focused app when returning from another app, and the read-only energy row cannot
trap focus. The browser preview has matching Home/All apps controls and keyboard
navigation. The sandbox screenshot is an illustrative Home composition, not an
Android emulator.

- Live sensor ingestion updates a single in-memory latest snapshot. Intermediate bursts
  are coalesced, not accumulated in an unbounded ADB queue.
- Value changes are eligible for delivery once per second. Identical readings only need
  a ten-second TV heartbeat, safely inside the 15-second live-contact freshness window.
- Routine heartbeat processing does not commit telemetry to SQLite. A graceful shutdown
  checkpoints the last sample; after restart that sample is explicitly stale until the
  live source reconnects. Rules and daily usage remain transactionally persistent.
- Energy delivery yields to foreground/power checks and never starts a new ADB pairing
  connection. One checksum-verified atomic shell transfer has a two-second I/O budget,
  rather than multiple long shell/file-push operations holding the TV control lock.
- APK and theme-image uploads use the upstream shell/base64 chunk-transfer approach,
  capped to the TV's negotiated ADB payload size. Large ADB SYNC pushes timed out on
  the real TCL during staged installation; chunks are SHA-256 verified before an
  atomic rename, and failed transfers never replace an existing destination.
- The TCP transport completes short socket writes within the original write deadline.
  On the NAS a 65,103-byte ADB packet initially sent only 44,864 bytes; the upstream
  transport discarded the remainder and then timed out. Completing the packet fixes
  large updates without shrinking chunks or extending background telemetry timeouts.
- Package-installer shell commands retain their 30-second read budget: a real
  six-second no-output command exposed an accidental five-second transport timeout.
  Background energy delivery still has its separate two-second limit.
- Timer sampling accounts for the sample duration rather than adding five seconds of
  sleep after every operation. Unknown/offline time still is not invented as usage.
- Native energy files are checked/read/parsed on a lifecycle-bound worker, not Android's
  UI thread. Unchanged values/heartbeats do not invalidate the view. Labels are formatted
  once per visible change and cached; arrow paths are reused while drawing.
- Dashboard refresh retains unchanged app/favorite nodes, images and keyboard focus.
  Content-addressed images use private immutable caching; control/state responses remain
  `no-store` and still require the same session/CSRF checks.

Reproduce the offline work-count benchmark without touching HA, ADB or your real TV:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark-performance.py --assert-optimized
```

For 60 identical updates, the baseline made 60 publications and 120 SQLite commits.
The optimized burst makes 1 publication and 0 commits; five-second steady heartbeats
make 30 publications and 0 commits. These are repeatable work counts, not an on-TV
FPS or startup-time claim. Validate actual Home/remote/standby latency on the physical
TCL when real deployment resumes.

### Temporary live source during sandbox development

The development bridge subscribes to the seven real sensors in the already-authorized
shared HA page. `static/ha-live-bridge.js` contains the small browser-side adapter.
The browser automation host relays only sensor states and units to the local sandbox,
using a same-origin callback in the local dashboard. HA access/refresh tokens never
leave HA, and no new HA token was minted for this development session.
Keep the HA page, local dashboard and connected VS Code browser host open during this mode. The local dashboard
also supplies a heartbeat because hidden tabs may throttle timers. Reloading the local
dashboard reinstalls that heartbeat while its authorized browser-host callback exists.
Closing/disconnecting the bridge marks data stale rather than falling back to fixtures.

This temporary browser bridge is **not** the permanent TV deployment path.
After returning home, the NAS backend's own authorized WebSocket connection provides
the persistent live feed independently of open browser tabs. That one-time backend
setup does not introduce any TV authentication or login prompts.
Core app launching and the daily timer do not depend on Home Assistant.

## Preservation and recovery

The first inspection saves the actual stock Home and installed/enabled/disabled sets.
Subsequent operations verify that the original apps and states are preserved.
By default, any newly disabled package still fails that check. For a user-approved
privacy maintenance change, the saved baseline can carry an
`approved_disabled_packages` list without changing its original `installed`,
`enabled` or `disabled` arrays. Only original enabled packages are eligible;
Android, the launcher, stock Home and the default five favorites cannot be
approved this way. Unknown, duplicate or malformed approvals fail explicitly.
The dashboard displays the exceptions and warns when its last inspection did
not confirm them disabled. All original packages must remain installed, and an
unapproved state change still blocks installation, theme and Home operations.
Saved ordering/artwork for an approved disabled package remains valid so restoring
it does not lose the original layout; disabled packages cannot become favorites.
Record the exact prior Android enabled state and keep a separate unmodified
baseline before adding approval metadata. Remove approvals only after restoring
their original states. Package disabling is not a guarantee against every ad or
vendor connection; mixed-purpose input/casting/update services require narrower
privacy controls, and firmware updates may change package states.
Installing the new launcher opens it but does not change default Home.
Selecting Home is a separate confirmed operation; no stock package is disabled.
This normal/compatibility operation never disables packages. The separately confirmed
**Make true default + NAS failback** operation is described below.
On Android 10+, normal selection uses the standard Home role command. Actual testing
showed this TCL firmware caps third-party HOME filters to priority 0 while stock
Google TV has priority 2; even an accepted role choice may therefore lose resolution.
When this happens the controller restores the stock role and requires explicit
confirmation for its minimal Home compatibility service, rather than disabling apps.
The service only observes Google TV's Home window appearing. It cannot retrieve screen
content or filter key presses, and ignores account/settings windows and other apps.
Restoring original Home removes only this service from the enabled list, preserves
other accessibility services and restores the prior global flag when appropriate.
The controller reports compatibility mode honestly instead of claiming the firmware's
default component changed. An initial stock-Home frame may appear briefly during redirection.

### True-default Home: verified TCL restrictions

On the tested TCL Android 14 firmware `V8-T653T01-LF1V643`,
`cmd package set-home-activity` returns `Success` and assigns the Home role to
`com.gathin.tvhome`, but actual HOME resolution still selects Google TV at
priority 2. The setup wizard's `RecoveryActivity` is another HOME candidate at
priority 1; Clean Home is priority 0. Always verify the resolved activity, not
just the command result or role holder.

[Other TCL owners](https://xdaforums.com/t/replace-tcl-c735-55-launcher.4574095/)
have obtained a true default by disabling the complete Google TV launcher and
setup-wizard packages. Disabling only their competing HOME activities would
retain more functionality, but the tested TV rejects that operation with
`SecurityException: Shell cannot change component state`. This is consistent
with [Android 14's package-manager restriction](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-14.0.0_r1/services/core/java/com/android/server/pm/PackageManagerService.java):
shell cannot change components of a non-test package.

The inspection also found a saved Wi-Fi configuration owned by
`com.google.android.tungsten.setupwraith`. Disabling that package can delete its
saved networks, including connectivity needed for ADB recovery. Do not use the
whole-package method until the affected networks have been forgotten and
rejoined through the TV's Network & Internet settings, or privately preserved and
recreated through the authorized Android Wi-Fi service. Verify their creator is
no longer the setup wizard and their security, credentials and IP settings are
unchanged before proceeding. Ethernet provides
an independent recovery connection but does not preserve deleted Wi-Fi settings.
See [the network-preservation warning](../docs/TROUBLESHOOTING.md#working-on-the-tv).

**Make true default + NAS failback** is an explicit opt-in, separate from
compatibility mode. It validates the exact original Google TV Home, Android
version, both packages' enabled states and saved Wi-Fi ownership before changing
anything. Clean Home must open successfully first. The controller saves
`native-home-backup.json` atomically in its persistent private data volume, turns
off only its own accessibility redirect, disables the setup wizard before the
stock launcher, and verifies that Android actually resolves HOME to Clean Home.
Neither package is uninstalled; all other installed/enabled/disabled states and
the original app baseline are preserved. Errors trigger restoration, not a
success-shaped compatibility fallback.

**Restore original Home** restores the two packages' exact saved enabled states
before selecting and verifying the recorded stock component. It also turns off
the compatibility service. Switching back to compatibility mode restores those
packages first. Do not use the generic debloat installer to manage this mode.

While native mode is active, the normal NAS monitor checks the actual HOME
resolver and Android's launcher process-exit diagnostics every 10 seconds.
It restores Google TV after the resolver is missing/wrong for at least 15
seconds, three Java/native crashes within two minutes, or a reported Android
ANR. Old exits recorded before activation, normal memory reclamation, app
updates and deliberate process stops are not classified as crashes.
An interrupted activation or restoration is recovered on the next monitor
connection, including after a NAS restart. Recovery opens Home only if the
TV is already awake on a launcher/fallback screen; it never wakes the TV or
replaces playback, another app, or a real Settings page. The portal reports the
failback reason and any recovery error rather than reactivating Clean Home
silently.

The HOME preference and disabled package states are stored on the TV and do not
require the NAS or accessibility redirection to handle Home. **Automatic
failback and the portal's restore action require a running NAS, authorized ADB
and developer/debugging access.** This is not an independent on-TV watchdog,
nor detection of every visual/rendering freeze: ANR recovery depends on
Android reporting the ANR. An offline NAS cannot perform failback.
If the TV or ADB is unavailable, the portal remains usable with its last captured
inventory. Home is explicitly labeled **Last verified**, and monitor/recovery
errors remain visible. Inventory verification resumes after reconnection;
the portal being online does not mean TV commands, timer enforcement, or
automatic restoration can currently run.
The remote's Settings button remains an independent route to the TV's Apps
settings for re-enabling Google TV. Do not turn off debugging before testing
your available recovery routes.

For manual ADB recovery on the tested TV, restore the packages before the role:

```sh
pm default-state --user 0 com.google.android.apps.tv.launcherx
pm default-state --user 0 com.google.android.tungsten.setupwraith
cmd package set-home-activity --user 0 com.google.android.apps.tv.launcherx/.home.HomeActivity
input keyevent 3
```

These packages originally used enabled state 0 (`default`) on this TV; use the
exact states from the private backup for a different installation. The normal
portal recovery performs that choice automatically.

### Compatibility-mode boot recovery

The real TCL also resets the accessibility master flag at full boot while retaining
its selected-service list. On a new authorized ADB connection the NAS compares boot
counts and restores that flag only after a new TV boot and completed Android startup,
only if this Home service is
still selected. It does not override a same-boot manual switch-off or re-enable a
service the user removed. The native service opens clean Home once per boot, not on
every rebind while another app is playing. Installer updates retain a previously
enabled owned override.
On this firmware changing the master flag alone does not bind the selected service.
Boot recovery refreshes only the owned entry, waits for the accessibility manager to
acknowledge it, and verifies the actual service process/binder connection before
recording the boot as recovered. All other selected services are retained.
TCL's `TclAppBoot` also blocks service binding until the launcher has been opened
in that boot. Recovery opens clean Home first only when the TV is awake and already
on stock Home; it never interrupts another app, Settings page, input or standby.
If another app is active, recovery is explicitly pending until Home appears.
This Home-only recovery state is shown separately and does not stop daily usage
monitoring or live energy delivery.
Explicitly selecting clean Home also opens the app before enabling the service and
verifies its binding, so re-enabling it after a stock-Home reboot follows the same
TCL startup requirement.

For this firmware, the NAS must be online after a full TV reboot to repair that
manufacturer-reset flag. Once restored, Home handling runs on the TV even if the
NAS later stops. If the NAS is unavailable during boot, open the launcher manually
until it reconnects; no broad secure-settings permission is granted to the TV app.
Restoring original Home uses the recorded component, not a guessed generic package.

Image uploads are bounded, normalized and content-addressed. Immutable images are
verified on upload before the theme JSON is atomically activated. Clearing only the
launcher's activity task reloads its theme without force-stopping accessibility services.
Undo restores a previous applied theme; it is not a full firmware/app-data restore.
The last working design remains available to Undo even if a later theme commits its files
but cannot restart the launcher. Failed configuration activation stops before Home is restarted.

The baseline download excludes passwords, session tokens and private ADB keys.
The parent API accepts defined operations only, not arbitrary shell commands.
Standby is requested and confirmed separately; a disconnected TV is not proof of success.
