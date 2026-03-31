from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time as _time
import xml.etree.ElementTree as ET
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
  
For full docs, see: https://github.com/Mohamad-Kamar/maestro-android""",
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

    subparsers.add_parser(
        "devices", help="list adb devices", epilog="Example: maestro-android devices"
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
    lane.add_argument("name", help="lane name")
    lane.add_argument("args", nargs=argparse.REMAINDER, help="extra lane args")

    scoped = subparsers.add_parser(
        "scoped",
        help="run the scoped repro loop",
        epilog="Examples:\n  maestro-android scoped --flow tmp/repro.yaml\n  maestro-android scoped --flow tmp/repro.yaml --pattern 'NullPointerException'\n  maestro-android scoped --flow tmp/repro.yaml --no-build",
    )
    scoped.add_argument("--flow", required=True, help="tmp flow path")
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
        help="fully qualified test class for instrumented/unit runs",
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
        epilog="Examples:\n  maestro-android clean\n  maestro-android clean --include-repo-artifacts",
    )
    clean.add_argument("--include-repo-artifacts", action="store_true")

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

    cloud_status = cloud_subparsers.add_parser(
        "status", help="poll Maestro Cloud upload status"
    )
    cloud_status.add_argument(
        "--project-id", default="", help="override Maestro Cloud project id"
    )
    cloud_status.add_argument("--watch", action="store_true")
    cloud_status.add_argument("--interval", type=int, default=60)
    cloud_status.add_argument("uploads", nargs="+", help="label:upload-id entries")

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
    devices: list[dict[str, str]] = []
    for line in (completed.stdout or "").splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        serial = parts[0]
        details = " ".join(parts[2:])
        model = ""
        for token in parts[2:]:
            if token.startswith("model:"):
                model = token.split(":", 1)[1]
                break
        devices.append({"serial": serial, "details": details, "model": model})
    if not devices:
        raise MaestroAndroidError("DEVICE_ERROR", "No connected adb device detected.")
    if len(devices) > 1:
        models = [d["model"] for d in devices if d["model"]]
        unique_models = set(models)
        if len(unique_models) == 1 and len(models) == len(devices):
            print_step(
                f"Warning: {len(devices)} transports detected for the same device "
                f"(model={models[0]}). Using first: {devices[0]['serial']}"
            )
            return devices[0]["serial"]
        raise MaestroAndroidError(
            "DEVICE_ERROR",
            f"Multiple adb devices detected ({len(devices)}); pass --device. "
            + ", ".join(f"{d['serial']} ({d['details']})" for d in devices),
        )
    return devices[0]["serial"]


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
    _cloud_api_key(config)
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
        _write_json(
            run_dir / "summary.json",
            {
                "api_level": api_level,
                "project_id": project_id,
                "device_locale": device_locale,
                "flows": str(flows_root),
                "returncode": completed.returncode,
            },
        )
        if completed.returncode != 0:
            exit_code = 1
    print_step(f"Maestro Cloud smoke artifacts: {output_root}")
    return exit_code


def _run_cloud_benchmark(
    parsed: argparse.Namespace, config: MaestroAndroidConfig, project_root: Path
) -> int:
    _load_env_file(project_root)
    _cloud_api_key(config)
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
        _write_json(
            run_dir / "summary.json",
            {
                "api_level": api_level,
                "project_id": project_id,
                "device_locale": device_locale,
                "flow": str(flow),
                "returncode": completed.returncode,
            },
        )
        if completed.returncode != 0:
            exit_code = 1
    print_step(f"Maestro Cloud benchmark artifacts: {output_root}")
    return exit_code


def _project_root(parsed: argparse.Namespace) -> Path:
    return (parsed.project_root or Path.cwd()).resolve()


def _list_devices() -> list[dict[str, str]]:
    completed = run_subprocess(
        ["adb", "devices", "-l"], capture_output=True, check=False
    )
    devices: list[dict[str, str]] = []
    for line in (completed.stdout or "").splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        serial = parts[0]
        state = parts[1] if len(parts) > 1 else "unknown"
        details = " ".join(parts[2:]) if len(parts) > 2 else ""
        devices.append({"serial": serial, "state": state, "details": details})
    return devices


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
    project_root: Path, config: MaestroAndroidConfig
) -> list[Path]:
    paths: list[Path] = []
    for root in config.flows.roots:
        candidate_root = project_root / root
        if not candidate_root.exists():
            continue
        paths.extend(sorted(candidate_root.rglob("*.yaml")))
    return paths


def _discover_all_flow_paths(
    project_root: Path, config: MaestroAndroidConfig
) -> list[Path]:
    return _discover_flow_paths(project_root, config)


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
    completed = run_subprocess(
        command,
        capture_output=True,
        check=False,
        cwd=project_root,
        timeout_seconds=maestro_timeout_sec,
    )
    finished_at = _time.time()
    duration_s = round(finished_at - started_at, 1)

    junit_path = flow_dir / "junit.xml"
    stderr_path = flow_dir / "maestro-stderr.log"
    stdout_path = flow_dir / "maestro-stdout.log"
    if output_format == "junit":
        junit_path.write_text(completed.stdout or "", encoding="utf-8")
    else:
        stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")
    logcat_path = flow_dir / "logcat.txt"
    _capture_logcat(serial, logcat_path, timeout_seconds=adb_timeout_sec)

    return {
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
        run_subprocess(command, cwd=project_root)
        return
    if extra_args:
        raise MaestroAndroidError(
            "CONFIG_ERROR", f"Lane '{parsed.name}' does not accept extra args."
        )
    serial = _resolve_serial("")
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


def _run_scoped_gradle(
    parsed: argparse.Namespace,
    config: MaestroAndroidConfig,
    project_root: Path,
    serial: str,
    artifact_root: Path,
) -> int:
    run_subprocess(
        ["adb", "-s", serial, "logcat", "-c"],
        check=False,
        cwd=project_root,
        timeout_seconds=float(parsed.adb_timeout_sec),
    )
    if parsed.scoped_type == "unit":
        task = "testDebugUnitTest"
    else:
        task = "connectedDebugAndroidTest"
    command = ["./gradlew", task]
    if parsed.test_class:
        command.append(f"--tests={parsed.test_class}")
    env_override = {**os.environ, "ANDROID_SERIAL": serial}
    completed = run_subprocess(
        command, capture_output=True, check=False, cwd=project_root,
        env=env_override,
    )
    stdout_path = artifact_root / "gradle-stdout.log"
    stderr_path = artifact_root / "gradle-stderr.log"
    stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")
    logcat_path = artifact_root / "logcat.txt"
    _capture_logcat(serial, logcat_path, timeout_seconds=float(parsed.adb_timeout_sec))

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
            "logcat": "logcat.txt",
            "stderr": "gradle-stderr.log",
        }],
    }
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
    flow_path = (
        (project_root / parsed.flow).resolve()
        if not Path(parsed.flow).is_absolute()
        else Path(parsed.flow).resolve()
    )
    _validate_scoped_flow(flow_path, config, project_root)
    serial = _resolve_serial(parsed.device)
    artifact_root = _normalize_artifact_root(
        project_root / config.artifacts.scratch_root, serial, "scoped"
    )
    artifact_root.mkdir(parents=True, exist_ok=True)

    if not parsed.no_build:
        build_cmd = list(config.project.build_command)
        for prop in parsed.gradle_properties:
            build_cmd.append(f"-P{prop}")
        run_subprocess(build_cmd, cwd=project_root)
    if not parsed.no_install:
        run_subprocess(config.project.install_command, cwd=project_root)

    if parsed.scoped_type != "maestro":
        return _run_scoped_gradle(parsed, config, project_root, serial, artifact_root)

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
    flow_paths = _discover_all_flow_paths(project_root, config)
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
    if parsed.cloud_command == "smoke":
        return _run_cloud_smoke(parsed, config, project_root)
    if parsed.cloud_command == "benchmark":
        return _run_cloud_benchmark(parsed, config, project_root)
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
    adb_ok = shutil.which("adb") is not None
    if adb_ok:
        completed = run_subprocess(
            ["adb", "devices"], capture_output=True, check=False, cwd=project_root
        )
        checks.append(
            {"name": "adb-devices", "kind": "runtime", "ok": completed.returncode == 0}
        )
    if parsed.as_json:
        print(
            json.dumps({"project_root": str(project_root), "checks": checks}, indent=2)
        )
    else:
        print(f"Project root: {project_root}")
        for check in checks:
            status = "ok" if check["ok"] else "missing"
            print(f"{status:7} {check['kind']:17} {check['name']}")
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


def _run_devices() -> None:
    devices = _list_devices()
    if not devices:
        print("No adb devices detected.")
        return
    for device in devices:
        detail = f" {device['details']}" if device["details"] else ""
        print(f"{device['serial']} [{device['state']}] {detail}".rstrip())


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
    subprocess.Popen([emulator_bin, "-avd", avd_name], cwd=str(_project_root(parsed)))
    timeout = parsed.boot_timeout_seconds
    run_subprocess(["adb", "wait-for-device"], timeout_seconds=timeout)


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
        suggestions.append("./gradlew connectedDebugAndroidTest      # native bridge validation")
    if has_test_flows:
        suggestions.append("maestro-android lint                     # validate flow health after flow edits")
        suggestions.append("maestro-android audit-selectors          # check selector coverage")
    if has_kotlin and not has_native:
        suggestions.append("maestro-android suggest                  # broader pre-merge analysis")

    if not suggestions:
        suggestions.append("./gradlew testDebugUnitTest              # default: compile + unit tests")

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
            _run_devices()
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
        if parsed.command == "audit-testtags":
            return _run_audit_testtags(parsed, config, project_root)
        if parsed.command == "cloud":
            return _run_cloud(parsed, config, project_root)
        if parsed.command == "suggest":
            return _run_suggest(parsed, config, project_root)
        raise MaestroAndroidError("CONFIG_ERROR", f"Unknown command '{parsed.command}'")
    except MaestroAndroidError as exc:
        print(f"{exc.code}: {exc.message}")
        return 1
