#!/usr/bin/env python3
"""Fail-closed validation and execution for Issue #383 B3 provider contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping, Sequence

REQUIRED = {
    "message-peek-v1",
    "message-claim-v1",
    "message-release-v1",
    "message-ack-v1",
    "message-send-v1",
    "handoff-receipt-v1",
}

NOT_USED = {"history"}

EXPECTED_ARGV = {
    "message-peek-v1": [
        "message-peek",
        "<team>",
        "<recipient>",
    ],
    "message-claim-v1": [
        "message-claim",
        "<team>",
        "<messageId>",
        "<owner>",
    ],
    "message-release-v1": [
        "message-release",
        "<team>",
        "<messageId>",
        "<owner>",
    ],
    "message-ack-v1": [
        "message-ack",
        "<team>",
        "<messageId>",
        "<owner>",
        "<receiptId>",
    ],
    "message-send-v1": [
        "message-send",
        "<team>",
        "<from>",
        "<recipient>",
        "<requestId>",
        "<body>",
    ],
    "handoff-receipt-v1": [
        "handoff-receipt",
        "<team>",
        "<inputMessageId>",
        "<requestId>",
        "<delegateMessageId>",
    ],
}

EXPECTED_IDS = {
    "message-peek-v1": [
        "messageId",
    ],
    "message-claim-v1": [
        "messageId",
        "owner",
    ],
    "message-release-v1": [
        "messageId",
        "owner",
    ],
    "message-ack-v1": [
        "messageId",
        "owner",
        "receiptId",
    ],
    "message-send-v1": [
        "messageId",
        "requestId",
    ],
    "handoff-receipt-v1": [
        "inputMessageId",
        "requestId",
        "delegateMessageId",
        "receiptId",
    ],
}

EXPECTED_SUCCESS = {
    "message-peek-v1": [
        "state=ok",
        "messageId",
        "from",
        "to",
        "body",
        "createdAt",
    ],
    "message-claim-v1": [
        "state=claimed",
        "messageId",
        "owner",
    ],
    "message-release-v1": [
        "state=released",
        "messageId",
        "owner",
    ],
    "message-ack-v1": [
        "state=acked",
        "messageId",
        "owner",
        "receiptId",
    ],
    "message-send-v1": [
        "state=queued",
        "messageId",
        "requestId",
        "team",
        "from",
        "to",
    ],
    "handoff-receipt-v1": [
        "state=recorded",
        "inputMessageId",
        "requestId",
        "delegateMessageId",
        "receiptId",
        "team",
    ],
}

EXPECTED_FAILURE = {
    "message-peek-v1": [
        "absent",
        "unknown",
    ],
    "message-claim-v1": [
        "unknown",
        "busy",
    ],
    "message-release-v1": [
        "unknown",
        "owner_mismatch",
    ],
    "message-ack-v1": [
        "unknown",
        "receipt_missing",
    ],
    "message-send-v1": [
        "unknown",
    ],
    "handoff-receipt-v1": [
        "unknown",
        "id_mismatch",
    ],
}

PLACEHOLDER_RE = re.compile(r"^<([A-Za-z][A-Za-z0-9]*)>$")
FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class ContractError(RuntimeError):
    """The declared provider contract or provider response is invalid."""


def load_contract(path: Path) -> dict[str, Any]:
    """Load a capability manifest as exactly one JSON object."""
    try:
        value = json.loads(path.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError("contract JSON") from error

    if not isinstance(value, dict):
        raise ContractError("contract JSON object")

    return value


def validate_provider_pin(
    contract: Mapping[str, Any],
    provider_commit: str,
) -> None:
    """Require the G2 manifest and G1 provider pin to name the same commit."""
    if not isinstance(provider_commit, str):
        raise ContractError("provider commit")

    if not FULL_COMMIT_RE.fullmatch(provider_commit):
        raise ContractError("provider commit")

    contract_commit = contract.get("providerCommit")
    if not isinstance(contract_commit, str):
        raise ContractError("providerCommit")

    if not FULL_COMMIT_RE.fullmatch(contract_commit):
        raise ContractError("providerCommit")

    if contract_commit != provider_commit:
        raise ContractError("provider pin mismatch")


def _validate_exit_status(
    name: str,
    item: Mapping[str, Any],
) -> None:
    exit_status = item.get("exitStatus")
    if not isinstance(exit_status, dict):
        raise ContractError(f"exitStatus:{name}")

    if set(exit_status) != {"json", "error"}:
        raise ContractError(f"exitStatus:{name}")

    if exit_status.get("json") != 0:
        raise ContractError(f"exitStatus:{name}")

    errors = exit_status.get("error")
    if (
        not isinstance(errors, list)
        or not errors
        or any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
            for value in errors
        )
        or len(errors) != len(set(errors))
    ):
        raise ContractError(f"exitStatus:{name}")


def validate_contract(contract: Mapping[str, Any]) -> None:
    """Validate the actual Issue #383 capability manifest fail-closed."""
    if not isinstance(contract, Mapping):
        raise ContractError("contract schema")

    if set(contract) != {
        "providerCommit",
        "schemaVersion",
        "command",
        "requiredCapabilities",
        "notUsedCapabilities",
        "capabilities",
    }:
        raise ContractError("contract fields")

    if contract.get("schemaVersion") != 1:
        raise ContractError("schemaVersion")

    provider_commit = contract.get("providerCommit")
    if (
        not isinstance(provider_commit, str)
        or not FULL_COMMIT_RE.fullmatch(provider_commit)
    ):
        raise ContractError("providerCommit")

    if contract.get("command") != "scripts/p2-provider.sh":
        raise ContractError("command")

    required = contract.get("requiredCapabilities")
    if (
        not isinstance(required, list)
        or any(not isinstance(value, str) for value in required)
        or len(required) != len(set(required))
        or set(required) != REQUIRED
    ):
        raise ContractError("declared capabilities")

    not_used = contract.get("notUsedCapabilities")
    if (
        not isinstance(not_used, list)
        or any(not isinstance(value, str) for value in not_used)
        or len(not_used) != len(set(not_used))
        or set(not_used) != NOT_USED
    ):
        raise ContractError("not-used capabilities")

    capabilities = contract.get("capabilities")
    if not isinstance(capabilities, dict):
        raise ContractError("capability schema")

    if set(capabilities) != REQUIRED:
        raise ContractError("capability map")

    for name in REQUIRED:
        item = capabilities.get(name)
        if not isinstance(item, dict):
            raise ContractError(f"capability:{name}")

        if set(item) != {
            "argv",
            "ids",
            "success",
            "failure",
            "exitStatus",
        }:
            raise ContractError(f"capability fields:{name}")

        argv = item.get("argv")
        if argv != EXPECTED_ARGV[name]:
            raise ContractError(f"argv:{name}")

        for value in argv:
            if not isinstance(value, str) or not value:
                raise ContractError(f"argv:{name}")

            if value.startswith("<") or value.endswith(">"):
                if PLACEHOLDER_RE.fullmatch(value) is None:
                    raise ContractError(f"argv placeholder:{name}")

        ids = item.get("ids")
        if ids != EXPECTED_IDS[name]:
            raise ContractError(f"ids:{name}")

        if (
            not isinstance(ids, list)
            or not ids
            or any(not isinstance(value, str) or not value for value in ids)
            or len(ids) != len(set(ids))
        ):
            raise ContractError(f"ids:{name}")

        success = item.get("success")
        if success != EXPECTED_SUCCESS[name]:
            raise ContractError(f"success:{name}")

        failure = item.get("failure")
        if failure != EXPECTED_FAILURE[name]:
            raise ContractError(f"failure:{name}")

        _validate_exit_status(name, item)


def expand_argv(
    contract: Mapping[str, Any],
    capability: str,
    bindings: Mapping[str, str],
) -> list[str]:
    """Expand one capability's exact manifest argv using explicit bindings."""
    validate_contract(contract)

    if capability not in REQUIRED:
        raise ContractError("undeclared capability")

    if not isinstance(bindings, Mapping):
        raise ContractError("bindings")

    argv = contract["capabilities"][capability]["argv"]
    expanded: list[str] = []
    required_bindings: set[str] = set()

    for value in argv:
        match = PLACEHOLDER_RE.fullmatch(value)
        if match is None:
            expanded.append(value)
            continue

        name = match.group(1)
        required_bindings.add(name)

        bound = bindings.get(name)
        if not isinstance(bound, str) or not bound:
            raise ContractError(f"binding:{name}")

        expanded.append(bound)

    unknown_bindings = set(bindings) - required_bindings
    if unknown_bindings:
        raise ContractError(
            "unused binding:" + ",".join(sorted(unknown_bindings))
        )

    return expanded


def invoke(
    command: Path | str,
    argv: Sequence[str],
) -> dict[str, Any]:
    """Invoke the public provider and require exactly one JSON object."""
    if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence):
        raise ContractError("provider argv")

    if any(not isinstance(value, str) or not value for value in argv):
        raise ContractError("provider argv")

    try:
        result = subprocess.run(
            [str(command), *argv],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise ContractError("provider invocation") from error

    if result.returncode != 0:
        raise ContractError(f"provider nonzero:{result.returncode}")

    lines = [
        line
        for line in result.stdout.splitlines()
        if line.strip()
    ]

    if len(lines) != 1:
        raise ContractError("provider JSONL cardinality")

    try:
        value = json.loads(lines[0])
    except json.JSONDecodeError as error:
        raise ContractError("provider JSON") from error

    if not isinstance(value, dict):
        raise ContractError("provider JSON object")

    return value


def _require_string(
    response: Mapping[str, Any],
    field: str,
) -> str:
    value = response.get(field)
    if not isinstance(value, str) or not value:
        raise ContractError(f"field:{field}")

    return value


def _binding(
    bindings: Mapping[str, str],
    name: str,
) -> str:
    value = bindings.get(name)
    if not isinstance(value, str) or not value:
        raise ContractError(f"binding:{name}")

    return value


def _validate_declared_success_fields(
    contract: Mapping[str, Any],
    capability: str,
    response: Mapping[str, Any],
) -> None:
    declarations = contract["capabilities"][capability]["success"]

    for declaration in declarations:
        if "=" in declaration:
            field, expected = declaration.split("=", 1)
            if response.get(field) != expected:
                raise ContractError(f"success:{field}")
            continue

        _require_string(response, declaration)


def _validate_id_fields(
    contract: Mapping[str, Any],
    capability: str,
    response: Mapping[str, Any],
) -> None:
    for field in contract["capabilities"][capability]["ids"]:
        _require_string(response, field)


def validate_response(
    contract: Mapping[str, Any],
    capability: str,
    response: Mapping[str, Any],
    bindings: Mapping[str, str],
) -> None:
    """Validate response schema, success state, scope, and ID correspondence."""
    validate_contract(contract)

    if capability not in REQUIRED:
        raise ContractError("undeclared capability")

    if not isinstance(response, Mapping):
        raise ContractError("response schema")

    if not isinstance(bindings, Mapping):
        raise ContractError("bindings")

    if response.get("schemaVersion") != 1:
        raise ContractError("response schemaVersion")

    state = response.get("state")
    if not isinstance(state, str) or not state:
        raise ContractError("response state")

    declared_failures = set(
        contract["capabilities"][capability]["failure"]
    )
    if state in declared_failures:
        raise ContractError(f"provider failure:{state}")

    if state in {
        "unknown",
        "busy",
        "conflict",
        "error",
        "owner_mismatch",
        "receipt_missing",
        "id_mismatch",
    }:
        raise ContractError(f"non-success response:{state}")

    _validate_declared_success_fields(
        contract,
        capability,
        response,
    )
    _validate_id_fields(
        contract,
        capability,
        response,
    )

    if capability == "message-peek-v1":
        if response.get("to") != _binding(bindings, "recipient"):
            raise ContractError("recipient scope")

    elif capability == "message-claim-v1":
        if response.get("messageId") != _binding(
            bindings,
            "messageId",
        ):
            raise ContractError("message ID correspondence")

        if response.get("owner") != _binding(bindings, "owner"):
            raise ContractError("owner correspondence")

    elif capability == "message-release-v1":
        if response.get("messageId") != _binding(
            bindings,
            "messageId",
        ):
            raise ContractError("message ID correspondence")

        if response.get("owner") != _binding(bindings, "owner"):
            raise ContractError("owner correspondence")

    elif capability == "message-ack-v1":
        if response.get("messageId") != _binding(
            bindings,
            "messageId",
        ):
            raise ContractError("message ID correspondence")

        if response.get("owner") != _binding(bindings, "owner"):
            raise ContractError("owner correspondence")

        if response.get("receiptId") != _binding(
            bindings,
            "receiptId",
        ):
            raise ContractError("receipt ID correspondence")

    elif capability == "message-send-v1":
        if response.get("requestId") != _binding(
            bindings,
            "requestId",
        ):
            raise ContractError("request ID correspondence")

        if response.get("team") != _binding(bindings, "team"):
            raise ContractError("team scope")

        if response.get("from") != _binding(bindings, "from"):
            raise ContractError("sender scope")

        if response.get("to") != _binding(bindings, "recipient"):
            raise ContractError("recipient scope")

    elif capability == "handoff-receipt-v1":
        if response.get("inputMessageId") != _binding(
            bindings,
            "inputMessageId",
        ):
            raise ContractError("input message ID correspondence")

        if response.get("requestId") != _binding(
            bindings,
            "requestId",
        ):
            raise ContractError("request ID correspondence")

        if response.get("delegateMessageId") != _binding(
            bindings,
            "delegateMessageId",
        ):
            raise ContractError("delegate message ID correspondence")

        if response.get("team") != _binding(bindings, "team"):
            raise ContractError("team scope")


def run_capability(
    command: Path | str,
    contract: Mapping[str, Any],
    capability: str,
    bindings: Mapping[str, str],
) -> dict[str, Any]:
    """Expand and execute one manifest-declared provider capability."""
    argv = expand_argv(
        contract,
        capability,
        bindings,
    )

    response = invoke(
        command,
        argv,
    )

    validate_response(
        contract,
        capability,
        response,
        bindings,
    )

    return response


def _load_bindings(path: Path) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError("bindings JSON") from error

    if not isinstance(value, dict):
        raise ContractError("bindings JSON object")

    result: dict[str, str] = {}

    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(item, str)
            or not item
        ):
            raise ContractError("bindings schema")

        result[key] = item

    return result


def _load_g1_provider_commit(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError("G1 manifest JSON") from error

    if not isinstance(value, dict):
        raise ContractError("G1 manifest JSON object")

    commit = value.get("commit")
    if not isinstance(commit, str):
        raise ContractError("G1 provider commit")

    return commit


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser(
        description=(
            "Validate and invoke one Issue #383 provider capability "
            "using the real capability manifest."
        )
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=(
            root
            / "docs"
            / "decisions"
            / "issue383-capability-manifest.json"
        ),
    )
    parser.add_argument(
        "--g1-manifest",
        type=Path,
        default=(
            root
            / "docs"
            / "decisions"
            / "issue379-provider-manifest.json"
        ),
    )
    parser.add_argument(
        "--command",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--capability",
        choices=sorted(REQUIRED),
    )
    parser.add_argument(
        "--bindings",
        type=Path,
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
    )
    args = parser.parse_args()

    try:
        contract = load_contract(args.manifest)
        validate_contract(contract)

        g1_provider_commit = _load_g1_provider_commit(
            args.g1_manifest
        )
        validate_provider_pin(
            contract,
            g1_provider_commit,
        )

        if args.validate_only:
            print(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "state": "valid",
                        "providerCommit": contract["providerCommit"],
                        "requiredCapabilities": (
                            contract["requiredCapabilities"]
                        ),
                    },
                    separators=(",", ":"),
                )
            )
            return 0

        if args.capability is None:
            raise ContractError("capability required")

        if args.bindings is None:
            raise ContractError("bindings required")

        bindings = _load_bindings(args.bindings)

        if args.command is None:
            command = root / contract["command"]
        else:
            command = args.command

        response = run_capability(
            command,
            contract,
            args.capability,
            bindings,
        )

        print(
            json.dumps(
                response,
                separators=(",", ":"),
            )
        )
        return 0

    except ContractError as error:
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "state": "error",
                    "error": str(error),
                },
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
