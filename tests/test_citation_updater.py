"""Offline checks: run with python -m unittest discover -s tests -v."""

import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

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
        for status, force in ((0, "false"), (1, "false"), (124, "false"), (0, "true"), (1, "true"), (124, "true")):
            with self.subTest(status=status, force=force), tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                command = directory / "timeout"
                command.write_text(
                    "#!/bin/bash\n"
                    'expected="90 python bin/update_scholar_citations.py"\n'
                    '[[ "$FORCE_UPDATE" == "true" ]] && expected+=" --force"\n'
                    '[[ "$*" == "$expected" ]] || exit 99\n'
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
                        "FORCE_UPDATE": force,
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

    def test_force_bypasses_same_day_cache_and_preserves_data_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            previous_directory = Path.cwd()
            try:
                os.chdir(directory)
                data = Path("_data")
                data.mkdir()
                (data / "socials.yml").write_text("scholar_userid: offline-test\n")
                output = data / "citations.yml"
                output.write_text(yaml.safe_dump({
                    "metadata": {"last_updated": datetime.now().strftime("%Y-%m-%d")},
                    "papers": {},
                }))
                before = output.read_bytes()
                module = runpy.run_path(str(ROOT / "bin/update_scholar_citations.py"))
                scholarly = module["scholarly"]
                with mock.patch.object(scholarly, "search_author_id") as search, mock.patch.object(
                    scholarly, "fill", return_value={"publications": []}
                ) as fill:
                    module["get_scholar_citations"]()
                    search.assert_not_called()
                    module["get_scholar_citations"](force=True)
                    search.assert_called_once_with("offline-test")
                    fill.assert_called_once_with(search.return_value)
                    self.assertEqual(output.read_bytes(), before)
                    search.side_effect = RuntimeError("Scholar unavailable")
                    with self.assertRaises(SystemExit) as error:
                        module["get_scholar_citations"](force=True)
                    self.assertEqual(error.exception.code, 1)
                    self.assertEqual(output.read_bytes(), before)
            finally:
                os.chdir(previous_directory)


if __name__ == "__main__":
    unittest.main()
