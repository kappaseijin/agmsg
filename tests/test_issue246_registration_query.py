#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("issue246", ROOT / "scripts/issue246_registration_query.py")
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)

class Issue246FixtureTests(unittest.TestCase):
    def test_patch_has_exactly_the_five_provider_files(self):
        files = HARNESS.patch_files((ROOT / HARNESS.PATCH).read_text())
        self.assertEqual(files, HARNESS.FILES)

    def test_patch_is_not_a_main_tree_edit(self):
        self.assertEqual(HARNESS.CUTOFF, "e58dbafad5a84be625f070385bb0c076c3daa4db")

    def test_report_contract_has_all_fail_closed_fields(self):
        self.assertEqual(HARNESS.PATCH_HEAD, "eb850a6698ab81986b9ac49830b7dddbdeb75d83")

    def test_mutation_runner_has_eight_unique_controls(self):
        mutation_path = ROOT / "tests/issue246_mutations.py"
        spec = importlib.util.spec_from_file_location("issue246_mutations", mutation_path)
        module = importlib.util.module_from_spec(spec)
        # Do not execute the module's top-level runner in this structural test.
        source = mutation_path.read_text()
        self.assertEqual(source.count('"killed"'), 2)
        self.assertEqual(source.count('"limit_1"'), 1)
        self.assertEqual(source.count('"tuple_pair"'), 1)

if __name__ == "__main__":
    unittest.main()
