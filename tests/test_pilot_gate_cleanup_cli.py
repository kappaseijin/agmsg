"""pilot-gate-cleanup.py CLI: status arguments are required (runbook §32).

An omitted --execution-status / --after-status used to default to 0 and
was read as success. There is no default now: omission is a caller bug
and stops with the invocation-error status 64 before any handler runs.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLEANUP_HELPER = Path(
    os.environ.get(
        "PILOT_GATE_CLEANUP_UNDER_TEST",
        ROOT / "scripts" / "lib" / "pilot-gate-cleanup.py",
    )
).resolve()


class CleanupCliRequiredStatus(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.artifact = Path(self._tmp.name) / "artifacts"
        self.artifact.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, str(CLEANUP_HELPER), *argv],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def assert_usage_error(self, result, needle):
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn(needle, result.stderr)
        # Stopped before the handler: nothing was evaluated or written.
        self.assertEqual(sorted(os.listdir(self.artifact)), [])

    def test_evaluate_without_execution_status_is_usage_error(self):
        result = self.run_cli(
            "evaluate", "--run-id", "r", "--artifact-dir", str(self.artifact)
        )
        self.assert_usage_error(result, "--execution-status")

    def test_compare_live_without_after_status_is_usage_error(self):
        result = self.run_cli("compare-live", "--artifact-dir", str(self.artifact))
        self.assert_usage_error(result, "--after-status")

    def test_invalid_status_values_are_usage_errors(self):
        self.assert_usage_error(
            self.run_cli("evaluate", "--run-id", "r", "--artifact-dir",
                         str(self.artifact), "--execution-status", "9"),
            "--execution-status",
        )
        self.assert_usage_error(
            self.run_cli("compare-live", "--artifact-dir", str(self.artifact),
                         "--after-status", "3"),
            "--after-status",
        )

    def test_missing_or_unknown_subcommand_is_usage_error(self):
        self.assertEqual(self.run_cli().returncode, 64)
        self.assertEqual(self.run_cli("nosuch").returncode, 64)

    def test_explicit_status_reaches_the_handler(self):
        # Positive control: with the argument present the handler runs.
        # No check results exist, so the evaluation itself is unknown.
        result = self.run_cli(
            "evaluate", "--run-id", "r", "--artifact-dir", str(self.artifact),
            "--execution-status", "0",
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue((self.artifact / "results.json").is_file())

        result = self.run_cli(
            "compare-live", "--artifact-dir", str(self.artifact),
            "--after-status", "0",
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(
            (self.artifact / "live-pm" / "result.json").is_file()
        )

    def test_help_still_exits_zero(self):
        self.assertEqual(self.run_cli("--help").returncode, 0)


if __name__ == "__main__":
    unittest.main()
