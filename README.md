# maestro-android

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://pypi.org/project/maestro-android/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

`maestro-android` is a standalone companion CLI for Android projects that use [Maestro](https://maestro.mobile.dev/). It adds the higher-level workflow that Android teams usually end up rebuilding locally: build/install bootstrap, device selection, lane commands, scoped repros, structured artifacts, report lookup, and report merging.

It is intentionally project-agnostic. Repo-specific behavior belongs in `.maestro-android.yaml`, not in the package code.

## Default Evidence Matrix

For Android UI and runtime work, treat evidence as a three-surface matrix by default:

1. **Emulator** for fast harness, bootstrap, and selector proof.
2. **Connected device** for real transport, storage, permissions, thermals, and OEM behavior.
3. **Cloud** for hosted fan-out and hosted-contract confirmation.

Do not silently substitute one surface for another. If startup, provisioning, runtime readiness, selectors, or release confidence changed, the default route is emulator + connected device + cloud unless the failure theory is already narrow.

## Install

For local development on this machine:

```bash
python3 -m pip install --user pipx
pipx ensurepath
pipx install -e .
```

For another machine after release:

```bash
pipx install git+https://github.com/kalibraring/maestro-android.git@vX.Y.Z
```

If you want a pinned wheel instead of source install:

```bash
pipx install https://github.com/kalibraring/maestro-android/releases/download/vX.Y.Z/maestro_android-X.Y.Z-py3-none-any.whl
```

## Publish Options

Ways to publish this tool:

1. GitHub Releases with tagged wheel/sdist assets.
2. PyPI with `pipx install maestro-android`.
3. Install directly from a Git tag with `pipx install git+https://...@vX.Y.Z`.
4. Internal package index if you later want private distribution.

Simplest and best for this tool right now:

1. GitHub Releases, generated automatically from a tag push.
2. Install on other machines with `pipx install git+https://github.com/kalibraring/maestro-android.git@vX.Y.Z`.

That keeps publishing one-step on my side, needs no manual asset upload, and stays easy to update when I push the next tag.

## Use

Run the CLI from the Android project root, or point it at a project explicitly:

```bash
maestro-android doctor
maestro-android devices --json
maestro-android init
maestro-android start-device
maestro-android test --include-tags smoke
maestro-android lane smoke
maestro-android scoped --flow tmp/repro.yaml
maestro-android lane screenshot-pack --device <serial>
maestro-android scoped --type instrumented --device <serial> --test-class com.example.RuntimeInstrumentationTest#loads_model
maestro-android scoped --type instrumented --device <serial> --test-class com.example.UiSmokeTest --runner-arg screenshot_pack_dir=tmp/screens
maestro-android device probe --device <serial>
maestro-android device files --storage media models/
maestro-android device foreground
maestro-android report latest
maestro-android cloud probe --flow tests/maestro-cloud/scenario-runtime-ready-smoke.yaml
maestro-android cloud flow tests/maestro-cloud/scenario-runtime-ready-smoke.yaml
maestro-android cloud flow tests/maestro-cloud/scenario-runtime-ready-smoke.yaml --watch
maestro-android suggest
maestro-android clean --stale-flows
maestro-android --project-root /path/to/project doctor
```

Core commands:

- `doctor`: verify `adb`, `maestro`, optional emulator tooling, `gradlew`, config presence, and print the current emulator/device/cloud matrix
- `devices --json`: show attached transports, transport kind, parsed model/product metadata, and duplicate-transport groups
- `init`: write a starter `.maestro-android.yaml` for the current project
- `devices`: list connected adb devices
- `start-device`: start an AVD, wait for a new emulator transport, then wait for boot-complete + package-manager readiness
- `test`: run one or more flows with build/install bootstrap and structured artifacts
- `lane`: run a configured lane; add `--device <serial>` to pin delegated lanes to one target
- `scoped`: run one minimal flow with logcat capture and crash-signature scanning
- `scoped --type instrumented|unit`: run a targeted Gradle test loop; use `--test-class Class[#method]` and `--runner-arg key=value` for connected Android tests
- `device probe`: run a pinned adb transport check and an optional launchApp bootstrap probe with artifacts
- `device`: inspect app-aware storage, foreground ownership, logcat, UI hierarchy, and process state; use `--storage media` for repos that persist assets in `Android/media/<app>`
- `report`: locate and optionally open the latest artifact bundle
- `trace`: show the latest trace-capable bundle and `trace.json`
- `merge-reports`: merge multiple run manifests and JUnit outputs
- `clean`: remove scratch artifacts or stale generated prepared-flow files
- `cloud run`: pass through to `maestro cloud`
- `cloud smoke`: hosted `cloud-smoke` suite with build, APK resolution, and API-level fan-out
- `cloud probe`: hosted one-flow or tag-slice run for narrow diagnosis
- `cloud flow`: hosted one-flow or one-directory run when a full cloud smoke rerun would be wasteful
- `cloud benchmark`: hosted GPU-vs-CPU benchmark fan-out
- `cloud status`: poll upload ids from Maestro Cloud
- `suggest`: recommend wrapper commands from the current diff
- `lint`, `audit-selectors`, `audit-testtags`: keep flow/testTag health current before widening runs

## Config

Place `.maestro-android.yaml` in the target project root.
If you want a starter file, run `maestro-android init`.

Minimal example:

```yaml
project:
  apk_glob: app/build/outputs/apk/debug/*.apk
  build_command: ["./gradlew", "assembleDebug"]
  install_command: ["./gradlew", "installDebug"]
  app_id: com.example.app

flows:
  roots:
    - maestro
    - tests/maestro

lanes:
  smoke:
    kind: test
    include_tags: [smoke]
    label: smoke
  full:
    kind: test
    label: full
```

Pocket-GPT’s config is included as a worked example in `examples/pocket-gpt/maestro-android.pocket-gpt.yaml`.

## Short Device Loops

Prefer these over long manual Gradle or `adb` commands when narrowing a device failure:

```bash
# One instrumented class or method
maestro-android scoped --type instrumented --device <serial> --test-class com.example.RuntimeInstrumentationTest#loads_model

# Same, but with instrumentation runner args
maestro-android scoped --type instrumented --device <serial> --test-class com.example.UiSmokeTest --runner-arg screenshot_pack_dir=tmp/screens

# Shared app-owned media storage
maestro-android device files --storage media models/
maestro-android device foreground
maestro-android device push --storage media mmproj.gguf models/
maestro-android device probe --device <serial>
```

For a narrow hosted repro, use the smallest hosted command that can fail authoritatively:

```bash
maestro-android cloud probe --flow tests/maestro-cloud/scenario-runtime-ready-smoke.yaml
maestro-android cloud flow tests/maestro-cloud/scenario-runtime-ready-smoke.yaml
```

When a Maestro flow fails or times out, inspect the flow artifact directory before repeating the same command:

- `flow-state.json` records whether the flow was still `running` or finished `passed` / `failed`
- `failure-context/foreground.json` records the top package/activity and classifies app vs permission dialog vs Play Store vs other package
- `failure-context/ui.xml` captures the raw UI hierarchy when available

If the same path gets stuck twice, stop repeating it. Step back, classify the problem as product, harness/bootstrap, device transport, or hosted infrastructure, then pivot to the smallest higher-signal command or another surface in the matrix.

When multiple transports are attached for the same phone, do not let the tool guess. Run:

```bash
maestro-android devices --json
maestro-android lane smoke --device <serial>
```

`device probe` is the canonical local recovery path for "is adb up but Maestro still unhealthy?" cases. Use it before reviving ad-hoc bootstrap shell helpers.

## Generated Flow Hygiene

Prepared-flow artifacts should not live in the repo flow tree.

```bash
maestro-android clean --stale-flows          # dry run
maestro-android clean --stale-flows --confirm
```

## Publish

Recommended distribution model:

1. Push a tag that matches `v*`, for example `vX.Y.Z`.
2. GitHub Actions builds the wheel and sdist.
3. GitHub Releases gets the assets automatically.
4. Install elsewhere with `pipx install git+https://github.com/kalibraring/maestro-android.git@vX.Y.Z`.

Cloud usage:

```bash
maestro-android cloud smoke
maestro-android cloud flow tests/maestro-cloud/scenario-runtime-ready-smoke.yaml
maestro-android cloud benchmark
maestro-android cloud status label:upload-id
```

Choose and add a real `LICENSE` file before publishing publicly.

That gives you:

- easy install on your own machine with `pipx install -e ...`
- easy install on other machines from a tag
- a clean path to versioned release artifacts later
