"""Live-PM negative control: deny-semantic-before-after (runbook §36).

The semantic comparison used to look only at files present on both
sides, so a deny/semantic file that disappeared (or appeared) after the
change went unnoticed as long as another common file still matched.
The file sets are now compared first: a different set is a mutation.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLEANUP_HELPER = Path(
    os.environ.get(
        "PILOT_GATE_CLEANUP_UNDER_TEST",
        ROOT / "scripts" / "lib" / "pilot-gate-cleanup.py",
    )
).resolve()

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

BASE = {
    "guard.sha256": "digest-1",
    "deny-exit": "2",
    "deny-response.json": json.dumps({"decision": "deny"}),
    "semantic.txt": "blocked by guard",
}


class LivePmSemanticSet(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.artifact = Path(self._tmp.name) / "art"

    def tearDown(self):
        for path in self.artifact.rglob("*"):
            if not path.is_symlink():
                path.chmod(0o700)
        self._tmp.cleanup()

    def side(self, name, files):
        directory = self.artifact / "live-pm" / name
        directory.mkdir(parents=True)
        for relative, content in files.items():
            (directory / relative).write_text(content)
        return directory

    def compare(self, before, after):
        if before is not None:
            self.side("before", before)
        if after is not None:
            self.side("after", after)
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

    def test_identical_sets_and_content_pass(self):
        rc, result, semantic = self.compare(BASE, BASE)
        self.assertEqual(rc, 0)
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(semantic["verdict"], "pass")
        self.assertEqual(semantic["missingAfter"], [])
        self.assertEqual(semantic["addedAfter"], [])

    def test_semantic_file_removed_after_is_fail(self):
        after = dict(BASE)
        del after["semantic.txt"]
        rc, result, semantic = self.compare(BASE, after)
        self.assertEqual(semantic["verdict"], "fail")
        self.assertEqual(semantic["reason"], "semantic_file_set_changed")
        self.assertEqual(semantic["missingAfter"], ["semantic.txt"])
        self.assertEqual(semantic["addedAfter"], [])
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(rc, 1)

    def test_semantic_file_added_after_is_fail(self):
        after = dict(BASE, **{"decision-extra.txt": "allow"})
        rc, _, semantic = self.compare(BASE, after)
        self.assertEqual(semantic["verdict"], "fail")
        self.assertEqual(semantic["addedAfter"], ["decision-extra.txt"])
        self.assertEqual(semantic["missingAfter"], [])
        self.assertEqual(rc, 1)

    def test_changed_content_is_fail(self):
        after = dict(BASE, **{"deny-response.json":
                              json.dumps({"decision": "allow"})})
        rc, _, semantic = self.compare(BASE, after)
        self.assertEqual(semantic["verdict"], "fail")
        self.assertEqual(semantic["reason"], "semantic_content_changed")
        self.assertEqual(rc, 1)

    def test_volatile_only_change_is_pass(self):
        after = dict(BASE, **{"deny-response.json":
                              json.dumps({"decision": "deny", "pid": 9})})
        _, _, semantic = self.compare(BASE, after)
        self.assertEqual(semantic["verdict"], "pass")

    def test_unreadable_file_with_same_set_is_unknown(self):
        self.side("before", BASE)
        after_dir = self.side("after", BASE)
        (after_dir / "semantic.txt").chmod(0o000)
        rc = CL.compare_live_pm(
            argparse.Namespace(artifact_dir=str(self.artifact), after_status=0)
        )
        result = json.loads(
            (self.artifact / "live-pm" / "result.json").read_text()
        )
        semantic = [c for c in result["checks"]
                    if c["name"] == "deny-semantic-before-after"][0]
        self.assertEqual(semantic["verdict"], "unknown")
        self.assertEqual(semantic["reason"], "semantic_file_unreadable")
        self.assertEqual(semantic["unreadable"], ["after/semantic.txt"])
        self.assertEqual(rc, 2)

    def test_set_change_wins_over_unreadable(self):
        self.side("before", BASE)
        after = dict(BASE)
        del after["deny-response.json"]
        after_dir = self.side("after", after)
        (after_dir / "semantic.txt").chmod(0o000)
        CL.compare_live_pm(
            argparse.Namespace(artifact_dir=str(self.artifact), after_status=0)
        )
        result = json.loads(
            (self.artifact / "live-pm" / "result.json").read_text()
        )
        semantic = [c for c in result["checks"]
                    if c["name"] == "deny-semantic-before-after"][0]
        self.assertEqual(semantic["verdict"], "fail")
        self.assertEqual(semantic["missingAfter"], ["deny-response.json"])

    def test_nothing_observed_is_unknown(self):
        bare = {"guard.sha256": "digest-1", "deny-exit": "2"}
        _, _, semantic = self.compare(bare, bare)
        self.assertEqual(semantic["verdict"], "unknown")
        self.assertEqual(semantic["reason"], "semantic_artifact_missing")

    def test_missing_after_directory_is_unknown_not_fail(self):
        _, _, semantic = self.compare(BASE, None)
        self.assertEqual(semantic["verdict"], "unknown")
        self.assertEqual(semantic["reason"], "semantic_artifact_missing")

    def test_all_semantic_files_gone_after_is_fail(self):
        bare = {"guard.sha256": "digest-1", "deny-exit": "2"}
        _, _, semantic = self.compare(BASE, bare)
        self.assertEqual(semantic["verdict"], "fail")
        self.assertEqual(
            semantic["missingAfter"], ["deny-response.json", "semantic.txt"]
        )

    def test_semantic_candidates_reports_unreadable_files(self):
        directory = self.side("x", {"deny.txt": "a", "response.txt": "b",
                                    "notes.txt": "c"})
        (directory / "response.txt").chmod(0o000)
        values, unreadable = CL.semantic_candidates(directory)
        self.assertEqual(values, {"deny.txt": "a"})
        self.assertEqual(unreadable, ["response.txt"])


if __name__ == "__main__":
    unittest.main()
