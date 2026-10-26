# Documentation

[Project home](../README.md) | [Downloads](https://github.com/zangkasi55/tcl-clean-launcher-with-backend/releases/latest) | [Contributing](../CONTRIBUTING.md)

These guides cover **TCL Clean Launcher with Backend**, not a universal
one-click debloating tool. They describe current **2.6.26** controller source
and native launcher **2.6.26-tcl**. Direct-HA is a source candidate awaiting
physical-TV acceptance, not an already deployed or packaged release.
The existing **v2.6.23** download contains native **2.6.18-tcl** and lacks
direct-HA support. The Android launcher, controller
bundle and Python package metadata use different version numbers; see the
[version explanation](TECH_STACK.md#versions-and-sources-of-truth).

## Choose a path

### I want to use it

1. Read [requirements, downloads and installation](INSTALLATION.md).
2. Complete its first-run acceptance checklist before changing default Home.
3. Follow the [user guide](USER_GUIDE.md) for wallpaper, app layout and limits.
4. Add optional Home Assistant feeds using [configuration](CONFIGURATION.md);
   use [direct-HA setup](DIRECT_HA.md) for the new source candidate.
5. Keep [backup, recovery and troubleshooting](OPERATIONS.md) available.

The guides include eight
[safe example screens](screenshots/README.md), including mobile cropping and
the separate save/apply steps. These are labeled sandbox examples, not
personal TV photos or physical-iPhone test results.
The original seven show 2.6.23; the direct-mapping panel shows the 2.6.26
source candidate and is not proof of TV installation or authentication.

You do not need Java, Gradle, Node.js or an Android SDK to deploy the release
ZIP on a compatible NAS. Its image is built locally from the included APK,
Python controller and static web assets.

### I want to understand or develop it

1. Read [architecture and diagrams](ARCHITECTURE.md).
2. Check the [technology stack and resource limits](TECH_STACK.md).
3. Set up the isolated [development sandbox](BUILD.md#sandbox-development).
4. Follow the [build, test and packaging instructions](BUILD.md).
5. Read the [contribution workflow and review checklist](../CONTRIBUTING.md).

Most backend, web UI, image and accounting changes can be developed without a
TV. Android rendering, remote behavior, firmware restrictions and physical
iPhone behavior still need explicitly identified real-device testing.

## Guide map

| Guide | Contents |
|---|---|
| [Installation](INSTALLATION.md) | Compatibility, checksums, Synology setup, reverse proxy, pairing, staged Home activation |
| [User guide](USER_GUIDE.md) | D-pad navigation, Home design, local photo conversion/cropping, saving/applying, daily allowance |
| [Architecture](ARCHITECTURE.md) | System diagram, module map, persistence, photo sequence, telemetry and HOME recovery |
| [Tech stack](TECH_STACK.md) | Java/Android, Python, browser code, dependencies, tooling and design constraints |
| [Configuration](CONFIGURATION.md) | Environment variables, saved-setting precedence, authentication, HA secrets and iPhone embedding |
| [Direct HA](DIRECT_HA.md) | TV-only credentials, non-secret mappings, Energy/Haval/Weather setup, validation and explicit rollback |
| [Build](BUILD.md) | Developer setup, Windows and Unix commands, testing, signing, Docker image and ZIP creation |
| [Operations](OPERATIONS.md) | Updates, private backups, restore procedures, symptoms and diagnostic information |
| [Contributing](../CONTRIBUTING.md) | Access, issues, branches, PRs, test matrix, licensing and good first tasks |
| [Sample screens](screenshots/README.md) | Illustrated guide index, synthetic-image provenance and capture limits |
| [Privacy](PRIVACY.md) | Excluded credentials/media, approved image exceptions and history limitations |

The older [controller implementation notes](../controller/README.md) retain
detailed deployment and implementation notes. Configuration examples use
generic hostnames and addresses; replace them with your own values rather
than treating them as ready-made configuration for another home.

## Important boundaries

- **No firmware flashing, root requirement or app-data clearing.**
- **No automatic bulk debloating.** Historical scripts and TV lists remain
  available, but are not part of this TCL installation procedure.
- True-default Home is opt-in and firmware-dependent. Automatic failback is a
  NAS feature, **not an independent on-TV watchdog**.
- Password-free mode is suitable only behind a trusted private access boundary.
  Do not expose the portal or ADB to the public internet.
- Home Assistant is optional. Direct mode stores a dedicated encrypted token
  only on the TV; backend mode keeps its separate token on the NAS.
- The source repository is public; private household settings, credentials
  and media are excluded. Issues/PRs require a GitHub account, and direct
  writes/releases remain maintainer-controlled.

## Licensing

The project derives from [Gathin23/CleanYourTV](https://github.com/Gathin23/cleanyourtv)
and preserves its [MIT license](../LICENSE). The Outfit font uses the
[SIL Open Font License](../launcher/FONT-LICENSE-OFL.txt). The separately loaded
HEIC module has its own [LGPL license](../controller/tvcontrol/static/heic-to-LICENSE.txt)
and [corresponding-source notices](../controller/tvcontrol/static/heic-to-NOTICE.txt).
