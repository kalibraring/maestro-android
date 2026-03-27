from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from maestro_android.common import MaestroAndroidError

try:
    import yaml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - validated at runtime
    yaml = None


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectConfig(_StrictModel):
    apk_glob: str = "app/build/outputs/apk/debug/*.apk"
    build_command: list[str] = Field(default_factory=lambda: ["./gradlew", "assembleDebug"])
    install_command: list[str] = Field(default_factory=lambda: ["./gradlew", "installDebug"])
    app_id: str = "com.example.app"
    test_app_id: str = ""


class DoctorConfig(_StrictModel):
    required_commands: list[str] = Field(default_factory=lambda: ["adb", "maestro"])
    optional_commands: list[str] = Field(default_factory=lambda: ["emulator"])
    require_gradlew: bool = True


class ArtifactsConfig(_StrictModel):
    scratch_root: str = ".maestro-android/runs"
    report_roots: list[str] = Field(default_factory=lambda: [".maestro-android/runs"])
    lifecycle_root: str = ".maestro-android/lifecycle"
    clean_roots: list[str] = Field(default_factory=lambda: [".maestro-android"])


class FlowConfig(_StrictModel):
    roots: list[str] = Field(default_factory=lambda: ["maestro", "tests/maestro"])


class ScopedConfig(_StrictModel):
    require_tmp_flow: bool = True
    require_title_description_comments: bool = True
    crash_signature_regex: str = (
        "FATAL EXCEPTION|Fatal signal|SIGSEGV|Abort message|ANR in|Process .* has died|"
        "Runtime: Error|AssertionError|IllegalStateException"
    )
    app_context_regex: str = ""


class CloudConfig(_StrictModel):
    api_key_env: str = "MAESTRO_CLOUD_API_KEY"
    project_id_env: str = "MAESTRO_PROJECT_ID"
    device_locale: str = "en_US"
    smoke_api_levels: list[int] = Field(default_factory=lambda: [34])
    benchmark_api_levels: list[int] = Field(default_factory=lambda: [34, 33])
    smoke_flows_root: str = "tests/maestro-cloud"
    smoke_tags: list[str] = Field(default_factory=lambda: ["cloud-smoke"])
    benchmark_flow: str = "tests/maestro-cloud/scenario-gpu-cpu-benchmark.yaml"


class LaneConfig(_StrictModel):
    kind: Literal["test", "command"]
    argv: list[str] = Field(default_factory=list)
    flows: list[str] = Field(default_factory=list)
    include_tags: list[str] = Field(default_factory=list)
    exclude_tags: list[str] = Field(default_factory=list)
    no_build: bool = False
    no_install: bool = False
    clear_state: bool = False
    format: Literal["junit", "html", "json"] = "junit"
    label: str | None = None


class MaestroAndroidConfig(_StrictModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    doctor: DoctorConfig = Field(default_factory=DoctorConfig)
    artifacts: ArtifactsConfig = Field(default_factory=ArtifactsConfig)
    flows: FlowConfig = Field(default_factory=FlowConfig)
    scoped: ScopedConfig = Field(default_factory=ScopedConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    lanes: dict[str, LaneConfig] = Field(
        default_factory=lambda: {
            "smoke": LaneConfig(kind="test", include_tags=["smoke"], label="smoke"),
            "full": LaneConfig(kind="test", label="full"),
            "cloud-smoke": LaneConfig(kind="command", argv=["maestro-android", "cloud", "smoke"]),
        }
    )


DEFAULT_CONFIG = MaestroAndroidConfig().model_dump(mode="python")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise MaestroAndroidError(
            "ENVIRONMENT_ERROR",
            "PyYAML is required. Run: python3 -m pip install PyYAML",
        )
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise MaestroAndroidError("CONFIG_ERROR", f"Failed to parse {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MaestroAndroidError("CONFIG_ERROR", f"Config root must be an object in {path}")
    return dict(payload)


def load_config(repo_root: Path, explicit_path: Path | None = None) -> MaestroAndroidConfig:
    config_path = explicit_path or (repo_root / ".maestro-android.yaml")
    payload: dict[str, Any] = DEFAULT_CONFIG
    if explicit_path is not None and not config_path.exists():
        raise MaestroAndroidError("CONFIG_ERROR", f"Missing config file: {config_path}")
    if config_path.exists():
        payload = _deep_merge(payload, _load_yaml(config_path))
    try:
        return MaestroAndroidConfig.model_validate(payload)
    except ValidationError as exc:
        raise MaestroAndroidError("CONFIG_ERROR", f"Schema validation failed for {config_path}: {exc}") from exc
