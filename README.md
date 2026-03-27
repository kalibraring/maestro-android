# maestro-android

`maestro-android` is a standalone companion CLI for Android projects that use [Maestro](https://maestro.mobile.dev/). It adds the higher-level workflow that Android teams usually end up rebuilding locally: build/install bootstrap, device selection, lane commands, scoped repros, structured artifacts, report lookup, and report merging.

It is intentionally project-agnostic. Repo-specific behavior belongs in `.maestro-android.yaml`, not in the package code.

## Install

For local development on this machine:

```bash
python3 -m pip install --user pipx
pipx ensurepath
pipx install -e .
```

For another machine after release:

```bash
pipx install git+https://github.com/Mohamad-Kamar/maestro-android.git@vX.Y.Z
```

If you want a pinned wheel instead of source install:

```bash
pipx install https://github.com/Mohamad-Kamar/maestro-android/releases/download/vX.Y.Z/maestro_android-X.Y.Z-py3-none-any.whl
```

## Publish Options

Ways to publish this tool:

1. GitHub Releases with tagged wheel/sdist assets.
2. PyPI with `pipx install maestro-android`.
3. Install directly from a Git tag with `pipx install git+https://...@vX.Y.Z`.
4. Internal package index if you later want private distribution.

Simplest and best for this tool right now:

1. GitHub Releases, generated automatically from a tag push.
2. Install on other machines with `pipx install git+https://github.com/Mohamad-Kamar/maestro-android.git@vX.Y.Z`.

That keeps publishing one-step on my side, needs no manual asset upload, and stays easy to update when I push the next tag.

## Use

Run the CLI from the Android project root, or point it at a project explicitly:

```bash
maestro-android doctor
maestro-android test --include-tags smoke
maestro-android lane smoke
maestro-android scoped --flow tmp/repro.yaml
maestro-android report latest
maestro-android --project-root /path/to/project doctor
```

Core commands:

- `doctor`: verify `adb`, `maestro`, optional emulator tooling, `gradlew`, and config presence
- `devices`: list connected adb devices
- `start-device`: start an AVD and wait for adb
- `test`: run one or more flows with build/install bootstrap and structured artifacts
- `lane`: run a configured lane; use `kind: test` for built-in flow selection or `kind: command` for project wrappers
- `scoped`: run one minimal flow with logcat capture and crash-signature scanning
- `report`: locate and optionally open the latest artifact bundle
- `trace`: show the latest trace-capable bundle and `trace.json`
- `merge-reports`: merge multiple run manifests and JUnit outputs
- `clean`: remove scratch artifacts
- `cloud run`: pass through to `maestro cloud`
- `cloud smoke`: hosted `cloud-smoke` suite with build, APK resolution, and API-level fan-out
- `cloud benchmark`: hosted GPU-vs-CPU benchmark fan-out
- `cloud status`: poll upload ids from Maestro Cloud

## Config

Place `.maestro-android.yaml` in the target project root.

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

## Publish

Recommended distribution model:

1. Push a tag that matches `v*`, for example `vX.Y.Z`.
2. GitHub Actions builds the wheel and sdist.
3. GitHub Releases gets the assets automatically.
4. Install elsewhere with `pipx install git+https://github.com/Mohamad-Kamar/maestro-android.git@vX.Y.Z`.

Cloud usage:

```bash
maestro-android cloud smoke
maestro-android cloud benchmark
maestro-android cloud status label:upload-id
```

Choose and add a real `LICENSE` file before publishing publicly.

That gives you:

- easy install on your own machine with `pipx install -e ...`
- easy install on other machines from a tag
- a clean path to versioned release artifacts later
