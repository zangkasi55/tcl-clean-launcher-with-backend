<!-- Keep all diagnostics and samples free of credentials and personal images. -->

**Tested on:** <!-- model, Android version -->

### Summary and verification
- What problem does this solve?
- Which exact tests/commands passed?
- Was this sandbox, emulator or physical-device testing?

### Privacy and preservation
- [ ] No credentials, private keys, runtime data, household URLs or personal photos
- [ ] Any sample screen uses only synthetic sandbox data and is labeled simulated
- [ ] Existing apps, saved state and recovery routes are preserved
- [ ] Related documentation is updated

### If this touches the controller or web UI
- [ ] Relevant pytest/Ruff checks run; results stated
- [ ] Photo changes include Node and isolated WebKit tests when applicable
- [ ] Save vs Apply, failure/retry, mobile and keyboard behavior checked
- [ ] No production TV or HA writes used as a regression test

### If this adds or changes a TV list
- [ ] `examples/<brand>-<model>/debloat.txt`, header with model, Android version and firmware build
- [ ] Only packages I actually disabled and lived with; anything I had to re-enable is noted
- [ ] A row in `examples/README.md` and one in the README's TV table
- [ ] Nothing in the list uninstalls

### If this touches the launcher
- [ ] D-pad/rendering checked on a real TV when required; no private screenshot attached
- [ ] D-pad checked from every edge (Up, Down, Left, Right)
- [ ] No new network access, persistent service, permission or dependency without discussion
- [ ] New strings in `values/strings.xml` and the other `values-*` folders
- [ ] Anything TV-specific is in `config.xml` or a theme, not in code
