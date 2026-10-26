# Safe sample screens

[Illustrated manual](../USER_GUIDE.md) | [Installation](../INSTALLATION.md) | [Upload policy](../PRIVACY.md)

The original seven pictures illustrate controller **2.6.23**; the added direct
mapping screen illustrates the **2.6.26 source candidate**. They are real
captures of isolated browser sandboxes, not a household TV, NAS or Home
Assistant account. Every picture is visibly labeled **SIMULATED**.

## Screen index

| Screen | What to look for | Instructions |
|---|---|---|
| [Home preview](home-preview.png) | Lower favorites, separate information cards, clear upper wallpaper area | [TV navigation](../USER_GUIDE.md#tv-remote-navigation) |
| [All apps preview](all-apps-preview.png) | Full sample catalog on a separate page | [Favorites and catalog](../USER_GUIDE.md#favorites-and-full-catalog) |
| [Mobile wallpaper crop](wallpaper-crop-mobile.png) | Fixed 16:9 grid, zoom/position sliders, Upload & save to system | [Crop walkthrough](../USER_GUIDE.md#choose-and-crop-a-wallpaper) |
| [Saved mobile wallpaper](wallpaper-saved-mobile.png) | Persistent thumbnail after reload and separate Apply action | [Three save/apply states](../USER_GUIDE.md#understand-the-three-states) |
| [Daily allowance](daily-allowance.png) | Ten simulated minutes used out of a forty-minute example rule | [Allowance guide](../USER_GUIDE.md#daily-allowance) |
| [Device connection](device-connection.png) | Inspect/preserve and baseline download | [TV authorization](../INSTALLATION.md#6-authorize-the-controller-on-the-tv) |
| [Home and recovery](home-recovery.png) | Staged install, opt-in Home selection and stock-Home restoration controls | [Home modes and limitations](../INSTALLATION.md#choose-a-home-mode) |
| [Direct HA mappings](direct-ha-mappings.png) | Separate Save, Load, Download and Send; credentials/source unchanged | [Direct-HA setup](../DIRECT_HA.md#1-prepare-and-save-mappings) |

## Provenance

- Captured from a fresh, loopback-only `--sandbox` instance with a separate
  fake-data directory. Status was checked for
  `sandbox.real_device_access === false` before operations and capture.
- No production controller, TV, DSM or HA page was used. No production data
  volume or Home Assistant credentials were loaded. External browser requests
  were blocked; none were needed for the pictures.
- The sun/mountain wallpaper was drawn programmatically on a 1200x1800 canvas.
  It contains no people, camera image, account information or licensed stock
  photo. Only the screenshots, not a standalone source wallpaper, are included.
- The crop example uses a generated PNG. It illustrates the same crop/save UI
  reached after HEIC/HEIF conversion; it is not evidence of a camera HEIC upload.
- Captures used WebKit with a wide viewport and a 430-pixel mobile viewport.
  They do not prove physical iPhone, iOS or HA Companion compatibility.
- Sample app labels are the built-in fake catalog, not a dump of installed
  household apps. Dates, package counts and usage are simulated.
- Energy is explicitly simulated. No weather forecast is supplied in the
  Home example; unavailable data is not replaced with invented live readings.
- The crop was saved and reloaded successfully. The saved/applied references
  were checked separately: saving did not apply the design to the simulated TV.
- Temporary labels were added to the capture DOM only. Recovery screenshots
  show controls, not an actual default-launcher switch or failback test.
- All eight PNGs were visually inspected before publication. Views are cropped
  to the relevant controls for readability; no developer tools, session values,
  request headers, private hostnames or personal media are included.
- The direct-mapping capture used a new isolated 2.6.26 validation sandbox and
  WebKit 27.2, with generic mappings only. No token was entered, no external
  request was made and no TV transfer/source switch was attempted. Backend
  feeds, theme, app rule and device settings were checked unchanged.
  It is a portal-control illustration, not direct-TV connectivity evidence.

## Updating these examples

Follow the [sample-screen procedure](../PRIVACY.md#sample-screen-procedure).
Use only original synthetic graphics and isolated fake data, retain a visible
simulation label, update the matching manual steps and inspect every output.
Do not replace these files with real household screens or force-add an ignored
personal photo. The image allowlist is not permission to skip privacy review.
