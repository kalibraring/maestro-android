# Maestro Android CLI Reference

## Setup

```bash
pipx install -e /path/to/maestro-android
maestro-android init
```

## Local Workflow

- `maestro-android doctor` checks Android tooling, `adb`, `maestro`, `gradlew`, and config presence.
- `maestro-android init` writes a starter `.maestro-android.yaml`.
- `maestro-android lane smoke` runs the stable smoke flow set.
- `maestro-android lane journey` and `maestro-android lane screenshot-pack` inspect structured evidence outputs.
- `maestro-android scoped` is the fast one-flow repro path for crashes, hangs, or runtime regressions.
- `maestro-android report latest` finds the newest artifact bundle.
- `maestro-android trace latest` shows the trace-capable bundle and `trace.json`.

## Cloud Workflow

- `maestro-android cloud smoke` runs hosted cloud-smoke flows with API-level fan-out.
- `maestro-android cloud benchmark` runs the hosted GPU-vs-CPU benchmark loop.
- `maestro-android cloud status label:upload-id` polls upload ids from Maestro Cloud.
- `maestro-android cloud run -- ...` passes through to `maestro cloud`.

## Scoped Flow Rules

- Put scoped repro flows in `tmp/`.
- Start the file with title and description comments.
- Use `--no-build --no-install` for fast reruns once the repro is stable.
- Use `--device` or `--serial` only when multiple devices are attached.

## Publishing

- Push a `v*` tag to the GitHub repo.
- GitHub Actions builds the wheel and sdist.
- GitHub Releases gets the assets automatically.
- Other machines can then install with `pipx install git+https://github.com/kalibraring/maestro-android.git@vX.Y.Z`.
