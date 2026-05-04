# Maestro Android CLI Reference

## Device-Pinned Lanes

```bash
maestro-android lane smoke --device emulator-5554
maestro-android lane screenshot-pack --device <serial>
```

Use this instead of manually prefixing delegated lane commands with `ANDROID_SERIAL=...`.

## Targeted Instrumented Runs

```bash
maestro-android scoped \
  --type instrumented \
  --device <serial> \
  --test-class com.example.RuntimeInstrumentationTest
```

Method-level selection:

```bash
maestro-android scoped \
  --type instrumented \
  --device <serial> \
  --test-class com.example.RuntimeInstrumentationTest#loads_model
```

Runner args:

```bash
maestro-android scoped \
  --type instrumented \
  --device <serial> \
  --test-class com.example.UiSmokeTest \
  --runner-arg screenshot_pack_dir=tmp/screens \
  --runner-arg screenshot_pack_fallback_dir=tmp/screens-fallback \
  --no-build \
  --no-install
```

## Scoped Maestro Repros

```bash
maestro-android scoped --flow tmp/repro.yaml
maestro-android scoped --flow tmp/repro.yaml --no-build --no-install
```

## Targeted Hosted Repros

```bash
maestro-android cloud flow tests/maestro-cloud/scenario-runtime-ready-smoke.yaml
maestro-android cloud flow tests/maestro-cloud --tags cloud-smoke
```

Use this when one hosted contract is failing and a full `cloud smoke` rerun would be wasteful.

## Device Inspection

```bash
maestro-android device files
maestro-android device files --storage media models/
maestro-android device push --storage media mmproj.gguf models/
maestro-android device foreground
maestro-android device foreground --json
maestro-android device logcat --follow --filter "FATAL|ANR|Runtime"
maestro-android device ui
maestro-android device info
```

Use `--storage media` for repos that keep persistent assets under `Android/media/<app>`.

## Generated Flow Hygiene

```bash
maestro-android clean --stale-flows
maestro-android clean --stale-flows --confirm
```

## Failure Breadcrumbs

Recent `maestro-android test` and `maestro-android scoped --flow ...` failures now leave these files under the flow artifact directory:

- `flow-state.json` — whether the flow was still `running` or finished `passed` / `failed`
- `failure-context/foreground.json` — current top package/activity and whether it looks like the app, a permission controller, the Play Store, or another package
- `failure-context/ui.xml` — raw UI hierarchy captured at failure time when available

Use these before rerunning the same command a third time.
