"""Behavioral contract tests for Issue #383's fork-local B3 provider gate."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "issue383", ROOT / "scripts/issue383_provider_contract.py")
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


class Issue383ProviderContractTests(unittest.TestCase):
    def contract(self):
        return {
            "providerCommit": "a" * 40,
            "requiredCapabilities": [
                "message-peek-v1", "message-claim-v1", "message-release-v1",
                "message-ack-v1", "message-send-v1", "handoff-receipt-v1"],
            "capabilities": {
                "message-peek-v1": {"argv": ["get", "messages", "--recipient", "pilot"], "ids": ["messageId"]},
                "message-claim-v1": {"argv": ["claim", "message-1", "owner-1"], "ids": ["messageId", "owner"]},
                "message-release-v1": {"argv": ["release", "message-1", "owner-1"], "ids": ["messageId", "owner"]},
                "message-ack-v1": {"argv": ["ack", "message-1", "owner-1", "receipt-1"], "ids": ["messageId", "owner", "receiptId"]},
                "message-send-v1": {"argv": ["send", "worker"], "ids": ["messageId", "requestId"]},
                "handoff-receipt-v1": {"argv": ["receipt", "request-1"], "ids": ["requestId", "inputMessageId", "delegateMessageId", "receiptId"]},
            },
        }

    def response(self, capability):
        values = {
            "message-peek-v1": {"messageId": "message-1", "recipient": "pilot", "state": "ok"},
            "message-claim-v1": {"messageId": "message-1", "owner": "owner-1", "state": "claimed"},
            "message-release-v1": {"messageId": "message-1", "owner": "owner-1", "state": "released"},
            "message-ack-v1": {"messageId": "message-1", "owner": "owner-1", "receiptId": "receipt-1", "state": "acked"},
            "message-send-v1": {"messageId": "delegate-1", "requestId": "request-1", "state": "queued"},
            "handoff-receipt-v1": {"requestId": "request-1", "inputMessageId": "message-1", "delegateMessageId": "delegate-1", "receiptId": "receipt-1", "state": "recorded"},
        }
        return values[capability]

    def test_accepts_exactly_the_six_declared_capabilities(self):
        contract = self.contract()
        HARNESS.validate_contract(contract)
        self.assertEqual(set(contract["requiredCapabilities"]), set(contract["capabilities"]))
        self.assertNotIn("history", contract["requiredCapabilities"])

    def test_real_manifest_binds_the_six_capabilities_to_the_provider_facade(self):
        manifest = json.loads((ROOT / "docs/decisions/issue383-capability-manifest.json").read_text())
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertRegex(manifest["providerCommit"], r"^[0-9a-f]{40}$")
        self.assertEqual(manifest["command"], "scripts/p2-provider.sh")
        self.assertTrue((ROOT / manifest["command"]).is_file())
        self.assertEqual(set(manifest["requiredCapabilities"]), set(manifest["capabilities"]))
        self.assertEqual(len(manifest["requiredCapabilities"]), 6)
        self.assertEqual(manifest["notUsedCapabilities"], ["history"])
        for capability in manifest["requiredCapabilities"]:
            item = manifest["capabilities"][capability]
            self.assertEqual(item["argv"][0], capability.removesuffix("-v1"))
            self.assertIn("success", item)
            self.assertIn("failure", item)

    def test_rejects_undeclared_capability_and_provider_pin_mismatch(self):
        contract = self.contract()
        contract["requiredCapabilities"].append("history")
        with self.assertRaises(HARNESS.ContractError):
            HARNESS.validate_contract(contract)
        contract = self.contract()
        with self.assertRaises(HARNESS.ContractError):
            HARNESS.validate_provider_pin(contract, "b" * 40)

    def test_rejects_schema_scope_id_and_unknown_mutations(self):
        contract = self.contract()
        for capability in contract["requiredCapabilities"]:
            good = self.response(capability)
            HARNESS.validate_response(contract, capability, good)
            for field, bad in (("state", "unknown"), (contract["capabilities"][capability]["ids"][0], "swapped-id")):
                mutated = dict(good)
                mutated[field] = bad
                with self.assertRaises(HARNESS.ContractError, msg=f"{capability}:{field}"):
                    HARNESS.validate_response(contract, capability, mutated)

    def test_runner_rejects_provider_nonzero_and_malformed_json(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = Path(temp)
            command = provider / "capability.sh"
            command.write_text("#!/bin/sh\nprintf '%s\\n' not-json\n", encoding="utf8")
            command.chmod(0o755)
            with self.assertRaises(HARNESS.ContractError):
                HARNESS.invoke(command, ["get"])

    def test_runner_executes_only_manifest_fixed_argv(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = Path(temp) / "capability.py"
            provider.write_text('''#!/usr/bin/env python3
import json, sys
argv = sys.argv[1:]
responses = {
    ("get", "messages", "--recipient", "pilot"): {"messageId":"message-1","recipient":"pilot","state":"ok"},
    ("claim", "message-1", "owner-1"): {"messageId":"message-1","owner":"owner-1","state":"claimed"},
    ("release", "message-1", "owner-1"): {"messageId":"message-1","owner":"owner-1","state":"released"},
    ("ack", "message-1", "owner-1", "receipt-1"): {"messageId":"message-1","owner":"owner-1","receiptId":"receipt-1","state":"acked"},
    ("send", "worker"): {"messageId":"delegate-1","requestId":"request-1","state":"queued"},
    ("receipt", "request-1"): {"requestId":"request-1","inputMessageId":"message-1","delegateMessageId":"delegate-1","receiptId":"receipt-1","state":"recorded"},
}
value = responses.get(tuple(argv))
if value is None: sys.exit(9)
print(json.dumps(value))
''', encoding="utf8")
            provider.chmod(0o755)
            results = HARNESS.run_contract(provider, self.contract(), "a" * 40)
            self.assertEqual(set(results), set(self.contract()["requiredCapabilities"]))
            contract = self.contract()
            contract["capabilities"]["message-ack-v1"]["argv"][2] = "owner-other"
            with self.assertRaises(HARNESS.ContractError):
                HARNESS.run_contract(provider, contract, "a" * 40)


if __name__ == "__main__":
    unittest.main()
