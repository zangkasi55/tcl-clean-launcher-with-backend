# Configuration reference

[Documentation hub](README.md) | [Installation](INSTALLATION.md) | [Operations](OPERATIONS.md)

## Where settings come from

1. Environment and code defaults initialize a new deployment.
2. Saved TV address, rule, theme and feed configuration live in the private
   data volume; saved values take precedence where the implementation loads
   them from the store.
3. Browser controls update those saved values. Changing an initial environment
   default is not a reset command for an existing installation.

Do not delete the volume to apply a setting change. Change the saved setting
through the portal and verify the new state.

Compose `.env` interpolation only affects fields written as `${...}` in
[docker-compose.yaml](../docker-compose.yaml). Literal fields such as
`TVCONTROL_REQUIRE_LOGIN`, `TVCONTROL_SECURE_COOKIE` and `TVCONTROL_HA_PROFILE`
must be edited in Compose or explicitly overridden; adding them to `.env`
alone does not replace the literal.

## Production environment

| Variable | Default / supplied deployment | Meaning |
|---|---|---|
| `TVCONTROL_DATA_DIR` | Image: `/data`; standalone: per-user application-data directory | Private persisted state. Do not reuse sandbox storage. |
| `TVCONTROL_TV_HOST` | `192.168.1.148` unless overridden; saved target wins | Replace this example with the TV's private IPv4 address. Supported private/Tailscale addresses only. |
| `TVCONTROL_LISTEN` | Standalone `127.0.0.1`; image `0.0.0.0` | Process bind address inside its network namespace. |
| `TVCONTROL_PORT` | `8787` | Process port; coordinate any change with mapping, healthcheck and proxy. |
| `TVCONTROL_APK` | `/app/launcher.apk` | Controller's installable APK path. Sandbox does not require an APK. |
| `TVCONTROL_REQUIRE_LOGIN` | Standalone `true`; supplied Compose literal `false` | `true` or `false` only. Recommend login plus private network access. |
| `TVCONTROL_ADMIN_PASSWORD` | Unset | Optional 12-1,024 character initial admin password. Prefer private generated-password retrieval to putting a secret in configuration history. |
| `TVCONTROL_SECURE_COOKIE` | `true` | Keep true behind HTTPS. Sandbox forces false on loopback. |
| `TVCONTROL_PUBLIC_ORIGIN` | Empty unless configured | Exact external HTTPS origin used for write-origin validation. Set this behind a proxy. |
| `TVCONTROL_INGRESS_ORIGINS` | Empty; opt-in only | Comma-separated exact HTTPS HA browser origins accepted through an authenticated ingress app. Does not disable session, CSRF or management checks. |
| `TVCONTROL_FRAME_ORIGINS` | Empty | Comma-separated exact HTTPS HA origins allowed to frame the portal. No paths or wildcards. |
| `TVCONTROL_FRAME_ORIGIN` | Empty | Backward-compatible single-origin setting, combined with the list. |
| `TVCONTROL_MANAGEMENT_NETWORKS` | Empty | Optional comma-separated parent-device IP/CIDR allowlist for API writes. |
| `TVCONTROL_HA_URL` | Empty | Optional backend HA origin; use trusted HTTPS for deployment. |
| `TVCONTROL_HA_TOKEN_FILE` | Empty | Path **inside the container** to a private HA token file. |
| `TVCONTROL_HA_PROFILE` | Supplied Compose literal `ess_link` | Initial ESS-Link mapping preset; not a universal energy mapping. |

Boolean settings reject values other than `true` or `false`. Public/frame
origins must use HTTPS and contain no credentials, query, fragment or route
path. `https://ha.example.com` and `https://ha.example.com:8443` are different
origins.

### Parent-only write access

For example, after confirming the actual resolved parent-device address:

```dotenv
TVCONTROL_MANAGEMENT_NETWORKS=192.168.1.20/32,192.168.1.21/32
```

This restricts writes, not dashboard reads. Behind a reverse proxy, the app
must receive a correctly resolved client address. Uvicorn's
`FORWARDED_ALLOW_IPS` can identify exact trusted proxy addresses; configure it
only when you know the proxy address seen by the container, and verify it.
Do not use `*` or trust arbitrary `X-Forwarded-For` values to make a test pass.
The variable is an ASGI-server setting, not an automatically interpolated
field in the supplied Compose file.

## Home Assistant energy and weather

This heading also covers the Haval battery/charging feed. Current source
supports two independent credential locations:

- **Direct TV mode:** provision a dedicated TV token in the native connection
  screen; transfer only credential-free mappings from the portal. Follow
  [direct-HA setup and rollback](DIRECT_HA.md).
- **Backend mode and portal previews:** provision the NAS token file below.
  The portal preview is not proof of a direct TV connection.

Saving backend feeds and saving direct mappings are separate operations.
Neither silently selects the other TV source.

### Backend mode: store authorization only on the NAS

Create a dedicated HA account/token with appropriate access, and write the
token directly into a restricted file through your secure administration
channel. Do not put its contents into the browser, TV, repository, theme,
shell history or issue report.

The following is an **override fragment** for the existing service:

```yaml
services:
  tvcontrol:
    environment:
      TVCONTROL_HA_URL: "https://ha.example.com"
      TVCONTROL_HA_TOKEN_FILE: "/run/secrets/ha-token"
    volumes:
      - "./secrets/ha-token:/run/secrets/ha-token:ro"
```

Provision only this secret file so UID/GID 10001 can read it; keep other
accounts excluded. Preserve the existing `/data` mount and container
hardening. If DSM uses an override file, ensure Container Manager actually
loads it; otherwise merge the fragment into the existing service definition.
Do not assume a file present in the project folder is automatically active.

This backend authorization stays on the NAS. Connections verify TLS and do not
forward the token across redirects. The integration reads data/subscriptions;
it does not control an inverter or make HA service calls to change power flow.

### Energy mapping

Under **Device & recovery**:

1. Enable the energy feed and choose the real backend connection.
2. Either match a supported Energy Distribution card using its dashboard
   URL path and view title/path, or turn automatic matching off and map sensors.
3. Use instantaneous **W/kW** power sensors, not daily **kWh** totals.
4. Choose signed grid/battery sensors or separate directional pairs.
5. Confirm signs and units against your actual HA card.

Internal signs are grid positive = import, negative = export; battery
positive = discharge, negative = charge. Separate pairs become import minus
export and discharge minus charge. Use the sign switches when your source
uses the reverse convention.

The `ess_link` preset follows one verified card whose grid entity names were
counterintuitive. Do not infer the correct direction from those names for
another installation. Unavailable/offline/not-installed values remain
explicit; no simulated fallback hides a broken feed.

Automatic matching supports known declared card bindings and quality flags,
not arbitrary executable dashboard templates. Ambiguous or unsupported cards
are errors; use explicit mapping if your dashboard is not supported.

### Weather

In **Home design**, enable the weather feed and set an exact HA
`weather.*` entity that supports daily forecast subscriptions. Current
temperature, units and daily forecasts must be available from that entity.
Missing units/forecasts and unsupported subscriptions are reported, not
fabricated. The same backend URL/token-file connection is used.

### Haval battery and charging

In **Home design**, enable the vehicle feed and select a battery `sensor.*`
with `%` units and an exact `binary_sensor.*` for charging. SOC must be
0-100%; charging is HA `on`/`off`, with unknown/unavailable reported rather
than guessed off. The read-only native card occupies **10% of full-screen
width** between the 30%-width Energy and Weather cards.

Backend mode uses the NAS connection; direct mode uses the separate TV
connection and [saved direct mappings](DIRECT_HA.md#1-prepare-and-save-mappings).
No vehicle commands are exposed. Contact with HA is not evidence that a
manufacturer/cloud integration has refreshed its underlying measurement.

## Home Assistant dashboard and iPhone

### Start with a direct link

A built-in HA button card is enough and does not require custom frontend code:

```yaml
type: button
name: Open TCL controls
icon: mdi:television
tap_action:
  action: url
  url_path: https://tv.example.com/
```

This is the preferred fallback when embedding is blocked. The phone must
reach the controller hostname as well as HA; reaching HA alone is insufficient.

### Authenticated inline controls on the main Home tab

For Supervisor installations, the recommended
[TCL Control ingress app](../home-assistant/tcl_control/DOCS.md) keeps the NAS
controller LAN-only while making its complete interface available inside HA,
including from outside home. It authenticates through HA and explicitly
authorizes the configured HA user IDs. No HA long-lived token is copied.

The supplied [TCL control card](../controller/home-assistant/tcl-control-card.js)
uses a scrollable iframe without a button-card gesture wrapper, adapts to
mobile viewports, and offers an authenticated full-screen view. Add it to the
existing **Home** view after backing up the dashboard; preserve all other cards
and energy templates. Follow the app's setup steps, including the explicit
`TVCONTROL_INGRESS_ORIGINS` setting. That setting is separate from read-only
frame permission; it never disables CSRF or relaxes cookies.

### Legacy LAN-only inline view

Use exact HTTPS origins on the same site where possible:

```dotenv
TVCONTROL_PUBLIC_ORIGIN=https://tv.example.com
TVCONTROL_FRAME_ORIGINS=https://ha.example.com,https://home.example.com
```

The repository's optional [TCL TV view](../controller/home-assistant/tcl-tv-view.json)
uses `custom:button-card` in addition to built-in cards. Install that HA
frontend dependency separately if you choose this view. Customize **every**
controller URL, the `variables.frame_origins` array and the guidance text.
The sample domains are placeholders, not a working hosted service.

Back up the dashboard first. Add or replace only the intended TCL TV view,
not the entire dashboard or shared energy templates. The view always provides
a direct action and requests an iframe only from an allowed origin.

In HA Companion on iPhone, check the server's internal and external URL
settings. An HTTP/IP address or a different hostname may fall outside the
allowlist. Prefer opening the direct HTTPS page instead of globally changing
HA URLs or weakening browser protections as a workaround.

A frame does not inherit HA authentication. The portal retains its own
Secure/HttpOnly/SameSite=Strict session and CSRF checks. Adding a frame origin
is not CORS permission to write the API. Cross-site cookies or WebView storage
restrictions can still prevent embedding even if CSP permits it.

## Sandbox settings

| Variable | Default | Effect |
|---|---|---|
| `TVCONTROL_SANDBOX_DATA_DIR` | Separate per-user sandbox folder | Fake TV data, never the real controller volume |
| `TVCONTROL_SANDBOX_PORT` | `8790` | Isolated loopback development server port |
| `TCL_PHOTO_TEST_URL` | `http://127.0.0.1:18796` | WebKit test runner destination, not a production setting |

Sandbox forces loopback binding, disables app login and uses non-secure cookies
only for local HTTP. TV operations are simulated. Energy is independent:
choose **Explicit simulated test data** for fixtures, or deliberately configure
a real read-only HA source. A missing live source is unavailable, not a demo
reading. See [development and tests](BUILD.md).
