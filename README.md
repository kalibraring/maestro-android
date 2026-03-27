# maestro-android

`maestro-android` is a standalone companion CLI for Android projects that use [Maestro](https://maestro.mobile.dev/). It adds the higher-level workflow that Android teams usually end up rebuilding locally: build/install bootstrap, device selection, lane commands, scoped repros, structured artifacts, report lookup, and report merging.

It is intentionally project-agnostic. Repo-specific behavior belongs in `.maestro-android.yaml`, not in the package code.

## Install

For local development on this machine:

```bash
python3 -m pip install --user pipx
pipx ensurepath
pipx install -e /Users/mkamar/Non_Work/Projects/maestro-android
```

After you publish to GitHub:

```bash
pipx install git+https://github.com/<your-user>/maestro-android.git
```

If you prefer a packaged release:

```bash
python3 -m pip install --user build
cd /Users/mkamar/Non_Work/Projects/maestro-android
python3 -m build
pipx install dist/maestro_android-0.1.0-py3-none-any.whl
```

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
- `cloud`: pass through to `maestro cloud`

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

1. Put this directory in its own GitHub repo.
2. Keep the `pipx install git+https://github.com/<user>/maestro-android.git` path working.
3. Add GitHub Actions to run `python3 -m unittest discover -s tests` and `python3 -m build`.
4. Attach built wheels to GitHub Releases.

Choose and add a real `LICENSE` file before publishing publicly.

That gives you:

- easy install on your own machine with `pipx install -e ...`
- easy install on other machines from Git
- a clean path to versioned release artifacts later
