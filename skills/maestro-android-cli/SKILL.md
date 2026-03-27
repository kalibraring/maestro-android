---
name: maestro-android-cli
description: Use when working with the maestro-android CLI, .maestro-android.yaml, cloud smoke or benchmark runs, scoped repros, report or trace lookup, or publishing Android testing workflows.
---

# Maestro Android CLI

Use this skill for the standalone `maestro-android` companion CLI.

## Use When

- Run or debug Maestro flows in an Android project.
- Set up `.maestro-android.yaml` for a new repo.
- Inspect `doctor`, `lane`, `scoped`, `report`, `trace`, or `cloud` output.
- Scaffold a starter config with `maestro-android init`.
- Package or publish the CLI for local or shared use.

## Prerequisites

- `maestro-android` is installed, usually via `pipx`.
- The target project has a `.maestro-android.yaml` file.
- `adb`, `maestro`, and Android build tooling are available when running device workflows.

## Core Workflow

1. Start with `maestro-android doctor`.
2. Use `maestro-android init` when a repo does not yet have `.maestro-android.yaml`.
3. Confirm the project root and config path if the repo is not the current directory.
4. Use `maestro-android lane <name>` for canonical local flows.
5. Use `maestro-android scoped --flow tmp/<name>.yaml` for one-off crash or hang repros.
6. Use `maestro-android report latest` or `maestro-android trace latest` to inspect the newest artifacts.
7. Use `maestro-android cloud smoke|benchmark|status` for hosted Maestro workflows.

## Command Surface

- `doctor`
- `init`
- `devices`
- `start-device`
- `test`
- `lane`
- `scoped`
- `report`
- `trace`
- `merge-reports`
- `clean`
- `cloud run`
- `cloud smoke`
- `cloud benchmark`
- `cloud status`

See `references/testing-map.md` for the recommended testing ladder and when to use each command.
See `references/command-reference.md` for setup and concrete examples.

## Publishing

- For local machine installs, prefer `pipx install -e /path/to/maestro-android`.
- For other machines, prefer `pipx install git+https://github.com/kalibraring/maestro-android.git@vX.Y.Z`.
- For skills.sh distribution, keep this `skills/maestro-android-cli/` folder intact and publish the repo as-is.
