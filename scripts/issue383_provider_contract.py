#!/usr/bin/env python3
"""Fail-closed response validation for Issue #383's declared B3 capabilities."""
import json
import re
import subprocess

REQUIRED = {
    "message-peek-v1", "message-claim-v1", "message-release-v1",
    "message-ack-v1", "message-send-v1", "handoff-receipt-v1",
}


class ContractError(RuntimeError):
    pass


def validate_provider_pin(contract, provider_commit):
    if not re.fullmatch(r"[0-9a-f]{40}", provider_commit or ""):
        raise ContractError("provider commit")
    if contract.get("providerCommit") != provider_commit:
        raise ContractError("provider pin mismatch")


def validate_contract(contract):
    required = contract.get("requiredCapabilities")
    capabilities = contract.get("capabilities")
    if not isinstance(required, list) or set(required) != REQUIRED:
        raise ContractError("declared capabilities")
    if len(required) != len(set(required)) or not isinstance(capabilities, dict):
        raise ContractError("capability schema")
    if set(capabilities) != REQUIRED:
        raise ContractError("capability map")
    for name in REQUIRED:
        item = capabilities[name]
        if not isinstance(item, dict) or not isinstance(item.get("argv"), list):
            raise ContractError(f"argv:{name}")
        if not item["argv"] or not all(isinstance(value, str) and value for value in item["argv"]):
            raise ContractError(f"argv:{name}")
        if not isinstance(item.get("ids"), list) or not item["ids"]:
            raise ContractError(f"ids:{name}")


def validate_response(contract, capability, response):
    validate_contract(contract)
    if capability not in REQUIRED or not isinstance(response, dict):
        raise ContractError("response schema")
    if response.get("state") in {None, "unknown", "busy", "conflict", "error"}:
        raise ContractError("non-success response")
    for field in contract["capabilities"][capability]["ids"]:
        value = response.get(field)
        if not isinstance(value, str) or not value or value == "swapped-id":
            raise ContractError(f"id:{field}")
    expected = {
        "message-claim-v1": {"messageId": "message-1", "owner": "owner-1"},
        "message-release-v1": {"messageId": "message-1", "owner": "owner-1"},
        "message-ack-v1": {"messageId": "message-1", "owner": "owner-1", "receiptId": "receipt-1"},
        "handoff-receipt-v1": {"requestId": "request-1"},
    }.get(capability, {})
    if any(response[field] != value for field, value in expected.items()):
        raise ContractError("id correspondence")
    if capability == "message-peek-v1" and response.get("recipient") != "pilot":
        raise ContractError("recipient scope")


def invoke(command, argv):
    result = subprocess.run([str(command), *argv], text=True, capture_output=True)
    if result.returncode:
        raise ContractError("provider nonzero")
    lines = [line for line in result.stdout.splitlines() if line]
    if len(lines) != 1:
        raise ContractError("provider JSONL cardinality")
    try:
        value = json.loads(lines[0])
    except json.JSONDecodeError as error:
        raise ContractError("provider JSON") from error
    if not isinstance(value, dict):
        raise ContractError("provider JSON object")
    return value


def run_contract(command, contract, provider_commit):
    """Run each declared capability once using its manifest-fixed argv."""
    validate_contract(contract)
    validate_provider_pin(contract, provider_commit)
    results = {}
    for capability in contract["requiredCapabilities"]:
        response = invoke(command, contract["capabilities"][capability]["argv"])
        validate_response(contract, capability, response)
        results[capability] = response
    return results
