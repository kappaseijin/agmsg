#!/usr/bin/env bash
set -euo pipefail

# pilot-collector.sh — G4-C independent audit collector.
#
# Primary source:
#   Claude Code native session transcript JSONL.
#
# This collector MUST NOT read PostToolUse executions.jsonl, broker logs, or
# provider internals. It consumes only the immutable G4-A binding plus the
# native Claude transcript.
#
# Usage:
#   scripts/pilot-collector.sh discover
#   scripts/pilot-collector.sh scan
#   scripts/pilot-collector.sh status
#
# Required environment:
#   AGMSG_PM_BINDING_FILE
#   AGMSG_PM_COLLECTOR_STATE_DIR
#
# Exit status:
#   0  collectorStatus=ok
#   2  collectorStatus=unknown
#   3  collectorStatus=audit_unavailable
#   64 invocation/protocol error

usage() {
  cat >&2 <<'USAGE'
Usage:
  pilot-collector.sh discover
  pilot-collector.sh scan
  pilot-collector.sh status

Required environment:
  AGMSG_PM_BINDING_FILE
  AGMSG_PM_COLLECTOR_STATE_DIR
USAGE
}

emit_protocol_error() {
  local reason="$1"
  local operation="${2:-unknown}"

  python3 - "$operation" "$reason" <<'PY'
import json
import sys

print(json.dumps({
    "schemaVersion": 1,
    "collectorVersion": "pilot-collector-v1",
    "operation": sys.argv[1],
    "collectorStatus": "audit_unavailable",
    "reason": sys.argv[2],
}, separators=(",", ":")))
PY
}

if [ "$#" -ne 1 ]; then
  usage
  emit_protocol_error "usage_invalid"
  exit 64
fi

OPERATION="$1"

case "$OPERATION" in
  discover|scan|status)
    ;;
  *)
    usage
    emit_protocol_error "operation_invalid" "$OPERATION"
    exit 64
    ;;
esac

BINDING_FILE="${AGMSG_PM_BINDING_FILE:-}"
COLLECTOR_STATE_DIR="${AGMSG_PM_COLLECTOR_STATE_DIR:-}"

if [ -z "$BINDING_FILE" ]; then
  emit_protocol_error "binding_required" "$OPERATION"
  exit 64
fi

if [ -z "$COLLECTOR_STATE_DIR" ]; then
  emit_protocol_error "collector_state_dir_required" "$OPERATION"
  exit 64
fi

command -v python3 >/dev/null 2>&1 || {
  printf '%s\n' 'pilot-collector: python3 is required' >&2
  exit 64
}

exec python3 - "$OPERATION" "$BINDING_FILE" "$COLLECTOR_STATE_DIR" <<'PY'
from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1
COLLECTOR_VERSION = "pilot-collector-v1"
PILOT_AGENT = "agmsg_pm_pilot_claude"
PILOT_TYPE = "claude-code"
POLICY_VERSION = "pm-pilot-pretool-v1"
PROVIDER_COMMIT = "0b2117c5f91f7950cc196e52edb188748adfa50a"

EXIT_OK = 0
EXIT_UNKNOWN = 2
EXIT_UNAVAILABLE = 3
EXIT_PROTOCOL = 64

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
DECIMAL_RE = re.compile(r"^[1-9][0-9]*$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
SESSION_FILE_RE_TEMPLATE = "{}.jsonl"

REQUIRED_BINDING_FIELDS = {
    "schemaVersion",
    "team",
    "agent",
    "type",
    "project",
    "sessionId",
    "generation",
    "pid",
    "pidStart",
    "profileDigest",
    "policyVersion",
    "guardDigest",
    "brokerDigest",
    "providerCommit",
}

KNOWN_NON_TOOL_TOP_LEVEL_TYPES = {
    "system",
    "summary",
    "progress",
    "file-history-snapshot",
    "queue-operation",
}

KNOWN_ASSISTANT_BLOCK_TYPES = {
    "text",
    "thinking",
    "redacted_thinking",
}

KNOWN_USER_BLOCK_TYPES = {
    "text",
    "image",
    "document",
}


class CollectorError(Exception):
    def __init__(self, status: str, reason: str, exit_code: int):
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.exit_code = exit_code


def fail_unknown(reason: str) -> None:
    raise CollectorError("unknown", reason, EXIT_UNKNOWN)


def fail_unavailable(reason: str) -> None:
    raise CollectorError("audit_unavailable", reason, EXIT_UNAVAILABLE)


def fail_protocol(reason: str) -> None:
    raise CollectorError("audit_unavailable", reason, EXIT_PROTOCOL)


def now_utc() -> str:
    return (
        _datetime.datetime.now(_datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def compact_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    )


def is_object(value: Any) -> bool:
    return isinstance(value, dict)


def strict_text(value: Any, reason: str, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value:
        fail_unavailable(reason)
    if len(value) > max_len or CONTROL_RE.search(value):
        fail_unavailable(reason)
    return value


def strict_decimal(value: Any, reason: str) -> str:
    text = strict_text(value, reason, 32)
    if not DECIMAL_RE.fullmatch(text):
        fail_unavailable(reason)
    try:
        number = int(text, 10)
    except ValueError:
        fail_unavailable(reason)
    if number < 1 or number > (2**53 - 1):
        fail_unavailable(reason)
    return text


def strict_digest(value: Any, reason: str) -> str:
    text = strict_text(value, reason, 128)
    if not DIGEST_RE.fullmatch(text):
        fail_unavailable(reason)
    return text


def read_json_document(file_name: str, reason: str) -> Dict[str, Any]:
    try:
        with open(file_name, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except (OSError, UnicodeError):
        fail_unavailable(reason)

    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        fail_unavailable(reason)

    if not is_object(value):
        fail_unavailable(reason)
    return value


def lexical_regular_file(file_name: str, reason: str) -> str:
    if not isinstance(file_name, str) or not file_name:
        fail_protocol(reason)

    absolute = os.path.abspath(file_name)

    try:
        info = os.lstat(absolute)
    except OSError:
        fail_unavailable(reason)

    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        fail_unavailable(reason)

    return absolute


def canonical_existing_path(path_name: str, reason: str) -> str:
    try:
        return os.path.realpath(path_name, strict=True)
    except (OSError, TypeError):
        fail_unavailable(reason)


def canonical_existing_dir(path_name: str, reason: str) -> str:
    canonical = canonical_existing_path(path_name, reason)
    try:
        info = os.stat(canonical)
    except OSError:
        fail_unavailable(reason)
    if not stat.S_ISDIR(info.st_mode):
        fail_unavailable(reason)
    return canonical


def is_same_or_descendant(path_name: str, ancestor: str) -> bool:
    try:
        return os.path.commonpath([path_name, ancestor]) == ancestor
    except ValueError:
        return False


def prospective_canonical_path(path_name: str) -> str:
    absolute = os.path.abspath(path_name)
    cursor = absolute
    suffix: List[str] = []

    while not os.path.exists(cursor):
        parent, base = os.path.split(cursor)
        if not base or parent == cursor:
            fail_unavailable("collector_state_dir_unavailable")
        suffix.append(base)
        cursor = parent

    try:
        canonical = os.path.realpath(cursor, strict=True)
    except OSError:
        fail_unavailable("collector_state_dir_unavailable")

    for component in reversed(suffix):
        canonical = os.path.join(canonical, component)

    return os.path.normpath(canonical)


def ensure_collector_state_dir(
    requested: str,
    binding_dir_canonical: str,
    create: bool,
) -> str:
    if not isinstance(requested, str) or not requested:
        fail_protocol("collector_state_dir_required")
    if CONTROL_RE.search(requested):
        fail_protocol("collector_state_dir_invalid")

    absolute = os.path.abspath(requested)

    # Prove disjointness before mkdir. In particular, never create even a
    # temporary collector directory below G4-A's bindingsDir.
    prospective = prospective_canonical_path(absolute)
    if (
        is_same_or_descendant(prospective, binding_dir_canonical)
        or is_same_or_descendant(binding_dir_canonical, prospective)
    ):
        fail_protocol("collector_state_dir_overlaps_bindings")

    if create:
        try:
            os.makedirs(absolute, mode=0o700, exist_ok=True)
        except OSError:
            fail_unavailable("collector_state_dir_unavailable")
    elif not os.path.exists(absolute):
        fail_unavailable("collector_state_dir_unavailable")

    try:
        info = os.lstat(absolute)
    except OSError:
        fail_unavailable("collector_state_dir_unavailable")

    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        fail_unavailable("collector_state_dir_invalid")

    canonical = canonical_existing_dir(
        absolute,
        "collector_state_dir_unavailable",
    )

    # Re-check after creation to close symlink/race redirection between the
    # prospective check and the final directory identity.
    if (
        is_same_or_descendant(canonical, binding_dir_canonical)
        or is_same_or_descendant(binding_dir_canonical, canonical)
    ):
        fail_protocol("collector_state_dir_overlaps_bindings")

    return canonical
def validate_binding(
    binding_file: str,
) -> Tuple[Dict[str, Any], str, str]:
    lexical = lexical_regular_file(binding_file, "binding_unavailable")
    binding_dir = canonical_existing_dir(
        os.path.dirname(lexical),
        "binding_directory_unavailable",
    )

    value = read_json_document(lexical, "invalid_binding")

    if set(value.keys()) != REQUIRED_BINDING_FIELDS:
        fail_unavailable("binding_schema_invalid")

    if value.get("schemaVersion") != SCHEMA_VERSION:
        fail_unavailable("binding_schema_invalid")

    normalized: Dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "team": strict_text(value.get("team"), "binding_team_invalid"),
        "agent": strict_text(value.get("agent"), "binding_agent_invalid"),
        "type": strict_text(value.get("type"), "binding_type_invalid"),
        "project": strict_text(
            value.get("project"),
            "binding_project_invalid",
        ),
        "sessionId": strict_text(
            value.get("sessionId"),
            "binding_session_invalid",
            512,
        ),
        "generation": strict_decimal(
            value.get("generation"),
            "binding_generation_invalid",
        ),
        "pid": strict_decimal(value.get("pid"), "binding_pid_invalid"),
        "pidStart": strict_text(
            value.get("pidStart"),
            "binding_pid_start_invalid",
            512,
        ),
        "profileDigest": strict_digest(
            value.get("profileDigest"),
            "binding_profile_digest_invalid",
        ),
        "policyVersion": strict_text(
            value.get("policyVersion"),
            "binding_policy_invalid",
        ),
        "guardDigest": strict_digest(
            value.get("guardDigest"),
            "binding_guard_digest_invalid",
        ),
        "brokerDigest": strict_digest(
            value.get("brokerDigest"),
            "binding_broker_digest_invalid",
        ),
        "providerCommit": strict_text(
            value.get("providerCommit"),
            "binding_provider_invalid",
            64,
        ),
    }

    project_canonical = canonical_existing_dir(
        normalized["project"],
        "binding_project_unreadable",
    )
    if normalized["project"] != project_canonical:
        fail_unavailable("binding_project_mismatch")

    if (
        normalized["agent"] != PILOT_AGENT
        or normalized["type"] != PILOT_TYPE
    ):
        fail_unavailable("binding_launcher_mismatch")

    if normalized["policyVersion"] != POLICY_VERSION:
        fail_unavailable("binding_policy_mismatch")

    if normalized["providerCommit"] != PROVIDER_COMMIT:
        fail_unavailable("binding_provider_mismatch")

    env_checks = (
        ("AGMSG_PM_PILOT_SESSION_ID", "sessionId", "binding_session_mismatch"),
        (
            "AGMSG_PM_PROCESS_GENERATION",
            "generation",
            "binding_generation_mismatch",
        ),
        ("AGMSG_PM_TEAM", "team", "binding_team_mismatch"),
        ("AGMSG_PM_AGENT", "agent", "binding_actor_mismatch"),
    )

    for env_name, field, reason in env_checks:
        expected = os.environ.get(env_name)
        if expected is not None and expected != "":
            if expected != normalized[field]:
                fail_unavailable(reason)

    return normalized, lexical, binding_dir


def state_paths(
    state_dir: str,
    generation: str,
) -> Tuple[str, str]:
    base = "generation-{}".format(generation)
    return (
        os.path.join(state_dir, base + ".state.json"),
        os.path.join(state_dir, base + ".observations.jsonl"),
    )


def atomic_write_json(file_name: str, value: Dict[str, Any]) -> None:
    directory = os.path.dirname(file_name)
    base = os.path.basename(file_name)
    body = (compact_json(value) + "\n").encode("utf-8")

    fd: Optional[int] = None
    temporary: Optional[str] = None

    try:
        fd, temporary = tempfile.mkstemp(
            prefix="." + base + ".tmp.",
            dir=directory,
        )
        os.fchmod(fd, 0o600)

        written = 0
        while written < len(body):
            count = os.write(fd, body[written:])
            if count <= 0:
                raise OSError("short write")
            written += count

        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(temporary, file_name)
        temporary = None

        try:
            dir_fd = os.open(directory, os.O_RDONLY)
        except OSError:
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            except OSError:
                pass
            finally:
                os.close(dir_fd)
    except OSError:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        fail_unavailable("collector_state_write_failed")


def empty_state(binding: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = now_utc()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "collectorVersion": COLLECTOR_VERSION,
        "sessionId": binding["sessionId"],
        "generation": binding["generation"],
        "transcriptPath": None,
        "sourceOffset": 0,
        "sourceDevice": None,
        "sourceInode": None,
        "collectorStatus": "audit_unavailable",
        "statusReason": "not_discovered",
        "cliVersion": None,
        "pending": {},
        "observations": 0,
        "lastObservedAt": None,
        "updatedAt": timestamp,
    }


def validate_state(
    value: Dict[str, Any],
    binding: Dict[str, Any],
) -> Dict[str, Any]:
    required = {
        "schemaVersion",
        "collectorVersion",
        "sessionId",
        "generation",
        "transcriptPath",
        "sourceOffset",
        "sourceDevice",
        "sourceInode",
        "collectorStatus",
        "statusReason",
        "cliVersion",
        "pending",
        "observations",
        "lastObservedAt",
        "updatedAt",
    }

    if set(value.keys()) != required:
        fail_unavailable("collector_state_schema_invalid")
    if value["schemaVersion"] != SCHEMA_VERSION:
        fail_unavailable("collector_state_schema_invalid")
    if value["collectorVersion"] != COLLECTOR_VERSION:
        fail_unavailable("collector_state_version_invalid")
    if value["sessionId"] != binding["sessionId"]:
        fail_unavailable("collector_state_session_mismatch")
    if value["generation"] != binding["generation"]:
        fail_unavailable("collector_state_generation_mismatch")

    if value["collectorStatus"] not in (
        "ok",
        "unknown",
        "audit_unavailable",
    ):
        fail_unavailable("collector_state_status_invalid")

    if not isinstance(value["sourceOffset"], int) or value["sourceOffset"] < 0:
        fail_unavailable("collector_state_offset_invalid")
    if not isinstance(value["observations"], int) or value["observations"] < 0:
        fail_unavailable("collector_state_observations_invalid")

    transcript_path = value["transcriptPath"]
    if transcript_path is not None and (
        not isinstance(transcript_path, str)
        or not transcript_path
        or CONTROL_RE.search(transcript_path)
    ):
        fail_unavailable("collector_state_transcript_invalid")

    for key in ("sourceDevice", "sourceInode"):
        item = value[key]
        if item is not None and (
            not isinstance(item, int)
            or isinstance(item, bool)
            or item < 0
        ):
            fail_unavailable("collector_state_source_identity_invalid")

    if transcript_path is None:
        if value["sourceDevice"] is not None or value["sourceInode"] is not None:
            fail_unavailable("collector_state_source_identity_invalid")
    else:
        if value["sourceDevice"] is None or value["sourceInode"] is None:
            fail_unavailable("collector_state_source_identity_invalid")

    if value["statusReason"] is not None and (
        not isinstance(value["statusReason"], str)
        or not value["statusReason"]
        or CONTROL_RE.search(value["statusReason"])
    ):
        fail_unavailable("collector_state_reason_invalid")

    if value["cliVersion"] is not None and (
        not isinstance(value["cliVersion"], str)
        or not value["cliVersion"]
        or len(value["cliVersion"]) > 256
        or CONTROL_RE.search(value["cliVersion"])
    ):
        fail_unavailable("collector_state_cli_version_invalid")

    if value["lastObservedAt"] is not None and (
        not isinstance(value["lastObservedAt"], str)
        or not value["lastObservedAt"]
    ):
        fail_unavailable("collector_state_time_invalid")

    if not isinstance(value["updatedAt"], str) or not value["updatedAt"]:
        fail_unavailable("collector_state_time_invalid")

    pending = value["pending"]
    if not is_object(pending):
        fail_unavailable("collector_state_pending_invalid")

    for tool_id, item in pending.items():
        if (
            not isinstance(tool_id, str)
            or not tool_id
            or CONTROL_RE.search(tool_id)
            or not is_object(item)
            or set(item.keys())
            != {
                "toolName",
                "toolInputDigest",
                "toolUseRecordOffset",
            }
        ):
            fail_unavailable("collector_state_pending_invalid")

        if (
            not isinstance(item["toolName"], str)
            or not item["toolName"]
            or CONTROL_RE.search(item["toolName"])
            or not DIGEST_RE.fullmatch(item["toolInputDigest"])
            or not isinstance(item["toolUseRecordOffset"], int)
            or item["toolUseRecordOffset"] < 0
        ):
            fail_unavailable("collector_state_pending_invalid")

    return value


def load_state(
    state_file: str,
    binding: Dict[str, Any],
    required: bool,
) -> Optional[Dict[str, Any]]:
    if not os.path.exists(state_file):
        if required:
            fail_unavailable("collector_not_discovered")
        return None

    try:
        info = os.lstat(state_file)
    except OSError:
        fail_unavailable("collector_state_unavailable")

    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        fail_unavailable("collector_state_unavailable")

    return validate_state(
        read_json_document(state_file, "collector_state_invalid"),
        binding,
    )


def cli_version() -> Optional[str]:
    executable = shutil.which("claude")
    if not executable:
        return None

    try:
        completed = subprocess.run(
            [executable, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode != 0:
        return None

    try:
        output = completed.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeError:
        return None

    if (
        not output
        or len(output) > 256
        or "\n" in output
        or "\r" in output
        or CONTROL_RE.search(output)
    ):
        return None

    return output
def projects_root() -> str:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        root = os.path.join(config_dir, "projects")
    else:
        home = os.environ.get("HOME")
        if not home:
            fail_unavailable("claude_config_root_unavailable")
        root = os.path.join(home, ".claude", "projects")

    return canonical_existing_dir(
        root,
        "claude_projects_root_unavailable",
    )


def discover_candidates(
    root: str,
    session_id: str,
) -> List[Tuple[str, os.stat_result]]:
    target = SESSION_FILE_RE_TEMPLATE.format(session_id)
    matches: List[str] = []
    walk_errors: List[OSError] = []

    def onerror(error: OSError) -> None:
        walk_errors.append(error)

    for current, directories, files in os.walk(
        root,
        topdown=True,
        followlinks=False,
        onerror=onerror,
    ):
        directories[:] = sorted(directories)
        for name in sorted(files):
            if name == target:
                matches.append(os.path.join(current, name))

    if walk_errors:
        fail_unavailable("transcript_search_unreadable")

    if len(matches) == 0:
        fail_unavailable("transcript_not_found")
    if len(matches) != 1:
        fail_unavailable("transcript_ambiguous")

    candidate = matches[0]

    try:
        lexical_info = os.lstat(candidate)
    except OSError:
        fail_unavailable("transcript_unreadable")

    try:
        canonical = os.path.realpath(candidate, strict=True)
    except OSError:
        fail_unavailable("transcript_unreadable")

    if not is_same_or_descendant(canonical, root):
        fail_unavailable("transcript_outside_projects_root")

    if stat.S_ISLNK(lexical_info.st_mode):
        fail_unavailable("transcript_symlink_unsupported")

    try:
        info = os.stat(canonical)
    except OSError:
        fail_unavailable("transcript_unreadable")

    if not stat.S_ISREG(info.st_mode):
        fail_unavailable("transcript_unreadable")

    try:
        with open(canonical, "rb") as fh:
            fh.read(1)
    except OSError:
        fail_unavailable("transcript_unreadable")

    return [(canonical, info)]


def verify_fixed_source(
    state: Dict[str, Any],
) -> os.stat_result:
    transcript = state["transcriptPath"]
    if transcript is None:
        fail_unavailable("collector_not_discovered")

    try:
        canonical = os.path.realpath(transcript, strict=True)
    except OSError:
        fail_unavailable("transcript_unreadable")

    if canonical != transcript:
        fail_unavailable("transcript_path_changed")

    try:
        info = os.stat(transcript)
    except OSError:
        fail_unavailable("transcript_unreadable")

    if not stat.S_ISREG(info.st_mode):
        fail_unavailable("transcript_unreadable")

    if (
        info.st_dev != state["sourceDevice"]
        or info.st_ino != state["sourceInode"]
    ):
        fail_unavailable("transcript_replaced")

    if info.st_size < state["sourceOffset"]:
        fail_unavailable("transcript_truncated")

    return info


def ledger_record_valid(
    record: Any,
    binding: Dict[str, Any],
) -> bool:
    if not is_object(record):
        return False

    required = {
        "schemaVersion",
        "collectorVersion",
        "sessionId",
        "generation",
        "toolUseId",
        "toolName",
        "toolInputDigest",
        "completionState",
        "sourceLocator",
        "observedAt",
        "cliVersion",
    }

    if set(record.keys()) != required:
        return False
    if record["schemaVersion"] != SCHEMA_VERSION:
        return False
    if record["collectorVersion"] != COLLECTOR_VERSION:
        return False
    if record["sessionId"] != binding["sessionId"]:
        return False
    if record["generation"] != binding["generation"]:
        return False

    if (
        not isinstance(record["toolUseId"], str)
        or not record["toolUseId"]
        or CONTROL_RE.search(record["toolUseId"])
    ):
        return False

    if (
        not isinstance(record["toolName"], str)
        or not record["toolName"]
        or CONTROL_RE.search(record["toolName"])
    ):
        return False

    if (
        not isinstance(record["toolInputDigest"], str)
        or not DIGEST_RE.fullmatch(record["toolInputDigest"])
    ):
        return False

    if record["completionState"] not in ("success", "failure"):
        return False

    locator = record["sourceLocator"]
    if (
        not is_object(locator)
        or set(locator.keys())
        != {"toolUseRecordOffset", "resultRecordOffset"}
        or not isinstance(locator["toolUseRecordOffset"], int)
        or not isinstance(locator["resultRecordOffset"], int)
        or locator["toolUseRecordOffset"] < 0
        or locator["resultRecordOffset"] < 0
    ):
        return False

    if (
        not isinstance(record["observedAt"], str)
        or not record["observedAt"]
        or not isinstance(record["cliVersion"], str)
        or not record["cliVersion"]
    ):
        return False

    return True


def load_ledger(
    ledger_file: str,
    binding: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    if not os.path.exists(ledger_file):
        return [], {}

    try:
        info = os.lstat(ledger_file)
    except OSError:
        fail_unavailable("collector_ledger_unavailable")

    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        fail_unavailable("collector_ledger_unavailable")

    records: List[Dict[str, Any]] = []
    by_id: Dict[str, Dict[str, Any]] = {}

    try:
        with open(ledger_file, "rb") as fh:
            data = fh.read()
    except OSError:
        fail_unavailable("collector_ledger_unavailable")

    if data and not data.endswith(b"\n"):
        fail_unavailable("collector_ledger_incomplete")

    for raw_line in data.splitlines():
        if not raw_line:
            fail_unavailable("collector_ledger_invalid")
        try:
            value = json.loads(raw_line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            fail_unavailable("collector_ledger_invalid")

        if not ledger_record_valid(value, binding):
            fail_unavailable("collector_ledger_invalid")

        tool_id = value["toolUseId"]
        if tool_id in by_id:
            fail_unavailable("collector_ledger_duplicate")

        records.append(value)
        by_id[tool_id] = value

    return records, by_id


def append_ledger_record(
    ledger_file: str,
    record: Dict[str, Any],
) -> None:
    body = (compact_json(record) + "\n").encode("utf-8")

    try:
        fd = os.open(
            ledger_file,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
    except OSError:
        fail_unavailable("collector_ledger_write_failed")

    try:
        written = 0
        while written < len(body):
            count = os.write(fd, body[written:])
            if count <= 0:
                raise OSError("short write")
            written += count
        os.fsync(fd)
    except OSError:
        fail_unavailable("collector_ledger_write_failed")
    finally:
        os.close(fd)


def canonical_input_digest(tool_input: Any) -> str:
    if not is_object(tool_input):
        fail_unknown("tool_input_schema_unknown")

    try:
        raw = json.dumps(
            tool_input,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError):
        fail_unknown("tool_input_digest_unavailable")

    return "sha256:" + hashlib.sha256(raw).hexdigest()


def tool_use_from_block(
    block: Dict[str, Any],
    record_offset: int,
) -> Tuple[str, Dict[str, Any]]:
    tool_id = block.get("id")
    tool_name = block.get("name")

    if (
        not isinstance(tool_id, str)
        or not tool_id
        or CONTROL_RE.search(tool_id)
    ):
        fail_unknown("tool_use_id_unknown")

    if (
        not isinstance(tool_name, str)
        or not tool_name
        or CONTROL_RE.search(tool_name)
    ):
        fail_unknown("tool_name_unknown")

    if "input" not in block:
        fail_unknown("tool_input_missing")

    return tool_id, {
        "toolName": tool_name,
        "toolInputDigest": canonical_input_digest(block["input"]),
        "toolUseRecordOffset": record_offset,
    }


def tool_result_from_block(
    block: Dict[str, Any],
) -> Tuple[str, str]:
    tool_id = block.get("tool_use_id")

    if (
        not isinstance(tool_id, str)
        or not tool_id
        or CONTROL_RE.search(tool_id)
    ):
        fail_unknown("tool_result_id_unknown")

    is_error = block.get("is_error", False)
    if not isinstance(is_error, bool):
        fail_unknown("tool_result_state_unknown")

    return tool_id, ("failure" if is_error else "success")


def record_events(
    record: Any,
    record_offset: int,
) -> List[Tuple[str, str, Any]]:
    if not is_object(record):
        fail_unknown("transcript_record_schema_unknown")

    record_type = record.get("type")
    if not isinstance(record_type, str) or not record_type:
        fail_unknown("transcript_record_type_unknown")

    if record_type in KNOWN_NON_TOOL_TOP_LEVEL_TYPES:
        return []

    if record_type not in ("assistant", "user"):
        fail_unknown("transcript_record_type_unknown")

    message = record.get("message")
    if not is_object(message):
        fail_unknown("transcript_message_schema_unknown")

    content = message.get("content")

    if isinstance(content, str):
        return []

    if not isinstance(content, list):
        fail_unknown("transcript_content_schema_unknown")

    events: List[Tuple[str, str, Any]] = []

    for block in content:
        if not is_object(block):
            fail_unknown("transcript_content_block_unknown")

        block_type = block.get("type")
        if not isinstance(block_type, str) or not block_type:
            fail_unknown("transcript_content_block_unknown")

        if record_type == "assistant":
            if block_type == "tool_use":
                tool_id, pending = tool_use_from_block(block, record_offset)
                events.append(("tool_use", tool_id, pending))
                continue

            if block_type in KNOWN_ASSISTANT_BLOCK_TYPES:
                continue

            fail_unknown("assistant_content_block_unknown")

        if block_type == "tool_result":
            tool_id, completion = tool_result_from_block(block)
            events.append(("tool_result", tool_id, completion))
            continue

        if block_type in KNOWN_USER_BLOCK_TYPES:
            continue

        fail_unknown("user_content_block_unknown")

    return events
def observation_record(
    binding: Dict[str, Any],
    pending: Dict[str, Any],
    tool_id: str,
    completion: str,
    result_offset: int,
    cli: str,
) -> Dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "collectorVersion": COLLECTOR_VERSION,
        "sessionId": binding["sessionId"],
        "generation": binding["generation"],
        "toolUseId": tool_id,
        "toolName": pending["toolName"],
        "toolInputDigest": pending["toolInputDigest"],
        "completionState": completion,
        "sourceLocator": {
            "toolUseRecordOffset": pending["toolUseRecordOffset"],
            "resultRecordOffset": result_offset,
        },
        "observedAt": now_utc(),
        "cliVersion": cli,
    }


def same_observation_identity(
    old: Dict[str, Any],
    new: Dict[str, Any],
) -> bool:
    keys = (
        "schemaVersion",
        "collectorVersion",
        "sessionId",
        "generation",
        "toolUseId",
        "toolName",
        "toolInputDigest",
        "completionState",
        "sourceLocator",
        "cliVersion",
    )
    return all(old.get(key) == new.get(key) for key in keys)


def response(
    operation: str,
    state: Dict[str, Any],
    new_observations: Optional[int] = None,
) -> Dict[str, Any]:
    value: Dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "collectorVersion": COLLECTOR_VERSION,
        "operation": operation,
        "collectorStatus": state["collectorStatus"],
        "reason": state["statusReason"],
        "sessionId": state["sessionId"],
        "generation": state["generation"],
        "transcriptDiscovered": state["transcriptPath"] is not None,
        "sourceOffset": state["sourceOffset"],
        "observations": state["observations"],
        "pending": len(state["pending"]),
        "lastObservedAt": state["lastObservedAt"],
        "cliVersion": state["cliVersion"],
    }

    if new_observations is not None:
        value["newObservations"] = new_observations

    return value


def exit_for_status(status: str) -> int:
    if status == "ok":
        return EXIT_OK
    if status == "unknown":
        return EXIT_UNKNOWN
    return EXIT_UNAVAILABLE


def persist_failure_state(
    state_file: str,
    state: Dict[str, Any],
    error: CollectorError,
) -> None:
    state["collectorStatus"] = error.status
    state["statusReason"] = error.reason
    state["updatedAt"] = now_utc()
    atomic_write_json(state_file, state)


def operation_discover(
    binding: Dict[str, Any],
    state_file: str,
    ledger_file: str,
) -> Tuple[Dict[str, Any], int]:
    state = load_state(state_file, binding, required=False)
    if state is None:
        state = empty_state(binding)

    # Once a generation has fixed a transcript path, discover is idempotent
    # and MUST NOT search for or follow another path.
    if state["transcriptPath"] is not None:
        try:
            verify_fixed_source(state)
            records, _ = load_ledger(ledger_file, binding)
            if len(records) != state["observations"]:
                fail_unavailable("collector_ledger_count_mismatch")

            if state["cliVersion"] is None:
                detected = cli_version()
                if detected is None:
                    state["collectorStatus"] = "unknown"
                    state["statusReason"] = "cli_version_unavailable"
                else:
                    state["cliVersion"] = detected
                    if state["statusReason"] == "cli_version_unavailable":
                        state["collectorStatus"] = "ok"
                        state["statusReason"] = None
                state["updatedAt"] = now_utc()
                atomic_write_json(state_file, state)

            return state, exit_for_status(state["collectorStatus"])
        except CollectorError as error:
            persist_failure_state(state_file, state, error)
            return state, error.exit_code

    try:
        root = projects_root()
        candidates = discover_candidates(root, binding["sessionId"])
        transcript, info = candidates[0]

        state["transcriptPath"] = transcript
        state["sourceDevice"] = int(info.st_dev)
        state["sourceInode"] = int(info.st_ino)
        state["sourceOffset"] = 0
        state["pending"] = {}
        state["observations"] = 0
        state["lastObservedAt"] = None

        detected = cli_version()
        if detected is None:
            state["cliVersion"] = None
            state["collectorStatus"] = "unknown"
            state["statusReason"] = "cli_version_unavailable"
        else:
            state["cliVersion"] = detected
            state["collectorStatus"] = "ok"
            state["statusReason"] = None

        state["updatedAt"] = now_utc()
        atomic_write_json(state_file, state)
        return state, exit_for_status(state["collectorStatus"])
    except CollectorError as error:
        # Discovery failures before transcript fixation are retryable.
        state["collectorStatus"] = error.status
        state["statusReason"] = error.reason
        state["updatedAt"] = now_utc()
        atomic_write_json(state_file, state)
        return state, error.exit_code


def operation_scan(
    binding: Dict[str, Any],
    state_file: str,
    ledger_file: str,
) -> Tuple[Dict[str, Any], int, int]:
    state = load_state(state_file, binding, required=True)
    assert state is not None

    if state["transcriptPath"] is None:
        return state, EXIT_UNAVAILABLE, 0

    # Integrity failures after fixation are terminal for this generation.
    if state["collectorStatus"] == "audit_unavailable":
        return state, EXIT_UNAVAILABLE, 0

    # Unknown transcript schema is not silently retried as if it were healthy.
    # The one recoverable unknown is CLI-version acquisition.
    if (
        state["collectorStatus"] == "unknown"
        and state["statusReason"] != "cli_version_unavailable"
    ):
        return state, EXIT_UNKNOWN, 0

    if state["cliVersion"] is None:
        detected = cli_version()
        if detected is None:
            state["collectorStatus"] = "unknown"
            state["statusReason"] = "cli_version_unavailable"
            state["updatedAt"] = now_utc()
            atomic_write_json(state_file, state)
            return state, EXIT_UNKNOWN, 0

        state["cliVersion"] = detected
        state["collectorStatus"] = "ok"
        state["statusReason"] = None

    try:
        verify_fixed_source(state)
        ledger, ledger_by_id = load_ledger(ledger_file, binding)

        if len(ledger) != state["observations"]:
            fail_unavailable("collector_ledger_count_mismatch")

        transcript = state["transcriptPath"]
        offset = state["sourceOffset"]

        try:
            with open(transcript, "rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
        except OSError:
            fail_unavailable("transcript_unreadable")

        pending = dict(state["pending"])
        current = offset
        last_observed = state["lastObservedAt"]
        to_append: List[Dict[str, Any]] = []

        for raw_line in chunk.splitlines(keepends=True):
            line_start = current

            if not raw_line.endswith(b"\n"):
                # Native transcript is append-only. An unterminated final line
                # may be an in-progress write: do not consume or classify it.
                break

            line_end = current + len(raw_line)

            payload = raw_line[:-1]
            if payload.endswith(b"\r"):
                payload = payload[:-1]

            if not payload:
                fail_unknown("transcript_empty_record")

            try:
                text = payload.decode("utf-8", errors="strict")
                record = json.loads(text)
            except (UnicodeError, json.JSONDecodeError):
                fail_unknown("transcript_json_invalid")

            events = record_events(record, line_start)

            for kind, tool_id, data in events:
                if kind == "tool_use":
                    if tool_id in pending:
                        fail_unknown("duplicate_pending_tool_use")
                    if tool_id in ledger_by_id:
                        fail_unknown("duplicate_completed_tool_use")

                    pending[tool_id] = data
                    continue

                if tool_id not in pending:
                    fail_unknown("tool_result_without_tool_use")

                pending_record = pending[tool_id]
                new_record = observation_record(
                    binding,
                    pending_record,
                    tool_id,
                    data,
                    line_start,
                    state["cliVersion"],
                )

                if tool_id in ledger_by_id:
                    fail_unknown("duplicate_completed_tool_result")

                ledger_by_id[tool_id] = new_record
                to_append.append(new_record)
                last_observed = new_record["observedAt"]

                del pending[tool_id]

            current = line_end

        # Do not mutate the ledger until every complete source record in this
        # scan has been classified successfully. A later malformed record must
        # not leave observations ahead of the committed source offset.
        for new_record in to_append:
            append_ledger_record(ledger_file, new_record)
            ledger.append(new_record)

        new_count = len(to_append)

        state["sourceOffset"] = current
        state["pending"] = pending
        state["observations"] = len(ledger)
        state["lastObservedAt"] = last_observed
        state["collectorStatus"] = "ok"
        state["statusReason"] = None
        state["updatedAt"] = now_utc()
        atomic_write_json(state_file, state)

        return state, EXIT_OK, new_count

    except CollectorError as error:
        # Do not advance sourceOffset on the record that cannot be classified.
        # pending may have changed only in local memory since the last state
        # commit; the prior committed state remains the retry/review anchor.
        state["collectorStatus"] = error.status
        state["statusReason"] = error.reason
        state["updatedAt"] = now_utc()
        atomic_write_json(state_file, state)
        return state, error.exit_code, 0


def operation_status(
    binding: Dict[str, Any],
    state_file: str,
    ledger_file: str,
) -> Tuple[Dict[str, Any], int]:
    state = load_state(state_file, binding, required=True)
    assert state is not None

    records, _ = load_ledger(ledger_file, binding)
    if len(records) != state["observations"]:
        fail_unavailable("collector_ledger_count_mismatch")

    if state["transcriptPath"] is not None:
        try:
            verify_fixed_source(state)
        except CollectorError as error:
            # status is read-only: report the detected problem but do not
            # rewrite state merely because it was queried.
            transient = dict(state)
            transient["collectorStatus"] = error.status
            transient["statusReason"] = error.reason
            return transient, error.exit_code

    return state, exit_for_status(state["collectorStatus"])


def main() -> int:
    operation = sys.argv[1]
    binding_file_arg = sys.argv[2]
    state_dir_arg = sys.argv[3]

    try:
        binding, _, binding_dir = validate_binding(binding_file_arg)

        create_state_dir = operation in ("discover", "scan")
        state_dir = ensure_collector_state_dir(
            state_dir_arg,
            binding_dir,
            create=create_state_dir,
        )

        state_file, ledger_file = state_paths(
            state_dir,
            binding["generation"],
        )

        if operation == "discover":
            state, code = operation_discover(
                binding,
                state_file,
                ledger_file,
            )
            print(compact_json(response("discover", state)))
            return code

        if operation == "scan":
            state, code, new_count = operation_scan(
                binding,
                state_file,
                ledger_file,
            )
            print(
                compact_json(
                    response(
                        "scan",
                        state,
                        new_observations=new_count,
                    )
                )
            )
            return code

        state, code = operation_status(
            binding,
            state_file,
            ledger_file,
        )
        print(compact_json(response("status", state)))
        return code

    except CollectorError as error:
        print(
            compact_json(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "collectorVersion": COLLECTOR_VERSION,
                    "operation": operation,
                    "collectorStatus": error.status,
                    "reason": error.reason,
                }
            )
        )
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
PY