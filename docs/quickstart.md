# maestro-android quickstart

Use this when you want the smallest onboarding path instead of reading the full README first.

## 1. Install and point the tool at a repo

```bash
pipx install -e /path/to/maestro-android
cd /path/to/your-android-repo
maestro-android init
```

If the repo already has `.maestro-android.yaml`, skip `init`.

## 2. Check the matrix

```bash
maestro-android doctor
maestro-android devices --json
```

Look for three surfaces:

1. emulator
2. connected device
3. cloud-ready env vars

If one is missing, `doctor` now prints the next command to close that gap.

## 3. Start with the smallest useful command

```bash
maestro-android lane smoke
maestro-android lane smoke --device <serial>
maestro-android scoped --flow tmp/repro.yaml
maestro-android cloud probe --flow tests/maestro-cloud/scenario-runtime-ready-smoke.yaml
```

Use `scoped` for one local path and `cloud probe` for one hosted path.

## 4. When local Maestro feels suspect

```bash
maestro-android device probe --device <serial>
```

This is the canonical bootstrap check. It validates adb, captures the current foreground owner, and runs a pinned `launchApp` probe with artifacts unless you pass `--adb-only`.

## 5. When the same path fails twice

Do not run a third identical command. Switch to a higher-signal command:

1. `maestro-android device foreground`
2. `maestro-android device ui`
3. `maestro-android device probe --device <serial>`
4. `maestro-android cloud probe --flow <path>`

## 6. Clean up generated flow noise

```bash
maestro-android clean --stale-flows
maestro-android clean --stale-flows --confirm
```
