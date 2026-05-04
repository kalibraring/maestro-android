---
name: testing-android-maestro
description: Use when running, debugging, or triaging Android tests — Maestro flows, device-pinned lanes, targeted instrumented tests, screenshot-pack suites, selector drift, or device state issues. Prefer over raw adb or hand-written Gradle.
---

# Maestro Android CLI

## When to Use This

Prefer `maestro-android` over raw `adb` or ad-hoc Gradle when you need:

- stable device selection (auto-resolves serial, warns on duplicate transports)
- explicit duplicate-transport visibility (`devices --json`)
- structured artifacts per run (logcat, JUnit XML, crash signatures, failure hints)
- one-command scoped repro loops
- targeted instrumented runs without hand-writing `-P` runner-arg chains
- app-aware storage, UI, logcat, and process inspection

Use raw `adb` only for coordinate taps, keyevents, or shell ops the CLI does not wrap.

## Default Evidence Matrix

Treat Android evidence as a three-surface matrix by default:

- **Emulator** for fast bootstrap, selector, and harness proof.
- **Connected device** for real hardware state, storage, permissions, and OEM behavior.
- **Cloud** for hosted fan-out and hosted-contract confirmation.

Do not let one surface silently substitute for another when startup, provisioning, runtime readiness, selectors, or release confidence changed.

## Fast Decision Tree

| Situation | Command | Why |
|---|---|---|
| Kotlin logic only | repo unit test command | Fast compile + unit confidence |
| Need the default evidence matrix | emulator lane or scoped repro, one device lane, then `maestro-android cloud smoke` or `cloud flow` | Separates local/runtime issues from hosted/environment issues |
| Compose or selectors changed | above + `maestro-android lint` + `maestro-android audit-selectors` | Catch flow drift early |
| Need a repo lane on one device | `maestro-android lane <name> --device <serial>` | Pins delegated lanes to one phone/emulator |
| Need to verify local Maestro bootstrap on one device | `maestro-android device probe --device <serial>` | Canonical adb + launchApp bootstrap check with artifacts |
| One flaky UI/runtime path | `maestro-android scoped --flow tmp/repro.yaml [--no-build] [--no-install]` | Fast Maestro repro with artifacts |
| One instrumented class or method | `maestro-android scoped --type instrumented --device <serial> --test-class com.example.Test#method` | Short `connectedDebugAndroidTest` loop |
| Instrumented run needs harness args | add `--runner-arg key=value` | Avoids long `-Pandroid.testInstrumentationRunnerArguments.*` |
| Need current app state | `maestro-android device foreground\|info\|logcat\|ui\|files` | Fast device triage |
| Need one hosted repro only | `maestro-android cloud flow <path>` | Avoids rerunning full hosted smoke |
| Not sure what to run | `maestro-android suggest` | Diff-based lane recommendation |

## Core Commands

- `lane <name> --device <serial>` — run a configured lane on one target device
- `scoped --flow tmp/repro.yaml` — one Maestro repro with logcat + artifacts
- `scoped --type instrumented --test-class Class[#method] --runner-arg key=value` — targeted device test without a dummy flow
- `device files|push --storage data|media ...` — inspect app-private or shared app-owned storage
- `device foreground` — show the current top package/activity and classify app vs permission dialog vs external app
- `device probe` — validate adb transport and run a pinned bootstrap probe with artifacts
- `device logcat --follow --filter REGEX` — stream app logcat
- `device ui` — dump resource ids, labels, and bounds
- `lint`, `audit-selectors`, `audit-testtags` — catch flow/testTag drift before widening
- `clean --stale-flows [--confirm]` — list or delete generated prepared-flow YAML files
- `cloud flow <path>` — run one hosted flow or directory without widening to full smoke
- `report latest`, `trace latest` — find the newest artifact bundle
- `suggest` — recommend lanes based on `git diff`

## When Tests Fail: Triage

The CLI prints failure hints automatically. For manual triage:

| Symptom | Artifact | Likely cause |
|---|---|---|
| `FATAL EXCEPTION` | `logcat.txt` | App crash — read the stack trace |
| `Fatal signal` / `SIGSEGV` | `logcat.txt` | Native crash — check C++ backtrace |
| `Timeout waiting for` | `maestro-stderr.log` | Wrong selector or slow render — run `audit-selectors` |
| `No view found` | `maestro-stderr.log` | Missing element — check testTag via `audit-testtags` or `device ui` |
| App no longer visible / wrong system screen | `failure-context/foreground.json` | System permission dialog, Play Store, or another package took focus |
| `ANR in` | `logcat.txt` | App froze — blocking I/O on main thread |
| `OutOfMemoryError` | `logcat.txt` | Model too large or memory leak |
| Build failure | `gradle-stderr.log` | Kotlin compile error — fix code, not tests |
| Multiple devices error | CLI output | Duplicate ADB transports — pass `--device <serial>` |

## Workflow

1. Start with the lightest command that proves the risk.
2. Place the risk on the right surface in the matrix: emulator for fast local proof, connected device for real hardware proof, cloud for hosted proof.
3. If the failure is narrow, drop to `scoped` or `cloud flow` instead of rerunning a whole lane.
4. If the same path gets stuck twice, stop repeating it. Step back, classify the failure at a higher level, then pivot to a higher-signal command such as `device foreground`, `device ui`, a pinned lane, a narrower scoped repro, or a different surface in the matrix.
5. Read generated artifacts before guessing. Start with `flow-state.json`, then `failure-context/foreground.json`, then `maestro-stderr.log` / `logcat.txt`.
6. If more than one transport is attached for the same phone, do not rely on auto-pick behavior. Run `maestro-android devices --json`, then pin `--device <serial>`.
7. Re-run with `--no-build` or `--no-install` only when code/package inputs did not change.
8. Clean generated prepared-flow YAML files before widening runs.
9. Promote repeat repros from `tmp/` into stable flows or test classes.

## References

- [Testing map](references/testing-map.md)
- [Command reference](references/command-reference.md)
