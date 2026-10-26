# Contributing to TCL Clean Launcher with Backend

Thanks for helping make this launcher and controller clearer, safer and easier
to use. Documentation, accessibility, localization, reproducible tests,
backend fixes and small remote-first UI improvements are all welcome.

[Documentation hub](docs/README.md) | [Architecture](docs/ARCHITECTURE.md) | [Build and tests](docs/BUILD.md) | [Privacy](docs/PRIVACY.md)

## Access and scope

The repository is public: anyone can read/clone the source and download
published releases. A GitHub account is needed to open issues or pull requests.
Fork it for contributions, or ask owner [zangkasi55](https://github.com/zangkasi55)
for collaborator access through your existing contact channel. Direct push
access and release publication remain maintainer-controlled. Never include
private runtime data just because the source repository is public.

This edition is **preservation-first**. Do not make bulk debloating, factory
resets, app-data clearing, broad permissions or unrequested Home changes part
of normal installation. Stock apps and sign-ins remain intact; guarded native
HOME is a separately confirmed, bounded exception.

Open an issue before adding substantial dependencies, changing HOME/recovery
policy, redesigning a protocol or expanding the supported firmware claims.
Do not silently make an optional feature mandatory.

## Good first contributions

- Improve an installation step using a real, documented compatibility result.
- Fix keyboard/focus or touch handling with a regression test.
- Add a sanitized reproduction for an image, timezone or feed-quality edge case.
- Improve error messages, localization or accessibility.
- Extend bounded direct-HA, mapping, mobile photo or lifecycle regression coverage.
- Update generic examples without adding private hostnames or credentials.

Use issues in **this repository** for this controller/edition. The inherited
upstream prompt, themes and TV lists remain attributed to
[CleanYourTV](https://github.com/Gathin23/cleanyourtv); do not send local backend
support questions to upstream by default.

## Development workflow

1. Read [architecture](docs/ARCHITECTURE.md) and the relevant component guide.
2. Fork/clone the public repository and create a focused branch:

   ```powershell
   gh repo fork zangkasi55/tcl-clean-launcher-with-backend --clone
   Set-Location .\tcl-clean-launcher-with-backend
   git switch -c docs/clearer-installation
   ```

   Maintainers with direct push access can use `gh repo clone` instead of
   `gh repo fork --clone`; outside contributors push to their own fork.

3. Set up the [isolated sandbox](docs/BUILD.md#sandbox-development).
4. Reproduce the issue with fake/synthetic data, then make the smallest
   complete fix. Preserve unrelated changes and existing behavior.
5. Add tests appropriate to the behavior change.
6. Run the relevant checks below and state exactly which passed or were not run.
7. Review staged files for credentials, runtime data and unintended media.
8. Commit, push your branch and open a PR:

   ```powershell
   git add docs
   git commit -m "Clarify installation and recovery instructions"
   git push -u origin docs/clearer-installation
   gh pr create --repo zangkasi55/tcl-clean-launcher-with-backend --base main
   ```

The `git add docs` example is deliberately scoped. For code changes, stage
only the intended source/test files; do not blindly add a working data folder.
If you do not have push access, push your branch to your fork and open a PR
against this repository. Keep household configurations and credentials out
of the fork as well.

## Component expectations

### Native launcher

- Java/Android Views; keep the launcher small and responsive on constrained TVs.
- Keep D-pad movement deterministic, including first/last rows and app return.
- Preserve the approved optional direct-HA WSS path and its TV-private credential
  boundary. New endpoints, telemetry or persistent background services still
  require an explicit design discussion.
- Keep panel-file I/O off the UI thread and bound reads/resource usage.
- Test visible-card ownership, reconnect/source revisions, auth failures,
  strict mappings, stale observations and the absence of implicit backend fallback.
- Preserve lifecycle cancellation, input/Settings access and stock recovery.
- Put ordinary TV-specific options in configuration rather than scattered
  device-model checks.
- Reuse string resources and maintain relevant translations.
- State actual TV/firmware testing; emulator success is not a compatibility claim.

Existing optional accessibility services and the visible panel reader are part
of this edition. A blanket upstream rule saying "no background work" does not
justify removing those scoped behaviors without discussing the consequences.

### Backend and live feeds

- Use strict models and explicit errors, not silent fallbacks or invented data.
- Preserve app inventory/baseline checks and exact managed package-state recovery.
- Keep ADB operations serialized and telemetry lower-priority than TV control.
- Do not expose arbitrary shell commands or bypass origin/CSRF/session checks.
- Keep passwords, HA tokens and ADB/signing identities outside source, examples
  and logs. Backend HA tokens stay on the NAS; the dedicated direct token stays
  encrypted in TV-private storage and is never transferred by the controller.
- Preserve usage through restart/midnight; do not charge unknown observation gaps.
- Test stale/unavailable/offline states, not only happy-path sensor values.

### Browser and photos

- Keep mobile/touch, keyboard, upload retry and same-file reselection working.
- Keep Save and Apply distinct and wire pending crops into both paths.
- Retain local conversion and image limits; do not use a CDN conversion service.
- Preserve self-only script policy and the explicitly allowed local worker.
- Check portrait/landscape geometry and keep screenshots entirely synthetic.

### Documentation and example configuration

- Use generic domains, addresses and synthetic sample values.
- Use relative links inside Markdown and link new guides from the hub.
- Use Mermaid source for diagrams; label any demo screen as simulated.
- Check commands against the actual source and specify their shell.
- Do not represent CI, emulation or an illustrative preview as physical testing.
- Follow the [approved image/data policy](docs/PRIVACY.md).

## Test matrix

Exact setup and commands are in [BUILD.md](docs/BUILD.md).

| Change | Minimum relevant checks |
|---|---|
| README, example tables or TV-list docs | Targeted `test_examples.py`; local links/anchors; strict counts when tables change |
| Other documentation | Links, examples, diagram rendering and affected documentation checks |
| API/session/access | Focused pytest API/passwordless/management tests and Ruff |
| Accounting/persistence | Allowance, persistence and sandbox tests; midnight/restart edge cases |
| Images/photo UI | Python image/API tests, Node crop tests and isolated WebKit photo suite |
| Energy/Haval/weather | Source/topology/feed-quality and shared parity fixtures; explicit stale/unavailable/unknown charging checks |
| Direct HA/mapping UI | Controller strict-mapping tests; native session/converter/lifecycle/credential tests; isolated WebKit mapping regressions; no backend/token/source mutation |
| Release acceptance | Synthetic Pester signature/test-report/size regressions, including the exact 409,600-byte boundary |
| Native navigation/layout | JVM tests, Android lint/release build, D-pad/device smoke test |
| Native HOME/recovery | Backend device/Home tests, policy tests and an approved real-device recovery plan |

The GitHub CI runs native build/lint/JVM/size checks, upstream lists/tools/docs,
controller pytest/Ruff, Node photo tests, sequential WebKit mapping/photo tests
and Windows release acceptance. Include relevant local results, and identify
physical-TV/iPhone tests separately rather than treating CI as hardware proof.
Never run write-capable regression tests against production.

## Pull request checklist

- [ ] One coherent topic, with problem, expected behavior and implementation summary.
- [ ] Tests added/updated; exact commands and results recorded.
- [ ] Sandbox/emulator/physical-device evidence is identified accurately.
- [ ] Relevant install, usage, configuration and recovery documentation updated.
- [ ] No credentials, personal images, keys, private deployment settings or runtime data.
- [ ] No new Android permission/dependency/service without discussion.
- [ ] Existing apps, Home recovery and saved data remain preserved.
- [ ] License/source notices for bundled dependencies remain intact.

## Issues and private reports

Report reproducible bugs at
[this repository's issue tracker](https://github.com/zangkasi55/tcl-clean-launcher-with-backend/issues).
Include versions and sanitized text diagnostics. Use generated fixtures or
approved sandbox screenshots rather than personal backgrounds or full device
dumps. For a suspected credential leak, revoke/rotate it and contact the
maintainer privately; do not paste the secret into an issue to demonstrate it.

## Inherited TV lists and themes

The `examples/`, `tools/` and theme helpers come from upstream. They are
optional, not required by the TCL controller. For a tested TV list:

1. Follow the commented package-per-line format in
   [the sample list](examples/xiaomi-mi-tv-4a/debloat.txt).
2. Include model, Android version and firmware, not serial/MAC/network identity.
3. List only reversible changes you actually tested; never an uninstall list.
4. Keep the [examples index](examples/README.md) and root README's TV table
   consistent.
5. Preserve attribution and clearly separate optional maintenance from this
   edition's default app-preservation policy.

Theme contributions should use configuration and synthetic demonstrations,
not personal wallpapers. A new image asset needs explicit review under the
[privacy policy](docs/PRIVACY.md).

## License

By contributing your project code/docs, you agree to the repository's
[MIT license](LICENSE). Do not relicense bundled font/decoder assets as MIT:
their own [font](launcher/FONT-LICENSE-OFL.txt) and
[HEIC](controller/tvcontrol/static/heic-to-LICENSE.txt) terms/source notices
remain applicable.
