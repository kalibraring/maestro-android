from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time as _time
import xml.etree.ElementTree as ET
from contextlib import suppress
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen

from maestro_android.common import MaestroAndroidError, print_step, run_subprocess
from maestro_android.config import (
    DEFAULT_CONFIG,
    LaneConfig,
    MaestroAndroidConfig,
    load_config,
)
from maestro_android.reporting import find_bundle, open_bundle, print_bundle

try:
    import yaml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - validated by config load
    yaml = None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maestro-android",
        description="Standalone companion CLI for Android projects using Maestro.",
        epilog="""Examples:
  maestro-android doctor                           # Check environment
  maestro-android init                             # Write .maestro-android.yaml
  maestro-android lane smoke                      # Run configured smoke lane
  maestro-android scoped --flow tmp/repro.yaml    # Run one-off repro
  maestro-android report latest                   # View latest test artifacts
  maestro-android cloud smoke                     # Run hosted smoke suite
  
For full docs, see: https://github.com/kalibraring/maestro-android""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('maestro-android')}"
    )
    parser.add_argument(
        "--config", type=Path, default=None, help="override config file path"
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="run against a specific Android project root",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor",
        help="run environment diagnostics",
        epilog="Example: maestro-android doctor --json",
    )
    doctor.add_argument(
        "--json", action="store_true", dest="as_json", help="emit JSON when supported"
    )
    doctor.add_argument(
        "--include-generated-flows",
        action="store_true",
        help="include generated/prepared flow files in flow inventory output",
    )

    init = subparsers.add_parser(
        "init",
        help="write a starter .maestro-android.yaml",
        epilog="Example: maestro-android init --force",
    )
    init.add_argument(
        "--path",
        type=Path,
        default=None,
        help="config path to write (defaults to .maestro-android.yaml)",
    )
    init.add_argument("--force", action="store_true", help="overwrite existing file")

    devices = subparsers.add_parser(
        "devices",
        help="list adb devices with transport details",
        epilog="Examples:\n  maestro-android devices\n  maestro-android devices --json",
    )
    devices.add_argument(
        "--json", action="store_true", dest="as_json", help="emit JSON when supported"
    )

    start_device = subparsers.add_parser(
        "start-device", help="start an Android emulator AVD"
    )
    start_device.add_argument("name", nargs="?", help="AVD name")
    start_device.add_argument("--boot-timeout-seconds", type=int, default=180)

    test = subparsers.add_parser(
        "test",
        help="run one or more Maestro flows with Android bootstrap",
        epilog="Examples:\n  maestro-android test maestro/login.yaml\n  maestro-android test --include-tags smoke\n  maestro-android test --no-build --format html",
    )
    test.add_argument("flows", nargs="*", help="flow paths to run")
    test.add_argument(
        "--flows", dest="flow_csv", default="", help="comma-separated flow paths"
    )
    test.add_argument("--include-tags", default="", help="comma-separated tag filter")
    test.add_argument("--exclude-tags", default="", help="comma-separated tag filter")
    test.add_argument("--device", default="", help="device serial")
    test.add_argument("--no-build", action="store_true")
    test.add_argument("--no-install", action="store_true")
    test.add_argument("--format", choices=("junit", "html", "json"), default="junit")
    test.add_argument(
        "--clear-state", action="store_true", help="pm clear app before each flow"
    )

    lane = subparsers.add_parser(
        "lane",
        help="run a configured Maestro lane",
        epilog="Examples:\n  maestro-android lane smoke\n  maestro-android lane smoke -- --verbose\n  maestro-android lane my-custom-lane",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    lane.add_argument("--device", default="", help="device serial")
    lane.add_argument("name", help="lane name")
    lane.add_argument("args", nargs=argparse.REMAINDER, help="extra lane args")

    scoped = subparsers.add_parser(
        "scoped",
        help="run the scoped repro loop",
        epilog="Examples:\n  maestro-android scoped --flow tmp/repro.yaml\n  maestro-android scoped --flow tmp/repro.yaml --pattern 'NullPointerException'\n  maestro-android scoped --flow tmp/repro.yaml --no-build",
    )
    scoped.add_argument(
        "--flow",
        default="",
        help="tmp flow path (required for --type maestro)",
    )
    scoped.add_argument("--device", default="", help="device serial")
    scoped.add_argument("--no-build", action="store_true")
    scoped.add_argument("--no-install", action="store_true")
    scoped.add_argument("--pattern", default="", help="override crash signature regex")
    scoped.add_argument("--app-context", default="", help="override app context regex")
    scoped.add_argument(
        "--type",
        choices=("maestro", "instrumented", "unit"),
        default="maestro",
        dest="scoped_type",
        help="test type: maestro flow, instrumented (connectedAndroidTest), or unit test",
    )
    scoped.add_argument(
        "--test-class",
        default="",
        help="test selector. Use Class or Class#method for instrumented runs; becomes --tests for unit runs",
    )
    scoped.add_argument(
        "--runner-arg",
        action="append",
        default=[],
        dest="runner_args",
        help="extra Android instrumentation runner arg for --type instrumented (repeatable, key=value)",
    )
    scoped.add_argument(
        "--gradle-property",
        action="append",
        default=[],
        dest="gradle_properties",
        help="extra -P property for the build command (repeatable, e.g. --gradle-property key=value)",
    )
    scoped.add_argument(
        "--adb-timeout-sec",
        type=int,
        default=120,
        help="adb timeout in seconds",
    )
    scoped.add_argument(
        "--maestro-timeout-sec",
        type=int,
        default=1200,
        help="maestro timeout in seconds",
    )
    scoped.add_argument(
        "extra_args", nargs=argparse.REMAINDER, help="extra args after --"
    )

    report = subparsers.add_parser(
        "report",
        help="inspect latest report artifacts",
        epilog="Examples:\n  maestro-android report latest\n  maestro-android report smoke --open\n  maestro-android report journey",
    )
    report.add_argument(
        "kind",
        choices=("journey", "screenshot-pack", "smoke", "raw", "lifecycle", "latest"),
    )
    report.add_argument("--open", action="store_true", dest="open_files")

    trace = subparsers.add_parser(
        "trace",
        help="inspect latest trace-capable artifact bundle",
        epilog="Examples:\n  maestro-android trace latest\n  maestro-android trace smoke --open",
    )
    trace.add_argument(
        "kind",
        choices=("journey", "smoke", "raw", "latest"),
        default="latest",
        nargs="?",
    )
    trace.add_argument("--open", action="store_true", dest="open_files")

    merge = subparsers.add_parser(
        "merge-reports",
        help="merge run manifests and JUnit outputs",
        epilog="Example: maestro-android merge-reports --out build/merged run-a run-b",
    )
    merge.add_argument(
        "inputs", nargs="+", help="run directories or run-manifest.json files"
    )
    merge.add_argument("--out", type=Path, required=True, help="output directory")

    clean = subparsers.add_parser(
        "clean",
        help="remove maestro-android scratch artifacts",
        epilog="Examples:\n  maestro-android clean\n  maestro-android clean --include-repo-artifacts\n  maestro-android clean --stale-flows\n  maestro-android clean --stale-flows --confirm",
    )
    clean.add_argument("--include-repo-artifacts", action="store_true")
    clean.add_argument(
        "--generated-flows",
        action="store_true",
        help="also remove generated/prepared Maestro flow files under configured flow roots",
    )
    clean.add_argument(
        "--stale-flows",
        action="store_true",
        help="list or remove generated prepared-flow YAML files",
    )
    clean.add_argument(
        "--confirm",
        action="store_true",
        help="required to actually delete files selected by --stale-flows",
    )
    clean.add_argument(
        "--days",
        type=int,
        default=0,
        help="only match generated flow files older than this many days",
    )

    lint = subparsers.add_parser(
        "lint",
        help="validate Maestro flow YAML files",
        epilog="Examples:\n  maestro-android lint\n  maestro-android lint tests/maestro/login.yaml\n  maestro-android lint --strict",
    )
    lint.add_argument("flows", nargs="*", help="specific flow paths to lint (default: all discovered flows)")
    lint.add_argument("--strict", action="store_true", help="treat warnings as errors")
    lint.add_argument(
        "--include-generated-flows",
        action="store_true",
        help="lint generated/prepared flow files as well",
    )

    audit_selectors = subparsers.add_parser(
        "audit-selectors",
        help="cross-reference Maestro flow selectors with Kotlin testTag definitions",
        epilog="Examples:\n  maestro-android audit-selectors\n  maestro-android audit-selectors --source-roots apps/mobile-android/src",
    )
    audit_selectors.add_argument(
        "--source-roots",
        default="",
        help="comma-separated Kotlin source roots (default: auto-detect)",
    )
    audit_selectors.add_argument(
        "--include-generated-flows",
        action="store_true",
        help="include generated/prepared flow files in selector usage",
    )

    audit_tags = subparsers.add_parser(
        "audit-testtags",
        help="cross-reference Kotlin testTag definitions with Maestro flow usage",
        epilog="Example: maestro-android audit-testtags",
    )
    audit_tags.add_argument(
        "--source-roots",
        default="",
        help="comma-separated Kotlin source roots (default: auto-detect)",
    )
    audit_tags.add_argument(
        "--include-generated-flows",
        action="store_true",
        help="include generated/prepared flow files in flow usage",
    )

    cloud = subparsers.add_parser(
        "cloud",
        help="run hosted Maestro workflows",
        epilog="Examples:\n  maestro-android cloud run -- --help\n  maestro-android cloud smoke\n  maestro-android cloud status run1:abc123 run2:def456",
    )
    cloud_subparsers = cloud.add_subparsers(dest="cloud_command", required=True)

    cloud_run = cloud_subparsers.add_parser("run", help="pass through to maestro cloud")
    cloud_run.add_argument(
        "args", nargs=argparse.REMAINDER, help="arguments for maestro cloud"
    )

    cloud_probe = cloud_subparsers.add_parser(
        "probe",
        help="run a single hosted flow or tag slice for targeted diagnosis",
        epilog=(
            "Examples:\n"
            "  maestro-android cloud probe --flow tests/maestro-cloud/scenario-runtime-ready-smoke.yaml\n"
            "  maestro-android cloud probe --tags runtime-readiness\n"
            "  maestro-android cloud probe --flow tests/maestro-cloud/scenario-runtime-ready-smoke.yaml --run-root tmp/runtime-probe"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cloud_probe.add_argument("--api-level", type=int, default=34)
    cloud_probe.add_argument("--no-build", action="store_true")
    cloud_probe.add_argument(
        "--project-id", default="", help="override Maestro Cloud project id"
    )
    cloud_probe.add_argument(
        "--device-locale", default="", help="override device locale"
    )
    cloud_probe.add_argument(
        "--flow",
        default="",
        help="explicit hosted flow file path relative to project root",
    )
    cloud_probe.add_argument(
        "--flows-root",
        default="",
        help="override hosted flows root when probing by tags",
    )
    cloud_probe.add_argument(
        "--tags",
        default="",
        help="comma-separated include-tags for the probe",
    )
    cloud_probe.add_argument(
        "--run-root",
        default="",
        help="write artifacts under a specific directory",
    )
    cloud_probe.add_argument("--watch", action="store_true")
    cloud_probe.add_argument("--interval", type=int, default=60)

    cloud_smoke = cloud_subparsers.add_parser(
        "smoke", help="run the hosted cloud-smoke suite"
    )
    cloud_smoke.add_argument(
        "--api-levels", default="", help="comma-separated Android API levels"
    )
    cloud_smoke.add_argument("--no-build", action="store_true")
    cloud_smoke.add_argument(
        "--project-id", default="", help="override Maestro Cloud project id"
    )
    cloud_smoke.add_argument(
        "--device-locale", default="", help="override device locale"
    )
    cloud_smoke.add_argument(
        "--flows-root", default="", help="override hosted smoke flows root"
    )
    cloud_smoke.add_argument("--tags", default="", help="override tag filter")
    cloud_smoke.add_argument("--watch", action="store_true")
    cloud_smoke.add_argument("--interval", type=int, default=60)

    cloud_benchmark = cloud_subparsers.add_parser(
        "benchmark", help="run the hosted GPU-vs-CPU benchmark"
    )
    cloud_benchmark.add_argument(
        "--api-levels", default="", help="comma-separated Android API levels"
    )
    cloud_benchmark.add_argument("--no-build", action="store_true")
    cloud_benchmark.add_argument(
        "--project-id", default="", help="override Maestro Cloud project id"
    )
    cloud_benchmark.add_argument(
        "--device-locale", default="", help="override device locale"
    )
    cloud_benchmark.add_argument(
        "--flow", default="", help="override benchmark flow path"
    )
    cloud_benchmark.add_argument("--watch", action="store_true")
    cloud_benchmark.add_argument("--interval", type=int, default=60)

    cloud_status = cloud_subparsers.add_parser(
        "status", help="poll Maestro Cloud upload status"
    )
    cloud_status.add_argument(
        "--project-id", default="", help="override Maestro Cloud project id"
    )
    cloud_status.add_argument("--watch", action="store_true")
    cloud_status.add_argument("--interval", type=int, default=60)
    cloud_status.add_argument("uploads", nargs="+", help="label:upload-id entries")

    cloud_flow = cloud_subparsers.add_parser(
        "flow", help="run one hosted Maestro flow or flow directory"
    )
    cloud_flow.add_argument("flow", help="flow path or flow directory")
    cloud_flow.add_argument(
        "--api-levels", default="", help="comma-separated Android API levels"
    )
    cloud_flow.add_argument("--no-build", action="store_true")
    cloud_flow.add_argument(
        "--project-id", default="", help="override Maestro Cloud project id"
    )
    cloud_flow.add_argument(
        "--device-locale", default="", help="override device locale"
    )
    cloud_flow.add_argument(
        "--tags", default="", help="extra include-tags filter"
    )
    cloud_flow.add_argument("--watch", action="store_true")
    cloud_flow.add_argument("--interval", type=int, default=60)

    suggest = subparsers.add_parser(
        "suggest",
        help="suggest which lanes to run based on changed files",
        epilog="Examples:\n  maestro-android suggest\n  maestro-android suggest --diff HEAD~3",
    )
    suggest.add_argument(
        "--diff",
        default="HEAD",
        help="git diff target (default: HEAD for uncommitted changes)",
    )

    device = subparsers.add_parser(
        "device",
        help="ad-hoc device inspection and debugging",
        epilog="Examples:\n  maestro-android device files models/\n  maestro-android device logcat --filter 'MULTIMODAL|SendMessage'\n  maestro-android device ui\n  maestro-android device push mmproj.gguf models/\n  maestro-android device info",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    device.add_argument("--device", default="", help="device serial")
    device_sub = device.add_subparsers(dest="device_command", required=True)

    device_files = device_sub.add_parser(
        "files",
        help="list files in the app's external data directory",
        epilog="Examples:\n  maestro-android device files\n  maestro-android device files models/",
    )
    device_files.add_argument(
        "path", nargs="?", default="", help="relative path within the app's external data dir"
    )
    device_files.add_argument(
        "--storage",
        choices=("data", "media"),
        default="data",
        help="storage root: Android/data/<app>/files or Android/media/<app>",
    )
    device_files.add_argument(
        "--all", action="store_true", dest="show_all", help="include hidden files (-la)"
    )

    device_push = device_sub.add_parser(
        "push",
        help="push a local file to the app's external data directory",
        epilog="Examples:\n  maestro-android device push model.gguf models/\n  maestro-android device push config.json",
    )
    device_push.add_argument("local_path", help="local file to push")
    device_push.add_argument(
        "dest", nargs="?", default="", help="relative destination within app's external data dir"
    )
    device_push.add_argument(
        "--storage",
        choices=("data", "media"),
        default="data",
        help="storage root: Android/data/<app>/files or Android/media/<app>",
    )

    device_logcat = device_sub.add_parser(
        "logcat",
        help="capture logcat for the app process",
        epilog="Examples:\n  maestro-android device logcat --filter 'MULTIMODAL|SendMessage'\n  maestro-android device logcat --lines 200\n  maestro-android device logcat --follow --filter 'FATAL'",
    )
    device_logcat.add_argument(
        "--filter", default="", dest="logcat_filter", help="regex pattern to filter output (applied client-side)"
    )
    device_logcat.add_argument(
        "--lines", type=int, default=0, help="limit output to the last N matching lines (0 = all)"
    )
    device_logcat.add_argument(
        "--follow", action="store_true", help="stream logcat continuously (Ctrl-C to stop)"
    )
    device_logcat.add_argument(
        "--save", type=Path, default=None, help="save output to a file"
    )

    device_sub.add_parser(
        "ui",
        help="dump the current UI hierarchy (testTags, bounds, text)",
        epilog="Examples:\n  maestro-android device ui",
    )

    device_sub.add_parser(
        "info",
        help="show app process status, CPU usage, and memory",
        epilog="Examples:\n  maestro-android device info",
    )

    device_foreground = device_sub.add_parser(
        "foreground",
        help="show the current foreground package/activity",
        epilog="Examples:\n  maestro-android device foreground\n  maestro-android device foreground --json",
    )
    device_foreground.add_argument(
        "--json",
        action="store_true",
        dest="foreground_as_json",
        help="emit JSON instead of a human-readable summary",
    )

    device_probe = device_sub.add_parser(
        "probe",
        help="check one device and optionally prime Maestro bootstrap",
        epilog=(
            "Examples:\n"
            "  maestro-android device probe --device <serial>\n"
            "  maestro-android device probe --device emulator-5554 --adb-only\n"
            "  maestro-android device probe --device <host>:5555 --json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    device_probe.add_argument(
        "--adb-only",
        action="store_true",
        help="skip the Maestro launch probe and only validate adb/device state",
    )
    device_probe.add_argument(
        "--json",
        action="store_true",
        dest="probe_as_json",
        help="emit JSON instead of a human-readable summary",
    )

    return parser


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise MaestroAndroidError(
            "ENVIRONMENT_ERROR",
            "PyYAML is required. Run: python3 -m pip install PyYAML",
        )
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        raise MaestroAndroidError("CONFIG_ERROR", f"Invalid flow metadata in {path}")
    return dict(document)


def _parse_csv(raw: str) -> list[str]:
    return [token.strip() for token in raw.split(",") if token.strip()]


def _parse_int_csv(raw: str) -> list[int]:
    values: list[int] = []
    for token in _parse_csv(raw):
        try:
            values.append(int(token))
        except ValueError as exc:
            raise MaestroAndroidError(
                "CONFIG_ERROR", f"Invalid integer list value: {token}"
            ) from exc
    return values


_GENERATED_FLOW_MARKERS = (
    "prepared-flow",
    "prepared-no-launch",
    "prepared-",
)


def _is_generated_flow_path(path: Path) -> bool:
    name = path.name
    if name.startswith(".") and name.endswith((".yaml", ".yml")):
        return True
    return any(marker in name for marker in _GENERATED_FLOW_MARKERS)


def _load_env_file(project_root: Path) -> None:
    env_path = project_root / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_serial(explicit: str) -> str:
    if explicit:
        return explicit
    env_serial = os.environ.get("ADB_SERIAL") or os.environ.get("ANDROID_SERIAL")
    if env_serial:
        return env_serial
    completed = run_subprocess(["adb", "devices", "-l"], capture_output=True, check=False)
    devices: list[dict[str, Any]] = []
    for line in (completed.stdout or "").splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        serial = parts[0]
        details = " ".join(parts[2:])
        detail_map = _parse_device_details(details)
        devices.append(
            {
                "serial": serial,
                "details": details,
                "model": detail_map.get("model", ""),
                "product": detail_map.get("product", ""),
                "device_name": detail_map.get("device", ""),
                "transport": _device_transport_kind(serial),
            }
        )
    if not devices:
        raise MaestroAndroidError("DEVICE_ERROR", "No connected adb device detected.")
    if len(devices) > 1:
        duplicate_groups = _duplicate_transport_groups(devices)
        duplicate_hint = (
            " Duplicate transports are attached for at least one device."
            if duplicate_groups
            else ""
        )
        raise MaestroAndroidError(
            "DEVICE_ERROR",
            (
                f"Multiple adb devices detected ({len(devices)}); pass --device."
                f"{duplicate_hint} Run `maestro-android devices` to choose one serial. Connected: "
                + "; ".join(_device_display_name(d) for d in devices)
            ),
        )
    return str(devices[0]["serial"])


def _cloud_project_id(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> str:
    explicit = getattr(parsed, "project_id", "") or ""
    if explicit:
        return explicit
    env_value = os.environ.get(config.cloud.project_id_env, "")
    if env_value:
        return env_value
    raise MaestroAndroidError(
        "CONFIG_ERROR",
        f"Set --project-id or {config.cloud.project_id_env} in the project environment.",
    )


def _cloud_api_key(config: MaestroAndroidConfig) -> str:
    value = os.environ.get(config.cloud.api_key_env, "")
    if value:
        return value
    raise MaestroAndroidError(
        "CONFIG_ERROR",
        f"Set {config.cloud.api_key_env} in the project environment.",
    )


def _build_apk_if_needed(
    project_root: Path, config: MaestroAndroidConfig, no_build: bool
) -> Path:
    if not no_build:
        run_subprocess(config.project.build_command, cwd=project_root)
    apk_path = _resolve_apk(project_root, config)
    if apk_path is None:
        raise MaestroAndroidError(
            "CONFIG_ERROR", f"No APK matched {config.project.apk_glob}"
        )
    return apk_path


def _run_cloud_maestro(
    *,
    project_root: Path,
    apk_path: Path,
    api_level: int,
    device_locale: str,
    flows: Path,
    include_tags: list[str],
    project_id: str,
    output_path: Path,
    extra_args: Sequence[str] = (),
) -> subprocess.CompletedProcess[str]:
    command = [
        "maestro",
        "cloud",
        "--project-id",
        project_id,
        "--android-api-level",
        str(api_level),
        "--device-locale",
        device_locale,
        "--app-file",
        str(apk_path),
        "--flows",
        str(flows),
        "--format",
        "junit",
        "--output",
        str(output_path),
        *extra_args,
    ]
    for tag in include_tags:
        command.extend(["--include-tags", tag])
    return run_subprocess(command, capture_output=True, check=False, cwd=project_root)


def _run_cloud_smoke(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> int:
    _load_env_file(project_root)
    api_key = _cloud_api_key(config)
    project_id = _cloud_project_id(parsed, config, project_root)
    api_levels = (
        _parse_int_csv(parsed.api_levels)
        if parsed.api_levels
        else list(config.cloud.smoke_api_levels)
    )
    device_locale = parsed.device_locale or config.cloud.device_locale
    flows_root = Path(parsed.flows_root or config.cloud.smoke_flows_root)
    include_tags = (
        _parse_csv(parsed.tags) if parsed.tags else list(config.cloud.smoke_tags)
    )
    apk_path = _build_apk_if_needed(project_root, config, parsed.no_build)

    output_root = project_root / "tmp" / "maestro-cloud-smoke"
    output_root.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    uploads_to_watch: list[str] = []
    for api_level in api_levels:
        run_dir = output_root / f"api-{api_level}"
        run_dir.mkdir(parents=True, exist_ok=True)
        completed = _run_cloud_maestro(
            project_root=project_root,
            apk_path=apk_path,
            api_level=api_level,
            device_locale=device_locale,
            flows=flows_root,
            include_tags=include_tags,
            project_id=project_id,
            output_path=run_dir / "junit.xml",
        )
        (run_dir / "run.log").write_text(
            (completed.stdout or "") + (completed.stderr or ""), encoding="utf-8"
        )
        upload_ids = _extract_cloud_upload_ids(completed.stdout or "", completed.stderr or "")
        uploads_to_watch.extend(f"api-{api_level}:{upload_id}" for upload_id in upload_ids)
        _write_json(
            run_dir / "summary.json",
            {
                "api_level": api_level,
                "project_id": project_id,
                "device_locale": device_locale,
                "flows": str(flows_root),
                "returncode": completed.returncode,
                "upload_ids": upload_ids,
            },
        )
        if completed.returncode != 0:
            exit_code = 1
    print_step(f"Maestro Cloud smoke artifacts: {output_root}")
    watch_exit = _watch_cloud_uploads_if_requested(
        watch=parsed.watch,
        interval=parsed.interval,
        project_id=project_id,
        api_key=api_key,
        uploads=uploads_to_watch,
    )
    if watch_exit != 0:
        exit_code = watch_exit
    return exit_code


def _cloud_probe_run_root(project_root: Path, parsed: argparse.Namespace) -> Path:
    if parsed.run_root:
        requested = Path(parsed.run_root)
        return requested if requested.is_absolute() else (project_root / requested)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%SZ")
    return project_root / "tmp" / "maestro-cloud-probe" / stamp


def _run_cloud_probe(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> int:
    _load_env_file(project_root)
    api_key = _cloud_api_key(config)
    project_id = _cloud_project_id(parsed, config, project_root)
    device_locale = parsed.device_locale or config.cloud.device_locale
    apk_path = _build_apk_if_needed(project_root, config, parsed.no_build)
    include_tags = _parse_csv(parsed.tags)

    if parsed.flow:
        flows_target = (
            (project_root / parsed.flow).resolve()
            if not Path(parsed.flow).is_absolute()
            else Path(parsed.flow).resolve()
        )
        if not flows_target.exists():
            raise MaestroAndroidError(
                "CONFIG_ERROR", f"Hosted flow does not exist: {flows_target}"
            )
    else:
        flows_target = Path(parsed.flows_root or config.cloud.smoke_flows_root)
        if not include_tags:
            raise MaestroAndroidError(
                "CONFIG_ERROR",
                "cloud probe requires --flow or at least one --tags value",
            )

    run_root = _cloud_probe_run_root(project_root, parsed)
    run_root.mkdir(parents=True, exist_ok=True)
    completed = _run_cloud_maestro(
        project_root=project_root,
        apk_path=apk_path,
        api_level=parsed.api_level,
        device_locale=device_locale,
        flows=flows_target,
        include_tags=include_tags,
        project_id=project_id,
        output_path=run_root / "junit.xml",
    )
    (run_root / "run.log").write_text(
        (completed.stdout or "") + (completed.stderr or ""), encoding="utf-8"
    )
    upload_ids = _extract_cloud_upload_ids(completed.stdout or "", completed.stderr or "")
    _write_json(
        run_root / "summary.json",
        {
            "api_level": parsed.api_level,
            "project_id": project_id,
            "device_locale": device_locale,
            "flow": str(flows_target),
            "tags": include_tags,
            "returncode": completed.returncode,
            "upload_ids": upload_ids,
        },
    )
    print_step(f"Maestro Cloud probe artifacts: {run_root}")
    if completed.returncode != 0:
        return 1
    watch_exit = _watch_cloud_uploads_if_requested(
        watch=parsed.watch,
        interval=parsed.interval,
        project_id=project_id,
        api_key=api_key,
        uploads=[f"probe:{upload_id}" for upload_id in upload_ids],
    )
    return watch_exit


def _run_cloud_benchmark(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> int:
    _load_env_file(project_root)
    api_key = _cloud_api_key(config)
    project_id = _cloud_project_id(parsed, config, project_root)
    api_levels = (
        _parse_int_csv(parsed.api_levels)
        if parsed.api_levels
        else list(config.cloud.benchmark_api_levels)
    )
    device_locale = parsed.device_locale or config.cloud.device_locale
    flow = Path(parsed.flow or config.cloud.benchmark_flow)
    apk_path = _build_apk_if_needed(project_root, config, parsed.no_build)

    output_root = project_root / "tmp" / "maestro-cloud-gpu-benchmark"
    output_root.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    uploads_to_watch: list[str] = []
    for api_level in api_levels:
        run_dir = output_root / f"api-{api_level}"
        run_dir.mkdir(parents=True, exist_ok=True)
        completed = _run_cloud_maestro(
            project_root=project_root,
            apk_path=apk_path,
            api_level=api_level,
            device_locale=device_locale,
            flows=flow,
            include_tags=[],
            project_id=project_id,
            output_path=run_dir / "junit.xml",
        )
        (run_dir / "run.log").write_text(
            (completed.stdout or "") + (completed.stderr or ""), encoding="utf-8"
        )
        upload_ids = _extract_cloud_upload_ids(completed.stdout or "", completed.stderr or "")
        uploads_to_watch.extend(f"api-{api_level}:{upload_id}" for upload_id in upload_ids)
        _write_json(
            run_dir / "summary.json",
            {
                "api_level": api_level,
                "project_id": project_id,
                "device_locale": device_locale,
                "flow": str(flow),
                "returncode": completed.returncode,
                "upload_ids": upload_ids,
            },
        )
        if completed.returncode != 0:
            exit_code = 1
    print_step(f"Maestro Cloud benchmark artifacts: {output_root}")
    watch_exit = _watch_cloud_uploads_if_requested(
        watch=parsed.watch,
        interval=parsed.interval,
        project_id=project_id,
        api_key=api_key,
        uploads=uploads_to_watch,
    )
    if watch_exit != 0:
        exit_code = watch_exit
    return exit_code


def _sanitize_label(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw.strip())
    return cleaned.strip("-") or "flow"


def _run_cloud_flow(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> int:
    _load_env_file(project_root)
    api_key = _cloud_api_key(config)
    project_id = _cloud_project_id(parsed, config, project_root)
    api_levels = (
        _parse_int_csv(parsed.api_levels)
        if parsed.api_levels
        else list(config.cloud.smoke_api_levels)
    )
    device_locale = parsed.device_locale or config.cloud.device_locale
    flow = (
        (project_root / parsed.flow).resolve()
        if not Path(parsed.flow).is_absolute()
        else Path(parsed.flow).resolve()
    )
    if not flow.exists():
        raise MaestroAndroidError("CONFIG_ERROR", f"Flow does not exist: {flow}")
    include_tags = _parse_csv(parsed.tags)
    apk_path = _build_apk_if_needed(project_root, config, parsed.no_build)

    output_root = (
        project_root
        / "tmp"
        / "maestro-cloud-targeted"
        / _sanitize_label(_relativize(flow, project_root))
    )
    output_root.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    uploads_to_watch: list[str] = []
    for api_level in api_levels:
        run_dir = output_root / f"api-{api_level}"
        run_dir.mkdir(parents=True, exist_ok=True)
        completed = _run_cloud_maestro(
            project_root=project_root,
            apk_path=apk_path,
            api_level=api_level,
            device_locale=device_locale,
            flows=flow,
            include_tags=include_tags,
            project_id=project_id,
            output_path=run_dir / "junit.xml",
        )
        (run_dir / "run.log").write_text(
            (completed.stdout or "") + (completed.stderr or ""), encoding="utf-8"
        )
        upload_ids = _extract_cloud_upload_ids(completed.stdout or "", completed.stderr or "")
        uploads_to_watch.extend(f"api-{api_level}:{upload_id}" for upload_id in upload_ids)
        _write_json(
            run_dir / "summary.json",
            {
                "api_level": api_level,
                "project_id": project_id,
                "device_locale": device_locale,
                "flow": str(flow),
                "include_tags": include_tags,
                "returncode": completed.returncode,
                "upload_ids": upload_ids,
            },
        )
        if completed.returncode != 0:
            exit_code = 1
    print_step(f"Maestro Cloud targeted artifacts: {output_root}")
    watch_exit = _watch_cloud_uploads_if_requested(
        watch=parsed.watch,
        interval=parsed.interval,
        project_id=project_id,
        api_key=api_key,
        uploads=uploads_to_watch,
    )
    if watch_exit != 0:
        exit_code = watch_exit
    return exit_code


def _project_root(parsed: argparse.Namespace) -> Path:
    return (parsed.project_root or Path.cwd()).resolve()


def _list_devices() -> list[dict[str, Any]]:
    completed = run_subprocess(["adb", "devices", "-l"], capture_output=True, check=False)
    devices: list[dict[str, Any]] = []
    for line in (completed.stdout or "").splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        serial = parts[0]
        state = parts[1] if len(parts) > 1 else "unknown"
        details = " ".join(parts[2:]) if len(parts) > 2 else ""
        detail_map = _parse_device_details(details)
        devices.append(
            {
                "serial": serial,
                "state": state,
                "details": details,
                "transport": _device_transport_kind(serial),
                "model": detail_map.get("model", ""),
                "product": detail_map.get("product", ""),
                "device_name": detail_map.get("device", ""),
                "transport_id": detail_map.get("transport_id", ""),
            }
        )
    return devices


def _device_transport_kind(serial: str) -> str:
    if serial.startswith("emulator-"):
        return "emulator"
    if ":" in serial:
        return "network"
    return "usb"


def _summarize_devices(devices: Sequence[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "total": len(devices),
        "online": 0,
        "offline": 0,
        "unauthorized": 0,
        "emulator": 0,
        "network": 0,
        "usb": 0,
    }
    for device in devices:
        state = device.get("state", "unknown")
        transport = _device_transport_kind(device.get("serial", ""))
        if state == "device":
            summary["online"] += 1
            summary[transport] += 1
        elif state == "offline":
            summary["offline"] += 1
        elif state == "unauthorized":
            summary["unauthorized"] += 1
    return summary


def _resolve_apk(project_root: Path, config: MaestroAndroidConfig) -> Path | None:
    candidates = sorted(project_root.glob(config.project.apk_glob))
    if not candidates:
        return None
    return candidates[0]


def _normalize_artifact_root(base_root: Path, serial: str, label: str) -> Path:
    date_value = datetime.now().strftime("%Y-%m-%d")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return base_root / date_value / serial / label / stamp


def _flow_metadata(path: Path) -> dict[str, Any]:
    return _load_yaml(path)


def _discover_flow_paths(
    project_root: Path,
    config: MaestroAndroidConfig,
    *,
    include_generated: bool = False,
) -> list[Path]:
    paths: list[Path] = []
    for root in config.flows.roots:
        candidate_root = project_root / root
        if not candidate_root.exists():
            continue
        for path in sorted(candidate_root.rglob("*.yaml")):
            if not include_generated and _is_generated_flow_path(path):
                continue
            paths.append(path)
    return paths


def _discover_all_flow_paths(
    project_root: Path,
    config: MaestroAndroidConfig,
    *,
    include_generated: bool = False,
) -> list[Path]:
    return _discover_flow_paths(
        project_root,
        config,
        include_generated=include_generated,
    )


def _discover_generated_flow_paths(
    project_root: Path, config: MaestroAndroidConfig
) -> list[Path]:
    generated: list[Path] = []
    for root in config.flows.roots:
        candidate_root = project_root / root
        if not candidate_root.exists():
            continue
        for path in sorted(candidate_root.rglob("*.yaml")):
            if _is_generated_flow_path(path):
                generated.append(path)
    return generated


def _looks_like_generated_prepared_flow(path: Path) -> bool:
    name = path.name
    return name.endswith("-prepared-flow.yaml") or name.endswith("-prepared-flow.yml")


def _find_generated_prepared_flows(
    project_root: Path, config: MaestroAndroidConfig, *, min_age_days: int = 0
) -> list[Path]:
    roots = {project_root / "tmp"}
    roots.update(project_root / root for root in config.flows.roots)
    threshold = 0.0
    if min_age_days > 0:
        threshold = _time.time() - float(min_age_days) * 86400.0

    matches: list[Path] = []
    for root in sorted(roots):
        if not root.exists():
            continue
        for candidate in root.rglob("*.y*ml"):
            if not _looks_like_generated_prepared_flow(candidate):
                continue
            if threshold and candidate.stat().st_mtime > threshold:
                continue
            matches.append(candidate)
    return sorted(set(matches))


def _select_flows(
    project_root: Path,
    config: MaestroAndroidConfig,
    *,
    explicit_flows: Sequence[str],
    include_tags: list[str],
    exclude_tags: list[str],
) -> list[Path]:
    if explicit_flows:
        resolved: list[Path] = []
        for flow in explicit_flows:
            path = (
                (project_root / flow).resolve()
                if not Path(flow).is_absolute()
                else Path(flow).resolve()
            )
            if not path.exists():
                raise MaestroAndroidError(
                    "CONFIG_ERROR", f"Flow does not exist: {path}"
                )
            resolved.append(path)
        return resolved
    selected: list[Path] = []
    for path in _discover_flow_paths(project_root, config):
        metadata = _flow_metadata(path)
        tags = {
            str(value).strip()
            for value in metadata.get("tags", [])
            if str(value).strip()
        }
        if (
            include_tags
            and not tags.issuperset(include_tags)
            and not set(include_tags).issubset(tags)
        ):
            continue
        if exclude_tags and tags.intersection(exclude_tags):
            continue
        selected.append(path)
    if not selected:
        raise MaestroAndroidError(
            "CONFIG_ERROR", "No flows matched the requested selection."
        )
    return selected


def _capture_logcat(
    serial: str, output_path: Path, timeout_seconds: float | None = None
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    completed = run_subprocess(
        ["adb", "-s", serial, "logcat", "-d"],
        capture_output=True,
        check=False,
        timeout_seconds=timeout_seconds,
    )
    output_path.write_text(completed.stdout or "", encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_trace(path: Path, manifest: dict[str, Any]) -> None:
    steps = []
    for flow in manifest.get("flows", []):
        steps.append(
            {
                "flow": flow["flow"],
                "status": flow["status"],
                "junit": flow.get("junit"),
                "logcat": flow.get("logcat"),
                "debug_output": flow.get("debug_output"),
            }
        )
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "device": manifest.get("device"),
        "flows": steps,
    }
    _write_json(path, payload)


def _relativize(path: Path, project_root: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


_FOREGROUND_COMPONENT_PATTERN = re.compile(
    r"(?P<package>[A-Za-z0-9._$]+)/(?P<activity>[A-Za-z0-9._$/]+)"
)
_FOREGROUND_SIGNAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"mCurrentFocus=.*?(?P<component>[A-Za-z0-9._$]+/[A-Za-z0-9._$/]+)"),
    re.compile(r"topResumedActivity:.*?(?P<component>[A-Za-z0-9._$]+/[A-Za-z0-9._$/]+)"),
    re.compile(r"ResumedActivity:.*?(?P<component>[A-Za-z0-9._$]+/[A-Za-z0-9._$/]+)"),
)
_SYSTEM_PERMISSION_PACKAGES = {
    "com.google.android.permissioncontroller",
    "com.android.permissioncontroller",
}
_PLAY_STORE_PACKAGE = "com.android.vending"
_CLOUD_UPLOAD_ID_PATTERN = re.compile(r"\bmupload_[A-Za-z0-9_-]+\b")


def _parse_foreground_component(raw_text: str) -> dict[str, str | None]:
    for pattern in _FOREGROUND_SIGNAL_PATTERNS:
        match = pattern.search(raw_text)
        if not match:
            continue
        component = match.group("component")
        component_match = _FOREGROUND_COMPONENT_PATTERN.search(component)
        if component_match:
            return {
                "component": component,
                "package": component_match.group("package"),
                "activity": component_match.group("activity"),
            }
    return {"component": None, "package": None, "activity": None}


def _parse_device_details(details: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for token in details.split():
        if ":" not in token:
            continue
        key, value = token.split(":", 1)
        if key and value:
            parsed[key] = value
    return parsed


def _device_identity_key(device: dict[str, Any]) -> tuple[str, str, str] | None:
    model = str(device.get("model") or "").strip()
    product = str(device.get("product") or "").strip()
    hardware = str(device.get("device_name") or "").strip()
    if not any((model, product, hardware)):
        return None
    return (model, product, hardware)


def _duplicate_transport_groups(
    devices: Sequence[dict[str, Any]]
) -> list[list[dict[str, Any]]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for device in devices:
        if device.get("state") != "device":
            continue
        identity = _device_identity_key(device)
        if identity is None:
            continue
        groups.setdefault(identity, []).append(device)
    return [group for group in groups.values() if len(group) > 1]


def _device_display_name(device: dict[str, Any]) -> str:
    parts = [str(device.get("serial", "")).strip()]
    state = str(device.get("state", "")).strip()
    if state:
        parts.append(f"[{state}]")
    transport = str(device.get("transport") or _device_transport_kind(str(device.get("serial", ""))))
    if transport:
        parts.append(f"transport={transport}")
    if device.get("model"):
        parts.append(f"model={device['model']}")
    if device.get("product"):
        parts.append(f"product={device['product']}")
    if device.get("device_name"):
        parts.append(f"device={device['device_name']}")
    aliases = [str(alias).strip() for alias in device.get("aliases", []) if str(alias).strip()]
    if aliases:
        parts.append(f"aliases={','.join(aliases)}")
    details = str(device.get("details", "")).strip()
    if details and not any(
        token in details
        for token in (
            f"model:{device.get('model', '')}",
            f"product:{device.get('product', '')}",
            f"device:{device.get('device_name', '')}",
        )
    ):
        parts.append(details)
    return " ".join(part for part in parts if part)


def _doctor_recommendations(
    config: MaestroAndroidConfig,
    device_summary: dict[str, Any],
    duplicate_groups: Sequence[Sequence[dict[str, Any]]],
    cloud_ready: bool,
) -> list[str]:
    recommendations: list[str] = []
    if not device_summary.get("emulator"):
        recommendations.append(
            "Emulator proof missing: run `maestro-android start-device` then your local smoke/scoped check."
        )
    if not device_summary.get("online"):
        recommendations.append(
            "Connected-device proof missing: attach one phone and run `maestro-android lane smoke --device <serial>`."
        )
    elif duplicate_groups:
        chosen_serial = str(duplicate_groups[0][0].get("serial", "")).strip() or "<serial>"
        recommendations.append(
            "Multiple transports are attached; pin one target explicitly, for example "
            f"`maestro-android lane smoke --device {chosen_serial}`."
        )
    if not cloud_ready:
        recommendations.append(
            "Hosted proof unavailable: set "
            f"`{config.cloud.api_key_env}` and `{config.cloud.project_id_env}`, then run "
            "`maestro-android cloud probe --flow <path>` or `maestro-android cloud smoke`."
        )
    elif cloud_ready:
        recommendations.append(
            "Hosted proof ready: use `maestro-android cloud probe --flow <path>` for a narrow rerun before `cloud smoke`."
        )
    return recommendations


def _extract_cloud_upload_ids(*texts: str) -> list[str]:
    upload_ids: list[str] = []
    for text in texts:
        for upload_id in _CLOUD_UPLOAD_ID_PATTERN.findall(text or ""):
            if upload_id not in upload_ids:
                upload_ids.append(upload_id)
    return upload_ids


def _watch_cloud_uploads_if_requested(
    *,
    watch: bool,
    interval: int,
    project_id: str,
    api_key: str,
    uploads: Sequence[str],
) -> int:
    if not watch or not uploads:
        return 0
    return _run_cloud_status_command(
        project_id=project_id,
        api_key=api_key,
        uploads=uploads,
        watch=True,
        interval=interval,
    )


def _classify_foreground_package(package_name: str | None, app_id: str) -> str:
    if not package_name:
        return "unknown"
    if package_name == app_id:
        return "app"
    if package_name in _SYSTEM_PERMISSION_PACKAGES:
        return "system_permission_dialog"
    if package_name == _PLAY_STORE_PACKAGE:
        return "play_store"
    return "external"


def _collect_foreground_snapshot(serial: str, app_id: str) -> dict[str, Any]:
    window_dump = run_subprocess(
        ["adb", "-s", serial, "shell", "dumpsys", "window", "windows"],
        capture_output=True,
        check=False,
    )
    activity_dump = run_subprocess(
        ["adb", "-s", serial, "shell", "dumpsys", "activity", "activities"],
        capture_output=True,
        check=False,
    )
    window_text = window_dump.stdout or ""
    activity_text = activity_dump.stdout or ""
    parsed = _parse_foreground_component(f"{window_text}\n{activity_text}")
    classification = _classify_foreground_package(parsed["package"], app_id)
    return {
        "serial": serial,
        "app_id": app_id,
        "classification": classification,
        "component": parsed["component"],
        "package": parsed["package"],
        "activity": parsed["activity"],
        "window_dump": window_text,
        "activity_dump": activity_text,
    }


def _write_flow_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, payload)


def _capture_failure_context(serial: str, app_id: str, flow_dir: Path) -> dict[str, Any]:
    context_dir = flow_dir / "failure-context"
    context_dir.mkdir(parents=True, exist_ok=True)

    foreground = _collect_foreground_snapshot(serial, app_id)
    foreground_path = context_dir / "foreground.json"
    _write_json(foreground_path, foreground)

    ui_completed = run_subprocess(
        ["adb", "-s", serial, "exec-out", "uiautomator", "dump", "/dev/tty"],
        capture_output=True,
        check=False,
    )
    raw_ui = (ui_completed.stdout or "").replace(
        "UI hierchary dumped to: /dev/tty", ""
    ).strip()
    ui_path = context_dir / "ui.xml"
    if raw_ui:
        ui_path.write_text(raw_ui + "\n", encoding="utf-8")

    return {
        "foreground": foreground,
        "foreground_path": foreground_path,
        "ui_path": ui_path if raw_ui else None,
    }


_FAILURE_HINTS: list[tuple[str, str]] = [
    (r"FATAL EXCEPTION.*NullPointerException", "App crashed with NullPointerException. Check the stack trace in logcat.txt."),
    (r"FATAL EXCEPTION", "App crashed with a fatal exception. Check logcat.txt for the full stack trace."),
    (r"Fatal signal|SIGSEGV", "Native crash detected (SIGSEGV/signal). Check logcat.txt for the native backtrace."),
    (r"ANR in", "Application Not Responding. The app froze. Check logcat.txt for ANR details."),
    (r"Timeout.*waiting for|timed out", "Maestro timed out waiting for a UI element. The selector may be wrong or the app is slow to render."),
    (r"No views? found|Could not find", "Maestro could not find the target UI element. Run `maestro-android audit-selectors` to check selector health."),
    (r"Unable to launch app|app.*not installed", "The app could not be launched. Verify it is installed with `adb shell pm list packages`."),
    (r"OutOfMemoryError", "The app ran out of memory. Consider reducing model size or checking for memory leaks."),
]


def _diagnose_failure(flow_result: dict[str, Any], artifact_root: Path) -> None:
    foreground_rel = flow_result.get("foreground")
    if foreground_rel:
        foreground_path = artifact_root / foreground_rel
        if foreground_path.exists():
            foreground_payload = json.loads(
                foreground_path.read_text(encoding="utf-8")
            )
            classification = foreground_payload.get("classification")
            component = foreground_payload.get("component") or "unknown"
            if classification == "system_permission_dialog":
                print_step(
                    "Hint: a system permission dialog took the foreground. "
                    f"Current top activity: {component}."
                )
                return
            if classification == "play_store":
                print_step(
                    "Hint: the app lost foreground to the Play Store. "
                    f"Current top activity: {component}."
                )
                return
            if classification == "external":
                print_step(
                    "Hint: the app lost foreground to another package. "
                    f"Current top activity: {component}."
                )
                return

    texts_to_scan: list[str] = []
    for key in ("logcat", "stderr"):
        rel = flow_result.get(key)
        if not rel:
            continue
        path = artifact_root / rel
        if path.exists():
            texts_to_scan.append(path.read_text(encoding="utf-8", errors="replace"))
    combined = "\n".join(texts_to_scan)
    for pattern, hint in _FAILURE_HINTS:
        if re.search(pattern, combined, re.IGNORECASE):
            print_step(f"Hint: {hint}")
            return
    print_step("Hint: Check maestro-stderr.log and logcat.txt in the artifact directory for details.")


def _print_run_summary(flow_results: list[dict[str, Any]]) -> None:
    passed = sum(1 for f in flow_results if f["status"] == "passed")
    failed = sum(1 for f in flow_results if f["status"] != "passed")
    total = len(flow_results)
    print()
    print(f"  {'Flow':<55} {'Status':<10} {'Duration'}")
    print(f"  {'-' * 55} {'-' * 10} {'-' * 10}")
    for flow in flow_results:
        name = flow["flow"]
        if len(name) > 54:
            name = "..." + name[-51:]
        duration = f"{flow.get('duration_s', 0)}s"
        status = flow["status"].upper()
        print(f"  {name:<55} {status:<10} {duration}")
    print()
    slowest = max(flow_results, key=lambda f: f.get("duration_s", 0)) if flow_results else None
    slowest_note = f", slowest: {slowest['flow']} ({slowest.get('duration_s', 0)}s)" if slowest and total > 1 else ""
    print_step(f"Result: {passed} passed, {failed} failed of {total}{slowest_note}")


def _run_maestro_flow(
    *,
    project_root: Path,
    serial: str,
    flow: Path,
    app_id: str,
    clear_state: bool,
    output_format: str,
    artifact_root: Path,
    extra_args: Sequence[str] = (),
    adb_timeout_sec: float | None = None,
    maestro_timeout_sec: float | None = None,
) -> dict[str, Any]:
    flow_dir = artifact_root / "flows" / flow.stem
    debug_dir = flow_dir / "maestro-debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    state_path = flow_dir / "flow-state.json"
    run_subprocess(
        ["adb", "-s", serial, "logcat", "-c"],
        check=False,
        cwd=project_root,
        timeout_seconds=adb_timeout_sec,
    )
    if clear_state and app_id:
        run_subprocess(
            ["adb", "-s", serial, "shell", "pm", "clear", app_id],
            check=False,
            cwd=project_root,
            timeout_seconds=adb_timeout_sec,
        )
    command = [
        "maestro",
        "--device",
        serial,
        "test",
        str(flow),
        "--debug-output",
        str(debug_dir),
        "--format",
        output_format,
        *extra_args,
    ]
    started_at = _time.time()
    _write_flow_state(
        state_path,
        {
            "flow": _relativize(flow, project_root),
            "status": "running",
            "serial": serial,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "command": command,
        },
    )
    junit_path = flow_dir / "junit.xml"
    stderr_path = flow_dir / "maestro-stderr.log"
    stdout_path = flow_dir / "maestro-stdout.log"
    logcat_path = flow_dir / "logcat.txt"
    try:
        completed = run_subprocess(
            command,
            capture_output=True,
            check=False,
            cwd=project_root,
            timeout_seconds=maestro_timeout_sec,
        )
        finished_at = _time.time()
        duration_s = round(finished_at - started_at, 1)
        if output_format == "junit":
            junit_path.write_text(completed.stdout or "", encoding="utf-8")
        else:
            stdout_path.write_text(completed.stdout or "", encoding="utf-8")
        stderr_path.write_text(completed.stderr or "", encoding="utf-8")
        _capture_logcat(serial, logcat_path, timeout_seconds=adb_timeout_sec)
        failure_context = (
            _capture_failure_context(serial, app_id, flow_dir)
            if completed.returncode != 0
            else None
        )
        result = {
            "flow": _relativize(flow, project_root),
            "status": "passed" if completed.returncode == 0 else "failed",
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_s": duration_s,
            "returncode": completed.returncode,
            "junit": str(junit_path.relative_to(artifact_root))
            if junit_path.exists()
            else None,
            "stderr": str(stderr_path.relative_to(artifact_root)),
            "stdout": str(stdout_path.relative_to(artifact_root))
            if stdout_path.exists()
            else None,
            "logcat": str(logcat_path.relative_to(artifact_root)),
            "debug_output": str(debug_dir.relative_to(artifact_root)),
        }
        if failure_context is not None:
            result["foreground"] = str(
                failure_context["foreground_path"].relative_to(artifact_root)
            )
            if failure_context["ui_path"] is not None:
                result["ui_dump"] = str(
                    failure_context["ui_path"].relative_to(artifact_root)
                )
        _write_flow_state(
            state_path,
            {
                "flow": result["flow"],
                "status": result["status"],
                "serial": serial,
                "started_at": datetime.fromtimestamp(started_at).isoformat(
                    timespec="seconds"
                ),
                "finished_at": datetime.fromtimestamp(finished_at).isoformat(
                    timespec="seconds"
                ),
                "duration_s": duration_s,
                "command": command,
                "returncode": completed.returncode,
            },
        )
        return result
    except MaestroAndroidError as exc:
        finished_at = _time.time()
        duration_s = round(finished_at - started_at, 1)
        stderr_path.write_text(exc.message + "\n", encoding="utf-8")
        _capture_logcat(serial, logcat_path, timeout_seconds=adb_timeout_sec)
        failure_context = _capture_failure_context(serial, app_id, flow_dir)
        result = {
            "flow": _relativize(flow, project_root),
            "status": "failed",
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_s": duration_s,
            "returncode": None,
            "junit": None,
            "stderr": str(stderr_path.relative_to(artifact_root)),
            "stdout": None,
            "logcat": str(logcat_path.relative_to(artifact_root)),
            "debug_output": str(debug_dir.relative_to(artifact_root)),
            "foreground": str(
                failure_context["foreground_path"].relative_to(artifact_root)
            ),
        }
        if failure_context["ui_path"] is not None:
            result["ui_dump"] = str(
                failure_context["ui_path"].relative_to(artifact_root)
            )
        _write_flow_state(
            state_path,
            {
                "flow": result["flow"],
                "status": "failed",
                "serial": serial,
                "started_at": datetime.fromtimestamp(started_at).isoformat(
                    timespec="seconds"
                ),
                "finished_at": datetime.fromtimestamp(finished_at).isoformat(
                    timespec="seconds"
                ),
                "duration_s": duration_s,
                "command": command,
                "error_code": exc.code,
                "error_message": exc.message,
            },
        )
        return result


def _execute_test_run(
    *,
    project_root: Path,
    config: MaestroAndroidConfig,
    serial: str,
    flows: list[Path],
    no_build: bool,
    no_install: bool,
    clear_state: bool,
    output_format: str,
    label: str,
) -> Path:
    if not no_build:
        run_subprocess(config.project.build_command, cwd=project_root)
    if not no_install:
        run_subprocess(config.project.install_command, cwd=project_root)
    apk_path = _resolve_apk(project_root, config)

    artifact_root = _normalize_artifact_root(
        project_root / config.artifacts.scratch_root, serial, label
    )
    artifact_root.mkdir(parents=True, exist_ok=True)

    flow_results: list[dict[str, Any]] = []
    for flow in flows:
        flow_results.append(
            _run_maestro_flow(
                project_root=project_root,
                serial=serial,
                flow=flow,
                app_id=config.project.app_id,
                clear_state=clear_state,
                output_format=output_format,
                artifact_root=artifact_root,
            )
        )

    for flow_result in flow_results:
        if flow_result["status"] != "passed":
            _diagnose_failure(flow_result, artifact_root)

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "device": serial,
        "apk": str(apk_path) if apk_path is not None else None,
        "label": label,
        "flows": flow_results,
    }
    _write_json(artifact_root / "run-manifest.json", manifest)
    _write_trace(artifact_root / "trace.json", manifest)
    _print_run_summary(flow_results)
    print_step(f"Maestro test artifacts: {artifact_root}")
    return artifact_root


def _run_test(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> None:
    serial = _resolve_serial(parsed.device)
    explicit_flows = list(parsed.flows) + _parse_csv(parsed.flow_csv)
    include_tags = _parse_csv(parsed.include_tags)
    exclude_tags = _parse_csv(parsed.exclude_tags)
    flows = _select_flows(
        project_root,
        config,
        explicit_flows=explicit_flows,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
    )
    artifact_root = _execute_test_run(
        project_root=project_root,
        config=config,
        serial=serial,
        flows=flows,
        no_build=parsed.no_build,
        no_install=parsed.no_install,
        clear_state=parsed.clear_state,
        output_format=parsed.format,
        label="raw",
    )
    manifest = json.loads(
        (artifact_root / "run-manifest.json").read_text(encoding="utf-8")
    )
    failed = [flow for flow in manifest.get("flows", []) if flow["status"] != "passed"]
    if failed:
        names = ", ".join(flow["flow"] for flow in failed)
        raise MaestroAndroidError("DEVICE_ERROR", f"Flow run failed: {names}")


def _run_lane(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> None:
    lane = config.lanes.get(parsed.name)
    if lane is None:
        raise MaestroAndroidError("CONFIG_ERROR", f"Unknown lane '{parsed.name}'")
    extra_args = list(parsed.args)
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]
    if lane.kind == "command":
        command = [*lane.argv, *extra_args]
        if not command:
            raise MaestroAndroidError(
                "CONFIG_ERROR", f"Lane '{parsed.name}' is missing a command"
            )
        env_override = dict(os.environ)
        if parsed.device:
            env_override["ANDROID_SERIAL"] = parsed.device
            env_override["ADB_SERIAL"] = parsed.device
        run_subprocess(command, cwd=project_root, env=env_override)
        return
    if extra_args:
        raise MaestroAndroidError(
            "CONFIG_ERROR", f"Lane '{parsed.name}' does not accept extra args."
        )
    serial = _resolve_serial(parsed.device)
    flows = _select_flows(
        project_root,
        config,
        explicit_flows=lane.flows,
        include_tags=list(lane.include_tags),
        exclude_tags=list(lane.exclude_tags),
    )
    artifact_root = _execute_test_run(
        project_root=project_root,
        config=config,
        serial=serial,
        flows=flows,
        no_build=lane.no_build,
        no_install=lane.no_install,
        clear_state=lane.clear_state,
        output_format=lane.format,
        label=lane.label or parsed.name,
    )
    manifest = json.loads(
        (artifact_root / "run-manifest.json").read_text(encoding="utf-8")
    )
    failed = [flow for flow in manifest.get("flows", []) if flow["status"] != "passed"]
    if failed:
        names = ", ".join(flow["flow"] for flow in failed)
        raise MaestroAndroidError(
            "DEVICE_ERROR", f"Lane '{parsed.name}' failed: {names}"
        )


def _validate_scoped_flow(
    flow_path: Path, config: MaestroAndroidConfig, project_root: Path
) -> None:
    if not flow_path.exists():
        raise MaestroAndroidError(
            "CONFIG_ERROR", f"Scoped flow does not exist: {flow_path}"
        )
    if config.scoped.require_tmp_flow:
        try:
            rel = flow_path.relative_to(project_root)
        except ValueError:
            rel = flow_path
        if not str(rel).startswith("tmp/"):
            raise MaestroAndroidError(
                "CONFIG_ERROR", "Scoped flows must live under tmp/"
            )
    if not config.scoped.require_title_description_comments:
        return
    lines = flow_path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise MaestroAndroidError(
            "CONFIG_ERROR",
            f"Scoped flow must start with title/description comments: {flow_path}",
        )
    first = lines[0].strip().lower()
    second = lines[1].strip().lower()
    if not first.startswith("#") or "title" not in first:
        raise MaestroAndroidError(
            "CONFIG_ERROR", f"First line must be a title comment in {flow_path}"
        )
    if not second.startswith("#") or "description" not in second:
        raise MaestroAndroidError(
            "CONFIG_ERROR", f"Second line must be a description comment in {flow_path}"
        )


def _scan_logcat(
    logcat_text: str, signature_regex: str, app_context_regex: str
) -> list[str]:
    signature_pattern = re.compile(signature_regex)
    context_pattern = re.compile(app_context_regex) if app_context_regex else None
    lines = logcat_text.splitlines()
    matches: list[str] = []
    for index, line in enumerate(lines):
        if not signature_pattern.search(line):
            continue
        if context_pattern is None:
            matches.append(line)
            continue
        window = "\n".join(lines[max(0, index - 3) : min(len(lines), index + 4)])
        if context_pattern.search(window):
            matches.append(line)
    return matches


def _append_gradle_properties(
    command: Sequence[str], gradle_properties: Sequence[str]
) -> list[str]:
    appended = list(command)
    for prop in gradle_properties:
        appended.append(prop if prop.startswith("-P") else f"-P{prop}")
    return appended


def _parse_key_value_args(
    raw_values: Sequence[str], option_name: str
) -> dict[str, str]:
    parsed_values: dict[str, str] = {}
    for raw_value in raw_values:
        key, separator, value = raw_value.partition("=")
        if not separator or not key.strip() or not value.strip():
            raise MaestroAndroidError(
                "CONFIG_ERROR",
                f"Invalid {option_name} value '{raw_value}'. Expected key=value.",
            )
        parsed_values[key.strip()] = value.strip()
    return parsed_values


def _append_instrumentation_runner_args(
    command: Sequence[str], runner_args: dict[str, str]
) -> list[str]:
    appended = list(command)
    for key, value in runner_args.items():
        appended.append(f"-Pandroid.testInstrumentationRunnerArguments.{key}={value}")
    return appended


def _run_scoped_gradle(
    parsed: argparse.Namespace,
    config: MaestroAndroidConfig,
    project_root: Path,
    serial: str | None,
    artifact_root: Path,
) -> int:
    if parsed.scoped_type == "unit":
        if parsed.runner_args:
            raise MaestroAndroidError(
                "CONFIG_ERROR",
                "--runner-arg is only supported with --type instrumented",
            )
        task = "testDebugUnitTest"
        command = _append_gradle_properties(["./gradlew", task], parsed.gradle_properties)
        if parsed.test_class:
            command.append(f"--tests={parsed.test_class}")
        runner_args: dict[str, str] = {}
    else:
        assert serial is not None
        run_subprocess(
            ["adb", "-s", serial, "logcat", "-c"],
            check=False,
            cwd=project_root,
            timeout_seconds=float(parsed.adb_timeout_sec),
        )
        task = "connectedDebugAndroidTest"
        command = _append_gradle_properties(["./gradlew", task], parsed.gradle_properties)
        runner_args = _parse_key_value_args(parsed.runner_args, "--runner-arg")
        if parsed.test_class and "class" not in runner_args:
            runner_args["class"] = parsed.test_class
        command = _append_instrumentation_runner_args(command, runner_args)
    env_override = dict(os.environ)
    if serial:
        env_override["ANDROID_SERIAL"] = serial
    completed = run_subprocess(
        command, capture_output=True, check=False, cwd=project_root,
        env=env_override,
    )
    stdout_path = artifact_root / "gradle-stdout.log"
    stderr_path = artifact_root / "gradle-stderr.log"
    stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")
    logcat_name: str | None = None
    if serial is not None:
        logcat_path = artifact_root / "logcat.txt"
        _capture_logcat(serial, logcat_path, timeout_seconds=float(parsed.adb_timeout_sec))
        logcat_name = "logcat.txt"

    status = "passed" if completed.returncode == 0 else "failed"
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "device": serial,
        "label": f"scoped-{parsed.scoped_type}",
        "type": parsed.scoped_type,
        "test_class": parsed.test_class or None,
        "flows": [{
            "flow": parsed.test_class or task,
            "status": status,
            "returncode": completed.returncode,
            "logcat": logcat_name,
            "stderr": "gradle-stderr.log",
        }],
    }
    if runner_args:
        manifest["runner_args"] = runner_args
    _write_json(artifact_root / "run-manifest.json", manifest)
    print_step(f"Scoped {parsed.scoped_type} artifacts: {artifact_root}")
    if status != "passed":
        raise MaestroAndroidError(
            "DEVICE_ERROR",
            f"Scoped {parsed.scoped_type} run failed (exit {completed.returncode}). "
            f"See {stderr_path}",
        )
    return 0


def _run_scoped(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> int:
    serial = None if parsed.scoped_type == "unit" else _resolve_serial(parsed.device)
    artifact_root = _normalize_artifact_root(
        project_root / config.artifacts.scratch_root, serial or "host", "scoped"
    )
    artifact_root.mkdir(parents=True, exist_ok=True)
    env_override = dict(os.environ)
    if serial:
        env_override["ANDROID_SERIAL"] = serial

    if not parsed.no_build:
        build_cmd = _append_gradle_properties(
            list(config.project.build_command),
            parsed.gradle_properties,
        )
        run_subprocess(build_cmd, cwd=project_root, env=env_override)
    if not parsed.no_install and parsed.scoped_type != "unit":
        install_cmd = _append_gradle_properties(
            list(config.project.install_command),
            parsed.gradle_properties,
        )
        run_subprocess(install_cmd, cwd=project_root, env=env_override)

    if parsed.scoped_type != "maestro":
        return _run_scoped_gradle(parsed, config, project_root, serial, artifact_root)
    if not parsed.flow:
        raise MaestroAndroidError(
            "CONFIG_ERROR",
            "--flow is required unless --type is instrumented or unit",
        )
    flow_path = (
        (project_root / parsed.flow).resolve()
        if not Path(parsed.flow).is_absolute()
        else Path(parsed.flow).resolve()
    )
    _validate_scoped_flow(flow_path, config, project_root)

    extra_args = list(parsed.extra_args)
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]
    result = _run_maestro_flow(
        project_root=project_root,
        serial=serial,
        flow=flow_path,
        app_id=config.project.app_id,
        clear_state=False,
        output_format="junit",
        artifact_root=artifact_root,
        extra_args=extra_args,
        adb_timeout_sec=float(parsed.adb_timeout_sec),
        maestro_timeout_sec=float(parsed.maestro_timeout_sec),
    )
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "device": serial,
        "label": "scoped",
        "flows": [result],
    }
    _write_json(artifact_root / "run-manifest.json", manifest)
    _write_trace(artifact_root / "trace.json", manifest)

    logcat_path = artifact_root / result["logcat"]
    logcat_text = (
        logcat_path.read_text(encoding="utf-8") if logcat_path.exists() else ""
    )
    matches = _scan_logcat(
        logcat_text,
        parsed.pattern or config.scoped.crash_signature_regex,
        parsed.app_context or config.scoped.app_context_regex,
    )
    summary_lines = [
        f"# Scoped Run",
        "",
        f"- Flow: {_relativize(flow_path, project_root)}",
        f"- Device: {serial}",
        f"- Maestro status: {result['status']}",
        f"- Crash signatures: {len(matches)}",
        f"- Artifact root: {artifact_root}",
    ]
    if matches:
        summary_lines.extend(["", "## Matching Logcat Lines", ""])
        summary_lines.extend(f"- {line}" for line in matches[:20])
    summary_path = artifact_root / "scoped-summary.md"
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print_step(f"Scoped run artifacts: {artifact_root}")
    if result["status"] != "passed":
        raise MaestroAndroidError(
            "DEVICE_ERROR", f"Scoped run failed: {result['flow']}"
        )
    if matches:
        print("DEVICE_ERROR: Crash signatures detected in scoped run.")
        return 2
    return 0


def _run_report(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> None:
    bundle = find_bundle(parsed.kind, config=config, repo_root=project_root)
    print_bundle(bundle)
    if parsed.open_files:
        open_bundle(bundle)


def _run_trace(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> None:
    bundle = find_bundle(parsed.kind, config=config, repo_root=project_root)
    print(f"{bundle.kind} trace bundle:")
    print(f"  Artifact root: {bundle.artifact_root}")
    trace_path = bundle.artifact_root / "trace.json"
    if trace_path.exists():
        print(f"  {trace_path}")
        if parsed.open_files:
            open_bundle(
                type(bundle)(
                    kind=bundle.kind,
                    artifact_root=bundle.artifact_root,
                    report_files=(trace_path,),
                )
            )
        return
    debug_dirs = sorted(
        path for path in bundle.artifact_root.rglob("maestro-debug") if path.is_dir()
    )
    for path in debug_dirs:
        print(f"  {path}")


_MAESTRO_COMMANDS = {
    "launchApp", "stopApp", "clearState", "clearKeychain",
    "tapOn", "longPressOn", "doubleTapOn", "swipe", "scroll",
    "scrollUntilVisible", "assertVisible", "assertNotVisible",
    "inputText", "eraseText", "pressKey", "hideKeyboard",
    "openLink", "back", "copyTextFrom", "pasteText",
    "evalScript", "runFlow", "setLocation", "repeat",
    "waitForAnimationToEnd", "takeScreenshot", "startRecording",
    "stopRecording", "assertTrue", "extendedWaitUntil",
    "addMedia", "travel",
}


def _lint_flow(path: Path, strict: bool) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if _looks_like_generated_prepared_flow(path):
        issues.append(
            {
                "level": "warning",
                "message": "Generated prepared-flow artifact checked into the flow tree; clean it before widening runs",
            }
        )
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        issues.append({"level": "error", "message": f"Cannot read file: {exc}"})
        return issues

    if not text.strip():
        issues.append({"level": "error", "message": "File is empty"})
        return issues

    if yaml is None:
        issues.append({"level": "error", "message": "PyYAML not installed"})
        return issues

    try:
        documents = list(yaml.safe_load_all(text))
    except Exception as exc:
        issues.append({"level": "error", "message": f"YAML parse error: {exc}"})
        return issues

    document: Any = None
    for doc in documents:
        if doc is None:
            continue
        if document is None:
            document = doc
        elif isinstance(document, dict) and isinstance(doc, (list, dict)):
            if isinstance(doc, list):
                document["steps"] = doc
            else:
                document.update(doc)

    if document is None:
        issues.append({"level": "error", "message": "YAML parsed to null / empty"})
        return issues

    if isinstance(document, dict):
        app_id = document.get("appId")
        if not app_id:
            issues.append({"level": "warning", "message": "Missing 'appId' — flow may fail on device"})
        steps = document.get("steps") or document.get("commands") or []
        if not steps and not document.get("env"):
            has_commands = any(
                key in _MAESTRO_COMMANDS or key.startswith("-") for key in document.keys()
            )
            if not has_commands:
                issues.append({"level": "warning", "message": "No steps/commands found in flow"})
    elif isinstance(document, list):
        steps = document
    else:
        issues.append({"level": "error", "message": f"Unexpected YAML root type: {type(document).__name__}"})
        return issues

    if isinstance(document, dict):
        steps_to_check: list[Any] = []
        for key, value in document.items():
            if key.startswith("-") or key in _MAESTRO_COMMANDS:
                steps_to_check.append({key: value})
            elif key == "steps" and isinstance(value, list):
                steps_to_check.extend(value)
        for step in steps_to_check:
            if isinstance(step, dict):
                for command_name in step:
                    cleaned = command_name.lstrip("- ")
                    if cleaned and cleaned not in _MAESTRO_COMMANDS and cleaned not in {
                        "appId", "name", "tags", "env", "onFlowStart", "onFlowComplete",
                        "onFlowError", "steps", "commands",
                    }:
                        issues.append({
                            "level": "warning",
                            "message": f"Unknown command '{cleaned}' (may be valid in newer Maestro)",
                        })

    lines = text.splitlines()
    if lines:
        first = lines[0].strip()
        if not first.startswith("#") and not first.startswith("appId") and not first.startswith("---"):
            if strict:
                issues.append({"level": "warning", "message": "First line is not a comment or appId declaration"})

    return issues


def _run_lint(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> int:
    if yaml is None:
        raise MaestroAndroidError(
            "ENVIRONMENT_ERROR", "PyYAML is required. Run: python3 -m pip install PyYAML"
        )
    strict = parsed.strict
    if parsed.flows:
        flow_paths = [
            (project_root / f).resolve() if not Path(f).is_absolute() else Path(f).resolve()
            for f in parsed.flows
        ]
        for p in flow_paths:
            if not p.exists():
                raise MaestroAndroidError("CONFIG_ERROR", f"Flow does not exist: {p}")
    else:
        flow_paths = _discover_all_flow_paths(
            project_root,
            config,
            include_generated=parsed.include_generated_flows,
        )
    if not flow_paths:
        print_step("No flows found to lint.")
        return 0
    if not parsed.include_generated_flows:
        generated_count = len(_discover_generated_flow_paths(project_root, config))
        if generated_count:
            print_step(
                f"Skipping {generated_count} generated/prepared flow files "
                "(use --include-generated-flows to lint them)"
            )

    total_errors = 0
    total_warnings = 0
    for path in flow_paths:
        issues = _lint_flow(path, strict)
        rel = _relativize(path, project_root)
        errors = [i for i in issues if i["level"] == "error"]
        warnings = [i for i in issues if i["level"] == "warning"]
        total_errors += len(errors)
        total_warnings += len(warnings)
        if not issues:
            print(f"  ok     {rel}")
            continue
        for issue in issues:
            tag = "ERROR" if issue["level"] == "error" else "WARN "
            print(f"  {tag}  {rel}: {issue['message']}")

    print()
    effective_errors = total_errors + (total_warnings if strict else 0)
    print_step(
        f"Lint: {len(flow_paths)} flows, {total_errors} errors, {total_warnings} warnings"
        + (" (strict: warnings are errors)" if strict else "")
    )
    return 1 if effective_errors > 0 else 0


_TEST_TAG_PATTERN = re.compile(r'\.testTag\(\s*"([^"]+)"\s*\)')
_FLOW_ID_PATTERN = re.compile(r'\bid:\s*["\']?([^\s"\',}]+)')


def _scan_kotlin_test_tags(source_roots: list[Path]) -> dict[str, list[str]]:
    tags: dict[str, list[str]] = {}
    for root in source_roots:
        if not root.exists():
            continue
        for kt_file in sorted(root.rglob("*.kt")):
            text = kt_file.read_text(encoding="utf-8", errors="replace")
            for match in _TEST_TAG_PATTERN.finditer(text):
                tag = match.group(1)
                if tag not in tags:
                    tags[tag] = []
                tags[tag].append(str(kt_file))
    return tags


def _scan_flow_ids(flow_paths: list[Path]) -> dict[str, list[str]]:
    ids: dict[str, list[str]] = {}
    for path in flow_paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _FLOW_ID_PATTERN.finditer(text):
            tag = match.group(1)
            if tag not in ids:
                ids[tag] = []
            ids[tag].append(str(path))
    return ids


def _run_audit_testtags(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> int:
    explicit_roots = _parse_csv(getattr(parsed, "source_roots", ""))
    if explicit_roots:
        source_roots = [project_root / r for r in explicit_roots]
    else:
        candidates = ["app/src", "apps", "src/main", "src"]
        source_roots = [project_root / c for c in candidates if (project_root / c).exists()]
    if not source_roots:
        print_step("No Kotlin source roots found.")
        return 1

    kotlin_tags = _scan_kotlin_test_tags(source_roots)
    flow_paths = _discover_all_flow_paths(
        project_root,
        config,
        include_generated=getattr(parsed, "include_generated_flows", False),
    )
    flow_ids = _scan_flow_ids(flow_paths)

    all_tags = sorted(set(kotlin_tags.keys()) | set(flow_ids.keys()))
    orphaned = []
    missing = []
    matched = []

    print(f"  {'testTag':<45} {'Kotlin':<8} {'Flows':<8} {'Status'}")
    print(f"  {'-' * 45} {'-' * 8} {'-' * 8} {'-' * 10}")
    for tag in all_tags:
        in_kotlin = len(kotlin_tags.get(tag, []))
        in_flows = len(flow_ids.get(tag, []))
        if in_kotlin > 0 and in_flows > 0:
            status = "ok"
            matched.append(tag)
        elif in_kotlin > 0:
            status = "orphaned"
            orphaned.append(tag)
        else:
            status = "missing"
            missing.append(tag)
        print(f"  {tag:<45} {in_kotlin:<8} {in_flows:<8} {status}")

    print()
    print_step(
        f"testTag audit: {len(matched)} matched, {len(orphaned)} orphaned (in code but not flows), "
        f"{len(missing)} missing (in flows but not code)"
    )
    return 1 if missing else 0


def _resolve_source_roots(
    explicit: str, project_root: Path
) -> list[Path]:
    if explicit:
        return [project_root / r for r in _parse_csv(explicit)]
    candidates = ["app/src", "apps", "src/main", "src"]
    return [project_root / c for c in candidates if (project_root / c).exists()]


def _run_audit_selectors(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> int:
    source_roots = _resolve_source_roots(
        getattr(parsed, "source_roots", ""), project_root
    )
    if not source_roots:
        print_step("No Kotlin source roots found.")
        return 1

    kotlin_tags = _scan_kotlin_test_tags(source_roots)
    flow_paths = _discover_all_flow_paths(
        project_root,
        config,
        include_generated=getattr(parsed, "include_generated_flows", False),
    )
    flow_ids = _scan_flow_ids(flow_paths)

    dangling: list[tuple[str, list[str]]] = []
    covered: list[str] = []

    for selector, flow_files in sorted(flow_ids.items()):
        if selector in kotlin_tags:
            covered.append(selector)
        else:
            dangling.append((selector, flow_files))

    print(f"  {'Selector':<45} {'Flows':<6} {'Kotlin':<8} {'Status'}")
    print(f"  {'-' * 45} {'-' * 6} {'-' * 8} {'-' * 10}")
    for selector in covered:
        in_flows = len(flow_ids[selector])
        in_kotlin = len(kotlin_tags[selector])
        print(f"  {selector:<45} {in_flows:<6} {in_kotlin:<8} ok")
    for selector, flow_files in dangling:
        in_flows = len(flow_files)
        print(f"  {selector:<45} {in_flows:<6} {'0':<8} dangling")

    print()
    print_step(
        f"Selector audit: {len(covered)} covered, {len(dangling)} dangling "
        f"(used in flows but no matching testTag in code)"
    )
    if dangling:
        print_step("Hint: dangling selectors will cause 'No view found' failures at runtime.")
    return 1 if dangling else 0


def _merge_junit(inputs: list[Path], output_path: Path) -> None:
    root = ET.Element("testsuites")
    for path in inputs:
        parsed = ET.parse(path)
        current_root = parsed.getroot()
        if current_root.tag == "testsuite":
            root.append(current_root)
        else:
            for child in list(current_root):
                root.append(child)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)


def _run_merge_reports(parsed: argparse.Namespace) -> None:
    manifests: list[dict[str, Any]] = []
    junit_paths: list[Path] = []
    for raw_input in parsed.inputs:
        candidate = Path(raw_input)
        manifest_path = (
            candidate
            if candidate.name == "run-manifest.json"
            else candidate / "run-manifest.json"
        )
        if not manifest_path.exists():
            raise MaestroAndroidError(
                "CONFIG_ERROR", f"Missing run-manifest.json in {raw_input}"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifests.append(manifest)
        root_dir = manifest_path.parent
        for flow in manifest.get("flows", []):
            junit = flow.get("junit")
            if junit:
                junit_path = root_dir / junit
                if junit_path.exists():
                    junit_paths.append(junit_path)
    out_dir = parsed.out
    out_dir.mkdir(parents=True, exist_ok=True)
    merged_manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_count": len(manifests),
        "runs": manifests,
    }
    _write_json(out_dir / "merged-run-manifest.json", merged_manifest)
    if junit_paths:
        _merge_junit(junit_paths, out_dir / "merged-junit.xml")
    print_step(f"Merged reports written to {out_dir}")


def _run_clean(parsed: argparse.Namespace, config: MaestroAndroidConfig) -> None:
    project_root = _project_root(parsed)
    if getattr(parsed, "generated_flows", False):
        generated = _discover_generated_flow_paths(project_root, config)
        if not generated:
            print_step("No generated/prepared flow files found.")
        else:
            for path in generated:
                print_step(f"Removing generated flow {path}")
                with suppress(FileNotFoundError):
                    path.unlink()
    if getattr(parsed, "stale_flows", False):
        matches = _find_generated_prepared_flows(
            project_root, config, min_age_days=max(0, int(getattr(parsed, "days", 0)))
        )
        if not matches:
            print_step("No generated prepared-flow files found.")
            return
        for match in matches:
            print(_relativize(match, project_root))
        if not getattr(parsed, "confirm", False):
            print()
            print_step(
                f"Dry run: {len(matches)} generated flow file(s) listed. Re-run with --confirm to delete them."
            )
            return
        for match in matches:
            match.unlink(missing_ok=True)
        print_step(f"Removed {len(matches)} generated flow file(s).")
        return

    roots = [project_root / config.artifacts.clean_roots[0]]
    if parsed.include_repo_artifacts:
        roots = [project_root / root for root in config.artifacts.clean_roots]
    for root in roots:
        if root.exists():
            print_step(f"Removing {root}")
            shutil.rmtree(root)


def _cloud_request_json(
    project_id: str, upload_id: str, api_key: str
) -> dict[str, Any]:
    url = f"https://api.copilot.mobile.dev/v2/project/{project_id}/upload/{upload_id}"
    request = Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except URLError as exc:
        raise MaestroAndroidError(
            "ENVIRONMENT_ERROR",
            f"Failed to query Maestro Cloud upload {upload_id}: {exc}",
        ) from exc
    return json.loads(payload)


def _first_cloud_flow(payload: dict[str, Any]) -> dict[str, Any]:
    flows = payload.get("flows") or []
    if flows and isinstance(flows[0], dict):
        return flows[0]
    return {}


def _print_cloud_status_row(
    label: str, upload_id: str, payload: dict[str, Any]
) -> None:
    first_flow = _first_cloud_flow(payload)
    print(
        f"{label:<18} {upload_id:<28} {str(payload.get('status', '')):<10} "
        f"{str(first_flow.get('status', '')):<10} "
        f"{str(payload.get('completed', '')):<8} {str(payload.get('wasAppLaunched', '')):<12} "
        f"{' | '.join(first_flow.get('errors') or [])}"
    )


def _run_cloud_status_command(
    *,
    project_id: str,
    api_key: str,
    uploads: Sequence[str],
    watch: bool,
    interval: int,
) -> int:
    def poll_once() -> bool:
        print(
            f"{'label':<18} {'upload_id':<28} {'upload':<10} {'flow':<10} {'done':<8} {'launched':<12} errors"
        )
        all_done = True
        for entry in uploads:
            if ":" not in entry:
                raise MaestroAndroidError(
                    "CONFIG_ERROR", f"Upload entry must be label:upload-id, got {entry}"
                )
            label, upload_id = entry.split(":", 1)
            payload = _cloud_request_json(project_id, upload_id, api_key)
            _print_cloud_status_row(label, upload_id, payload)
            if not payload.get("completed", False):
                all_done = False
        return all_done

    if not watch:
        poll_once()
        return 0

    while True:
        print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        if poll_once():
            return 0
        print()
        _time.sleep(interval)


def _run_cloud(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> int:
    if parsed.cloud_command == "run":
        extra_args = list(parsed.args)
        if extra_args and extra_args[0] == "--":
            extra_args = extra_args[1:]
        run_subprocess(["maestro", "cloud", *extra_args], cwd=project_root)
        return 0
    if parsed.cloud_command == "probe":
        return _run_cloud_probe(parsed, config, project_root)
    if parsed.cloud_command == "smoke":
        return _run_cloud_smoke(parsed, config, project_root)
    if parsed.cloud_command == "benchmark":
        return _run_cloud_benchmark(parsed, config, project_root)
    if parsed.cloud_command == "flow":
        return _run_cloud_flow(parsed, config, project_root)
    if parsed.cloud_command == "status":
        _load_env_file(project_root)
        api_key = _cloud_api_key(config)
        project_id = _cloud_project_id(parsed, config, project_root)
        return _run_cloud_status_command(
            project_id=project_id,
            api_key=api_key,
            uploads=parsed.uploads,
            watch=parsed.watch,
            interval=parsed.interval,
        )
    raise MaestroAndroidError(
        "CONFIG_ERROR", f"Unknown cloud command '{parsed.cloud_command}'"
    )


def _run_doctor(parsed: argparse.Namespace, config: MaestroAndroidConfig) -> int:
    project_root = _project_root(parsed)
    config_path = parsed.config or (project_root / ".maestro-android.yaml")
    checks: list[dict[str, Any]] = []
    for command_name in config.doctor.required_commands:
        checks.append(
            {
                "name": command_name,
                "kind": "required-command",
                "ok": shutil.which(command_name) is not None,
            }
        )
    for command_name in config.doctor.optional_commands:
        checks.append(
            {
                "name": command_name,
                "kind": "optional-command",
                "ok": shutil.which(command_name) is not None,
            }
        )
    if config.doctor.require_gradlew:
        checks.append(
            {
                "name": "gradlew",
                "kind": "project-file",
                "ok": (project_root / "gradlew").exists(),
            }
        )
    checks.append(
        {
            "name": ".maestro-android.yaml",
            "kind": "project-config",
            "ok": (parsed.config is not None and parsed.config.exists())
            or (project_root / ".maestro-android.yaml").exists(),
        }
    )
    checks.append(
        {
            "name": config.cloud.api_key_env,
            "kind": "optional-cloud-env",
            "ok": bool(os.environ.get(config.cloud.api_key_env)),
        }
    )
    checks.append(
        {
            "name": config.cloud.project_id_env,
            "kind": "optional-cloud-env",
            "ok": bool(os.environ.get(config.cloud.project_id_env)),
        }
    )
    devices: list[dict[str, str]] = []
    device_summary = {
        "total": 0,
        "online": 0,
        "offline": 0,
        "unauthorized": 0,
        "emulator": 0,
        "network": 0,
        "usb": 0,
    }
    apk_path = _resolve_apk(project_root, config)
    flow_paths = _discover_all_flow_paths(
        project_root,
        config,
        include_generated=parsed.include_generated_flows,
    )
    generated_flow_paths = _discover_generated_flow_paths(project_root, config)
    adb_ok = shutil.which("adb") is not None
    if adb_ok:
        completed = run_subprocess(
            ["adb", "devices"], capture_output=True, check=False, cwd=project_root
        )
        checks.append(
            {"name": "adb-devices", "kind": "runtime", "ok": completed.returncode == 0}
        )
        if completed.returncode == 0:
            devices = _list_devices()
            device_summary = _summarize_devices(devices)
    duplicate_groups = _duplicate_transport_groups(devices)
    cloud_ready = all(
        bool(os.environ.get(env_name))
        for env_name in (config.cloud.api_key_env, config.cloud.project_id_env)
    )
    recommendations = _doctor_recommendations(
        config=config,
        device_summary=device_summary,
        duplicate_groups=duplicate_groups,
        cloud_ready=cloud_ready,
    )
    if parsed.as_json:
        print(
            json.dumps(
                {
                    "project_root": str(project_root),
                    "config_path": str(config_path),
                    "checks": checks,
                    "devices": devices,
                    "device_summary": device_summary,
                    "duplicate_transport_groups": duplicate_groups,
                    "apk": {
                        "glob": config.project.apk_glob,
                        "resolved": str(apk_path) if apk_path else None,
                    },
                    "flows": {
                        "roots": list(config.flows.roots),
                        "discovered": len(flow_paths),
                        "generated": len(generated_flow_paths),
                        "include_generated": parsed.include_generated_flows,
                    },
                    "cloud": {
                        "api_key_env": config.cloud.api_key_env,
                        "api_key_present": bool(os.environ.get(config.cloud.api_key_env)),
                        "project_id_env": config.cloud.project_id_env,
                        "project_id_present": bool(os.environ.get(config.cloud.project_id_env)),
                        "device_locale": config.cloud.device_locale,
                        "smoke_api_levels": list(config.cloud.smoke_api_levels),
                        "smoke_flows_root": config.cloud.smoke_flows_root,
                        "smoke_tags": list(config.cloud.smoke_tags),
                    },
                    "matrix": {
                        "local_device": device_summary["online"] > 0,
                        "emulator": device_summary["emulator"] > 0,
                        "cloud_ready": cloud_ready,
                    },
                    "recommendations": recommendations,
                },
                indent=2,
            )
        )
    else:
        print(f"Project root: {project_root}")
        print(f"Config path:  {config_path}")
        for check in checks:
            status = "ok" if check["ok"] else "missing"
            print(f"{status:7} {check['kind']:17} {check['name']}")
        print()
        print("Test matrix:")
        print(
            "  local devices: "
            f"{device_summary['online']} online"
            f" (usb={device_summary['usb']}, network={device_summary['network']}, emulator={device_summary['emulator']})"
        )
        if device_summary["offline"] or device_summary["unauthorized"]:
            print(
                "  adb issues: "
                f"offline={device_summary['offline']} unauthorized={device_summary['unauthorized']}"
            )
        print(f"  apk resolved:   {apk_path or 'missing'}")
        print(f"  cloud ready: {'yes' if cloud_ready else 'no'}")
        if duplicate_groups:
            print("  duplicates:     yes")
        else:
            print("  duplicates:     no")
        print()
        print("Flow hygiene:")
        print(f"  roots:          {', '.join(config.flows.roots)}")
        print(f"  discovered:     {len(flow_paths)}")
        print(f"  generated:      {len(generated_flow_paths)}")
        if generated_flow_paths and not parsed.include_generated_flows:
            print("  hint:           run `maestro-android clean --stale-flows` or `--generated-flows`")
        print()
        print("Hosted defaults:")
        print(f"  locale:         {config.cloud.device_locale}")
        print(
            "  smoke matrix:   "
            f"api={','.join(str(v) for v in config.cloud.smoke_api_levels)} "
            f"tags={','.join(config.cloud.smoke_tags)} "
            f"root={config.cloud.smoke_flows_root}"
        )
        if recommendations:
            print()
            print("Next commands:")
            for recommendation in recommendations:
                print(f"  - {recommendation}")
        if any(
            check["kind"] == "project-config" and not check["ok"] for check in checks
        ):
            print("hint    config            run `maestro-android init`")
    return (
        0
        if all(
            check["ok"] or check["kind"] in {"optional-command", "optional-cloud-env"}
            for check in checks
        )
        else 1
    )


def _run_devices(parsed: argparse.Namespace) -> None:
    devices = _list_devices()
    if not devices:
        if getattr(parsed, "as_json", False):
            print(json.dumps({"devices": [], "duplicate_transport_groups": []}, indent=2))
        else:
            print("No adb devices detected.")
        return
    duplicate_groups = _duplicate_transport_groups(devices)
    if getattr(parsed, "as_json", False):
        print(
            json.dumps(
                {
                    "devices": devices,
                    "duplicate_transport_groups": duplicate_groups,
                },
                indent=2,
            )
        )
        return
    for device in devices:
        print(_device_display_name(device))
    if duplicate_groups:
        print()
        print_step(
            "Duplicate transports detected for at least one device. Pin the target serial with `--device`."
        )


def _run_init(parsed: argparse.Namespace, project_root: Path) -> int:
    config_path = parsed.path or parsed.config or (project_root / ".maestro-android.yaml")
    if not config_path.is_absolute():
        config_path = (project_root / config_path).resolve()
    if config_path.exists() and not parsed.force:
        raise MaestroAndroidError(
            "CONFIG_ERROR",
            f"Config already exists at {config_path}. Use --force to overwrite.",
        )
    if yaml is None:
        raise MaestroAndroidError(
            "ENVIRONMENT_ERROR",
            "PyYAML is required. Run: python3 -m pip install PyYAML",
        )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "# Generated by maestro-android init\n# Edit values for your project.\n\n"
    payload += yaml.safe_dump(DEFAULT_CONFIG, sort_keys=False)
    config_path.write_text(payload, encoding="utf-8")
    print_step(f"Wrote starter config: {config_path}")
    return 0


def _wait_for_new_emulator(existing_serials: set[str], timeout_seconds: int) -> str:
    deadline = _time.time() + float(timeout_seconds)
    fallback_serial = ""
    while _time.time() < deadline:
        devices = _list_devices()
        for device in devices:
            if device.get("transport") != "emulator":
                continue
            serial = str(device.get("serial", "")).strip()
            if device.get("state") == "device":
                fallback_serial = serial
            if serial and serial not in existing_serials and device.get("state") == "device":
                return serial
        _time.sleep(2.0)
    if fallback_serial:
        return fallback_serial
    raise MaestroAndroidError(
        "DEVICE_ERROR",
        f"Timed out waiting for emulator transport after {timeout_seconds} seconds.",
    )


def _wait_for_boot_completed(serial: str, timeout_seconds: int) -> None:
    deadline = _time.time() + float(timeout_seconds)
    while _time.time() < deadline:
        completed = run_subprocess(
            ["adb", "-s", serial, "shell", "getprop", "sys.boot_completed"],
            capture_output=True,
            check=False,
        )
        if (completed.stdout or "").strip() == "1":
            pm_ready = run_subprocess(
                ["adb", "-s", serial, "shell", "pm", "path", "android"],
                capture_output=True,
                check=False,
            )
            if pm_ready.returncode == 0:
                return
        _time.sleep(2.0)
    raise MaestroAndroidError(
        "DEVICE_ERROR",
        f"Timed out waiting for emulator {serial} to finish booting after {timeout_seconds} seconds.",
    )


def _run_start_device(parsed: argparse.Namespace) -> None:
    emulator_bin = shutil.which("emulator")
    if emulator_bin is None:
        raise MaestroAndroidError(
            "ENVIRONMENT_ERROR", "Android emulator binary not found in PATH."
        )
    avds = run_subprocess(
        [emulator_bin, "-list-avds"], capture_output=True, check=False
    )
    names = [line.strip() for line in (avds.stdout or "").splitlines() if line.strip()]
    if not names:
        raise MaestroAndroidError("DEVICE_ERROR", "No AVDs are available.")
    avd_name = parsed.name or (names[0] if len(names) == 1 else "")
    if not avd_name:
        raise MaestroAndroidError(
            "CONFIG_ERROR", "Multiple AVDs detected; pass an AVD name."
        )
    print_step(f"Starting AVD {avd_name}")
    existing_emulators = {
        str(device.get("serial", "")).strip()
        for device in _list_devices()
        if device.get("transport") == "emulator"
    }
    subprocess.Popen([emulator_bin, "-avd", avd_name], cwd=str(_project_root(parsed)))
    timeout = parsed.boot_timeout_seconds
    run_subprocess(["adb", "wait-for-device"], timeout_seconds=timeout)
    serial = _wait_for_new_emulator(existing_emulators, timeout)
    _wait_for_boot_completed(serial, timeout)
    print_step(f"Emulator ready: {serial}")


def _run_suggest(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> int:
    completed = run_subprocess(
        ["git", "diff", "--name-only", parsed.diff],
        capture_output=True, check=False, cwd=project_root,
    )
    if completed.returncode != 0:
        completed = run_subprocess(
            ["git", "diff", "--name-only", "--cached"],
            capture_output=True, check=False, cwd=project_root,
        )
    changed = [f.strip() for f in (completed.stdout or "").splitlines() if f.strip()]
    if not changed:
        print_step("No changed files detected.")
        return 0

    has_kotlin = any(f.endswith(".kt") for f in changed)
    has_native = any(f.endswith((".cpp", ".c", ".h", ".hpp")) or "/cpp/" in f or "/jni/" in f for f in changed)
    has_compose_ui = any("ui/" in f and f.endswith(".kt") for f in changed)
    has_test_flows = any(f.endswith((".yaml", ".yml")) and ("maestro" in f or "tests/" in f) for f in changed)
    has_strings = any("strings.xml" in f for f in changed)
    has_gradle = any(f.endswith((".gradle", ".gradle.kts")) or f == "gradle.properties" for f in changed)

    suggestions: list[str] = []

    if has_kotlin or has_gradle:
        suggestions.append("./gradlew testDebugUnitTest              # compile + unit tests")
    if has_compose_ui or has_strings:
        suggestions.append("maestro-android lane smoke               # UI smoke after composable/string changes")
    if has_native:
        suggestions.append("maestro-android lane smoke --device <serial>  # pinned runtime/native validation on one target")
    if has_test_flows:
        suggestions.append("maestro-android lint                     # validate flow health after flow edits")
        suggestions.append("maestro-android audit-selectors          # check selector coverage")
        changed_cloud_flows = [
            f for f in changed if f.endswith((".yaml", ".yml")) and "maestro-cloud" in f
        ]
        if len(changed_cloud_flows) == 1:
            suggestions.append(
                f"maestro-android cloud probe --flow {changed_cloud_flows[0]}  # narrow hosted rerun"
            )
    if has_kotlin and not has_native:
        suggestions.append("maestro-android lane smoke --device <serial>  # one explicit device confirmation")

    if not suggestions:
        suggestions.append("maestro-android doctor                   # default environment and matrix check")

    print_step(f"Changed files: {len(changed)}")
    for f in changed[:15]:
        print(f"  {f}")
    if len(changed) > 15:
        print(f"  ... and {len(changed) - 15} more")
    print()
    print_step("Suggested commands:")
    for s in suggestions:
        print(f"  {s}")
    return 0


def _app_storage_dir(app_id: str, storage: str) -> str:
    if storage == "media":
        return f"/sdcard/Android/media/{app_id}"
    return f"/sdcard/Android/data/{app_id}/files"


def _app_external_data_dir(app_id: str) -> str:
    return _app_storage_dir(app_id, "data")


def _resolve_app_pid(serial: str, app_id: str) -> str | None:
    completed = run_subprocess(
        ["adb", "-s", serial, "shell", "pidof", "-s", app_id],
        capture_output=True, check=False,
    )
    pid = (completed.stdout or "").strip().replace("\r", "")
    return pid if pid and pid.isdigit() else None


def _run_device_files(
    parsed: argparse.Namespace, config: MaestroAndroidConfig
) -> int:
    serial = _resolve_serial(parsed.device)
    base = _app_storage_dir(config.project.app_id, parsed.storage)
    target = f"{base}/{parsed.path}".rstrip("/") if parsed.path else base
    flags = "-la" if parsed.show_all else "-l"
    completed = run_subprocess(
        ["adb", "-s", serial, "shell", "ls", flags, target],
        capture_output=True, check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        if "No such file" in stderr:
            print_step(f"Path does not exist on device: {target}")
            return 1
        print_step(f"Error listing files: {stderr}")
        return 1
    print(completed.stdout or "")
    return 0


def _run_device_push(
    parsed: argparse.Namespace, config: MaestroAndroidConfig
) -> int:
    serial = _resolve_serial(parsed.device)
    local = Path(parsed.local_path)
    if not local.exists():
        raise MaestroAndroidError("CONFIG_ERROR", f"Local file does not exist: {local}")
    base = _app_storage_dir(config.project.app_id, parsed.storage)
    if parsed.dest:
        remote = f"{base}/{parsed.dest.rstrip('/')}/{local.name}" if not parsed.dest.endswith(local.name) else f"{base}/{parsed.dest}"
    else:
        remote = f"{base}/{local.name}"
    run_subprocess(
        ["adb", "-s", serial, "push", str(local), remote],
        capture_output=False,
    )
    print_step(f"Pushed {local.name} -> {remote}")
    return 0


def _run_device_logcat(
    parsed: argparse.Namespace, config: MaestroAndroidConfig
) -> int:
    serial = _resolve_serial(parsed.device)
    pid = _resolve_app_pid(serial, config.project.app_id)
    if pid is None:
        raise MaestroAndroidError(
            "DEVICE_ERROR",
            f"App process not running: {config.project.app_id}. Launch the app first.",
        )
    filter_regex = parsed.logcat_filter
    filter_pattern = re.compile(filter_regex) if filter_regex else None

    if parsed.follow:
        print_step(
            f"Streaming logcat for {config.project.app_id} (pid {pid})"
            + (f" filter=/{filter_regex}/" if filter_regex else "")
        )
        command = ["adb", "-s", serial, "logcat", f"--pid={pid}"]
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, errors="replace",
        )
        save_file = parsed.save.open("w", encoding="utf-8") if parsed.save else None
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if filter_pattern and not filter_pattern.search(line):
                    continue
                print(line, end="")
                if save_file:
                    save_file.write(line)
        except KeyboardInterrupt:
            pass
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            if save_file:
                save_file.close()
                print_step(f"Saved logcat to {parsed.save}")
        return 0

    completed = run_subprocess(
        ["adb", "-s", serial, "logcat", "-d", f"--pid={pid}"],
        capture_output=True, check=False,
    )
    lines = (completed.stdout or "").splitlines()
    if filter_pattern:
        lines = [line for line in lines if filter_pattern.search(line)]
    if parsed.lines > 0:
        lines = lines[-parsed.lines:]
    output = "\n".join(lines)
    if parsed.save:
        parsed.save.write_text(output + "\n", encoding="utf-8")
        print_step(f"Saved {len(lines)} lines to {parsed.save}")
    else:
        print(output)
    print_step(f"{len(lines)} lines" + (f" matching /{filter_regex}/" if filter_regex else ""))
    return 0


def _run_device_ui(
    parsed: argparse.Namespace, config: MaestroAndroidConfig
) -> int:
    serial = _resolve_serial(parsed.device)
    completed = run_subprocess(
        ["adb", "-s", serial, "exec-out", "uiautomator", "dump", "/dev/tty"],
        capture_output=True, check=False,
    )
    raw = (completed.stdout or "").replace("UI hierchary dumped to: /dev/tty", "").strip()
    if not raw:
        print_step("No UI hierarchy returned. Is the screen on?")
        return 1

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        print(raw)
        return 0

    nodes: list[dict[str, str]] = []
    for elem in root.iter("node"):
        resource_id = elem.get("resource-id", "")
        text = elem.get("text", "")
        content_desc = elem.get("content-desc", "")
        bounds = elem.get("bounds", "")
        class_name = elem.get("class", "")
        if not resource_id and not text and not content_desc:
            continue
        nodes.append({
            "resource_id": resource_id,
            "text": text[:60],
            "content_desc": content_desc[:60],
            "bounds": bounds,
            "class": class_name.rsplit(".", 1)[-1] if "." in class_name else class_name,
        })

    print(f"  {'resource-id':<45} {'text':<30} {'class':<20} {'bounds'}")
    print(f"  {'-' * 45} {'-' * 30} {'-' * 20} {'-' * 25}")
    for node in nodes:
        rid = node["resource_id"].split("/")[-1] if "/" in node["resource_id"] else node["resource_id"]
        label = node["text"] or node["content_desc"] or ""
        print(f"  {rid:<45} {label:<30} {node['class']:<20} {node['bounds']}")

    print()
    ids = [n["resource_id"].split("/")[-1] for n in nodes if n["resource_id"]]
    print_step(f"UI dump: {len(nodes)} elements, {len(ids)} with resource-id")
    return 0


def _run_device_foreground(
    parsed: argparse.Namespace, config: MaestroAndroidConfig
) -> int:
    serial = _resolve_serial(parsed.device)
    snapshot = _collect_foreground_snapshot(serial, config.project.app_id)
    if getattr(parsed, "foreground_as_json", False):
        print(json.dumps(snapshot, indent=2))
        return 0

    print(f"  Device:         {serial}")
    print(f"  App:            {config.project.app_id}")
    print(f"  Classification: {snapshot['classification']}")
    print(f"  Package:        {snapshot['package'] or 'unknown'}")
    print(f"  Activity:       {snapshot['activity'] or 'unknown'}")
    if snapshot["component"]:
        print(f"  Component:      {snapshot['component']}")

    if snapshot["classification"] == "system_permission_dialog":
        print()
        print_step("A system permission dialog is currently on top of the app.")
    elif snapshot["classification"] == "play_store":
        print()
        print_step("The Play Store currently owns the foreground.")
    elif snapshot["classification"] == "external":
        print()
        print_step("Another package currently owns the foreground.")
    elif snapshot["classification"] == "app":
        print()
        print_step("The app currently owns the foreground.")
    else:
        print()
        print_step("Unable to determine the current foreground activity.")
    return 0


def _run_device_info(
    parsed: argparse.Namespace, config: MaestroAndroidConfig
) -> int:
    serial = _resolve_serial(parsed.device)
    app_id = config.project.app_id
    pid = _resolve_app_pid(serial, app_id)

    print(f"  App:     {app_id}")
    print(f"  Device:  {serial}")
    if pid is None:
        print(f"  Process: NOT RUNNING")
        return 1

    print(f"  PID:     {pid}")

    meminfo = run_subprocess(
        ["adb", "-s", serial, "shell", "dumpsys", "meminfo", pid],
        capture_output=True, check=False,
    )
    for line in (meminfo.stdout or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("TOTAL") and "TOTAL:" not in stripped:
            parts = stripped.split()
            if len(parts) >= 2:
                try:
                    kb = int(parts[1].replace(",", ""))
                    print(f"  Memory:  {kb // 1024} MB ({kb} KB)")
                except ValueError:
                    pass
            break

    cpu_out = run_subprocess(
        ["adb", "-s", serial, "shell", "top", "-b", "-n", "1", "-p", pid],
        capture_output=True, check=False, timeout_seconds=10,
    )
    cpu_header_idx: int | None = None
    for line in (cpu_out.stdout or "").splitlines():
        stripped = line.strip()
        if "PID" in stripped and "CPU" in stripped:
            headers = stripped.split()
            for i, h in enumerate(headers):
                if h.startswith("CPU") or h == "%CPU":
                    cpu_header_idx = i
                    break
        elif pid in stripped and cpu_header_idx is not None:
            parts = stripped.split()
            if cpu_header_idx < len(parts):
                print(f"  CPU:     {parts[cpu_header_idx]}%")
            break

    print()
    print_step("App process is running.")
    return 0


def _classify_probe_result(
    *,
    transport_ok: bool,
    maestro_result: dict[str, Any] | None,
    artifact_root: Path | None,
) -> str:
    if not transport_ok:
        return "device_transport_failure"
    if maestro_result is None:
        return "adb_ready"
    if maestro_result.get("status") == "passed":
        return "ready"
    if artifact_root is None:
        return "harness_bootstrap_failure"
    stderr_rel = str(maestro_result.get("stderr") or "")
    logcat_rel = str(maestro_result.get("logcat") or "")
    texts_to_scan: list[str] = []
    for rel in (stderr_rel, logcat_rel):
        if not rel:
            continue
        path = artifact_root / rel
        if path.exists():
            texts_to_scan.append(path.read_text(encoding="utf-8", errors="replace"))
    combined = "\n".join(texts_to_scan)
    if re.search(r"Unable to launch app|app.*not installed", combined, re.IGNORECASE):
        return "product_or_app_failure"
    if re.search(r"UNAVAILABLE|Connection refused|Connection reset|timed out", combined, re.IGNORECASE):
        return "harness_bootstrap_failure"
    return "unknown_failure"


def _run_device_probe(parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path) -> int:
    serial = _resolve_serial(parsed.device)
    transport = run_subprocess(
        ["adb", "-s", serial, "shell", "echo", "maestro-android-ok"],
        capture_output=True,
        check=False,
        timeout_seconds=15,
    )
    transport_ok = transport.returncode == 0 and "maestro-android-ok" in (transport.stdout or "")
    foreground = _collect_foreground_snapshot(serial, config.project.app_id)
    artifact_root: Path | None = None
    maestro_result: dict[str, Any] | None = None
    if not parsed.adb_only:
        artifact_root = _normalize_artifact_root(
            project_root / ".maestro-android" / "lifecycle", serial, "bootstrap-probe"
        )
        artifact_root.mkdir(parents=True, exist_ok=True)
        probe_flow = artifact_root / "bootstrap-probe.yaml"
        probe_flow.write_text(
            "\n".join(
                [
                    f"appId: {config.project.app_id}",
                    "---",
                    "- launchApp",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        maestro_result = _run_maestro_flow(
            project_root=project_root,
            serial=serial,
            flow=probe_flow,
            app_id=config.project.app_id,
            clear_state=False,
            output_format="junit",
            artifact_root=artifact_root,
            adb_timeout_sec=30,
            maestro_timeout_sec=180,
        )
        if maestro_result.get("status") != "passed":
            _diagnose_failure(maestro_result, artifact_root)
    classification = _classify_probe_result(
        transport_ok=transport_ok,
        maestro_result=maestro_result,
        artifact_root=artifact_root,
    )
    payload = {
        "serial": serial,
        "transport_ok": transport_ok,
        "transport_kind": _device_transport_kind(serial),
        "foreground": foreground,
        "classification": classification,
        "artifact_root": str(artifact_root) if artifact_root is not None else None,
        "maestro_probe": maestro_result,
    }
    if getattr(parsed, "probe_as_json", False):
        print(json.dumps(payload, indent=2))
    else:
        print(f"  Device:         {serial}")
        print(f"  Transport:      {payload['transport_kind']}")
        print(f"  ADB transport:  {'ok' if transport_ok else 'failed'}")
        print(f"  Foreground:     {foreground.get('component') or 'unknown'}")
        print(f"  Classification: {classification}")
        if artifact_root is not None:
            print(f"  Artifacts:      {artifact_root}")
        print()
        if classification == "ready":
            print_step("Pinned device bootstrap probe passed.")
        elif classification == "adb_ready":
            print_step("ADB transport is healthy. Maestro bootstrap was skipped.")
        elif classification == "harness_bootstrap_failure":
            print_step("ADB is up, but the Maestro bootstrap path failed. Inspect the artifact bundle before rerunning.")
        elif classification == "product_or_app_failure":
            print_step("Maestro attached, but the app could not launch cleanly. Fix the app/install state, not the transport.")
        else:
            print_step("Probe failed. Inspect the artifact bundle and foreground snapshot before retrying.")
    return 0 if classification in {"ready", "adb_ready"} else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    parsed = parser.parse_args(list(argv) if argv is not None else None)

    try:
        project_root = _project_root(parsed)
        if parsed.command == "init":
            return _run_init(parsed, project_root)
        config = load_config(repo_root=project_root, explicit_path=parsed.config)
        if parsed.command == "doctor":
            return _run_doctor(parsed, config)
        if parsed.command == "devices":
            _run_devices(parsed)
            return 0
        if parsed.command == "start-device":
            _run_start_device(parsed)
            return 0
        if parsed.command == "test":
            _run_test(parsed, config, project_root)
            return 0
        if parsed.command == "lane":
            _run_lane(parsed, config, project_root)
            return 0
        if parsed.command == "scoped":
            return _run_scoped(parsed, config, project_root)
        if parsed.command == "report":
            _run_report(parsed, config, project_root)
            return 0
        if parsed.command == "trace":
            _run_trace(parsed, config, project_root)
            return 0
        if parsed.command == "merge-reports":
            _run_merge_reports(parsed)
            return 0
        if parsed.command == "clean":
            _run_clean(parsed, config)
            return 0
        if parsed.command == "lint":
            return _run_lint(parsed, config, project_root)
        if parsed.command == "audit-selectors":
            return _run_audit_selectors(parsed, config, project_root)
        if parsed.command == "audit-testtags":
            return _run_audit_testtags(parsed, config, project_root)
        if parsed.command == "cloud":
            return _run_cloud(parsed, config, project_root)
        if parsed.command == "suggest":
            return _run_suggest(parsed, config, project_root)
        if parsed.command == "device":
            if parsed.device_command == "files":
                return _run_device_files(parsed, config)
            if parsed.device_command == "push":
                return _run_device_push(parsed, config)
            if parsed.device_command == "logcat":
                return _run_device_logcat(parsed, config)
            if parsed.device_command == "ui":
                return _run_device_ui(parsed, config)
            if parsed.device_command == "foreground":
                return _run_device_foreground(parsed, config)
            if parsed.device_command == "info":
                return _run_device_info(parsed, config)
            if parsed.device_command == "probe":
                return _run_device_probe(parsed, config, project_root)
            raise MaestroAndroidError("CONFIG_ERROR", f"Unknown device command '{parsed.device_command}'")
        raise MaestroAndroidError("CONFIG_ERROR", f"Unknown command '{parsed.command}'")
    except MaestroAndroidError as exc:
        print(f"{exc.code}: {exc.message}")
        return 1
