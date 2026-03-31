---
name: testing-android-maestro
description: Orchestrates Android testing via the maestro-android CLI. Handles lane runs, scoped repros, flow linting, selector and testTag audits, artifact inspection, cloud smoke and benchmark runs, and failure triage. Triggers on testing, maestro, lane, scoped, flow, smoke, audit-selectors, audit-testtags, report, cloud, test failure, or device error.
---

# Maestro Android CLI

## Why This Tool (Not Raw adb/maestro)

Reproducible device selection (auto-detects serial, warns on duplicate transports), automatic build/install, structured artifacts per flow (logcat + JUnit + debug output), crash signature scanning, failure diagnosis hints, pass/fail summary tables, and consistent lane definitions shared across team and CI.

## Decision Tree: What To Run

| What changed? | Command | Why |
|---|---|---|
| Code logic only (no UI) | Unit test lane or `./gradlew test` | Compile + unit tests |
| UI composables or strings | Above + `maestro-android lane smoke` | Catch selector breakage |
| Native C++ / JNI | Above + instrumented tests + journey lane | Bridge + runtime validation |
| Maestro flow YAML | `maestro-android lint` + `maestro-android audit-selectors` | Flow health |
| Pre-merge (any change) | Full test suite + `maestro-android lane smoke` | Broad confidence |
| Debugging specific failure | `maestro-android scoped --flow tmp/repro.yaml` | Fast one-flow repro |
| Debugging instrumented test | `maestro-android scoped --flow tmp/repro.yaml --type instrumented --test-class com.example.Test` | Gradle test with artifacts |
| Not sure what to run | `maestro-android suggest` | Auto-suggest based on git diff |

## Setup

```bash
pipx install maestro-android
maestro-android init
maestro-android doctor
```

## Core Commands

- `doctor` -- verify adb, maestro, gradlew, config
- `lane <name>` -- run configured lanes
- `scoped --flow tmp/repro.yaml` -- one-flow repro with crash scanning
- `scoped --flow tmp/repro.yaml --type instrumented|unit` -- Gradle test with structured artifacts
- `suggest` -- recommend lanes based on `git diff`
- `lint` / `audit-selectors` / `audit-testtags` -- flow and selector health
- `report latest` / `trace latest` -- inspect artifacts
- `clean [--include-repo-artifacts]` -- remove scratch artifacts
- `cloud smoke|benchmark|status` -- hosted workflows

## Feedback Loop

After any test run:
1. Read the pass/fail summary table printed by the CLI
2. For failures, follow the hint printed below the table
3. Fix code → re-run with `--no-build` if only flow changed, or rebuild if code changed
4. Repeat until all flows pass

## When Tests Fail: Triage

The CLI prints failure hints automatically. For manual triage:

| Symptom | Artifact | Likely cause |
|---|---|---|
| `FATAL EXCEPTION` | `logcat.txt` | App crash — read the stack trace |
| `Fatal signal` / `SIGSEGV` | `logcat.txt` | Native crash — check C++ backtrace |
| `Timeout waiting for` | `maestro-stderr.log` | Wrong selector or slow render — run `audit-selectors` |
| `No view found` | `maestro-stderr.log` | Missing element — check testTag via `audit-testtags` |
| `ANR in` | `logcat.txt` | App froze — blocking I/O on main thread |
| `OutOfMemoryError` | `logcat.txt` | Memory pressure |
| Build failure | `gradle-stderr.log` | Compile error — fix code, not tests |
| Multiple devices error | CLI output | Duplicate ADB transports — pass `--device <serial>` |
