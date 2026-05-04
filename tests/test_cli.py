from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from maestro_android import cli
from maestro_android.common import MaestroAndroidError


class CliTest(unittest.TestCase):
    def test_main_dispatches_lane(self) -> None:
        captured: list[tuple[list[str], dict[str, str] | None]] = []
        original = cli.run_subprocess
        try:
            cli.run_subprocess = lambda command, **kwargs: captured.append((list(command), kwargs.get("env")))  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
                (root / ".maestro-android.yaml").write_text("lanes:\n  smoke:\n    kind: command\n    argv: [bash, smoke.sh]\n", encoding="utf-8")
                exit_code = cli.main(["--project-root", str(root), "lane", "smoke"])
        finally:
            cli.run_subprocess = original  # type: ignore[assignment]

        self.assertEqual(0, exit_code)
        self.assertEqual([["bash", "smoke.sh"]], [command for command, _env in captured])
        self.assertNotIn("ANDROID_SERIAL", captured[0][1] or {})

    def test_main_dispatches_lane_with_device_env(self) -> None:
        captured: list[tuple[list[str], dict[str, str] | None]] = []
        original = cli.run_subprocess
        try:
            cli.run_subprocess = lambda command, **kwargs: captured.append((list(command), kwargs.get("env")))  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
                (root / ".maestro-android.yaml").write_text("lanes:\n  smoke:\n    kind: command\n    argv: [python3, tools/devctl/main.py, lane, maestro]\n", encoding="utf-8")
                exit_code = cli.main(["--project-root", str(root), "lane", "--device", "ABC123", "smoke"])
        finally:
            cli.run_subprocess = original  # type: ignore[assignment]

        self.assertEqual(0, exit_code)
        self.assertEqual(["python3", "tools/devctl/main.py", "lane", "maestro"], captured[0][0])
        self.assertEqual("ABC123", (captured[0][1] or {}).get("ANDROID_SERIAL"))
        self.assertEqual("ABC123", (captured[0][1] or {}).get("ADB_SERIAL"))

    def test_main_dispatches_cloud_smoke(self) -> None:
        captured: list[tuple[str, str]] = []
        original = cli._run_cloud_smoke
        try:
            cli._run_cloud_smoke = lambda parsed, config, project_root: captured.append((parsed.cloud_command, str(project_root))) or 0  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".maestro-android.yaml").write_text("", encoding="utf-8")
                exit_code = cli.main(["--project-root", str(root), "cloud", "smoke"])
        finally:
            cli._run_cloud_smoke = original  # type: ignore[assignment]

        self.assertEqual(0, exit_code)
        self.assertEqual([("smoke", str(root.resolve()))], captured)

    def test_main_dispatches_cloud_flow(self) -> None:
        captured: list[tuple[str, str, str]] = []
        original = cli._run_cloud_flow
        try:
            cli._run_cloud_flow = lambda parsed, config, project_root: captured.append((parsed.cloud_command, parsed.flow, str(project_root))) or 0  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                flow = root / "tests" / "maestro-cloud" / "one.yaml"
                flow.parent.mkdir(parents=True)
                flow.write_text("appId: com.example\n---\n- launchApp\n", encoding="utf-8")
                (root / ".maestro-android.yaml").write_text("", encoding="utf-8")
                exit_code = cli.main(["--project-root", str(root), "cloud", "flow", str(flow)])
        finally:
            cli._run_cloud_flow = original  # type: ignore[assignment]

        self.assertEqual(0, exit_code)
        self.assertEqual([("flow", str(flow), str(root.resolve()))], captured)

    def test_main_dispatches_cloud_status(self) -> None:
        captured: list[list[str]] = []
        original = cli._run_cloud_status_command
        try:
            cli._run_cloud_status_command = lambda **kwargs: captured.append(list(kwargs["uploads"])) or 0  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".maestro-android.yaml").write_text("", encoding="utf-8")
                (root / ".env").write_text("MAESTRO_CLOUD_API_KEY=abc\nMAESTRO_PROJECT_ID=proj\n", encoding="utf-8")
                exit_code = cli.main(["--project-root", str(root), "cloud", "status", "a:1"])
        finally:
            cli._run_cloud_status_command = original  # type: ignore[assignment]

        self.assertEqual(0, exit_code)
        self.assertEqual([["a:1"]], captured)

    def test_scoped_requires_title_description_comments(self) -> None:
        tmp_root = Path.cwd() / "tmp"
        tmp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_root) as tmp:
            flow_path = Path(tmp) / "bad.yaml"
            flow_path.write_text("appId: x\n---\n- launchApp\n", encoding="utf-8")
            with self.assertRaises(MaestroAndroidError):
                cli._validate_scoped_flow(flow_path, cli.load_config(repo_root=Path.cwd()), Path.cwd())


    def test_lint_valid_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".maestro-android.yaml").write_text("", encoding="utf-8")
            flows_dir = root / "tests" / "maestro"
            flows_dir.mkdir(parents=True)
            (flows_dir / "ok.yaml").write_text(
                "appId: com.example\n---\n- launchApp\n- tapOn: login_button\n",
                encoding="utf-8",
            )
            exit_code = cli.main(["--project-root", str(root), "lint"])
        self.assertEqual(0, exit_code)

    def test_lint_empty_flow_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".maestro-android.yaml").write_text("", encoding="utf-8")
            flows_dir = root / "tests" / "maestro"
            flows_dir.mkdir(parents=True)
            (flows_dir / "empty.yaml").write_text("", encoding="utf-8")
            exit_code = cli.main(["--project-root", str(root), "lint"])
        self.assertEqual(1, exit_code)

    def test_lint_no_flows_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".maestro-android.yaml").write_text("", encoding="utf-8")
            exit_code = cli.main(["--project-root", str(root), "lint"])
        self.assertEqual(0, exit_code)

    def test_lint_generated_prepared_flow_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            flow = Path(tmp) / ".scenario-onboarding-prepared-flow.yaml"
            flow.write_text("appId: com.example\n---\n- launchApp\n", encoding="utf-8")
            issues = cli._lint_flow(flow, strict=False)
        self.assertTrue(
            any("prepared-flow" in issue["message"] for issue in issues),
            issues,
        )

    def test_audit_selectors_all_covered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".maestro-android.yaml").write_text("", encoding="utf-8")
            flows_dir = root / "tests" / "maestro"
            flows_dir.mkdir(parents=True)
            (flows_dir / "login.yaml").write_text(
                'appId: com.example\n---\n- tapOn:\n    id: "login_btn"\n',
                encoding="utf-8",
            )
            src_dir = root / "apps" / "src"
            src_dir.mkdir(parents=True)
            (src_dir / "Login.kt").write_text(
                'Modifier.testTag("login_btn")\n',
                encoding="utf-8",
            )
            exit_code = cli.main(["--project-root", str(root), "audit-selectors"])
        self.assertEqual(0, exit_code)

    def test_audit_selectors_dangling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".maestro-android.yaml").write_text("", encoding="utf-8")
            flows_dir = root / "tests" / "maestro"
            flows_dir.mkdir(parents=True)
            (flows_dir / "login.yaml").write_text(
                'appId: com.example\n---\n- tapOn:\n    id: "no_such_tag"\n',
                encoding="utf-8",
            )
            src_dir = root / "apps" / "src"
            src_dir.mkdir(parents=True)
            (src_dir / "Login.kt").write_text(
                'Modifier.testTag("other_tag")\n',
                encoding="utf-8",
            )
            exit_code = cli.main(["--project-root", str(root), "audit-selectors"])
        self.assertEqual(1, exit_code)


def _fake_run(stdout: str = "", returncode: int = 0):
    """Return a factory that produces a fake run_subprocess matching any call."""
    def _impl(command: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(command), returncode, stdout=stdout, stderr="")
    return _impl


def _fake_run_router(routes: dict[str, subprocess.CompletedProcess[str]]):
    """Return a fake run_subprocess that dispatches by substring in the command."""
    def _impl(command: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        cmd_str = " ".join(str(c) for c in command)
        for key, result in routes.items():
            if key in cmd_str:
                return result
        return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")
    return _impl


class ScopedCommandsTest(unittest.TestCase):
    def _run(self, *argv: str, run_impl: Any) -> int:
        original = cli.run_subprocess
        try:
            cli.run_subprocess = run_impl  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".maestro-android.yaml").write_text(
                    "\n".join(
                        [
                            "project:",
                            "  app_id: com.pocketagent.android",
                            "  build_command: [./gradlew, assembleDebug]",
                            "  install_command: [./gradlew, installDebug]",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return cli.main(["--project-root", str(root), *argv])
        finally:
            cli.run_subprocess = original  # type: ignore[assignment]

    def test_scoped_instrumented_supports_runner_args_without_flow(self) -> None:
        captured: list[tuple[list[str], dict[str, str] | None]] = []

        def spy(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append((list(command), kwargs.get("env")))
            if "devices" in command:
                return subprocess.CompletedProcess(list(command), 0, stdout="List\nABC123\tdevice\n", stderr="")
            return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")

        exit_code = self._run(
            "scoped",
            "--type", "instrumented",
            "--test-class", "com.example.DeviceTest#works",
            "--runner-arg", "screenshot_pack_dir=tmp/screens",
            "--no-build",
            "--no-install",
            run_impl=spy,
        )

        self.assertEqual(0, exit_code)
        gradle_cmds = [command for command, _env in captured if command[:2] == ["./gradlew", "connectedDebugAndroidTest"]]
        self.assertEqual(1, len(gradle_cmds))
        gradle_cmd = gradle_cmds[0]
        self.assertIn("-Pandroid.testInstrumentationRunnerArguments.class=com.example.DeviceTest#works", gradle_cmd)
        self.assertIn("-Pandroid.testInstrumentationRunnerArguments.screenshot_pack_dir=tmp/screens", gradle_cmd)
        gradle_env = [env for command, env in captured if command[:2] == ["./gradlew", "connectedDebugAndroidTest"]][0] or {}
        self.assertEqual("ABC123", gradle_env.get("ANDROID_SERIAL"))

    def test_scoped_unit_does_not_require_device_or_flow(self) -> None:
        captured: list[tuple[list[str], dict[str, str] | None]] = []

        def spy(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append((list(command), kwargs.get("env")))
            return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")

        exit_code = self._run(
            "scoped",
            "--type", "unit",
            "--test-class", "com.example.UnitTest",
            "--no-build",
            "--no-install",
            run_impl=spy,
        )

        self.assertEqual(0, exit_code)
        self.assertEqual([["./gradlew", "testDebugUnitTest", "--tests=com.example.UnitTest"]], [command for command, _env in captured])


class DeviceCommandsTest(unittest.TestCase):
    """Tests for the maestro-android device * subcommands."""

    def _run(self, *argv: str, run_impl: Any = None) -> int:
        original = cli.run_subprocess
        impl = run_impl or _fake_run()
        try:
            cli.run_subprocess = impl  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".maestro-android.yaml").write_text(
                    "project:\n  app_id: com.pocketagent.android\n",
                    encoding="utf-8",
                )
                return cli.main(["--project-root", str(root), *argv])
        finally:
            cli.run_subprocess = original  # type: ignore[assignment]

    def test_device_files_dispatches_ls(self) -> None:
        captured: list[list[str]] = []
        def spy(command: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            captured.append(list(command))
            if "devices" in command:
                return subprocess.CompletedProcess(list(command), 0, stdout="List\nABC123\tdevice\n", stderr="")
            return subprocess.CompletedProcess(list(command), 0, stdout="drwxr-x 2 root root 4096 models\n", stderr="")

        exit_code = self._run("device", "files", "models/", run_impl=spy)
        self.assertEqual(0, exit_code)
        ls_cmds = [c for c in captured if "ls" in c]
        self.assertTrue(len(ls_cmds) >= 1)
        ls_cmd = " ".join(ls_cmds[0])
        self.assertIn("com.pocketagent.android", ls_cmd)
        self.assertIn("models", ls_cmd)

    def test_device_files_no_path_uses_base_dir(self) -> None:
        captured: list[list[str]] = []
        def spy(command: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            captured.append(list(command))
            if "devices" in command:
                return subprocess.CompletedProcess(list(command), 0, stdout="List\nABC123\tdevice\n", stderr="")
            return subprocess.CompletedProcess(list(command), 0, stdout="total 0\n", stderr="")

        exit_code = self._run("device", "files", run_impl=spy)
        self.assertEqual(0, exit_code)
        ls_cmds = [c for c in captured if "ls" in c]
        ls_target = ls_cmds[0][-1]
        self.assertEqual(ls_target, "/sdcard/Android/data/com.pocketagent.android/files")

    def test_device_files_media_storage_uses_android_media(self) -> None:
        captured: list[list[str]] = []

        def spy(command: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            captured.append(list(command))
            if "devices" in command:
                return subprocess.CompletedProcess(list(command), 0, stdout="List\nABC123\tdevice\n", stderr="")
            return subprocess.CompletedProcess(list(command), 0, stdout="total 0\n", stderr="")

        exit_code = self._run("device", "files", "--storage", "media", "models/", run_impl=spy)
        self.assertEqual(0, exit_code)
        ls_cmds = [c for c in captured if "ls" in c]
        self.assertEqual(ls_cmds[0][-1], "/sdcard/Android/media/com.pocketagent.android/models")

    def test_device_files_not_found_returns_1(self) -> None:
        routes = {
            "devices": subprocess.CompletedProcess([], 0, stdout="List\nABC123\tdevice\n", stderr=""),
            "ls": subprocess.CompletedProcess([], 1, stdout="", stderr="No such file or directory"),
        }
        exit_code = self._run("device", "files", "nonexistent/", run_impl=_fake_run_router(routes))
        self.assertEqual(1, exit_code)

    def test_device_push_dispatches_adb_push(self) -> None:
        captured: list[list[str]] = []
        def spy(command: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            captured.append(list(command))
            if "devices" in command:
                return subprocess.CompletedProcess(list(command), 0, stdout="List\nABC123\tdevice\n", stderr="")
            return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")

        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            f.write(b"fake model")
            local_path = f.name
        try:
            exit_code = self._run("device", "push", local_path, "models/", run_impl=spy)
        finally:
            Path(local_path).unlink(missing_ok=True)

        self.assertEqual(0, exit_code)
        push_cmds = [c for c in captured if "push" in c]
        self.assertTrue(len(push_cmds) >= 1)
        push_cmd = " ".join(push_cmds[0])
        self.assertIn("com.pocketagent.android", push_cmd)
        self.assertIn("models/", push_cmd)

    def test_device_push_missing_local_file_returns_1(self) -> None:
        exit_code = self._run(
            "device", "push", "/nonexistent/path/file.gguf", "models/",
            run_impl=_fake_run(stdout="List\nABC123\tdevice\n"),
        )
        self.assertEqual(1, exit_code)

    def test_device_push_media_storage_uses_android_media(self) -> None:
        captured: list[list[str]] = []

        def spy(command: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            captured.append(list(command))
            if "devices" in command:
                return subprocess.CompletedProcess(list(command), 0, stdout="List\nABC123\tdevice\n", stderr="")
            return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")

        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            f.write(b"fake model")
            local_path = f.name
        try:
            exit_code = self._run("device", "push", "--storage", "media", local_path, "models/", run_impl=spy)
        finally:
            Path(local_path).unlink(missing_ok=True)

        self.assertEqual(0, exit_code)
        push_cmds = [c for c in captured if "push" in c]
        self.assertIn("/sdcard/Android/media/com.pocketagent.android/models/", " ".join(push_cmds[0]))

    def test_device_logcat_dump_mode(self) -> None:
        logcat_output = (
            "03-31 12:00:01 I System.out: SendMessageUseCase|MULTIMODAL_DECISION|images=1\n"
            "03-31 12:00:02 I System.out: unrelated log line\n"
            "03-31 12:00:03 I PocketLlamaJNI: MULTIMODAL|tokenized|chunks=3\n"
        )
        routes = {
            "devices": subprocess.CompletedProcess([], 0, stdout="List\nABC123\tdevice\n", stderr=""),
            "pidof": subprocess.CompletedProcess([], 0, stdout="12345\n", stderr=""),
            "logcat": subprocess.CompletedProcess([], 0, stdout=logcat_output, stderr=""),
        }
        exit_code = self._run(
            "device", "logcat", "--filter", "MULTIMODAL",
            run_impl=_fake_run_router(routes),
        )
        self.assertEqual(0, exit_code)

    def test_device_logcat_lines_limit(self) -> None:
        many_lines = "\n".join(f"line {i} MATCH" for i in range(100))
        routes = {
            "devices": subprocess.CompletedProcess([], 0, stdout="List\nABC123\tdevice\n", stderr=""),
            "pidof": subprocess.CompletedProcess([], 0, stdout="12345\n", stderr=""),
            "logcat": subprocess.CompletedProcess([], 0, stdout=many_lines, stderr=""),
        }
        exit_code = self._run(
            "device", "logcat", "--filter", "MATCH", "--lines", "5",
            run_impl=_fake_run_router(routes),
        )
        self.assertEqual(0, exit_code)

    def test_device_logcat_no_process_returns_1(self) -> None:
        routes = {
            "devices": subprocess.CompletedProcess([], 0, stdout="List\nABC123\tdevice\n", stderr=""),
            "pidof": subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        }
        exit_code = self._run("device", "logcat", run_impl=_fake_run_router(routes))
        self.assertEqual(1, exit_code)

    def test_device_logcat_save_to_file(self) -> None:
        logcat_output = "line1 MATCH\nline2\nline3 MATCH\n"
        routes = {
            "devices": subprocess.CompletedProcess([], 0, stdout="List\nABC123\tdevice\n", stderr=""),
            "pidof": subprocess.CompletedProcess([], 0, stdout="12345\n", stderr=""),
            "logcat": subprocess.CompletedProcess([], 0, stdout=logcat_output, stderr=""),
        }
        with tempfile.TemporaryDirectory() as tmp:
            save_path = Path(tmp) / "captured.log"
            original = cli.run_subprocess
            try:
                cli.run_subprocess = _fake_run_router(routes)  # type: ignore[assignment]
                root_dir = Path(tmp) / "project"
                root_dir.mkdir()
                (root_dir / ".maestro-android.yaml").write_text(
                    "project:\n  app_id: com.pocketagent.android\n",
                    encoding="utf-8",
                )
                exit_code = cli.main([
                    "--project-root", str(root_dir),
                    "device", "logcat", "--filter", "MATCH", "--save", str(save_path),
                ])
            finally:
                cli.run_subprocess = original  # type: ignore[assignment]
            self.assertEqual(0, exit_code)
            self.assertTrue(save_path.exists())
            content = save_path.read_text(encoding="utf-8")
            self.assertIn("MATCH", content)
            self.assertNotIn("line2", content)

    def test_device_ui_parses_hierarchy(self) -> None:
        ui_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hierarchy rotation="0">'
            '<node resource-id="com.pocketagent.android:id/composer_input" '
            'text="Type a message" bounds="[0,100][500,200]" class="android.widget.EditText" content-desc="" />'
            '<node resource-id="" text="" bounds="[0,0][1080,50]" class="android.view.View" content-desc="" />'
            '</hierarchy>'
        )
        routes = {
            "devices": subprocess.CompletedProcess([], 0, stdout="List\nABC123\tdevice\n", stderr=""),
            "uiautomator": subprocess.CompletedProcess([], 0, stdout=ui_xml, stderr=""),
        }
        exit_code = self._run("device", "ui", run_impl=_fake_run_router(routes))
        self.assertEqual(0, exit_code)

    def test_device_ui_empty_returns_1(self) -> None:
        routes = {
            "devices": subprocess.CompletedProcess([], 0, stdout="List\nABC123\tdevice\n", stderr=""),
            "uiautomator": subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        }
        exit_code = self._run("device", "ui", run_impl=_fake_run_router(routes))
        self.assertEqual(1, exit_code)

    def test_device_info_running_process(self) -> None:
        routes = {
            "devices": subprocess.CompletedProcess([], 0, stdout="List\nABC123\tdevice\n", stderr=""),
            "pidof": subprocess.CompletedProcess([], 0, stdout="12345\n", stderr=""),
            "meminfo": subprocess.CompletedProcess([], 0, stdout="  TOTAL    512000\n", stderr=""),
            "top": subprocess.CompletedProcess([], 0, stdout="  PID  USER      CPU% 12345 u0_a123  45.2 ...\n", stderr=""),
        }
        exit_code = self._run("device", "info", run_impl=_fake_run_router(routes))
        self.assertEqual(0, exit_code)

    def test_device_info_not_running_returns_1(self) -> None:
        routes = {
            "devices": subprocess.CompletedProcess([], 0, stdout="List\nABC123\tdevice\n", stderr=""),
            "pidof": subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        }
        exit_code = self._run("device", "info", run_impl=_fake_run_router(routes))
        self.assertEqual(1, exit_code)

    def test_app_external_data_dir_uses_app_id(self) -> None:
        result = cli._app_external_data_dir("com.example.test")
        self.assertEqual("/sdcard/Android/data/com.example.test/files", result)

    def test_app_storage_dir_supports_media_root(self) -> None:
        result = cli._app_storage_dir("com.example.test", "media")
        self.assertEqual("/sdcard/Android/media/com.example.test", result)

    def test_resolve_app_pid_strips_whitespace(self) -> None:
        original = cli.run_subprocess
        try:
            cli.run_subprocess = _fake_run(stdout="  12345\r\n")  # type: ignore[assignment]
            pid = cli._resolve_app_pid("ABC123", "com.example")
        finally:
            cli.run_subprocess = original  # type: ignore[assignment]
        self.assertEqual("12345", pid)

    def test_resolve_app_pid_returns_none_on_failure(self) -> None:
        original = cli.run_subprocess
        try:
            cli.run_subprocess = _fake_run(stdout="", returncode=1)  # type: ignore[assignment]
            pid = cli._resolve_app_pid("ABC123", "com.example")
        finally:
            cli.run_subprocess = original  # type: ignore[assignment]
        self.assertIsNone(pid)


class DoctorAndCleanCommandsTest(unittest.TestCase):
    def test_doctor_json_reports_matrix(self) -> None:
        routes = {
            "adb devices -l": subprocess.CompletedProcess(
                [],
                0,
                stdout=(
                    "List of devices attached\n"
                    "emulator-5554\tdevice product:sdk_gphone_x86_64 model:sdk_gphone_x86_64 device:emu64xa\n"
                    "192.168.1.10:5555\tdevice product:a51 model:SM_A515F device:a51\n"
                    "USB123\toffline transport_id:7\n"
                ),
                stderr="",
            ),
            "adb devices": subprocess.CompletedProcess([], 0, stdout="List of devices attached\n", stderr=""),
        }
        original = cli.run_subprocess
        original_which = cli.shutil.which
        stdout = io.StringIO()
        old_api_key = os.environ.pop("MAESTRO_CLOUD_API_KEY", None)
        old_project_id = os.environ.pop("MAESTRO_PROJECT_ID", None)
        try:
            cli.run_subprocess = _fake_run_router(routes)  # type: ignore[assignment]
            cli.shutil.which = lambda command: f"/usr/bin/{command}"  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
                (root / ".maestro-android.yaml").write_text("", encoding="utf-8")
                with contextlib.redirect_stdout(stdout):
                    exit_code = cli.main(["--project-root", str(root), "doctor", "--json"])
        finally:
            cli.run_subprocess = original  # type: ignore[assignment]
            cli.shutil.which = original_which  # type: ignore[assignment]
            if old_api_key is not None:
                os.environ["MAESTRO_CLOUD_API_KEY"] = old_api_key
            if old_project_id is not None:
                os.environ["MAESTRO_PROJECT_ID"] = old_project_id

        self.assertEqual(0, exit_code)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(2, payload["device_summary"]["online"])
        self.assertEqual(1, payload["device_summary"]["emulator"])
        self.assertEqual(1, payload["device_summary"]["network"])
        self.assertEqual(1, payload["device_summary"]["offline"])
        self.assertTrue(payload["matrix"]["local_device"])
        self.assertTrue(payload["matrix"]["emulator"])
        self.assertFalse(payload["matrix"]["cloud_ready"])
        self.assertIn("recommendations", payload)
        self.assertEqual([], payload["duplicate_transport_groups"])

    def test_devices_json_reports_duplicate_transports(self) -> None:
        routes = {
            "adb devices -l": subprocess.CompletedProcess(
                [],
                0,
                stdout=(
                    "List of devices attached\n"
                    "USB123\tdevice product:dm1q model:SM_S901B device:dm1q\n"
                    "192.168.1.10:5555\tdevice product:dm1q model:SM_S901B device:dm1q\n"
                ),
                stderr="",
            ),
        }
        original = cli.run_subprocess
        stdout = io.StringIO()
        try:
            cli.run_subprocess = _fake_run_router(routes)  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".maestro-android.yaml").write_text("", encoding="utf-8")
                with contextlib.redirect_stdout(stdout):
                    exit_code = cli.main(["--project-root", str(root), "devices", "--json"])
        finally:
            cli.run_subprocess = original  # type: ignore[assignment]

        self.assertEqual(0, exit_code)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(2, len(payload["devices"]))
        self.assertEqual(1, len(payload["duplicate_transport_groups"]))

    def test_resolve_serial_errors_on_duplicate_same_model(self) -> None:
        original = cli.run_subprocess
        try:
            cli.run_subprocess = _fake_run_router(
                {
                    "adb devices -l": subprocess.CompletedProcess(
                        [],
                        0,
                        stdout=(
                            "List of devices attached\n"
                            "USB123\tdevice product:dm1q model:SM_S901B device:dm1q\n"
                            "192.168.1.10:5555\tdevice product:dm1q model:SM_S901B device:dm1q\n"
                        ),
                        stderr="",
                    )
                }
            )  # type: ignore[assignment]
            with self.assertRaises(MaestroAndroidError) as ctx:
                cli._resolve_serial("")
        finally:
            cli.run_subprocess = original  # type: ignore[assignment]

        self.assertIn("Multiple adb devices detected", str(ctx.exception))
        self.assertIn("maestro-android devices", str(ctx.exception))

    def test_clean_stale_flows_requires_confirm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".maestro-android.yaml").write_text("", encoding="utf-8")
            flow = root / "tests" / "maestro" / ".scenario-ready-prepared-flow.yaml"
            flow.parent.mkdir(parents=True)
            flow.write_text("appId: com.example\n---\n- launchApp\n", encoding="utf-8")

            exit_code = cli.main(["--project-root", str(root), "clean", "--stale-flows"])

            self.assertEqual(0, exit_code)
            self.assertTrue(flow.exists())

    def test_clean_stale_flows_confirm_deletes_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".maestro-android.yaml").write_text("", encoding="utf-8")
            flow = root / "tmp" / ".scenario-ready-prepared-flow.yaml"
            flow.parent.mkdir(parents=True)
            flow.write_text("appId: com.example\n---\n- launchApp\n", encoding="utf-8")

            exit_code = cli.main(
                ["--project-root", str(root), "clean", "--stale-flows", "--confirm"]
            )

            self.assertEqual(0, exit_code)
            self.assertFalse(flow.exists())


class ForegroundDiagnosticsTest(unittest.TestCase):
    def test_parse_foreground_component(self) -> None:
        parsed = cli._parse_foreground_component(
            "\n".join(
                [
                    "mCurrentFocus=Window{123 u0 com.google.android.permissioncontroller/com.android.permissioncontroller.permission.ui.GrantPermissionsActivity}",
                    "topResumedActivity: ActivityRecord{456 u0 com.google.android.permissioncontroller/com.android.permissioncontroller.permission.ui.GrantPermissionsActivity t42}",
                ]
            )
        )
        self.assertEqual(
            "com.google.android.permissioncontroller",
            parsed["package"],
        )
        self.assertEqual(
            "com.android.permissioncontroller.permission.ui.GrantPermissionsActivity",
            parsed["activity"],
        )

    def test_classify_foreground_package(self) -> None:
        self.assertEqual(
            "system_permission_dialog",
            cli._classify_foreground_package(
                "com.google.android.permissioncontroller", "com.example.app"
            ),
        )

    def test_extract_cloud_upload_ids(self) -> None:
        upload_ids = cli._extract_cloud_upload_ids(
            "upload created: mupload_abc123",
            "watch this next: mupload_def456 and mupload_abc123",
        )
        self.assertEqual(["mupload_abc123", "mupload_def456"], upload_ids)

    def test_device_probe_json_reports_ready(self) -> None:
        routes = {
            "adb devices -l": subprocess.CompletedProcess(
                [],
                0,
                stdout="List of devices attached\nABC123\tdevice product:pixel model:Pixel_8 device:husky\n",
                stderr="",
            ),
            "shell echo maestro-android-ok": subprocess.CompletedProcess(
                [], 0, stdout="maestro-android-ok\n", stderr=""
            ),
            "dumpsys window": subprocess.CompletedProcess(
                [],
                0,
                stdout="mCurrentFocus=Window{123 u0 com.pocketagent.android/.MainActivity}",
                stderr="",
            ),
            "dumpsys activity": subprocess.CompletedProcess(
                [],
                0,
                stdout="topResumedActivity: ActivityRecord{123 u0 com.pocketagent.android/.MainActivity t1}",
                stderr="",
            ),
            "logcat -c": subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            "maestro --device": subprocess.CompletedProcess([], 0, stdout="<testsuite/>", stderr=""),
            "logcat -d": subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        }
        original = cli.run_subprocess
        stdout = io.StringIO()
        try:
            cli.run_subprocess = _fake_run_router(routes)  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".maestro-android.yaml").write_text(
                    "project:\n  app_id: com.pocketagent.android\n",
                    encoding="utf-8",
                )
                with contextlib.redirect_stdout(stdout):
                    exit_code = cli.main(
                        ["--project-root", str(root), "device", "--device", "ABC123", "probe", "--json"]
                    )
        finally:
            cli.run_subprocess = original  # type: ignore[assignment]

        self.assertEqual(0, exit_code)
        payload = json.loads(stdout.getvalue())
        self.assertEqual("ready", payload["classification"])
        self.assertTrue(payload["transport_ok"])
        self.assertEqual(
            "play_store",
            cli._classify_foreground_package("com.android.vending", "com.example.app"),
        )
        self.assertEqual(
            "app",
            cli._classify_foreground_package("com.example.app", "com.example.app"),
        )
        self.assertEqual(
            "external",
            cli._classify_foreground_package("com.google.android.gm", "com.example.app"),
        )

    def test_run_maestro_flow_timeout_writes_failure_breadcrumbs(self) -> None:
        original = cli.run_subprocess

        def spy(command: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            command_list = list(command)
            if command_list[:5] == ["adb", "-s", "ABC123", "logcat", "-c"]:
                return subprocess.CompletedProcess(command_list, 0, stdout="", stderr="")
            if command_list[:5] == ["adb", "-s", "ABC123", "logcat", "-d"]:
                return subprocess.CompletedProcess(command_list, 0, stdout="timeout marker\n", stderr="")
            if command_list[:6] == ["adb", "-s", "ABC123", "shell", "dumpsys", "window"]:
                return subprocess.CompletedProcess(
                    command_list,
                    0,
                    stdout="mCurrentFocus=Window{123 u0 com.google.android.permissioncontroller/com.android.permissioncontroller.permission.ui.GrantPermissionsActivity}\n",
                    stderr="",
                )
            if command_list[:6] == ["adb", "-s", "ABC123", "shell", "dumpsys", "activity"]:
                return subprocess.CompletedProcess(
                    command_list,
                    0,
                    stdout="topResumedActivity: ActivityRecord{456 u0 com.google.android.permissioncontroller/com.android.permissioncontroller.permission.ui.GrantPermissionsActivity t42}\n",
                    stderr="",
                )
            if command_list[:5] == ["adb", "-s", "ABC123", "exec-out", "uiautomator"]:
                return subprocess.CompletedProcess(
                    command_list,
                    0,
                    stdout='<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0"></hierarchy>',
                    stderr="",
                )
            if command_list and command_list[0] == "maestro":
                raise MaestroAndroidError(
                    "ENVIRONMENT_ERROR",
                    "Command timed out after 120.0s: maestro --device ABC123 test",
                )
            return subprocess.CompletedProcess(command_list, 0, stdout="", stderr="")

        try:
            cli.run_subprocess = spy  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                flow = root / "tmp" / "repro.yaml"
                flow.parent.mkdir(parents=True)
                flow.write_text(
                    "# title: timeout repro\n# description: test timeout breadcrumbs\nappId: com.example.app\n---\n- launchApp\n",
                    encoding="utf-8",
                )
                artifact_root = root / "artifacts"
                artifact_root.mkdir()
                result = cli._run_maestro_flow(
                    project_root=root,
                    serial="ABC123",
                    flow=flow,
                    app_id="com.example.app",
                    clear_state=False,
                    output_format="junit",
                    artifact_root=artifact_root,
                    maestro_timeout_sec=1,
                )
                self.assertEqual("failed", result["status"])
                self.assertIn("foreground", result)
                flow_dir = artifact_root / "flows" / "repro"
                state_path = flow_dir / "flow-state.json"
                self.assertTrue(state_path.exists())
                state_text = state_path.read_text(encoding="utf-8")
                self.assertIn('"status": "failed"', state_text)
                self.assertIn('"error_code": "ENVIRONMENT_ERROR"', state_text)
                foreground_path = artifact_root / result["foreground"]
                self.assertTrue(foreground_path.exists())
                self.assertIn(
                    '"classification": "system_permission_dialog"',
                    foreground_path.read_text(encoding="utf-8"),
                )
                self.assertTrue((flow_dir / "maestro-stderr.log").exists())
                self.assertTrue((flow_dir / "logcat.txt").exists())
        finally:
            cli.run_subprocess = original  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
