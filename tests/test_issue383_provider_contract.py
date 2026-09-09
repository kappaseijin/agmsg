"""Behavioral contract tests for Issue #383's fork-local B3 provider gate."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

SPEC = importlib.util.spec_from_file_location(
    "issue383",
    ROOT / "scripts" / "issue383_provider_contract.py",
)
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)

MANIFEST = (
    ROOT
    / "docs"
    / "decisions"
    / "issue383-capability-manifest.json"
)

G1_MANIFEST = (
    ROOT
    / "docs"
    / "decisions"
    / "issue379-provider-manifest.json"
)


class Issue383ProviderContractTests(unittest.TestCase):
    def contract(self):
        return json.loads(
            MANIFEST.read_text(encoding="utf8")
        )

    def bindings(self, capability):
        values = {
            "message-peek-v1": {
                "team": "demo",
                "recipient": "pilot",
            },
            "message-claim-v1": {
                "team": "demo",
                "messageId": "message-1",
                "owner": "owner-1",
            },
            "message-release-v1": {
                "team": "demo",
                "messageId": "message-1",
                "owner": "owner-1",
            },
            "message-ack-v1": {
                "team": "demo",
                "messageId": "message-1",
                "owner": "owner-1",
                "receiptId": "receipt-1",
            },
            "message-send-v1": {
                "team": "demo",
                "from": "pilot",
                "recipient": "worker",
                "requestId": "request-1",
                "body": "task",
            },
            "handoff-receipt-v1": {
                "team": "demo",
                "inputMessageId": "message-1",
                "requestId": "request-1",
                "delegateMessageId": "delegate-1",
            },
        }
        return values[capability]

    def response(self, capability):
        values = {
            "message-peek-v1": {
                "schemaVersion": 1,
                "state": "ok",
                "messageId": "message-1",
                "from": "worker",
                "to": "pilot",
                "body": "result",
                "createdAt": "2026-09-10T00:00:00Z",
            },
            "message-claim-v1": {
                "schemaVersion": 1,
                "state": "claimed",
                "messageId": "message-1",
                "owner": "owner-1",
            },
            "message-release-v1": {
                "schemaVersion": 1,
                "state": "released",
                "messageId": "message-1",
                "owner": "owner-1",
            },
            "message-ack-v1": {
                "schemaVersion": 1,
                "state": "acked",
                "messageId": "message-1",
                "owner": "owner-1",
                "receiptId": "receipt-1",
            },
            "message-send-v1": {
                "schemaVersion": 1,
                "state": "queued",
                "messageId": "delegate-1",
                "requestId": "request-1",
                "team": "demo",
                "from": "pilot",
                "to": "worker",
            },
            "handoff-receipt-v1": {
                "schemaVersion": 1,
                "state": "recorded",
                "inputMessageId": "message-1",
                "requestId": "request-1",
                "delegateMessageId": "delegate-1",
                "receiptId": "receipt-1",
                "team": "demo",
            },
        }
        return values[capability]

    def test_real_manifest_is_the_contract_and_matches_g1_provider_pin(self):
        contract = self.contract()

        HARNESS.validate_contract(contract)

        g1 = json.loads(
            G1_MANIFEST.read_text(encoding="utf8")
        )
        HARNESS.validate_provider_pin(
            contract,
            g1["commit"],
        )

        self.assertEqual(
            contract["command"],
            "scripts/p2-provider.sh",
        )
        self.assertEqual(
            len(contract["requiredCapabilities"]),
            6,
        )
        self.assertEqual(
            set(contract["requiredCapabilities"]),
            set(contract["capabilities"]),
        )
        self.assertEqual(
            contract["notUsedCapabilities"],
            ["history"],
        )

        for capability in contract["requiredCapabilities"]:
            item = contract["capabilities"][capability]

            self.assertTrue(item["ids"])
            self.assertEqual(
                item["argv"][0],
                capability.removesuffix("-v1"),
            )
            self.assertEqual(
                item["exitStatus"],
                {
                    "json": 0,
                    "error": [1, 2],
                },
            )

    def test_rejects_missing_ids_placeholder_drift_and_provider_pin_mismatch(
        self,
    ):
        contract = self.contract()
        del contract["capabilities"]["message-send-v1"]["ids"]

        with self.assertRaises(HARNESS.ContractError):
            HARNESS.validate_contract(contract)

        contract = self.contract()
        contract["capabilities"]["message-send-v1"]["argv"][2] = (
            "<arbitraryFrom>"
        )

        with self.assertRaises(HARNESS.ContractError):
            HARNESS.validate_contract(contract)

        with self.assertRaises(HARNESS.ContractError):
            HARNESS.validate_provider_pin(
                self.contract(),
                "b" * 40,
            )

    def test_rejects_manifest_without_exact_required_capability_set(self):
        contract = self.contract()
        contract["requiredCapabilities"].append("history")

        with self.assertRaises(HARNESS.ContractError):
            HARNESS.validate_contract(contract)

        contract = self.contract()
        del contract["capabilities"]["message-ack-v1"]

        with self.assertRaises(HARNESS.ContractError):
            HARNESS.validate_contract(contract)

    def test_expand_argv_uses_three_argument_signature_and_real_placeholders(
        self,
    ):
        contract = self.contract()

        expected = {
            "message-peek-v1": [
                "message-peek",
                "demo",
                "pilot",
            ],
            "message-claim-v1": [
                "message-claim",
                "demo",
                "message-1",
                "owner-1",
            ],
            "message-release-v1": [
                "message-release",
                "demo",
                "message-1",
                "owner-1",
            ],
            "message-ack-v1": [
                "message-ack",
                "demo",
                "message-1",
                "owner-1",
                "receipt-1",
            ],
            "message-send-v1": [
                "message-send",
                "demo",
                "pilot",
                "worker",
                "request-1",
                "task",
            ],
            "handoff-receipt-v1": [
                "handoff-receipt",
                "demo",
                "message-1",
                "request-1",
                "delegate-1",
            ],
        }

        for capability in contract["requiredCapabilities"]:
            argv = HARNESS.expand_argv(
                contract,
                capability,
                self.bindings(capability),
            )
            self.assertEqual(
                argv,
                expected[capability],
            )

    def test_expand_argv_rejects_missing_and_unused_bindings(self):
        contract = self.contract()

        with self.assertRaises(HARNESS.ContractError):
            HARNESS.expand_argv(
                contract,
                "message-claim-v1",
                {
                    "team": "demo",
                    "messageId": "message-1",
                },
            )

        bindings = self.bindings("message-claim-v1")
        bindings["recipient"] = "pilot"

        with self.assertRaises(HARNESS.ContractError):
            HARNESS.expand_argv(
                contract,
                "message-claim-v1",
                bindings,
            )

    def test_validate_response_accepts_all_six_provider_response_shapes(self):
        contract = self.contract()

        for capability in contract["requiredCapabilities"]:
            HARNESS.validate_response(
                contract,
                capability,
                self.response(capability),
                self.bindings(capability),
            )

    def test_validate_response_rejects_scope_and_id_correspondence_mutations(
        self,
    ):
        contract = self.contract()

        mutations = {
            "message-peek-v1": (
                "to",
                "other-recipient",
            ),
            "message-claim-v1": (
                "owner",
                "owner-other",
            ),
            "message-release-v1": (
                "messageId",
                "message-other",
            ),
            "message-ack-v1": (
                "receiptId",
                "receipt-other",
            ),
            "message-send-v1": (
                "requestId",
                "request-other",
            ),
            "handoff-receipt-v1": (
                "delegateMessageId",
                "delegate-other",
            ),
        }

        for capability in contract["requiredCapabilities"]:
            field, bad_value = mutations[capability]

            mutated = copy.deepcopy(
                self.response(capability)
            )
            mutated[field] = bad_value

            with self.assertRaises(
                HARNESS.ContractError,
                msg=f"{capability}:{field}",
            ):
                HARNESS.validate_response(
                    contract,
                    capability,
                    mutated,
                    self.bindings(capability),
                )

    def test_validate_response_rejects_unknown_and_wrong_schema(self):
        contract = self.contract()

        for capability in contract["requiredCapabilities"]:
            unknown = copy.deepcopy(
                self.response(capability)
            )
            unknown["state"] = "unknown"

            with self.assertRaises(
                HARNESS.ContractError,
                msg=f"{capability}:unknown",
            ):
                HARNESS.validate_response(
                    contract,
                    capability,
                    unknown,
                    self.bindings(capability),
                )

            wrong_schema = copy.deepcopy(
                self.response(capability)
            )
            wrong_schema["schemaVersion"] = 2

            with self.assertRaises(
                HARNESS.ContractError,
                msg=f"{capability}:schemaVersion",
            ):
                HARNESS.validate_response(
                    contract,
                    capability,
                    wrong_schema,
                    self.bindings(capability),
                )

    def test_validate_response_rejects_declared_failure_states(self):
        contract = self.contract()

        failures = {
            "message-peek-v1": "absent",
            "message-claim-v1": "busy",
            "message-release-v1": "owner_mismatch",
            "message-ack-v1": "receipt_missing",
            "message-send-v1": "unknown",
            "handoff-receipt-v1": "id_mismatch",
        }

        for capability, state in failures.items():
            response = {
                "schemaVersion": 1,
                "state": state,
            }

            with self.assertRaises(
                HARNESS.ContractError,
                msg=f"{capability}:{state}",
            ):
                HARNESS.validate_response(
                    contract,
                    capability,
                    response,
                    self.bindings(capability),
                )

    def test_invoke_rejects_malformed_json_multiple_objects_and_nonzero(self):
        with tempfile.TemporaryDirectory() as temp:
            command = Path(temp) / "provider.sh"

            command.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' not-json\n",
                encoding="utf8",
            )
            command.chmod(0o755)

            with self.assertRaises(HARNESS.ContractError):
                HARNESS.invoke(
                    command,
                    [
                        "message-peek",
                        "demo",
                        "pilot",
                    ],
                )

            command.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' "
                "'{\"schemaVersion\":1,\"state\":\"absent\"}'\n"
                "printf '%s\\n' "
                "'{\"schemaVersion\":1,\"state\":\"absent\"}'\n",
                encoding="utf8",
            )
            command.chmod(0o755)

            with self.assertRaises(HARNESS.ContractError):
                HARNESS.invoke(
                    command,
                    [
                        "message-peek",
                        "demo",
                        "pilot",
                    ],
                )

            command.write_text(
                "#!/bin/sh\n"
                "exit 7\n",
                encoding="utf8",
            )
            command.chmod(0o755)

            with self.assertRaises(HARNESS.ContractError):
                HARNESS.invoke(
                    command,
                    [
                        "message-peek",
                        "demo",
                        "pilot",
                    ],
                )

    def test_run_capability_uses_expanded_manifest_argv_and_validates_response(
        self,
    ):
        contract = self.contract()

        with tempfile.TemporaryDirectory() as temp:
            command = Path(temp) / "provider.py"

            command.write_text(
                """#!/usr/bin/env python3
import json
import sys

expected = [
    "message-send",
    "demo",
    "pilot",
    "worker",
    "request-1",
    "task",
]

if sys.argv[1:] != expected:
    sys.exit(9)

print(json.dumps({
    "schemaVersion": 1,
    "state": "queued",
    "messageId": "delegate-1",
    "requestId": "request-1",
    "team": "demo",
    "from": "pilot",
    "to": "worker",
}))
""",
                encoding="utf8",
            )
            command.chmod(0o755)

            response = HARNESS.run_capability(
                command,
                contract,
                "message-send-v1",
                self.bindings("message-send-v1"),
            )

            self.assertEqual(
                response["messageId"],
                "delegate-1",
            )
            self.assertEqual(
                response["requestId"],
                "request-1",
            )
            self.assertEqual(
                response["team"],
                "demo",
            )
            self.assertEqual(
                response["from"],
                "pilot",
            )
            self.assertEqual(
                response["to"],
                "worker",
            )

    def test_run_capability_rejects_provider_response_with_swapped_scope(self):
        contract = self.contract()

        with tempfile.TemporaryDirectory() as temp:
            command = Path(temp) / "provider.py"

            command.write_text(
                """#!/usr/bin/env python3
import json
import sys

expected = [
    "message-send",
    "demo",
    "pilot",
    "worker",
    "request-1",
    "task",
]

if sys.argv[1:] != expected:
    sys.exit(9)

print(json.dumps({
    "schemaVersion": 1,
    "state": "queued",
    "messageId": "delegate-1",
    "requestId": "request-1",
    "team": "demo",
    "from": "pilot",
    "to": "other-worker",
}))
""",
                encoding="utf8",
            )
            command.chmod(0o755)

            with self.assertRaises(HARNESS.ContractError):
                HARNESS.run_capability(
                    command,
                    contract,
                    "message-send-v1",
                    self.bindings("message-send-v1"),
                )


if __name__ == "__main__":
    unittest.main()
