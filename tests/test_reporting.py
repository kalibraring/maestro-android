from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from maestro_android.config import load_config
from maestro_android.reporting import find_bundle


class ReportingTest(unittest.TestCase):
    def test_find_raw_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_root = root / ".maestro-android/runs/2026-03-27/SER123/raw/20260327-120000"
            run_root.mkdir(parents=True, exist_ok=True)
            (run_root / "run-manifest.json").write_text("{}\n", encoding="utf-8")
            (run_root / "trace.json").write_text("{}\n", encoding="utf-8")
            config = load_config(repo_root=root)

            bundle = find_bundle("raw", config=config, repo_root=root)

        self.assertEqual(run_root, bundle.artifact_root)
        self.assertEqual([run_root / "run-manifest.json", run_root / "trace.json"], list(bundle.report_files))

    def test_find_lifecycle_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lifecycle_root = root / ".maestro-android/lifecycle/attempt-1"
            lifecycle_root.mkdir(parents=True, exist_ok=True)
            (root / ".maestro-android/lifecycle/retry-summary.txt").write_text("ok\n", encoding="utf-8")
            (lifecycle_root / "junit.xml").write_text("<testsuite/>\n", encoding="utf-8")
            config = load_config(repo_root=root)

            bundle = find_bundle("lifecycle", config=config, repo_root=root)

        self.assertEqual(root / ".maestro-android/lifecycle", bundle.artifact_root)
        self.assertTrue(any(path.name == "retry-summary.txt" for path in bundle.report_files))


if __name__ == "__main__":
    unittest.main()
