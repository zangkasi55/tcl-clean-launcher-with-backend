# Operations, backup and recovery

[Documentation hub](README.md) | [Installation](INSTALLATION.md) | [Configuration](CONFIGURATION.md)

## Before changing a working system

Record the current controller image tag, APK version, project name, data
volume name, TV address, HOME mode and whether recovery is armed. Keep that
record private. Arrange a maintenance window for install/Home/standby
operations; they are not background cosmetic changes during someone else's
playback.

The controller being reachable does not prove that TV control is reachable.
Check service, TV authorization, HOME recovery and HA-source/delivery states
separately.

## Back up private state

Back up all of the following through a private NAS/backup channel:

- The complete controller data volume, including SQLite and any sidecars,
  images, revisions, ADB identity and Home recovery backups.
- Local production Compose/override/environment settings.
- Any separately mounted HA token file, under an appropriate secret backup
  policy.
- The exported original app baseline.
- The original APK signing keystore and password file, stored outside source.

The exported baseline is **not** a full backup: it excludes secrets and does
not replace images, daily usage, database state or signing keys.

For a simple file-level data-volume backup, stop the controller cleanly and
wait for it to exit before taking the snapshot/copy, then start it again and
verify health. The supplied shutdown grace is 120 seconds. Alternatively,
use a deliberately implemented consistent SQLite/volume backup strategy.
Copying only the main `.sqlite3` file while WAL writes are active is not a
reliable backup.

Stopping the controller temporarily stops NAS timer enforcement, live-file
delivery and automatic HOME failback. Keep the TV in a known safe state
during the window. Do not delete the data volume or stop unrelated projects.

## Update the controller

1. Download the intended release and verify its checksums.
2. Back up private state and note the known-good image/source.
3. Compare the release Compose with your actual deployment. Preserve your
   private settings, project name, ports and **existing data-volume mount**.
4. Stage the new controller/build inputs in the existing project's build
   folder. Do not blindly overwrite local `.env`, secrets or customized Compose.
5. Build the image. If your environment supports an isolated prebuild, build
   before stopping the running service.
6. Recreate/start only the TCL controller using the same persistent volume.
7. Verify running/healthy status, external `/healthz`, portal version and TV
   connection.
8. Confirm saved/applied theme, app rule/timezone, address, baseline and HOME
   recovery state are preserved. Confirm HA data reconnects if configured.
9. Refresh the phone/browser to load the new UI.

Controller 2.6.23's photo changes do not require replacing native APK
2.6.18-tcl. Only use **Install & open clean launcher** when a native update is
actually intended, with a compatible signing identity.

### Roll back deliberately

Keep the previous known-good image/build context and a matching private data
backup. If a release fails, restore service using those known-good inputs
and the documented compatibility of the data. Do not assume every future
database/schema change is reversible by changing only an image tag.

Never delete the volume to fix a failed startup. Diagnose the error or restore
the appropriate backup. A new empty volume loses the original baseline,
usage, ADB authorization and recovery metadata.

## Restore original Home

The primary recovery action is **Device & recovery > Restore original Home**.
It restores saved package states and selects/verifies the captured stock
Home, including removing this project's compatibility redirect while retaining
unrelated accessibility services.

In native mode, stock packages must be restored **before** selecting stock
Home. Both remain installed; neither should be uninstalled as a recovery step.
Automatic failback uses the same private metadata.

### If the portal is down

1. Use the remote's independent Settings route if available.
2. Restore the controller/network/authorized debugging connection.
3. Use a trusted, already authorized ADB client only if necessary.
4. Recover the exact original package states from your private backup.

The following PowerShell example applies **only** to the tested Google TV
packages when both original enabled states were Android's `default` (0).
Replace the example address, keep all commands within an authorized private
network, and do not copy these state values onto a different baseline:

```powershell
adb connect 192.168.1.50:5555
adb -s 192.168.1.50:5555 shell pm default-state --user 0 com.google.android.apps.tv.launcherx
adb -s 192.168.1.50:5555 shell pm default-state --user 0 com.google.android.tungsten.setupwraith
adb -s 192.168.1.50:5555 shell cmd package set-home-activity --user 0 com.google.android.apps.tv.launcherx/.home.HomeActivity
```

These commands change the real TV. Do not run them as a test, against an
unconfirmed device, or when the saved states differ. Once the packages and
role are restored, verify Home when doing so will not interrupt playback.
The portal's restore action is preferable because it uses the recorded states.

### Recovery limits

- The NAS cannot fail back while offline or unable to authenticate/reach ADB.
- Native Home can keep resolving without the NAS, but failback cannot.
- The watchdog detects the documented resolver/crash/Android-ANR conditions,
  not every visual freeze.
- Compatibility mode can briefly show Google TV and may need NAS-assisted
  accessibility recovery after TCL boot.
- Do not disable debugging until you have tested independent recovery routes.
- A stale inventory is labeled last-verified; it is not proof that a device
  operation was just checked.

## Troubleshooting

| Symptom | Check | Safe next action |
|---|---|---|
| HTTPS portal returns 502 | Container running/healthy, image build, loopback port, proxy destination | Inspect that project's startup/logs; restore known-good service without deleting data |
| Container healthy but TV offline | TV awake, current DHCP address, debugging and container's key authorization | Correct saved address, approve the TV prompt and re-inspect |
| Unauthorized/debugging prompt never appears | NAS can reach private TCP 5555; firmware supports this ADB mode | Keep TV awake; do not open router ports or clear all debugging authorizations casually |
| Save reports 401 | Expired/missing portal session | Refresh/sign in again; do not disable cookie protection |
| Write returns 403 | Exact public origin, CSRF/session, parent-device network/proxy resolution | Fix the specific configuration; do not allow all origins or spoof forwarded headers |
| Wallpaper selection never opens crop | Type, 40 MiB/60MP limit, native/HEIC decode and local error text | Retry with a smaller JPEG; keep local worker CSP intact |
| Crop visible but image not saved | Upload/save feedback, request-size proxy limit and remaining source memory | Wait for saved confirmation; retry the retained crop |
| NAS thumbnail changed but TV did not | Saved vs applied state; TV/ADB connection | Apply separately after checking device status |
| Red wash on a face | Focused-app color glow, not just gradient opacity | Turn glow off and Apply |
| HA iframe is blank/unavailable | Exact HTTPS origin, same-site cookies, phone reachability, custom card installation | Use direct Open TCL controls and fix only the intended view/settings |
| Energy is stale, negative or unavailable | Source freshness, actual sensor units/signs, cloud-quality flags and TV delivery | Compare real HA readings; do not force zero or substitute fixtures |
| Weather has no forecast | Correct `weather.*` entity, units and daily subscription support | Choose a supported source and read the explicit error |
| Timer did not stop playback | Rule enabled, selected foreground app, timezone, observed usage and ADB health | Check actual observations; offline gaps are not charged |
| Google TV flashes before Clean Home | Compatibility redirect is active or native mode failed verification | Review Home mode; do not bulk-disable packages |
| APK update reports incompatible signatures | APK signing certificate differs from installed one | Use the original signed release/key; do not uninstall to bypass the protection |
| Image build cannot find APK | Missing `dist/tvhome-release.apk` in build context | Download/build the matching APK and verify its checksum |

## What to include in a support issue

- Controller bundle version and native APK version.
- TV model, Android version and firmware identifier, without serial/MAC/device IDs.
- Browser/iOS/Companion version if relevant.
- Whether this was sandbox, emulator or physical-device testing.
- Exact action, expected/observed behavior and the sanitized error message.
- Whether TV connection, HA source and TV delivery were live/stale/unavailable.
- Reproduction using a synthetic image or fake data, where possible.

Do not post passwords, HA tokens, cookies, CSRF values, signing/ADB keys,
private URLs, environment files, runtime databases, full device dumps or
personal photos. The [privacy policy for contributions](PRIVACY.md) explains
which sample images are intentionally safe to publish.
