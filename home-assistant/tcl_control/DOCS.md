# Home Assistant inline TCL controls

## Requirements

- Home Assistant OS or another supported installation with Supervisor apps.
- A working private TCL controller reachable **from the HA host** over HTTPS.
- A trusted HTTPS address for Home Assistant.
- Permission to install apps and edit the intended dashboard.

Home Assistant Core/Container without Supervisor cannot install this app. Keep
using the LAN controller there; do not expose a passwordless portal to the
Internet as a workaround.

## Install

1. Add `https://github.com/zangkasi55/tcl-clean-launcher-with-backend` to the HA
   app store repositories. Install **TCL Control**.
2. Configure these options, replacing **every** example:

   ```yaml
   backend_url: https://tv.example.com
   browser_origins:
     - https://ha.example.com
   allowed_user_ids:
     - 0123456789abcdef0123456789abcdef
   ```

   `backend_url` is an exact HTTPS origin, not a path. Its TLS certificate must
   be trusted by HA. Keep the NAS reverse-proxy access profile private; permit
   the HA host's legitimate LAN connection, not the public Internet.

   `browser_origins` lists the exact HTTPS addresses used to open HA. There
   are no wildcards, credentials, URL paths or HTTP origins.

   `allowed_user_ids` lists the specific HA users who may manage this TV. An
   empty list fails closed. In HA Settings > People > Users, open the intended
   user and obtain its user ID. A display name or username is not a user ID.
3. Update the controller to a version supporting `TVCONTROL_INGRESS_ORIGINS`
   and add the same HA origins, comma-separated, to its environment:

   ```yaml
   TVCONTROL_INGRESS_ORIGINS: https://ha.example.com
   ```

   Keep `TVCONTROL_PUBLIC_ORIGIN`, existing frame origins, secure Strict
   cookies, CSRF checks, management networks and the original data volume.
   Restart only the controller after changing its environment. No launcher
   reinstall or TV wake is required.
4. Start the app. **Open Web UI** must show the actual controller, not a NAS
   error page. Enabling **Show in sidebar** is optional.

## Put the controls on the main Home tab

1. Copy
   [`tcl-control-card.js`](../../controller/home-assistant/tcl-control-card.js)
   to your HA configuration's `www` directory.
2. Add `/local/tcl-control-card.js?v=1.0.0` as a **JavaScript module** dashboard
   resource. Reload the dashboard.
3. Copy the installed app's slug from its HA app details URL, then add:

   ```yaml
   type: custom:tcl-control-card
   title: TCL TV Control
   addon: your_repository_id_tcl_control
   height: 720
   ```

   The slug includes the repository prefix; `tcl_control` alone is usually
   insufficient. `height` is a maximum from 360 to 1600 pixels. The actual
   frame adapts to the available mobile viewport.
4. Back up the storage dashboard before editing it. Insert this card into the
   existing **Home** view; do not replace its other cards or energy templates.
   An old dedicated TCL view can be retained as a subview for existing links.

The card uses a real scrollable iframe, not an iframe nested inside a
gesture-handling button card. Scroll inside it to reach lower forms. The
full-screen link opens HA's authenticated app view for more room.

The browser uses HA's normal, short-lived ingress session flow. The card never
reads or exports HA's long-lived access token, stores an ingress session in
dashboard configuration, or puts authentication material in a shareable URL.

## Security boundaries

- No host port, host networking, host mount, Supervisor API grant, HA API
  grant, privileged mode or protection-mode exception is requested.
- Only the Supervisor ingress peer `172.30.32.2` is accepted. Unknown or
  missing HA user IDs are denied on **every** request, including assets and
  live events. Sidebar visibility is not the authorization boundary.
- The upstream HTTPS destination is fixed by administrator configuration.
  Client URLs cannot select another server, and external redirects fail.
- HA cookies, authorization headers and user identity headers are not
  forwarded to the controller. The controller's own HttpOnly, Secure,
  SameSite=Strict session cookie is scoped to this app's ingress path.
- The real browser Origin and controller CSRF header are preserved, not
  rewritten or bypassed. Frame permission alone never grants write access.
- Live events stream through the app. Uploads remain bounded; the original
  controller's 8 MiB prepared-image limit and local photo conversion remain
  unchanged.
- Request access logs are disabled so ingress paths and cookies are not
  written to app logs. Failures have explicit, credential-free diagnostics.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| App refuses to start | Exact HTTPS origins and at least one real allowed user ID. |
| User denied | Match the logged-in user's ID, not its display name; missing ingress identity is denied. |
| NAS rejects ingress | HA-to-NAS LAN routing and the existing private access profile. Do not remove the profile. |
| TLS error / HTTP 502 | HA must trust the controller certificate and reach the configured private HTTPS origin. |
| Unexpected request origin | Add the exact HA HTTPS address to both origin lists, retaining controller CSRF checks. |
| Custom element missing | Verify the JS module resource, reload, and use the installed app slug. |
| Companion app uses HTTP | Change that app's saved HA address to the trusted HTTPS address. |
| Need more room | Open full-screen TCL controls; the same authenticated app is used. |

Test photo selection/cropping before applying anything. **Save to system**
does not mean **Apply to TV**, and opening this app does not select direct HA
mode or provision the TV's private HA credential.
