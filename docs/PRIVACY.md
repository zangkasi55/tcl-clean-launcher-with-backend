# Upload privacy and sample-screen policy

[Documentation hub](README.md) | [Contributing](../CONTRIBUTING.md)

## Never upload

- Admin/HA passwords or tokens, session cookies, CSRF values or authorization headers.
- APK signing keystores/passwords or private ADB identities.
- Local `.env`/secret files, runtime SQLite databases, private backups or device dumps.
- Personal wallpapers, camera photos or screenshots of the real TV/NAS/HA account.
- Serial numbers, MAC addresses, private account details or household network maps.

Configuration documentation uses generic example domains/addresses. Ignore
rules help prevent accidental staging; they do not replace a staged-content
review and a fully redacted secret scan.

## Images intentionally retained

To preserve a working build and useful tests, this repository retains:

| Files | Why they are allowed |
|---|---|
| [Launcher banner](../launcher/app/src/main/res/drawable-nodpi/banner.jpg) and [app icon](../launcher/app/src/main/res/mipmap-xhdpi/ic_launcher.jpg) | Required, inherited application resources, not user photos |
| [EXIF test JPEG](../controller/tests/fixtures/iphone-orientation.jpg) and [HEIC test pattern](../controller/tests/fixtures/portrait.heic) | Original synthetic colored patterns for automated tests |
| Explicitly reviewed [manual sample screens](screenshots/README.md) | Generated only in the isolated sandbox, with synthetic wallpaper and fake values |

The manual's sample screens are an explicit documentation exception, not
permission to upload user images. They must be labeled simulated examples,
contain no live household data and be inspected before publication.
Architecture diagrams are Mermaid text, not captured production screens.

## Sample-screen procedure

1. Use a new `--sandbox` server and separate fake-data directory.
2. Clear HA connection environment settings and do not load production storage.
3. Confirm `sandbox.real_device_access === false` before any write or capture.
4. Use only a programmatically drawn synthetic wallpaper and simulated values.
5. Do not open the production TV portal, NAS or HA account to obtain examples.
6. Capture only the relevant sandbox controls; label cropped views and previews.
7. Inspect every output for personal images, identities, tokens and misleading
   claims. Do not include browser developer-tools/network-header screenshots.
8. Record provenance in [the sample-screen notes](screenshots/README.md).

## Git history and existing releases

The documentation cleanup removes nonessential inherited screenshots and the
sample wallpaper from the **current source tree**. Earlier Git revisions and
the original release's automatically generated source archives can still
contain those upstream demonstration images. This is not a history rewrite.
The original application icons and synthetic tests are intentionally retained.

The existing history and packaged release assets were checked for credentials
before this update. A clean scan is a check, not a mathematical guarantee.
Do not claim a deletion from the current branch purges all historical copies.
If a real secret is ever committed, revoke/rotate it immediately, notify the
maintainer privately and arrange any authorized history/release cleanup.
