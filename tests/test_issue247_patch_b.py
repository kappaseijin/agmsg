#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "issue247_patch_b", ROOT / "scripts/issue247_patch_b.py")
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


class PatchBUnitTests(unittest.TestCase):
    def test_patch_contains_only_fixed_boundary(self):
        text = (ROOT / "patches/issue247-patch-b.patch").read_text()
        self.assertEqual(HARNESS.patch_files(text), HARNESS.EXPECTED_FILES)

    def test_patch_does_not_import_b1_registration_api(self):
        text = (ROOT / "patches/issue247-patch-b.patch").read_text()
        self.assertNotIn("api-registrations.sh", text)
        self.assertNotIn("get_registrations", text)

    def test_all_deferred_mutations_are_named(self):
        source = Path(HARNESS.__file__).read_text()
        for name in (
            "remove_fingerprint_recheck",
            "nonregular_to_absent",
            "follow_concurrent_owner",
        ):
            self.assertIn(name, source)


if __name__ == "__main__":
    unittest.main()
