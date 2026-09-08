#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "issue247_patch_a", ROOT / "scripts/issue247_patch_a.py")
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


class PatchAUnitTests(unittest.TestCase):
    def test_patch_shape_is_exactly_two_source_and_two_test_hunks(self):
        patch = (ROOT / "patches/issue247-patch-a.patch").read_text()
        self.assertEqual(
            HARNESS.patch_shape(patch),
            {"scripts/lib/actas-lock.sh": 2, "tests/test_actas_lock.bats": 2},
        )

    def test_patch_a_verdict_requires_red_green_and_no_side_effects(self):
        observed = HARNESS.expected_observation()
        self.assertEqual(HARNESS.patch_a_verdict(observed), "pass")
        for path, value in (
            (("red", "detected"), False),
            (("green", "focused_pass"), False),
            (("green", "side_effects_equal"), False),
            (("green", "empty"), "free"),
            (("green", "read_error"), "free"),
            (("green", "owner_swap_preserved"), False),
        ):
            candidate = HARNESS.expected_observation()
            candidate[path[0]][path[1]] = value
            self.assertEqual(HARNESS.patch_a_verdict(candidate), "incompatible")

    def test_scope_marks_snapshot_mutations_as_patch_b(self):
        scope = HARNESS.mutation_scope()
        self.assertEqual(scope["empty_to_absent"], "patch_a")
        self.assertEqual(scope["read_error_to_absent"], "patch_a")
        self.assertEqual(scope["stale_query_gc"], "patch_a")
        self.assertEqual(scope["sid_only_compare"], "patch_a")
        self.assertEqual(scope["remove_fingerprint_recheck"], "patch_b")
        self.assertEqual(scope["nonregular_to_absent"], "patch_b")
        self.assertEqual(scope["follow_concurrent_owner"], "patch_b")


if __name__ == "__main__":
    unittest.main()
