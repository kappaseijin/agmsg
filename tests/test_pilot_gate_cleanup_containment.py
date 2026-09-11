"""Issue #400: cleanup must never delete outside the disposable run root.

Reproduction (before the fix): with run/link -> outside, removing
run/link/victim passed the lexical containment check, rmtree() followed
the link and deleted outside/victim, and the step reported "pass".

The fix is fail-closed: any symlink between the run root and the target
(including the target itself and the run root) stops the removal with
"unknown", leaves everything in place, and records what was found.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLEANUP_HELPER = Path(
    os.environ.get(
        "PILOT_GATE_CLEANUP_UNDER_TEST",
        ROOT / "scripts" / "lib" / "pilot-gate-cleanup.py",
    )
).resolve()

SPEC = importlib.util.spec_from_file_location(
    "pilot_gate_cleanup_containment",
    CLEANUP_HELPER,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(
        f"cannot load pilot gate cleanup helper: {CLEANUP_HELPER}"
    )

CL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CL)


class ContainmentCase(unittest.TestCase):
    """All paths live under a private temporary directory.

    "outside" is a sibling of the run root inside that directory, so a
    broken containment check can only ever delete test fixtures.
    Real signals are blocked: cleanup_run must not reach os.kill here.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # Keep the unresolved path: on macOS the temp dir sits under the
        # /var -> /private/var symlink, which is above the run root and
        # must not be treated as a symlink inside it.
        self.base = Path(self._tmp.name)
        self.run_root = self.base / "run"
        self.run_root.mkdir()
        self.outside = self.base / "outside"
        (self.outside / "victim").mkdir(parents=True)
        (self.outside / "victim" / "data").write_text("keep")
        for name in ("kill", "killpg"):
            patcher = mock.patch.object(
                CL.os,
                name,
                side_effect=AssertionError(f"real os.{name} reached"),
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def assert_outside_intact(self):
        self.assertEqual(
            (self.outside / "victim" / "data").read_text(), "keep"
        )


class RemoveTreeSafelyContainment(ContainmentCase):
    def remove(self, path):
        evidence = {}
        status = CL.remove_tree_safely(path, self.run_root, evidence)
        return status, evidence

    # --- positive control: the reported reproduction ---------------------

    def test_intermediate_symlink_stops_removal_and_keeps_outside(self):
        (self.run_root / "link").symlink_to(self.outside)
        target = self.run_root / "link" / "victim"

        status, evidence = self.remove(target)

        self.assertEqual(status, "unknown")
        self.assert_outside_intact()
        self.assertTrue(os.path.lexists(self.run_root / "link"))
        self.assertEqual(
            evidence["symlinkComponents"], [str(self.run_root / "link")]
        )
        self.assertEqual(evidence["reason"], "symlink_in_removal_path")

    def test_target_symlink_stops_removal_and_is_left_in_place(self):
        link = self.run_root / "claude"
        link.symlink_to(self.outside / "victim")

        status, evidence = self.remove(link)

        self.assertEqual(status, "unknown")
        self.assertTrue(os.path.lexists(link))
        self.assert_outside_intact()
        self.assertEqual(evidence["symlinkComponents"], [str(link)])

    def test_dangling_symlink_also_stops_removal(self):
        link = self.run_root / "home"
        link.symlink_to(self.base / "nowhere")
        status, evidence = self.remove(link)
        self.assertEqual(status, "unknown")
        self.assertTrue(os.path.lexists(link))
        self.assertEqual(evidence["symlinkComponents"], [str(link)])

    def test_run_root_symlink_stops_removal(self):
        real_root = self.base / "real-root"
        (real_root / "xdg").mkdir(parents=True)
        linked_root = self.base / "linked-root"
        linked_root.symlink_to(real_root)

        evidence = {}
        status = CL.remove_tree_safely(
            linked_root / "xdg", linked_root, evidence
        )
        self.assertEqual(status, "unknown")
        self.assertEqual(evidence["symlinkComponents"], [str(linked_root)])
        self.assertTrue((real_root / "xdg").is_dir())

    def test_every_symlink_on_the_path_is_reported(self):
        inner = self.base / "inner"
        (inner / "deeper").mkdir(parents=True)
        (inner / "deeper" / "victim").mkdir()
        (self.run_root / "a").symlink_to(inner)
        status, evidence = self.remove(
            self.run_root / "a" / "deeper" / "victim"
        )
        self.assertEqual(status, "unknown")
        self.assertEqual(
            evidence["symlinkComponents"], [str(self.run_root / "a")]
        )
        self.assertTrue((inner / "deeper" / "victim").is_dir())

    def test_resolved_escape_without_visible_symlink_stops_removal(self):
        # Secondary check: even if the lstat walk saw no symlink (e.g. the
        # tree changed between checks), the resolved target must still be
        # inside the resolved run root.
        target = self.run_root / "xdg"
        target.mkdir()
        real_resolve = Path.resolve

        def fake_resolve(self_path, *args, **kwargs):
            if self_path == target:
                return (self.outside / "victim").resolve()
            return real_resolve(self_path, *args, **kwargs)

        with mock.patch.object(Path, "resolve", fake_resolve):
            status, evidence = self.remove(target)
        self.assertEqual(status, "unknown")
        self.assertEqual(evidence["reason"], "resolved_path_escapes_run_root")
        self.assertTrue(target.is_dir())
        self.assert_outside_intact()

    def test_lstat_error_is_unknown_not_pass(self):
        target = self.run_root / "xdg"
        target.mkdir()
        real_lstat = os.lstat

        def failing_lstat(path, *args, **kwargs):
            if Path(path) == target:
                raise PermissionError("denied")
            return real_lstat(path, *args, **kwargs)

        with mock.patch.object(CL.os, "lstat", failing_lstat):
            status, evidence = self.remove(target)
        self.assertEqual(status, "unknown")
        self.assertTrue(evidence["reason"].startswith("lstat_failed:"))
        self.assertTrue(target.is_dir())

    # --- negative control: ordinary trees are still removed --------------

    def test_ordinary_tree_inside_run_root_is_removed(self):
        target = self.run_root / "xdg" / "config"
        (target / "sub").mkdir(parents=True)
        (target / "sub" / "f").write_text("x")
        status, evidence = self.remove(target)
        self.assertEqual(status, "pass")
        self.assertFalse(os.path.lexists(target))
        self.assertTrue((self.run_root / "xdg").is_dir())
        self.assertEqual(evidence["symlinkComponents"], [])

    def test_symlink_inside_target_is_removed_without_following(self):
        # A link *below* the target is deleted by rmtree as a link; its
        # destination is untouched. This is not a stop condition.
        target = self.run_root / "claude"
        target.mkdir()
        (target / "escape").symlink_to(self.outside)
        status, _ = self.remove(target)
        self.assertEqual(status, "pass")
        self.assertFalse(os.path.lexists(target))
        self.assert_outside_intact()

    def test_regular_file_and_absent_paths(self):
        single = self.run_root / "file"
        single.write_text("x")
        self.assertEqual(self.remove(single)[0], "pass")
        self.assertFalse(single.exists())
        self.assertEqual(self.remove(self.run_root / "absent")[0], "pass")
        self.assertEqual(
            self.remove(self.run_root / "absent" / "deeper")[0], "pass"
        )

    def test_lexical_escape_and_run_root_itself_still_fail(self):
        self.assertEqual(self.remove(self.outside)[0], "fail")
        self.assert_outside_intact()
        self.assertEqual(self.remove(self.run_root)[0], "fail")
        self.assertTrue(self.run_root.is_dir())

    def test_evidence_argument_is_optional(self):
        target = self.run_root / "xdg"
        target.mkdir()
        self.assertEqual(CL.remove_tree_safely(target, self.run_root), "pass")


class CleanupRunContainment(ContainmentCase):
    """cleanup_run stops, keeps leftovers and reports them as unknown."""

    TEAM = "agmsg-g4gate-400"

    def setUp(self):
        super().setUp()
        self.gate_repo = self.run_root / "repo"
        (self.gate_repo / "scripts").mkdir(parents=True)
        self.paths = {
            "gate_home": self.run_root / "home",
            "xdg_config": self.run_root / "xdg" / "config",
            "xdg_cache": self.run_root / "xdg" / "cache",
            "xdg_data": self.run_root / "xdg" / "data",
            "xdg_state": self.run_root / "xdg" / "state",
            "claude_config": self.run_root / "claude",
        }
        for path in self.paths.values():
            path.mkdir(parents=True)
        self.artifact_dir = self.base / "artifacts"

    def args(self):
        return argparse.Namespace(
            run_id="run-400",
            run_root=str(self.run_root),
            gate_repo=str(self.gate_repo),
            artifact_dir=str(self.artifact_dir),
            gate_team=self.TEAM,
            **{key: str(value) for key, value in self.paths.items()},
        )

    def run_cleanup(self):
        i1 = mock.Mock()
        i1.sanitize_env.return_value = {"PATH": os.environ["PATH"]}
        with mock.patch.object(CL, "load_module", return_value=i1), \
                mock.patch.object(
                    CL, "terminate_owned_processes",
                    return_value=("pass", []),
                ), \
                mock.patch.object(CL, "process_table", return_value=[]), \
                mock.patch.object(
                    CL, "release_message_claims",
                    return_value=("pass", []),
                ), \
                mock.patch.object(
                    CL, "reset_registrations",
                    return_value=("pass", []),
                ):
            rc = CL.cleanup_run(self.args())
        return rc, json.loads((self.artifact_dir / "cleanup.json").read_text())

    def test_symlink_planted_in_run_root_stops_cleanup_with_unknown(self):
        # A pilot process replaces an intermediate directory with a link.
        os.rmdir(self.paths["xdg_config"])
        os.rmdir(self.paths["xdg_cache"])
        os.rmdir(self.paths["xdg_data"])
        os.rmdir(self.paths["xdg_state"])
        os.rmdir(self.run_root / "xdg")
        (self.outside / "config").mkdir()
        (self.outside / "config" / "data").write_text("keep")
        (self.run_root / "xdg").symlink_to(self.outside)

        rc, cleanup = self.run_cleanup()

        self.assertEqual(rc, 2)
        self.assertEqual(cleanup["status"], "unknown")
        self.assertEqual((self.outside / "config" / "data").read_text(),
                         "keep")
        self.assert_outside_intact()
        self.assertEqual(
            cleanup["steps"]["xdgConfig"]["verdict"], "unknown"
        )
        self.assertEqual(
            cleanup["steps"]["xdgConfig"]["symlinkComponents"],
            [str(self.run_root / "xdg")],
        )
        # Stop and keep: the run root is not removed either.
        self.assertTrue(self.run_root.is_dir())
        self.assertEqual(cleanup["steps"]["runRoot"]["verdict"], "unknown")
        self.assertEqual(
            cleanup["steps"]["runRoot"]["reason"],
            "cleanup_stopped_on_symlink",
        )
        self.assertIs(cleanup["incomplete"], True)
        self.assertIn(str(self.run_root), cleanup["remainingPaths"])
        self.assertIn(str(self.paths["xdg_config"]),
                      cleanup["remainingPaths"])
        # Unaffected steps still ran normally.
        self.assertEqual(cleanup["steps"]["gateHome"]["verdict"], "pass")
        self.assertFalse(self.paths["gate_home"].exists())

    def test_ordinary_run_root_is_fully_removed(self):
        (self.paths["claude_config"] / "projects").mkdir()
        (self.paths["claude_config"] / "projects" / "t.jsonl").write_text("t")

        rc, cleanup = self.run_cleanup()

        self.assertEqual(rc, 0)
        self.assertEqual(cleanup["status"], "pass")
        self.assertFalse(os.path.lexists(self.run_root))
        self.assertIs(cleanup["incomplete"], False)
        self.assertEqual(cleanup["remainingPaths"], [])
        self.assert_outside_intact()

    def test_symlinked_run_root_is_not_followed(self):
        # The whole run root is a link to a real directory: nothing under
        # the real directory may be removed.
        real_root = self.base / "real-run"
        self.run_root.rename(real_root)
        self.run_root.symlink_to(real_root)

        rc, cleanup = self.run_cleanup()

        self.assertEqual(rc, 2)
        self.assertEqual(cleanup["status"], "unknown")
        for relative in ("home", "claude", "xdg/config", "repo/scripts"):
            self.assertTrue((real_root / relative).is_dir(), relative)
        self.assertTrue(os.path.islink(self.run_root))
        self.assertEqual(
            cleanup["steps"]["runRoot"]["symlinkComponents"],
            [str(self.run_root)],
        )
        for name in ("gateHome", "claudeConfig", "xdgConfig", "repository"):
            self.assertEqual(cleanup["steps"][name]["verdict"], "unknown")

    def test_fail_elsewhere_is_not_hidden_by_symlink_unknown(self):
        (self.run_root / "claude").rmdir()
        (self.run_root / "claude").symlink_to(self.outside)
        i1 = mock.Mock()
        i1.sanitize_env.return_value = {}
        with mock.patch.object(CL, "load_module", return_value=i1), \
                mock.patch.object(
                    CL, "terminate_owned_processes",
                    return_value=("fail", [{"pid": 1}]),
                ), \
                mock.patch.object(CL, "process_table", return_value=[]), \
                mock.patch.object(
                    CL, "release_message_claims",
                    return_value=("pass", []),
                ), \
                mock.patch.object(
                    CL, "reset_registrations",
                    return_value=("pass", []),
                ):
            rc = CL.cleanup_run(self.args())
        cleanup = json.loads((self.artifact_dir / "cleanup.json").read_text())
        self.assertEqual(rc, 1)
        self.assertEqual(cleanup["status"], "fail")
        self.assertIs(cleanup["incomplete"], True)
        self.assert_outside_intact()


class EvaluateRunReportsIncompleteCleanup(ContainmentCase):
    def write_results(self, cleanup):
        artifact_dir = self.base / "artifacts"
        for check in CL.CHECKS:
            path = artifact_dir / check / "result.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"verdict": "pass"}))
        live = artifact_dir / "live-pm" / "result.json"
        live.parent.mkdir(parents=True)
        live.write_text(json.dumps({"verdict": "pass"}))
        (artifact_dir / "cleanup.json").write_text(json.dumps(cleanup))
        return artifact_dir

    def evaluate(self, artifact_dir):
        args = argparse.Namespace(
            artifact_dir=str(artifact_dir),
            execution_status=0,
            run_id="run-400",
            requested_check="all",
        )
        rc = CL.evaluate_run(args)
        return rc, json.loads((artifact_dir / "results.json").read_text())

    def test_incomplete_cleanup_is_reported_in_gate_results_as_unknown(self):
        remaining = [str(self.run_root), str(self.run_root / "xdg" / "config")]
        artifact_dir = self.write_results({
            "status": "unknown",
            "incomplete": True,
            "remainingPaths": remaining,
        })
        rc, results = self.evaluate(artifact_dir)
        self.assertEqual(rc, 2)
        self.assertEqual(results["verdict"], "unknown")
        self.assertIs(results["pilot_ready"], False)
        self.assertIs(results["cleanupIncomplete"], True)
        self.assertEqual(results["cleanupRemainingPaths"], remaining)
        self.assertIn(
            {
                "check": "cleanup",
                "reason": "cleanup_incomplete",
                "evidence": "cleanup.json",
            },
            results["unknown"],
        )

    def test_complete_cleanup_reports_no_remaining_paths(self):
        artifact_dir = self.write_results({
            "status": "pass",
            "incomplete": False,
            "remainingPaths": [],
        })
        rc, results = self.evaluate(artifact_dir)
        self.assertEqual(rc, 0)
        self.assertIs(results["cleanupIncomplete"], False)
        self.assertEqual(results["cleanupRemainingPaths"], [])

    def test_missing_incomplete_field_is_not_read_as_complete(self):
        artifact_dir = self.write_results({"status": "pass"})
        rc, results = self.evaluate(artifact_dir)
        # A cleanup record that does not say whether it completed cannot
        # prove completion.
        self.assertIsNone(results["cleanupIncomplete"])
        self.assertEqual(rc, 2)
        self.assertEqual(results["verdict"], "unknown")


if __name__ == "__main__":
    unittest.main()
