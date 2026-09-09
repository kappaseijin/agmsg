"""Contract tests for Issue #379's fork-local B1/B2 provider fixture."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "issue379", ROOT / "scripts/issue379_provider_pin.py")
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


class Issue379ProviderPinTests(unittest.TestCase):
    def test_manifest_requires_the_fork_repository_schema_and_full_commit(self):
        manifest = HARNESS.load_manifest(ROOT / HARNESS.MANIFEST)
        self.assertEqual(manifest["repository"], "kappaseijin/agmsg")
        self.assertEqual(manifest["contractSchemaVersion"], 1)
        self.assertRegex(manifest["commit"], r"^[0-9a-f]{40}$")
        self.assertIn("canonicalRoot", manifest)

    def test_manifest_rejects_short_sha_unknown_schema_and_other_root(self):
        manifest = HARNESS.load_manifest(ROOT / HARNESS.MANIFEST)
        for field, value in (("commit", manifest["commit"][:12]),
                             ("commit", "0" * 40),
                             ("contractSchemaVersion", 2),
                             ("canonicalRoot", "/tmp/not-provider")):
            bad = dict(manifest)
            bad[field] = value
            with self.assertRaises(HARNESS.ProviderError):
                HARNESS.validate_manifest(bad, ROOT)

    def test_fixture_reports_b1_b2_controls_and_read_only_snapshots(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "artifact"
            report = HARNESS.run_fixture(ROOT, output)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["provider"]["commit"],
                             HARNESS.load_manifest(ROOT / HARNESS.MANIFEST)["commit"])
            self.assertEqual(report["consumer"]["providerCommit"], report["provider"]["commit"])
            self.assertRegex(report["reviewHead"], r"^[0-9a-f]{40}$")
            self.assertEqual(report["readOnly"]["before"], report["readOnly"]["after"])
            self.assertEqual(report["b1"]["negative"], "pass")
            self.assertEqual(report["b2"]["negative"], "pass")
            self.assertTrue((output / "report.json").is_file())
            self.assertEqual(json.loads((output / "report.json").read_text())["status"], "pass")

    def test_read_only_snapshot_detects_a_provider_code_change(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = Path(temp)
            source = provider / "scripts" / "api.sh"
            source.parent.mkdir()
            source.write_text("#!/usr/bin/env bash\nexit 0\n")
            before = HARNESS._snapshot(provider)
            source.write_text("#!/usr/bin/env bash\nexit 1\n")
            self.assertNotEqual(before, HARNESS._snapshot(provider))


if __name__ == "__main__":
    unittest.main()
