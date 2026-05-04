# Maestro Android Testing Map

Use the lightest command that proves the risk.

## Default Evidence Matrix

1. **Emulator**: fast harness, bootstrap, and selector proof.
2. **Connected device**: real hardware proof for storage, permissions, transport, thermal, and OEM behavior.
3. **Cloud**: hosted fan-out and hosted-contract confirmation.

Unless the failure theory is already narrow, do not stop after one surface.

## Standard Ladder

1. `maestro-android doctor`
   - Use first on a new machine or a new repo.
   - Proves the Android tooling and config are wired correctly.
2. `maestro-android init`
   - Use when the repo does not yet have `.maestro-android.yaml`.
   - Gives a safe starter config you can edit in place.
3. `maestro-android lane <name>`
   - Use for the repo's canonical local/test flows.
   - Best for stable smoke suites, journey evidence, and screenshot contracts.
   - Pin `--device <serial>` whenever more than one transport is attached.
4. `maestro-android scoped --flow tmp/<name>.yaml`
   - Use only for a single crash, hang, or regression path.
   - Keep the flow minimal and one-path only.
5. `maestro-android device probe --device <serial>`
   - Use when adb looks healthy but you do not trust the local Maestro bootstrap state yet.
   - This is the canonical local recovery/probe path.
6. `maestro-android scoped --type instrumented --device <serial> --test-class com.example.Test[#method]`
   - Use for one connected Android test class or method.
   - Add `--runner-arg key=value` for screenshot-pack or other harness args.
7. `maestro-android report latest`
   - Use when you need the latest report bundle without hunting through files.
8. `maestro-android trace latest`
   - Use when you need the newest trace-capable artifact bundle.
9. `maestro-android cloud probe --flow <path>`
   - Use for one hosted diagnosis path before you widen to a full suite.
10. `maestro-android cloud flow <path>`
   - Use for one hosted flow or directory without widening to full hosted smoke.
11. `maestro-android cloud smoke|benchmark|status`
   - Use for hosted coverage and upload polling.
   - Keep cloud work supplemental, not your merge gate.

## Best Practices

- Prefer stable selectors and deterministic flows.
- Keep scoped repros in `tmp/` and make them easy to rerun.
- Promote recurring regressions into a named lane or a stable flow.
- Use the report and trace helpers before browsing raw artifact folders.
- Let repo policy decide whether a path belongs in `lane` or `scoped`.
- Use `lane --device <serial>` when a repo lane delegates to another tool that expects `ANDROID_SERIAL`.
- Use `device files --storage media ...` when the app stores persistent assets in `Android/media/<app>`.
- Use `clean --stale-flows` after interrupted wrapper runs so generated prepared-flow YAML files do not pollute later lint or cloud uploads.
- Use `device probe --device <serial>` instead of reviving an ad-hoc shell bootstrap helper when local Maestro bootstrap is in doubt.
- If the same path fails twice, stop looping on it. Pivot upward: check `device foreground`, inspect `device ui`, then choose a narrower or more canonical command or a different surface in the matrix.
