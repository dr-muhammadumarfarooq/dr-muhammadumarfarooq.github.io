"""Offline checks: run with python -m unittest discover -s tests -v."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class CitationUpdaterTests(unittest.TestCase):
    def test_updater_imports_with_installed_dependencies(self):
        # Execute real imports and configuration loading, but not the main block,
        # so this catches dependency API breakage without contacting Scholar.
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "_data"
            data.mkdir()
            (data / "socials.yml").write_text("scholar_userid: offline-test\n")
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import runpy, sys; runpy.run_path(sys.argv[1])",
                    str(ROOT / "bin/update_scholar_citations.py"),
                ],
                cwd=directory,
                # No network is needed; ignore host-specific proxy settings.
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key.lower() not in ("http_proxy", "https_proxy", "all_proxy")
                },
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_workflow_preserves_command_status_and_summary(self):
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/update-citations.yml").read_text()
        )
        job = workflow["jobs"]["update-citations"]
        step = next(
            step for step in job["steps"] if step.get("id") == "run_citation_update"
        )
        self.assertFalse(job.get("continue-on-error", False))
        self.assertFalse(step.get("continue-on-error", False))
        # Run the actual workflow shell under Actions' Bash flags. Replace only
        # the external command boundary to simulate success, failure and timeout.
        for status in (0, 1, 124):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                command = directory / "timeout"
                command.write_text(
                    "#!/bin/bash\n"
                    '[[ "$*" == "90 python bin/update_scholar_citations.py" ]] || exit 99\n'
                    'exit "$TEST_EXIT_STATUS"\n'
                )
                command.chmod(0o755)
                summary = directory / "summary"
                result = subprocess.run(
                    ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
                    cwd=directory,
                    env={
                        **os.environ,
                        "PATH": str(directory) + os.pathsep + os.environ["PATH"],
                        "GITHUB_STEP_SUMMARY": str(summary),
                        "TEST_EXIT_STATUS": str(status),
                    },
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                expected = "succeeded" if status == 0 else f"failed with exit code {status}"
                self.assertIn(expected, summary.read_text())
                if status:
                    self.assertNotIn("succeeded", summary.read_text())


if __name__ == "__main__":
    unittest.main()
