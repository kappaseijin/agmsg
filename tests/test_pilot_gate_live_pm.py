"""Live-PM negative control: deny-semantic-before-after (Issue #406).

The semantic comparison used to select files by keyword ("deny",
"semantic", "response", "decision"). The harness never writes such
files: run_live_pm_control() writes input.raw / stdout.raw / stderr.raw /
exit-status / guard.sha256 and the isolation helper writes control.json.
Both sides were therefore always empty, the check was always unknown and
pilot_ready could never become true.

The comparison now targets exactly those artifacts (runbook §8.2, §36):
raw files byte for byte without normalization, control.json on its
contract-fixed fields. control.json is produced here by the real
isolation helper so the fixtures cannot drift from what the harness
writes.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
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
ISOLATION_HELPER = ROOT / "scripts" / "lib" / "pilot-gate-isolation.py"
RUNNER = ROOT / "scripts" / "pilot-gate-runner.sh"

SPEC = importlib.util.spec_from_file_location(
    "pilot_gate_cleanup_live_pm",
    CLEANUP_HELPER,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(
        f"cannot load pilot gate cleanup helper: {CLEANUP_HELPER}"
    )

CL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CL)

PROBE = {
    "input.raw": b"{}\n",
    "stdout.raw": b"",
    "stderr.raw": b"pm-pretool-guard: invalid input\n",
    "exit-status": b"2\n",
    "guard.sha256": b"abc123\n",
}


def record_control(directory: Path, phase: str) -> None:
    """Write control.json exactly as the harness does."""
    subprocess.run(
        [sys.executable, str(ISOLATION_HELPER), "record-live-control",
         "--directory", str(directory), "--phase", phase],
        check=True,
        capture_output=True,
        timeout=60,
    )


class LivePmArtifactComparison(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.artifact = Path(self._tmp.name) / "art"

    def tearDown(self):
        for path in self.artifact.rglob("*"):
            if not path.is_symlink():
                path.chmod(0o700)
        self._tmp.cleanup()

    def side(self, phase, files=None, *, record=True):
        directory = self.artifact / "live-pm" / phase
        directory.mkdir(parents=True)
        for name, content in (PROBE if files is None else files).items():
            (directory / name).write_bytes(content)
        if record:
            record_control(directory, phase)
        return directory

    def compare(self):
        rc = CL.compare_live_pm(
            argparse.Namespace(artifact_dir=str(self.artifact), after_status=0)
        )
        result = json.loads(
            (self.artifact / "live-pm" / "result.json").read_text()
        )
        semantic = [
            c for c in result["checks"]
            if c["name"] == "deny-semantic-before-after"
        ][0]
        return rc, result, semantic

    # --- the harness's own artifacts are what gets compared ------------

    def test_identical_harness_artifacts_pass(self):
        # The case that could never pass before: real artifacts, same probe.
        self.side("before")
        self.side("after")
        rc, result, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "pass")
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(rc, 0)
        self.assertEqual(
            sorted(semantic["before"]),
            sorted([
                "input.raw", "stdout.raw", "stderr.raw",
                *(f"control.json:{f}" for f in CL.LIVE_CONTROL_FIELDS),
            ]),
        )

    def test_compared_raw_files_are_the_ones_the_runner_writes(self):
        text = RUNNER.read_text()
        body = text[text.index("run_live_pm_control() {"):]
        body = body[:body.index("\n}\n")]
        written = set(re.findall(r'\$out_dir/([A-Za-z0-9_.-]+)"', body))
        for name in CL.LIVE_RAW_FILES:
            self.assertIn(name, written)
        # exit-status and guard.sha256 have their own checks.
        self.assertEqual(
            written - set(CL.LIVE_RAW_FILES), {"exit-status", "guard.sha256"}
        )

    def test_compared_control_fields_are_the_ones_the_helper_writes(self):
        directory = self.side("before")
        record = json.loads((directory / "control.json").read_text())
        self.assertEqual(
            set(record) - set(CL.LIVE_CONTROL_FIELDS),
            {"phase", "observedAt"},
        )
        for field in CL.LIVE_CONTROL_FIELDS:
            self.assertIn(field, record)

    # --- issue acceptance: 1 byte, deletion, identical ------------------

    def test_one_byte_change_in_after_stdout_is_fail(self):
        self.side("before")
        self.side("after", dict(PROBE, **{"stdout.raw": b"x"}))
        rc, _, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "fail")
        self.assertEqual(semantic["reason"], "semantic_content_changed")
        self.assertIn("stdout.raw", semantic["changed"])
        self.assertIn("control.json:stdoutSha256", semantic["changed"])
        self.assertEqual(rc, 1)

    def test_changed_stdout_without_re_recording_control_is_fail(self):
        self.side("before")
        after = self.side("after")
        (after / "stdout.raw").write_bytes(b"x")
        _, _, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "fail")
        self.assertEqual(semantic["changed"], ["stdout.raw"])

    def test_deleted_after_stdout_is_fail(self):
        self.side("before")
        after = self.side("after")
        (after / "stdout.raw").unlink()
        rc, _, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "fail")
        self.assertEqual(semantic["reason"], "semantic_file_set_changed")
        self.assertEqual(semantic["missingAfter"], ["stdout.raw"])
        self.assertEqual(rc, 1)

    def test_added_after_file_is_fail(self):
        self.side("before", record=False)
        after = self.side("after", record=False)
        before = self.artifact / "live-pm" / "before"
        (before / "stdout.raw").unlink()
        _, _, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "fail")
        self.assertEqual(semantic["addedAfter"], ["stdout.raw"])
        self.assertTrue((after / "stdout.raw").exists())

    # --- no normalization -------------------------------------------------

    def test_trailing_newline_difference_is_not_normalized_away(self):
        self.side("before")
        self.side("after", dict(PROBE, **{
            "stderr.raw": b"pm-pretool-guard: invalid input\n\n"}))
        _, _, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "fail")
        self.assertIn("stderr.raw", semantic["changed"])

    def test_input_change_is_fail(self):
        self.side("before")
        self.side("after", dict(PROBE, **{"input.raw": b'{"x":1}\n'}))
        _, _, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "fail")
        self.assertIn("input.raw", semantic["changed"])

    def test_only_phase_and_observed_at_differ_by_construction(self):
        self.side("before")
        self.side("after")
        before = json.loads(
            (self.artifact / "live-pm/before/control.json").read_text())
        after = json.loads(
            (self.artifact / "live-pm/after/control.json").read_text())
        self.assertNotEqual(before["phase"], after["phase"])
        _, _, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "pass")

    def test_changed_fixed_control_field_is_fail(self):
        self.side("before")
        after = self.side("after")
        record = json.loads((after / "control.json").read_text())
        record["guardSha256"] = "other"
        (after / "control.json").write_text(json.dumps(record))
        _, _, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "fail")
        self.assertEqual(semantic["changed"], ["control.json:guardSha256"])

    def test_missing_fixed_control_field_is_fail(self):
        self.side("before")
        after = self.side("after")
        record = json.loads((after / "control.json").read_text())
        del record["exitStatus"]
        (after / "control.json").write_text(json.dumps(record))
        _, _, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "fail")
        self.assertEqual(semantic["missingAfter"],
                         ["control.json:exitStatus"])

    # --- cannot compare -> unknown ----------------------------------------

    def test_unreadable_raw_file_is_unknown(self):
        self.side("before")
        after = self.side("after")
        (after / "stderr.raw").chmod(0o000)
        rc, _, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "unknown")
        self.assertEqual(semantic["reason"], "semantic_file_unreadable")
        self.assertEqual(semantic["unreadable"], ["after/stderr.raw"])
        self.assertEqual(rc, 2)

    def test_unreadable_control_json_is_unknown_not_a_set_change(self):
        self.side("before")
        after = self.side("after")
        (after / "control.json").write_text("{broken")
        _, _, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "unknown")
        self.assertEqual(semantic["missingAfter"], [])
        self.assertEqual(semantic["unreadable"], ["after/control.json"])

    def test_symlinked_artifact_is_unreadable(self):
        self.side("before")
        after = self.side("after")
        (after / "stdout.raw").unlink()
        (after / "stdout.raw").symlink_to(
            self.artifact / "live-pm" / "before" / "stdout.raw")
        _, _, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "unknown")
        self.assertEqual(semantic["unreadable"], ["after/stdout.raw"])

    def test_change_elsewhere_wins_over_unreadable(self):
        self.side("before")
        after = self.side("after", dict(PROBE, **{"input.raw": b"[]\n"}))
        (after / "stderr.raw").chmod(0o000)
        _, _, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "fail")

    def test_missing_side_or_nothing_observed_is_unknown(self):
        self.side("before")
        _, _, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "unknown")
        self.assertEqual(semantic["reason"], "semantic_artifact_missing")

    def test_empty_directories_are_unknown(self):
        self.side("before", {}, record=False)
        self.side("after", {}, record=False)
        _, _, semantic = self.compare()
        self.assertEqual(semantic["verdict"], "unknown")

    def test_raw_values_are_reported_as_digests(self):
        self.side("before")
        self.side("after")
        _, _, semantic = self.compare()
        self.assertEqual(
            semantic["before"]["stderr.raw"],
            {"sha256": __import__("hashlib").sha256(
                PROBE["stderr.raw"]).hexdigest(),
             "bytes": len(PROBE["stderr.raw"])},
        )


if __name__ == "__main__":
    unittest.main()
