# Maestro Android Testing Map

Use the lightest command that proves the risk.

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
4. `maestro-android scoped --flow tmp/<name>.yaml`
   - Use only for a single crash, hang, or regression path.
   - Keep the flow minimal and one-path only.
5. `maestro-android report latest`
   - Use when you need the latest report bundle without hunting through files.
6. `maestro-android trace latest`
   - Use when you need the newest trace-capable artifact bundle.
7. `maestro-android cloud smoke|benchmark|status`
   - Use for hosted coverage and upload polling.
   - Keep cloud work supplemental, not your merge gate.

## Best Practices

- Prefer stable selectors and deterministic flows.
- Keep scoped repros in `tmp/` and make them easy to rerun.
- Promote recurring regressions into a named lane or a stable flow.
- Use the report and trace helpers before browsing raw artifact folders.
- Let repo policy decide whether a path belongs in `lane` or `scoped`.
