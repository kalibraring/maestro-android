from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from maestro_android import cli
from maestro_android.common import MaestroAndroidError


class CliTest(unittest.TestCase):
    def test_main_dispatches_lane(self) -> None:
        captured: list[list[str]] = []
        original = cli.run_subprocess
        try:
            cli.run_subprocess = lambda command, **_kwargs: captured.append(list(command))  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
                (root / ".maestro-android.yaml").write_text("lanes:\n  smoke:\n    kind: command\n    argv: [bash, smoke.sh]\n", encoding="utf-8")
                exit_code = cli.main(["--project-root", str(root), "lane", "smoke"])
        finally:
            cli.run_subprocess = original  # type: ignore[assignment]

        self.assertEqual(0, exit_code)
        self.assertEqual([["bash", "smoke.sh"]], captured)

    def test_scoped_requires_title_description_comments(self) -> None:
        tmp_root = Path.cwd() / "tmp"
        tmp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_root) as tmp:
            flow_path = Path(tmp) / "bad.yaml"
            flow_path.write_text("appId: x\n---\n- launchApp\n", encoding="utf-8")
            with self.assertRaises(MaestroAndroidError):
                cli._validate_scoped_flow(flow_path, cli.load_config(repo_root=Path.cwd()), Path.cwd())


if __name__ == "__main__":
    unittest.main()
