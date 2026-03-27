from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from pathlib import Path

from maestro_android.common import MaestroAndroidError
from maestro_android.config import MaestroAndroidConfig


@dataclass(frozen=True)
class ReportBundle:
    kind: str
    artifact_root: Path
    report_files: tuple[Path, ...]


def _latest_path(patterns: list[str], repo_root: Path) -> Path | None:
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(path for path in repo_root.glob(pattern) if path.exists())
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, str(path)))[-1]


def _bundle_from_root(kind: str, artifact_root: Path, preferred_files: list[str]) -> ReportBundle:
    report_files = tuple(path for path in (artifact_root / name for name in preferred_files) if path.exists())
    return ReportBundle(kind=kind, artifact_root=artifact_root, report_files=report_files)


def find_bundle(kind: str, *, config: MaestroAndroidConfig, repo_root: Path) -> ReportBundle:
    normalized = kind.strip().lower()
    if normalized == "latest":
        bundles = []
        for candidate in ("journey", "screenshot-pack", "smoke", "raw", "lifecycle"):
            try:
                bundles.append(find_bundle(candidate, config=config, repo_root=repo_root))
            except MaestroAndroidError:
                continue
        if not bundles:
            raise MaestroAndroidError("CONFIG_ERROR", "No report artifacts were found.")
        return sorted(bundles, key=lambda bundle: (bundle.artifact_root.stat().st_mtime, str(bundle.artifact_root)))[-1]

    scratch_root = Path(config.artifacts.scratch_root)
    report_roots = [Path(root) for root in config.artifacts.report_roots]

    if normalized in {"journey", "screenshot-pack", "smoke"}:
        labels_by_kind = {
            "journey": ["journey"],
            "screenshot-pack": ["screenshot-pack"],
            "smoke": ["smoke", "maestro", "raw"],
        }
        patterns: list[str] = []
        for root in report_roots:
            for label in labels_by_kind[normalized]:
                patterns.append(f"{root.as_posix()}/*/*/{label}/*")
        if normalized != "smoke":
            patterns.append(f"{scratch_root.as_posix()}/*/*/{normalized}/*")
        artifact_root = _latest_path(patterns, repo_root)
        if artifact_root is None:
            raise MaestroAndroidError("CONFIG_ERROR", f"No {normalized} report artifacts were found.")
        files_by_kind = {
            "journey": ["journey-summary.md", "journey-report.json", "logcat.txt", "run-manifest.json", "trace.json"],
            "screenshot-pack": ["inventory-report.md", "inventory-report.json", "logcat.txt", "run-manifest.json"],
            "smoke": ["run-manifest.json", "logcat.txt", "trace.json"],
        }
        return _bundle_from_root(normalized, artifact_root, files_by_kind[normalized])

    if normalized == "raw":
        artifact_root = _latest_path([f"{scratch_root.as_posix()}/*/*/raw/*"], repo_root)
        if artifact_root is None:
            raise MaestroAndroidError("CONFIG_ERROR", "No raw Maestro test artifacts were found.")
        return _bundle_from_root("raw", artifact_root, ["run-manifest.json", "trace.json"])

    if normalized == "lifecycle":
        artifact_root = repo_root / config.artifacts.lifecycle_root
        if not artifact_root.exists():
            raise MaestroAndroidError("CONFIG_ERROR", "No lifecycle artifacts were found.")
        files = ["retry-summary.txt"]
        attempts = sorted(path for path in artifact_root.glob("attempt-*") if path.is_dir())
        if attempts:
            latest_attempt = attempts[-1]
            files.extend(
                [
                    f"{latest_attempt.name}/junit.xml",
                    f"{latest_attempt.name}/logcat.txt",
                    f"{latest_attempt.name}/maestro-stderr.log",
                ]
            )
        return _bundle_from_root("lifecycle", artifact_root, files)

    raise MaestroAndroidError("CONFIG_ERROR", f"Unknown report kind '{kind}'")


def print_bundle(bundle: ReportBundle) -> None:
    print(f"{bundle.kind} report artifacts:")
    print(f"  Artifact root: {bundle.artifact_root}")
    if not bundle.report_files:
        print("  (no indexed report files found)")
        return
    for path in bundle.report_files:
        print(f"  {path}")


def open_bundle(bundle: ReportBundle) -> None:
    for path in bundle.report_files:
        webbrowser.open(path.resolve().as_uri())
