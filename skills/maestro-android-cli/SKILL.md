---
name: maestro-android-cli
description: 'Use when working with the maestro-android CLI, configuring .maestro-android.yaml, running doctor/lane/scoped/report/trace/merge-reports, or publishing the tool as a reusable Android testing skill.'
---

# Maestro Android CLI

Use this skill when you need to operate the standalone `maestro-android` companion CLI for Android projects.

## When To Use This Skill

- Run or debug Maestro flows in an Android project.
- Set up `.maestro-android.yaml` for a new repo.
- Inspect `doctor`, `lane`, `scoped`, `report`, or `trace` output.
- Package or publish the CLI for local or shared use.

## Prerequisites

- `maestro-android` is installed, usually via `pipx`.
- The target project has a `.maestro-android.yaml` file.
- `adb`, `maestro`, and Android build tooling are available when running device workflows.

## Core Workflow

1. Start with `maestro-android doctor`.
2. Confirm the project root and config path if the repo is not the current directory.
3. Use `maestro-android lane <name>` for canonical flows.
4. Use `maestro-android scoped --flow tmp/<name>.yaml` for one-off crash or hang repros.
5. Use `maestro-android report latest` or `maestro-android trace latest` to inspect the newest artifacts.

## Common Commands

```bash
maestro-android doctor
maestro-android --project-root /path/to/project lane smoke
maestro-android scoped --flow tmp/maestro-repro.yaml
maestro-android report latest
maestro-android trace latest
maestro-android merge-reports --out build/merged run-a run-b
```

## Config Guidance

- Prefer config over code changes when the project only needs different app IDs, APK paths, flow roots, or lane definitions.
- Keep scoped repro flows under `tmp/` and include `title` and `description` comments on the first two lines.
- For Pocket-GPT, reuse `examples/pocket-gpt/maestro-android.pocket-gpt.yaml` from the standalone CLI repo.

## Publishing And Reuse

- For local machine installs, prefer `pipx install -e /Users/mkamar/Non_Work/Projects/maestro-android`.
- For other machines, prefer `pipx install git+https://github.com/<user>/maestro-android.git`.
- For skills.sh distribution, publish the standalone repo with this `skills/maestro-android-cli/` folder intact, then add it with `npx skills add <repo> --skill maestro-android-cli`.
