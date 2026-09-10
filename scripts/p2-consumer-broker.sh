#!/usr/bin/env bash
set -euo pipefail

# p2-consumer-broker.sh — G4-B minimal broker adapter.
#
# Public consumer operations are deliberately limited to:
#
#   receive
#   delegate
#   collect-result
#   issue-record
#   observe-owner
#
# This file MUST NOT source/import/dispatch through pm-broker.js or pm-broker.sh.
# The existing PM broker has a broader permission surface and is not a backend
# for the P2 pilot.
#
# Backends:
#
#   scripts/p2-provider.sh
#   scripts/api.sh
#   gh
#
# Invocation:
#
#   printf '%s\n' '<request-json>' |
#     scripts/p2-consumer-broker.sh \
#       --config /lexical/path/to/run-config.json \
#       receive
#
#   printf '%s\n' '<request-json>' |
#     scripts/p2-consumer-broker.sh \
#       --config /lexical/path/to/run-config.json \
#       --gh-config-dir /lexical/path/to/gh-config \
#       issue-record
#
# Required launcher environment:
#
#   AGMSG_PM_BINDING_FILE
#
# Optional-but-validated launcher environment when present:
#
#   AGMSG_PM_PILOT_SESSION_ID
#   AGMSG_PM_PROCESS_GENERATION
#   AGMSG_PM_TEAM
#   AGMSG_PM_AGENT
#
# Run config schema:
#
# {
#   "schemaVersion": 1,
#   "runId": "opaque-run-id",
#   "worker": "agmsg_worker_codex",
#   "testIssueNumber": 123,
#   "repo": "owner/repository"
# }
#
# The config is intentionally a narrow projection of the eventual run
# manifest. The integration layer may generate this object from a richer
# manifest without expanding this adapter's permission surface.
#
# Owner format:
#
#   p2:<sessionId>:<generation>:<runId>
#
# p2-provider.sh treats owner as opaque. The exact same owner is used for every
# claim/release/ack belonging to one broker run.
#
# Result envelope expected from the registered worker:
#
# {
#   "schemaVersion": 1,
#   "requestId": "<delegate request id>",
#   "delegateMessageId": "<delegate message id>",
#   "result": "<result text>"
# }
#
# Receipt policy:
#
# p2-provider.sh handoff-receipt records one input id, and message-ack requires
# that the acknowledged message id match that receipt's input id.
#
# Therefore G4-B uses two receipts:
#
#   delegate:
#     handoff-receipt(
#       team,
#       inputMessageId,
#       requestId,
#       delegateMessageId
#     )
#
#   collect-result:
#     handoff-receipt(
#       team,
#       resultMessageId,
#       requestId,
#       delegateMessageId
#     )
#
# This allows receipt-before-ack for both the original input and the worker
# result without modifying the fixed G2 provider.
#
# Validator execution rule:
#
# Never use:
#
#   if ! eval "$(validator ...)"; then
#
# A validator can exit non-zero while emitting no stdout. eval would then
# receive an empty program and return zero, turning failed validation into a
# fail-open path with unset variables.
#
# load_assignments() first captures and checks the validator exit status and
# evaluates assignment output only after success.
#
# G2 surface limitation:
#
# message-peek exposes only the first unread message, and message-claim does not
# itself make that message disappear from subsequent message-peek calls.
# Therefore this adapter cannot enumerate unread messages to prove absence of a
# second matching result without adding a forbidden history/storage capability.
# Tests must expose that limitation rather than silently treating it as solved.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PROVIDER="$SCRIPT_DIR/p2-provider.sh"
API="$SCRIPT_DIR/api.sh"

PILOT_AGENT="agmsg_pm_pilot_claude"
PROVIDER_COMMIT="0b2117c5f91f7950cc196e52edb188748adfa50a"

MAX_REQUEST_BYTES=8192
MAX_TEXT_BYTES=4096

CONFIG_FILE=""
GH_CONFIG_DIR_ARG=""
OPERATION=""

STATE_FILE=""
STATE_DIR=""
LOCK_DIR=""
LOCK_HELD=0
REQUEST_FILE=""
ISSUE_BODY_FILE=""

cleanup() {
  local rc="$?"

  if [ -n "$REQUEST_FILE" ] &&
     [ -f "$REQUEST_FILE" ]
  then
    rm -f "$REQUEST_FILE"
  fi

  if [ -n "$ISSUE_BODY_FILE" ] &&
     [ -f "$ISSUE_BODY_FILE" ]
  then
    rm -f "$ISSUE_BODY_FILE"
  fi

  if [ "$LOCK_HELD" -eq 1 ] &&
     [ -n "$LOCK_DIR" ]
  then
    rmdir "$LOCK_DIR" >/dev/null 2>&1 || true
  fi

  exit "$rc"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

usage() {
  cat >&2 <<'USAGE'
Usage:
  p2-consumer-broker.sh \
    --config <run-config.json> \
    [--gh-config-dir <directory>] \
    <receive|delegate|collect-result|issue-record|observe-owner>

Request JSON is read from stdin.
USAGE
}

protocol_error() {
  local reason="$1"

  printf \
    '{"schemaVersion":1,"state":"error","reason":"%s"}\n' \
    "$reason"

  exit 2
}

require_command() {
  command -v "$1" >/dev/null 2>&1 ||
    protocol_error "dependency_unavailable"
}

ensure_regular_file_lexical() {
  local file="$1"

  [ -n "$file" ] ||
    protocol_error "path_invalid"

  [ -e "$file" ] ||
    protocol_error "path_unavailable"

  [ -f "$file" ] ||
    protocol_error "path_not_regular_file"

  [ ! -L "$file" ] ||
    protocol_error "path_symlink_rejected"
}

ensure_real_directory_lexical() {
  local directory="$1"

  [ -n "$directory" ] ||
    protocol_error "path_invalid"

  if [ -L "$directory" ]; then
    protocol_error "path_symlink_rejected"
  fi

  if [ -e "$directory" ]; then
    [ -d "$directory" ] ||
      protocol_error "path_not_directory"
  else
    mkdir "$directory" ||
      protocol_error "path_create_failed"
  fi

  [ ! -L "$directory" ] ||
    protocol_error "path_symlink_rejected"
}

sha256_file() {
  python3 - "$1" <<'PY'
import hashlib
import sys

with open(sys.argv[1], "rb") as fh:
    digest = hashlib.sha256(
        fh.read()
    ).hexdigest()

sys.stdout.write(
    "sha256:" + digest
)
PY
}

sha256_text() {
  python3 - "$1" <<'PY'
import hashlib
import sys

sys.stdout.write(
    hashlib.sha256(
        sys.argv[1].encode("utf-8")
    ).hexdigest()
)
PY
}

# Execute one trusted assignment-emitting helper without losing its exit status.
#
# Helpers used here emit only NAME=<shlex.quote(value)> assignments.
#
# This construction deliberately avoids:
#
#   eval "$(validator)"
#
# because eval of an empty string returns success even if the validator inside
# command substitution failed.
load_assignments() {
  local assignments

  if ! assignments="$("$@")"; then
    return 1
  fi

  eval "$assignments"
}

json_result() {
  python3 - "$@" <<'PY'
import json
import sys

if (len(sys.argv) - 1) % 2 != 0:
    raise SystemExit(1)

value = {}

for index in range(
    1,
    len(sys.argv),
    2,
):
    key = sys.argv[index]
    raw = sys.argv[index + 1]

    if raw == "__NULL__":
        value[key] = None

    elif raw == "__TRUE__":
        value[key] = True

    elif raw == "__FALSE__":
        value[key] = False

    elif raw.startswith("__INT__:"):
        value[key] = int(
            raw[len("__INT__:"):]
        )

    else:
        value[key] = raw

sys.stdout.write(
    json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    + "\n"
)
PY
}

emit_stopped() {
  local reason="$1"
  local stopped_from="${2:-unknown}"

  write_stopped_state \
    "stopped" \
    "$reason" \
    "$stopped_from"

  json_result \
    schemaVersion "__INT__:1" \
    state "stopped" \
    reason "$reason" \
    stoppedFrom "$stopped_from" \
    runId "$RUN_ID" \
    operation "$OPERATION"
}

emit_unknown() {
  local reason="$1"
  local stopped_from="${2:-unknown}"

  write_stopped_state \
    "stopped_for_unknown" \
    "$reason" \
    "$stopped_from"

  json_result \
    schemaVersion "__INT__:1" \
    state "stopped_for_unknown" \
    reason "$reason" \
    stoppedFrom "$stopped_from" \
    runId "$RUN_ID" \
    operation "$OPERATION"
}

read_json_file_strict() {
  local file="$1"
  local mode="$2"

  python3 \
    - "$file" "$mode" \
    <<'PY'
import json
import os
import re
import shlex
import sys

file_name = sys.argv[1]
mode = sys.argv[2]

CONTROL = re.compile(
    r"[\x00-\x1f\x7f]"
)

POSITIVE = re.compile(
    r"^[1-9][0-9]*$"
)

DIGEST = re.compile(
    r"^sha256:[0-9a-f]{64}$"
)

COMMIT = re.compile(
    r"^[0-9a-f]{40}$"
)

REPO = re.compile(
    r"^[A-Za-z0-9_.-]+/"
    r"[A-Za-z0-9_.-]+$"
)

PILOT_AGENT = (
    "agmsg_pm_pilot_claude"
)

PROVIDER_COMMIT = (
    "0b2117c5f91f7950cc196e52edb188748adfa50a"
)


def reject_duplicates(pairs):
    result = {}

    for key, value in pairs:
        if key in result:
            raise ValueError(
                "duplicate_key"
            )

        result[key] = value

    return result


def load():
    with open(
        file_name,
        "r",
        encoding="utf-8",
    ) as fh:
        return json.load(
            fh,
            object_pairs_hook=(
                reject_duplicates
            ),
        )


def text(
    value,
    name,
    max_bytes=4096,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(name)

    if not value:
        raise ValueError(name)

    if CONTROL.search(value):
        raise ValueError(name)

    if (
        len(
            value.encode("utf-8")
        )
        > max_bytes
    ):
        raise ValueError(name)

    return value


def assign(
    name,
    value,
):
    print(
        name
        + "="
        + shlex.quote(
            str(value)
        )
    )


try:
    obj = load()

    if not isinstance(
        obj,
        dict,
    ):
        raise ValueError(
            "not_object"
        )

    if mode == "config":
        allowed = {
            "schemaVersion",
            "runId",
            "worker",
            "testIssueNumber",
            "repo",
        }

        if set(obj) != allowed:
            raise ValueError(
                "config_fields"
            )

        if (
            obj["schemaVersion"]
            != 1
        ):
            raise ValueError(
                "config_schema"
            )

        run_id = text(
            obj["runId"],
            "runId",
            512,
        )

        worker = text(
            obj["worker"],
            "worker",
            512,
        )

        if worker in {
            "agmsg_pm_claude",
            PILOT_AGENT,
        }:
            raise ValueError(
                "worker_scope"
            )

        issue = (
            obj[
                "testIssueNumber"
            ]
        )

        if (
            not isinstance(
                issue,
                int,
            )
            or isinstance(
                issue,
                bool,
            )
            or issue < 1
        ):
            raise ValueError(
                "testIssueNumber"
            )

        repo = text(
            obj["repo"],
            "repo",
            512,
        )

        if not REPO.fullmatch(
            repo
        ):
            raise ValueError(
                "repo"
            )

        assign(
            "RUN_ID",
            run_id,
        )

        assign(
            "WORKER",
            worker,
        )

        assign(
            "TEST_ISSUE_NUMBER",
            issue,
        )

        assign(
            "REPO",
            repo,
        )

    elif mode == "binding":
        required = {
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

        if set(obj) != required:
            raise ValueError(
                "binding_fields"
            )

        if (
            obj["schemaVersion"]
            != 1
        ):
            raise ValueError(
                "binding_schema"
            )

        team = text(
            obj["team"],
            "team",
            512,
        )

        agent = text(
            obj["agent"],
            "agent",
            512,
        )

        if agent != PILOT_AGENT:
            raise ValueError(
                "binding_agent"
            )

        project = text(
            obj["project"],
            "project",
            4096,
        )

        canonical_project = (
            os.path.realpath(
                project
            )
        )

        if (
            project
            != canonical_project
        ):
            raise ValueError(
                "binding_project"
            )

        session_id = text(
            obj["sessionId"],
            "sessionId",
            512,
        )

        generation = text(
            obj["generation"],
            "generation",
            64,
        )

        if not POSITIVE.fullmatch(
            generation
        ):
            raise ValueError(
                "generation"
            )

        pid = text(
            obj["pid"],
            "pid",
            64,
        )

        if not POSITIVE.fullmatch(
            pid
        ):
            raise ValueError(
                "pid"
            )

        pid_start = text(
            obj["pidStart"],
            "pidStart",
            512,
        )

        profile_digest = text(
            obj["profileDigest"],
            "profileDigest",
            128,
        )

        guard_digest = text(
            obj["guardDigest"],
            "guardDigest",
            128,
        )

        broker_digest = text(
            obj["brokerDigest"],
            "brokerDigest",
            128,
        )

        if not DIGEST.fullmatch(
            profile_digest
        ):
            raise ValueError(
                "profileDigest"
            )

        if not DIGEST.fullmatch(
            guard_digest
        ):
            raise ValueError(
                "guardDigest"
            )

        if not DIGEST.fullmatch(
            broker_digest
        ):
            raise ValueError(
                "brokerDigest"
            )

        policy = text(
            obj["policyVersion"],
            "policyVersion",
            512,
        )

        provider = text(
            obj["providerCommit"],
            "providerCommit",
            64,
        )

        if not COMMIT.fullmatch(
            provider
        ):
            raise ValueError(
                "providerCommit"
            )

        if (
            provider
            != PROVIDER_COMMIT
        ):
            raise ValueError(
                "providerCommit"
            )

        assign(
            "BIND_TEAM",
            team,
        )

        assign(
            "BIND_AGENT",
            agent,
        )

        assign(
            "BIND_PROJECT",
            project,
        )

        assign(
            "BIND_SESSION_ID",
            session_id,
        )

        assign(
            "BIND_GENERATION",
            generation,
        )

        assign(
            "BIND_BROKER_DIGEST",
            broker_digest,
        )

    else:
        raise ValueError(
            "mode"
        )

except Exception:
    raise SystemExit(1)
PY
}
validate_request() {
  python3 \
    - \
    "$REQUEST_FILE" \
    "$OPERATION" \
    "$RUN_ID" \
    "$BIND_TEAM" \
    "$BIND_AGENT" \
    "$BIND_GENERATION" \
    <<'PY'
import json
import re
import shlex
import sys

file_name = sys.argv[1]
expected_operation = sys.argv[2]
expected_run = sys.argv[3]
expected_team = sys.argv[4]
expected_actor = sys.argv[5]
expected_generation = sys.argv[6]

CONTROL = re.compile(
    r"[\x00-\x1f\x7f]"
)

# Issue body is explicitly allowed to contain real LF/CR line breaks.
# Other C0 controls remain forbidden.
CONTROL_BODY = re.compile(
    r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]"
)

POSITIVE = re.compile(
    r"^[1-9][0-9]*$"
)

COMMON = {
    "schemaVersion",
    "runId",
    "requestId",
    "operation",
    "team",
    "actor",
    "generation",
}

ADDITIONAL = {
    "receive": set(),

    "delegate": {
        "inputMessageId",
        "worker",
        "task",
    },

    "collect-result": {
        "delegateMessageId",
    },

    "issue-record": {
        "inputMessageId",
        "delegateMessageId",
        "resultMessageId",
        "body",
    },

    "observe-owner": {
        "agent",
    },
}


def reject_duplicates(pairs):
    result = {}

    for key, value in pairs:
        if key in result:
            raise ValueError(
                "duplicate_key"
            )

        result[key] = value

    return result


def text(
    value,
    name,
    max_bytes=4096,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(name)

    if not value:
        raise ValueError(name)

    if CONTROL.search(value):
        raise ValueError(name)

    if (
        len(
            value.encode("utf-8")
        )
        > max_bytes
    ):
        raise ValueError(name)

    return value


def body_text(
    value,
    name,
    max_bytes=4096,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(name)

    if not value:
        raise ValueError(name)

    if CONTROL_BODY.search(value):
        raise ValueError(name)

    if (
        len(
            value.encode("utf-8")
        )
        > max_bytes
    ):
        raise ValueError(name)

    return value


def assign(
    name,
    value,
):
    print(
        name
        + "="
        + shlex.quote(
            str(value)
        )
    )


def task_is_allowed(value):
    # A task is data, not an executable command.
    #
    # Reject rather than sanitize. Shell/program execution requires a
    # separately authorized worker-side contract and must not be smuggled
    # through this delegation payload.

    if (
        len(
            value.encode("utf-8")
        )
        > 4096
    ):
        return False

    if CONTROL.search(value):
        return False

    forbidden_chars = re.compile(
        r"[;&|`$<>\\]"
    )

    if forbidden_chars.search(
        value
    ):
        return False

    if re.search(
        r"https?://",
        value,
        flags=re.IGNORECASE,
    ):
        return False

    command_words = re.compile(
        r"(^|[\s])"
        r"(?:"
        r"bash|"
        r"sh|"
        r"zsh|"
        r"fish|"
        r"python(?:3)?|"
        r"node|"
        r"ruby|"
        r"perl|"
        r"curl|"
        r"wget|"
        r"ssh|"
        r"scp|"
        r"git|"
        r"gh|"
        r"sudo|"
        r"rm|"
        r"mv|"
        r"cp|"
        r"sqlite3"
        r")"
        r"([\s]|$)",
        flags=re.IGNORECASE,
    )

    if command_words.search(
        value
    ):
        return False

    return True


try:
    with open(
        file_name,
        "r",
        encoding="utf-8",
    ) as fh:
        obj = json.load(
            fh,
            object_pairs_hook=(
                reject_duplicates
            ),
        )

    if not isinstance(
        obj,
        dict,
    ):
        raise ValueError(
            "not_object"
        )

    if (
        expected_operation
        not in ADDITIONAL
    ):
        raise ValueError(
            "operation"
        )

    expected_fields = (
        COMMON
        | ADDITIONAL[
            expected_operation
        ]
    )

    if set(obj) != expected_fields:
        raise ValueError(
            "fields"
        )

    if (
        obj["schemaVersion"]
        != 1
    ):
        raise ValueError(
            "schema"
        )

    run_id = text(
        obj["runId"],
        "runId",
        512,
    )

    request_id = text(
        obj["requestId"],
        "requestId",
        512,
    )

    operation = text(
        obj["operation"],
        "operation",
        64,
    )

    team = text(
        obj["team"],
        "team",
        512,
    )

    actor = text(
        obj["actor"],
        "actor",
        512,
    )

    generation = text(
        obj["generation"],
        "generation",
        64,
    )

    if not POSITIVE.fullmatch(
        generation
    ):
        raise ValueError(
            "generation"
        )

    if run_id != expected_run:
        raise ValueError(
            "run_scope"
        )

    if (
        operation
        != expected_operation
    ):
        raise ValueError(
            "operation_scope"
        )

    if team != expected_team:
        raise ValueError(
            "team_scope"
        )

    if actor != expected_actor:
        raise ValueError(
            "actor_scope"
        )

    if (
        generation
        != expected_generation
    ):
        raise ValueError(
            "generation_scope"
        )

    assign(
        "REQUEST_ID",
        request_id,
    )

    if (
        expected_operation
        == "delegate"
    ):
        input_id = text(
            obj["inputMessageId"],
            "inputMessageId",
            512,
        )

        worker = text(
            obj["worker"],
            "worker",
            512,
        )

        task = text(
            obj["task"],
            "task",
            4096,
        )

        if not task_is_allowed(
            task
        ):
            raise ValueError(
                "task"
            )

        assign(
            "REQ_INPUT_MESSAGE_ID",
            input_id,
        )

        assign(
            "REQ_WORKER",
            worker,
        )

        assign(
            "REQ_TASK",
            task,
        )

    elif (
        expected_operation
        == "collect-result"
    ):
        delegate_id = text(
            obj[
                "delegateMessageId"
            ],
            "delegateMessageId",
            512,
        )

        assign(
            "REQ_DELEGATE_MESSAGE_ID",
            delegate_id,
        )

    elif (
        expected_operation
        == "issue-record"
    ):
        input_id = text(
            obj["inputMessageId"],
            "inputMessageId",
            512,
        )

        delegate_id = text(
            obj[
                "delegateMessageId"
            ],
            "delegateMessageId",
            512,
        )

        result_id = text(
            obj[
                "resultMessageId"
            ],
            "resultMessageId",
            512,
        )

        body = body_text(
            obj["body"],
            "body",
            4096,
        )

        assign(
            "REQ_INPUT_MESSAGE_ID",
            input_id,
        )

        assign(
            "REQ_DELEGATE_MESSAGE_ID",
            delegate_id,
        )

        assign(
            "REQ_RESULT_MESSAGE_ID",
            result_id,
        )

        assign(
            "REQ_BODY",
            body,
        )

    elif (
        expected_operation
        == "observe-owner"
    ):
        agent = text(
            obj["agent"],
            "agent",
            512,
        )

        if (
            agent
            != "agmsg_pm_pilot_claude"
        ):
            raise ValueError(
                "agent_scope"
            )

        assign(
            "REQ_AGENT",
            agent,
        )

except Exception:
    raise SystemExit(1)
PY
}

read_state_assignments() {
  if [ ! -f "$STATE_FILE" ]; then
    printf '%s\n' \
      'STATE_PHASE='

    return 0
  fi

  [ ! -L "$STATE_FILE" ] ||
    return 1

  python3 \
    - "$STATE_FILE" "$RUN_ID" \
    <<'PY'
import json
import shlex
import sys

file_name = sys.argv[1]
expected_run = sys.argv[2]


def reject_duplicates(pairs):
    result = {}

    for key, value in pairs:
        if key in result:
            raise ValueError(
                "duplicate"
            )

        result[key] = value

    return result


def assign(
    name,
    value,
):
    if value is None:
        value = ""

    print(
        name
        + "="
        + shlex.quote(
            str(value)
        )
    )


with open(
    file_name,
    "r",
    encoding="utf-8",
) as fh:
    obj = json.load(
        fh,
        object_pairs_hook=(
            reject_duplicates
        ),
    )

if not isinstance(
    obj,
    dict,
):
    raise SystemExit(1)

if (
    obj.get("schemaVersion")
    != 1
):
    raise SystemExit(1)

if (
    obj.get("runId")
    != expected_run
):
    raise SystemExit(1)

assign(
    "STATE_PHASE",
    obj.get(
        "phase",
        "",
    ),
)

assign(
    "STATE_INPUT_ID",
    obj.get(
        "inputMessageId",
        "",
    ),
)

assign(
    "STATE_OWNER",
    obj.get(
        "owner",
        "",
    ),
)

assign(
    "STATE_REQUEST_ID",
    obj.get(
        "requestId",
        "",
    ),
)

assign(
    "STATE_DELEGATE_ID",
    obj.get(
        "delegateMessageId",
        "",
    ),
)

assign(
    "STATE_INPUT_RECEIPT",
    obj.get(
        "inputReceiptId",
        "",
    ),
)

assign(
    "STATE_RESULT_ID",
    obj.get(
        "resultMessageId",
        "",
    ),
)

assign(
    "STATE_RESULT_RECEIPT",
    obj.get(
        "resultReceiptId",
        "",
    ),
)

assign(
    "STATE_COMMENT_URL",
    obj.get(
        "issueCommentUrl",
        "",
    ),
)
PY
}

write_state() {
  local phase="$1"
  local input_id="${2:-}"
  local owner="${3:-}"
  local request_id="${4:-}"
  local delegate_id="${5:-}"
  local input_receipt="${6:-}"
  local result_id="${7:-}"
  local result_receipt="${8:-}"
  local comment_url="${9:-}"

  python3 \
    - \
    "$STATE_FILE" \
    "$RUN_ID" \
    "$phase" \
    "$input_id" \
    "$owner" \
    "$request_id" \
    "$delegate_id" \
    "$input_receipt" \
    "$result_id" \
    "$result_receipt" \
    "$comment_url" \
    <<'PY'
import json
import os
import secrets
import sys

(
    file_name,
    run_id,
    phase,
    input_id,
    owner,
    request_id,
    delegate_id,
    input_receipt,
    result_id,
    result_receipt,
    comment_url,
) = sys.argv[1:]

value = {
    "schemaVersion": 1,
    "runId": run_id,
    "phase": phase,
}

optional = {
    "inputMessageId": (
        input_id
    ),
    "owner": owner,
    "requestId": request_id,
    "delegateMessageId": (
        delegate_id
    ),
    "inputReceiptId": (
        input_receipt
    ),
    "resultMessageId": (
        result_id
    ),
    "resultReceiptId": (
        result_receipt
    ),
    "issueCommentUrl": (
        comment_url
    ),
}

for key, item in optional.items():
    if item:
        value[key] = item

directory = os.path.dirname(
    file_name
)

temporary = os.path.join(
    directory,
    "."
    + os.path.basename(
        file_name
    )
    + ".tmp."
    + str(os.getpid())
    + "."
    + secrets.token_hex(8),
)

body = (
    json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    + "\n"
)

fd = os.open(
    temporary,
    (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
    ),
    0o600,
)

try:
    with os.fdopen(
        fd,
        "w",
        encoding="utf-8",
    ) as fh:
        fh.write(body)
        fh.flush()
        os.fsync(
            fh.fileno()
        )

    os.replace(
        temporary,
        file_name,
    )

except Exception:
    try:
        os.unlink(
            temporary
        )
    except OSError:
        pass

    raise
PY
}

write_stopped_state() {
  local phase="$1"
  local reason="$2"
  local stopped_from="$3"

  python3 \
    - \
    "$STATE_FILE" \
    "$RUN_ID" \
    "$phase" \
    "$reason" \
    "$stopped_from" \
    <<'PY'
import json
import os
import secrets
import sys

(
    file_name,
    run_id,
    phase,
    reason,
    stopped_from,
) = sys.argv[1:]

previous = {}

try:
    with open(
        file_name,
        "r",
        encoding="utf-8",
    ) as fh:
        candidate = json.load(
            fh
        )

    if (
        isinstance(
            candidate,
            dict,
        )
        and candidate.get(
            "runId"
        )
        == run_id
    ):
        previous = candidate

except FileNotFoundError:
    pass

except Exception:
    # Corrupted local state is fail-closed.
    # Do not preserve unvalidated fields.
    previous = {}

value = dict(previous)

value.update(
    {
        "schemaVersion": 1,
        "runId": run_id,
        "phase": phase,
        "reason": reason,
        "stoppedFrom": (
            stopped_from
        ),
    }
)

directory = os.path.dirname(
    file_name
)

temporary = os.path.join(
    directory,
    "."
    + os.path.basename(
        file_name
    )
    + ".tmp."
    + str(os.getpid())
    + "."
    + secrets.token_hex(8),
)

with open(
    temporary,
    "x",
    encoding="utf-8",
) as fh:
    os.chmod(
        temporary,
        0o600,
    )

    fh.write(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(
                ",",
                ":",
            ),
        )
        + "\n"
    )

    fh.flush()

    os.fsync(
        fh.fileno()
    )

os.replace(
    temporary,
    file_name,
)
PY
}

validate_provider_json() {
  local json="$1"
  local mode="$2"

  python3 \
    - "$json" "$mode" \
    <<'PY'
import json
import shlex
import sys

raw = sys.argv[1]
mode = sys.argv[2]


def assign(
    name,
    value,
):
    if value is None:
        value = ""

    print(
        name
        + "="
        + shlex.quote(
            str(value)
        )
    )


try:
    value = json.loads(raw)

except Exception:
    raise SystemExit(1)

if not isinstance(
    value,
    dict,
):
    raise SystemExit(1)

if (
    value.get(
        "schemaVersion"
    )
    != 1
):
    raise SystemExit(1)

if mode == "peek":
    state = value.get(
        "state"
    )

    if state == "absent":
        if set(value) != {
            "schemaVersion",
            "state",
        }:
            raise SystemExit(1)

        assign(
            "BACKEND_STATE",
            "absent",
        )

        raise SystemExit(0)

    expected = {
        "schemaVersion",
        "state",
        "messageId",
        "from",
        "to",
        "body",
        "createdAt",
    }

    if set(value) != expected:
        raise SystemExit(1)

    if state != "ok":
        raise SystemExit(1)

    for field in (
        "messageId",
        "from",
        "to",
        "body",
        "createdAt",
    ):
        if not isinstance(
            value[field],
            str,
        ):
            raise SystemExit(1)

    assign(
        "BACKEND_STATE",
        state,
    )

    assign(
        "BACKEND_MESSAGE_ID",
        value["messageId"],
    )

    assign(
        "BACKEND_FROM",
        value["from"],
    )

    assign(
        "BACKEND_TO",
        value["to"],
    )

    assign(
        "BACKEND_BODY",
        value["body"],
    )

elif mode in {
    "claim",
    "release",
}:
    expected = {
        "schemaVersion",
        "state",
        "messageId",
        "owner",
    }

    if set(value) != expected:
        raise SystemExit(1)

    wanted = (
        "claimed"
        if mode == "claim"
        else "released"
    )

    if value["state"] != wanted:
        raise SystemExit(1)

    assign(
        "BACKEND_STATE",
        value["state"],
    )

    assign(
        "BACKEND_MESSAGE_ID",
        value["messageId"],
    )

    assign(
        "BACKEND_OWNER",
        value["owner"],
    )

elif mode == "send":
    expected = {
        "schemaVersion",
        "state",
        "messageId",
        "requestId",
        "team",
        "from",
        "to",
    }

    if set(value) != expected:
        raise SystemExit(1)

    if (
        value["state"]
        != "queued"
    ):
        raise SystemExit(1)

    for field in (
        "messageId",
        "requestId",
        "team",
        "from",
        "to",
    ):
        if not isinstance(
            value[field],
            str,
        ):
            raise SystemExit(1)

    assign(
        "BACKEND_STATE",
        "queued",
    )

    assign(
        "BACKEND_MESSAGE_ID",
        value["messageId"],
    )

    assign(
        "BACKEND_REQUEST_ID",
        value["requestId"],
    )

    assign(
        "BACKEND_TEAM",
        value["team"],
    )

    assign(
        "BACKEND_FROM",
        value["from"],
    )

    assign(
        "BACKEND_TO",
        value["to"],
    )

elif mode == "receipt":
    expected = {
        "schemaVersion",
        "state",
        "inputMessageId",
        "requestId",
        "delegateMessageId",
        "receiptId",
        "team",
    }

    if set(value) != expected:
        raise SystemExit(1)

    if (
        value["state"]
        != "recorded"
    ):
        raise SystemExit(1)

    for field in (
        "inputMessageId",
        "requestId",
        "delegateMessageId",
        "receiptId",
        "team",
    ):
        if not isinstance(
            value[field],
            str,
        ):
            raise SystemExit(1)

    assign(
        "BACKEND_STATE",
        "recorded",
    )

    assign(
        "BACKEND_INPUT_MESSAGE_ID",
        value["inputMessageId"],
    )

    assign(
        "BACKEND_REQUEST_ID",
        value["requestId"],
    )

    assign(
        "BACKEND_DELEGATE_MESSAGE_ID",
        value[
            "delegateMessageId"
        ],
    )

    assign(
        "BACKEND_RECEIPT_ID",
        value["receiptId"],
    )

    assign(
        "BACKEND_TEAM",
        value["team"],
    )

elif mode == "ack":
    expected = {
        "schemaVersion",
        "state",
        "messageId",
        "owner",
        "receiptId",
    }

    if set(value) != expected:
        raise SystemExit(1)

    if (
        value["state"]
        != "acked"
    ):
        raise SystemExit(1)

    assign(
        "BACKEND_STATE",
        "acked",
    )

    assign(
        "BACKEND_MESSAGE_ID",
        value["messageId"],
    )

    assign(
        "BACKEND_OWNER",
        value["owner"],
    )

    assign(
        "BACKEND_RECEIPT_ID",
        value["receiptId"],
    )

else:
    raise SystemExit(1)
PY
}

validate_owner_observation() {
  local json="$1"

  python3 \
    - \
    "$json" \
    "$BIND_TEAM" \
    "$PILOT_AGENT" \
    <<'PY'
import json
import shlex
import sys

raw = sys.argv[1]
team = sys.argv[2]
agent = sys.argv[3]

lines = [
    line
    for line in raw.splitlines()
    if line.strip()
]

if len(lines) != 1:
    raise SystemExit(1)

try:
    value = json.loads(
        lines[0]
    )

except Exception:
    raise SystemExit(1)

expected = {
    "schemaVersion",
    "resource",
    "team",
    "agent",
    "status",
    "reason",
    "owner",
    "ownerKind",
    "liveness",
    "consistency",
}

if not isinstance(
    value,
    dict,
):
    raise SystemExit(1)

if set(value) != expected:
    raise SystemExit(1)

if (
    value["schemaVersion"]
    != 1
):
    raise SystemExit(1)

if (
    value["resource"]
    != "actas-owner"
):
    raise SystemExit(1)

if value["team"] != team:
    raise SystemExit(1)

if value["agent"] != agent:
    raise SystemExit(1)

if value["status"] not in {
    "unknown",
    "error",
    "not_found",
    "absent",
    "owned",
    "stale",
}:
    raise SystemExit(1)

for field in (
    "reason",
    "owner",
    "ownerKind",
    "liveness",
):
    if (
        value[field] is not None
        and not isinstance(
            value[field],
            str,
        )
    ):
        raise SystemExit(1)

if not isinstance(
    value["consistency"],
    str,
):
    raise SystemExit(1)


def assign(
    name,
    item,
):
    if item is None:
        item = ""

    print(
        name
        + "="
        + shlex.quote(
            str(item)
        )
    )


assign(
    "OBS_STATUS",
    value["status"],
)

assign(
    "OBS_REASON",
    value["reason"],
)

assign(
    "OBS_OWNER",
    value["owner"],
)

assign(
    "OBS_OWNER_KIND",
    value["ownerKind"],
)

assign(
    "OBS_LIVENESS",
    value["liveness"],
)

assign(
    "OBS_CONSISTENCY",
    value["consistency"],
)
PY
}

validate_result_envelope() {
  local body="$1"

  python3 \
    - \
    "$body" \
    "$REQUEST_ID" \
    "$REQ_DELEGATE_MESSAGE_ID" \
    <<'PY'
import json
import shlex
import sys

raw = sys.argv[1]
expected_request = sys.argv[2]
expected_delegate = sys.argv[3]


def reject_duplicates(pairs):
    result = {}

    for key, value in pairs:
        if key in result:
            raise ValueError(
                "duplicate"
            )

        result[key] = value

    return result


try:
    value = json.loads(
        raw,
        object_pairs_hook=(
            reject_duplicates
        ),
    )

except Exception:
    raise SystemExit(1)

expected = {
    "schemaVersion",
    "requestId",
    "delegateMessageId",
    "result",
}

if not isinstance(
    value,
    dict,
):
    raise SystemExit(1)

if set(value) != expected:
    raise SystemExit(1)

if (
    value["schemaVersion"]
    != 1
):
    raise SystemExit(1)

for field in (
    "requestId",
    "delegateMessageId",
    "result",
):
    if not isinstance(
        value[field],
        str,
    ):
        raise SystemExit(1)

if not value["requestId"]:
    raise SystemExit(1)

if not value[
    "delegateMessageId"
]:
    raise SystemExit(1)

if (
    len(
        value[
            "result"
        ].encode("utf-8")
    )
    > 4096
):
    raise SystemExit(1)

if (
    value["requestId"]
    != expected_request
):
    raise SystemExit(3)

if (
    value[
        "delegateMessageId"
    ]
    != expected_delegate
):
    raise SystemExit(4)

print(
    "RESULT_TEXT="
    + shlex.quote(
        value["result"]
    )
)
PY
}

build_delegate_body() {
  python3 \
    - \
    "$REQUEST_ID" \
    "$REQ_INPUT_MESSAGE_ID" \
    "$REQ_TASK" \
    <<'PY'
import json
import sys

value = {
    "schemaVersion": 1,
    "kind": "p2-delegation",
    "requestId": sys.argv[1],
    "inputMessageId": (
        sys.argv[2]
    ),
    "task": sys.argv[3],
}

sys.stdout.write(
    json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )
)
PY
}

verify_issue_readback() {
  local json="$1"
  local expected_url="$2"
  local expected_body="$3"

  python3 \
    - \
    "$json" \
    "$expected_url" \
    "$expected_body" \
    <<'PY'
import json
import sys

raw = sys.argv[1]
expected_url = sys.argv[2]
expected_body = sys.argv[3]

try:
    value = json.loads(raw)

except Exception:
    raise SystemExit(1)

if not isinstance(
    value,
    dict,
):
    raise SystemExit(1)

comments = value.get(
    "comments"
)

if not isinstance(
    comments,
    list,
):
    raise SystemExit(1)

matches = []

for comment in comments:
    if not isinstance(
        comment,
        dict,
    ):
        continue

    if (
        comment.get("url")
        == expected_url
    ):
        matches.append(
            comment
        )

if len(matches) != 1:
    raise SystemExit(1)

if (
    matches[0].get("body")
    != expected_body
):
    raise SystemExit(2)
PY
}

parse_comment_url() {
  local output="$1"

  python3 \
    - "$output" \
    <<'PY'
import re
import sys

lines = [
    line.strip()
    for line in (
        sys.argv[1]
        .splitlines()
    )
    if line.strip()
]

urls = []

for line in lines:
    if re.fullmatch(
        r"https://github\.com/"
        r"[^/\s]+/[^/\s]+/"
        r"issues/[0-9]+"
        r"#issuecomment-[0-9]+",
        line,
    ):
        urls.append(line)

if len(urls) != 1:
    raise SystemExit(1)

sys.stdout.write(
    urls[0]
)
PY
}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --config)
      [ "$#" -ge 2 ] ||
        protocol_error \
          "arguments_invalid"

      [ -z "$CONFIG_FILE" ] ||
        protocol_error \
          "arguments_invalid"

      CONFIG_FILE="$2"
      shift 2
      ;;

    --gh-config-dir)
      [ "$#" -ge 2 ] ||
        protocol_error \
          "arguments_invalid"

      [ -z "$GH_CONFIG_DIR_ARG" ] ||
        protocol_error \
          "arguments_invalid"

      GH_CONFIG_DIR_ARG="$2"
      shift 2
      ;;

    receive|delegate|collect-result|issue-record|observe-owner)
      [ -z "$OPERATION" ] ||
        protocol_error \
          "arguments_invalid"

      OPERATION="$1"
      shift
      ;;

    -h|--help)
      usage
      exit 0
      ;;

    *)
      protocol_error \
        "arguments_invalid"
      ;;
  esac
done

[ -n "$CONFIG_FILE" ] ||
  protocol_error \
    "config_required"

[ -n "$OPERATION" ] ||
  protocol_error \
    "operation_required"

require_command python3

ensure_regular_file_lexical \
  "$PROVIDER"

ensure_regular_file_lexical \
  "$API"

ensure_regular_file_lexical \
  "$CONFIG_FILE"

BINDING_FILE="${AGMSG_PM_BINDING_FILE:-}"

[ -n "$BINDING_FILE" ] ||
  protocol_error \
    "binding_required"

ensure_regular_file_lexical \
  "$BINDING_FILE"

if ! load_assignments \
  read_json_file_strict \
  "$CONFIG_FILE" \
  config
then
  protocol_error \
    "invalid_config"
fi

if ! load_assignments \
  read_json_file_strict \
  "$BINDING_FILE" \
  binding
then
  protocol_error \
    "invalid_binding"
fi

[ "$BIND_AGENT" = "$PILOT_AGENT" ] ||
  protocol_error \
    "binding_actor_mismatch"

if [ -n "${AGMSG_PM_PILOT_SESSION_ID:-}" ]; then
  [ \
    "$AGMSG_PM_PILOT_SESSION_ID" \
    = "$BIND_SESSION_ID" \
  ] ||
    protocol_error \
      "binding_session_mismatch"
fi

if [ -n "${AGMSG_PM_PROCESS_GENERATION:-}" ]; then
  [ \
    "$AGMSG_PM_PROCESS_GENERATION" \
    = "$BIND_GENERATION" \
  ] ||
    protocol_error \
      "binding_generation_mismatch"
fi

if [ -n "${AGMSG_PM_TEAM:-}" ]; then
  [ \
    "$AGMSG_PM_TEAM" \
    = "$BIND_TEAM" \
  ] ||
    protocol_error \
      "binding_team_mismatch"
fi

if [ -n "${AGMSG_PM_AGENT:-}" ]; then
  [ \
    "$AGMSG_PM_AGENT" \
    = "$BIND_AGENT" \
  ] ||
    protocol_error \
      "binding_actor_mismatch"
fi

CURRENT_BROKER_DIGEST="$(
  sha256_file "$0"
)" ||
  protocol_error \
    "broker_digest_unavailable"

[ \
  "$CURRENT_BROKER_DIGEST" \
  = "$BIND_BROKER_DIGEST" \
] ||
  protocol_error \
    "broker_digest_mismatch"

REQUEST_FILE="$(
  dirname "$BINDING_FILE"
)/.p2-request.$$"

umask 077

cat > "$REQUEST_FILE"

REQUEST_SIZE="$(
  wc -c < "$REQUEST_FILE" |
    tr -d '[:space:]'
)"

case "$REQUEST_SIZE" in
  ''|*[!0-9]*)
    protocol_error \
      "invalid_payload"
    ;;
esac

[ \
  "$REQUEST_SIZE" \
  -le "$MAX_REQUEST_BYTES" \
] ||
  protocol_error \
    "invalid_payload"

if ! load_assignments \
  validate_request
then
  protocol_error \
    "invalid_payload"
fi

RUN_KEY="$(
  sha256_text "$RUN_ID"
)" ||
  protocol_error \
    "state_key_failed"

BINDINGS_DIR="$(
  dirname "$BINDING_FILE"
)"

SEAT_DIR="$(
  dirname "$BINDINGS_DIR"
)"

STATE_DIR="$SEAT_DIR/broker-state"

ensure_real_directory_lexical \
  "$STATE_DIR"

STATE_FILE="$STATE_DIR/$RUN_KEY.json"
LOCK_DIR="$STATE_DIR/$RUN_KEY.lock"

if mkdir \
  "$LOCK_DIR" \
  2>/dev/null
then
  LOCK_HELD=1
else
  json_result \
    schemaVersion "__INT__:1" \
    state "stopped" \
    reason "busy" \
    runId "$RUN_ID" \
    operation "$OPERATION"

  exit 0
fi

if ! load_assignments \
  read_state_assignments
then
  emit_unknown \
    "state_unreadable" \
    "unknown"

  exit 0
fi

OWNER="p2:${BIND_SESSION_ID}:${BIND_GENERATION}:${RUN_ID}"

case "$OPERATION" in
  receive)
    if [ -n "$STATE_PHASE" ]; then
      emit_stopped \
        "invalid_state" \
        "$STATE_PHASE"

      exit 0
    fi

    PEEK_OUTPUT=""

    if ! PEEK_OUTPUT="$(
      "$PROVIDER" \
        message-peek \
        "$BIND_TEAM" \
        "$BIND_AGENT"
    )"; then
      emit_stopped \
        "backend_failed" \
        "peek"

      exit 0
    fi

    BACKEND_STATE=""

    if ! load_assignments \
      validate_provider_json \
      "$PEEK_OUTPUT" \
      peek
    then
      emit_unknown \
        "backend_response_unidentifiable" \
        "peek"

      exit 0
    fi

    if [ \
      "$BACKEND_STATE" \
      = "absent" \
    ]; then
      json_result \
        schemaVersion "__INT__:1" \
        state "absent" \
        runId "$RUN_ID" \
        requestId "$REQUEST_ID" \
        operation "receive" \
        team "$BIND_TEAM" \
        actor "$BIND_AGENT" \
        generation "$BIND_GENERATION"

      exit 0
    fi

    [ "$BACKEND_STATE" = "ok" ] || {
      emit_unknown \
        "backend_response_unidentifiable" \
        "peek"

      exit 0
    }

    if [ \
      "$BACKEND_TO" \
      != "$BIND_AGENT" \
    ]; then
      emit_stopped \
        "recipient_mismatch" \
        "peek"

      exit 0
    fi

    PEEK_MESSAGE_ID="$BACKEND_MESSAGE_ID"

    CLAIM_OUTPUT=""

    if ! CLAIM_OUTPUT="$(
      "$PROVIDER" \
        message-claim \
        "$BIND_TEAM" \
        "$PEEK_MESSAGE_ID" \
        "$OWNER"
    )"; then
      emit_stopped \
        "claim_failed" \
        "peek"

      exit 0
    fi

    if ! load_assignments \
      validate_provider_json \
      "$CLAIM_OUTPUT" \
      claim
    then
      emit_unknown \
        "claim_response_unidentifiable" \
        "peek"

      exit 0
    fi

    if [ \
      "$BACKEND_MESSAGE_ID" \
      != "$PEEK_MESSAGE_ID" \
    ]; then
      emit_unknown \
        "claim_message_mismatch" \
        "peek"

      exit 0
    fi

    if [ \
      "$BACKEND_OWNER" \
      != "$OWNER" \
    ]; then
      emit_unknown \
        "claim_owner_mismatch" \
        "peek"

      exit 0
    fi

    write_state \
      "claimed" \
      "$BACKEND_MESSAGE_ID" \
      "$OWNER"

    json_result \
      schemaVersion "__INT__:1" \
      state "claimed" \
      runId "$RUN_ID" \
      requestId "$REQUEST_ID" \
      operation "receive" \
      team "$BIND_TEAM" \
      actor "$BIND_AGENT" \
      generation "$BIND_GENERATION" \
      inputMessageId "$BACKEND_MESSAGE_ID" \
      owner "$OWNER"
    ;;

  delegate)
    [ \
      "$STATE_PHASE" \
      = "claimed" \
    ] || {
      emit_stopped \
        "invalid_state" \
        "${STATE_PHASE:-none}"

      exit 0
    }

    [ \
      "$STATE_OWNER" \
      = "$OWNER" \
    ] || {
      emit_stopped \
        "owner_mismatch" \
        "$STATE_PHASE"

      exit 0
    }

    [ \
      "$REQ_INPUT_MESSAGE_ID" \
      = "$STATE_INPUT_ID" \
    ] || {
      emit_stopped \
        "input_message_mismatch" \
        "$STATE_PHASE"

      exit 0
    }

    [ \
      "$REQ_WORKER" \
      = "$WORKER" \
    ] || {
      emit_stopped \
        "worker_scope_mismatch" \
        "$STATE_PHASE"

      exit 0
    }

    DELEGATE_BODY="$(
      build_delegate_body
    )" || {
      emit_stopped \
        "delegate_payload_failed" \
        "$STATE_PHASE"

      exit 0
    }

    SEND_OUTPUT=""

    if ! SEND_OUTPUT="$(
      "$PROVIDER" \
        message-send \
        "$BIND_TEAM" \
        "$BIND_AGENT" \
        "$WORKER" \
        "$REQUEST_ID" \
        "$DELEGATE_BODY"
    )"; then
      # A fixed-provider non-zero exit is terminal for this request.
      #
      # Do not retry, ack, release after an ambiguous post-call condition, or
      # fall back to generic Bash/pm-broker.
      emit_stopped \
        "send_failed" \
        "$STATE_PHASE"

      exit 0
    fi

    if ! load_assignments \
      validate_provider_json \
      "$SEND_OUTPUT" \
      send
    then
      emit_unknown \
        "send_response_unidentifiable" \
        "$STATE_PHASE"

      exit 0
    fi

    if [ \
         "$BACKEND_REQUEST_ID" \
         != "$REQUEST_ID" \
       ] ||
       [ \
         "$BACKEND_TEAM" \
         != "$BIND_TEAM" \
       ] ||
       [ \
         "$BACKEND_FROM" \
         != "$BIND_AGENT" \
       ] ||
       [ \
         "$BACKEND_TO" \
         != "$WORKER" \
       ]
    then
      emit_unknown \
        "send_response_mismatch" \
        "$STATE_PHASE"

      exit 0
    fi

    DELEGATE_ID="$BACKEND_MESSAGE_ID"

    # queued is not delivery and not worker execution.
    write_state \
      "delegated" \
      "$STATE_INPUT_ID" \
      "$OWNER" \
      "$REQUEST_ID" \
      "$DELEGATE_ID"

    RECEIPT_OUTPUT=""

    if ! RECEIPT_OUTPUT="$(
      "$PROVIDER" \
        handoff-receipt \
        "$BIND_TEAM" \
        "$STATE_INPUT_ID" \
        "$REQUEST_ID" \
        "$DELEGATE_ID"
    )"; then
      emit_stopped \
        "input_receipt_failed" \
        "delegated"

      exit 0
    fi

    if ! load_assignments \
      validate_provider_json \
      "$RECEIPT_OUTPUT" \
      receipt
    then
      emit_unknown \
        "input_receipt_response_unidentifiable" \
        "delegated"

      exit 0
    fi

    if [ \
         "$BACKEND_INPUT_MESSAGE_ID" \
         != "$STATE_INPUT_ID" \
       ] ||
       [ \
         "$BACKEND_REQUEST_ID" \
         != "$REQUEST_ID" \
       ] ||
       [ \
         "$BACKEND_DELEGATE_MESSAGE_ID" \
         != "$DELEGATE_ID" \
       ] ||
       [ \
         "$BACKEND_TEAM" \
         != "$BIND_TEAM" \
       ]
    then
      emit_unknown \
        "input_receipt_response_mismatch" \
        "delegated"

      exit 0
    fi

    INPUT_RECEIPT="$BACKEND_RECEIPT_ID"

    write_state \
      "waiting_result" \
      "$STATE_INPUT_ID" \
      "$OWNER" \
      "$REQUEST_ID" \
      "$DELEGATE_ID" \
      "$INPUT_RECEIPT"

    json_result \
      schemaVersion "__INT__:1" \
      state "delegated" \
      deliveryState "queued" \
      runId "$RUN_ID" \
      requestId "$REQUEST_ID" \
      operation "delegate" \
      team "$BIND_TEAM" \
      actor "$BIND_AGENT" \
      generation "$BIND_GENERATION" \
      inputMessageId "$STATE_INPUT_ID" \
      delegateMessageId "$DELEGATE_ID" \
      inputReceiptId "$INPUT_RECEIPT" \
      worker "$WORKER"
    ;;

  collect-result)
    [ \
      "$STATE_PHASE" \
      = "waiting_result" \
    ] || {
      emit_stopped \
        "invalid_state" \
        "${STATE_PHASE:-none}"

      exit 0
    }

    [ \
      "$STATE_OWNER" \
      = "$OWNER" \
    ] || {
      emit_stopped \
        "owner_mismatch" \
        "$STATE_PHASE"

      exit 0
    }

    [ \
      "$REQUEST_ID" \
      = "$STATE_REQUEST_ID" \
    ] || {
      emit_stopped \
        "request_mismatch" \
        "$STATE_PHASE"

      exit 0
    }

    [ \
      "$REQ_DELEGATE_MESSAGE_ID" \
      = "$STATE_DELEGATE_ID" \
    ] || {
      emit_stopped \
        "delegate_message_mismatch" \
        "$STATE_PHASE"

      exit 0
    }

    RESULT_PEEK=""

    if ! RESULT_PEEK="$(
      "$PROVIDER" \
        message-peek \
        "$BIND_TEAM" \
        "$BIND_AGENT"
    )"; then
      emit_stopped \
        "result_peek_failed" \
        "$STATE_PHASE"

      exit 0
    fi

    BACKEND_STATE=""

    if ! load_assignments \
      validate_provider_json \
      "$RESULT_PEEK" \
      peek
    then
      emit_unknown \
        "result_peek_unidentifiable" \
        "$STATE_PHASE"

      exit 0
    fi

    if [ \
      "$BACKEND_STATE" \
      = "absent" \
    ]; then
      emit_stopped \
        "result_absent" \
        "$STATE_PHASE"

      exit 0
    fi

    if [ \
      "$BACKEND_TO" \
      != "$BIND_AGENT" \
    ]; then
      emit_stopped \
        "result_recipient_mismatch" \
        "$STATE_PHASE"

      exit 0
    fi

    if [ \
      "$BACKEND_FROM" \
      != "$WORKER" \
    ]; then
      emit_stopped \
        "result_worker_mismatch" \
        "$STATE_PHASE"

      exit 0
    fi

    RESULT_MESSAGE_ID="$BACKEND_MESSAGE_ID"
    RESULT_BODY="$BACKEND_BODY"

    RESULT_VALIDATE_OUTPUT=""
    RESULT_VALIDATE_RC=0

    RESULT_VALIDATE_OUTPUT="$(
      validate_result_envelope \
        "$RESULT_BODY"
    )" ||
      RESULT_VALIDATE_RC="$?"

    case "$RESULT_VALIDATE_RC" in
      0)
        eval "$RESULT_VALIDATE_OUTPUT"
        ;;

      3)
        emit_stopped \
          "result_request_mismatch" \
          "$STATE_PHASE"

        exit 0
        ;;

      4)
        emit_stopped \
          "result_delegate_mismatch" \
          "$STATE_PHASE"

        exit 0
        ;;

      *)
        emit_unknown \
          "result_envelope_unidentifiable" \
          "$STATE_PHASE"

        exit 0
        ;;
    esac

    RESULT_CLAIM_OUTPUT=""

    if ! RESULT_CLAIM_OUTPUT="$(
      "$PROVIDER" \
        message-claim \
        "$BIND_TEAM" \
        "$RESULT_MESSAGE_ID" \
        "$OWNER"
    )"; then
      emit_stopped \
        "result_claim_failed" \
        "$STATE_PHASE"

      exit 0
    fi

    if ! load_assignments \
      validate_provider_json \
      "$RESULT_CLAIM_OUTPUT" \
      claim
    then
      emit_unknown \
        "result_claim_response_unidentifiable" \
        "$STATE_PHASE"

      exit 0
    fi

    if [ \
         "$BACKEND_MESSAGE_ID" \
         != "$RESULT_MESSAGE_ID" \
       ] ||
       [ \
         "$BACKEND_OWNER" \
         != "$OWNER" \
       ]
    then
      emit_unknown \
        "result_claim_response_mismatch" \
        "$STATE_PHASE"

      exit 0
    fi

    write_state \
      "result_claimed" \
      "$STATE_INPUT_ID" \
      "$OWNER" \
      "$REQUEST_ID" \
      "$STATE_DELEGATE_ID" \
      "$STATE_INPUT_RECEIPT" \
      "$RESULT_MESSAGE_ID"

    RESULT_RECEIPT_OUTPUT=""

    if ! RESULT_RECEIPT_OUTPUT="$(
      "$PROVIDER" \
        handoff-receipt \
        "$BIND_TEAM" \
        "$RESULT_MESSAGE_ID" \
        "$REQUEST_ID" \
        "$STATE_DELEGATE_ID"
    )"; then
      emit_stopped \
        "result_receipt_failed" \
        "result_claimed"

      exit 0
    fi

    if ! load_assignments \
      validate_provider_json \
      "$RESULT_RECEIPT_OUTPUT" \
      receipt
    then
      emit_unknown \
        "result_receipt_response_unidentifiable" \
        "result_claimed"

      exit 0
    fi

    if [ \
         "$BACKEND_INPUT_MESSAGE_ID" \
         != "$RESULT_MESSAGE_ID" \
       ] ||
       [ \
         "$BACKEND_REQUEST_ID" \
         != "$REQUEST_ID" \
       ] ||
       [ \
         "$BACKEND_DELEGATE_MESSAGE_ID" \
         != "$STATE_DELEGATE_ID" \
       ] ||
       [ \
         "$BACKEND_TEAM" \
         != "$BIND_TEAM" \
       ]
    then
      emit_unknown \
        "result_receipt_response_mismatch" \
        "result_claimed"

      exit 0
    fi

    RESULT_RECEIPT="$BACKEND_RECEIPT_ID"

    write_state \
      "receipt_recorded" \
      "$STATE_INPUT_ID" \
      "$OWNER" \
      "$REQUEST_ID" \
      "$STATE_DELEGATE_ID" \
      "$STATE_INPUT_RECEIPT" \
      "$RESULT_MESSAGE_ID" \
      "$RESULT_RECEIPT"

    json_result \
      schemaVersion "__INT__:1" \
      state "result_claimed" \
      runId "$RUN_ID" \
      requestId "$REQUEST_ID" \
      operation "collect-result" \
      team "$BIND_TEAM" \
      actor "$BIND_AGENT" \
      generation "$BIND_GENERATION" \
      delegateMessageId "$STATE_DELEGATE_ID" \
      resultMessageId "$RESULT_MESSAGE_ID" \
      owner "$OWNER" \
      resultReceiptId "$RESULT_RECEIPT" \
      result "$RESULT_TEXT"
    ;;

  issue-record)
    [ \
      "$STATE_PHASE" \
      = "receipt_recorded" \
    ] || {
      emit_stopped \
        "invalid_state" \
        "${STATE_PHASE:-none}"

      exit 0
    }

    [ \
      "$STATE_OWNER" \
      = "$OWNER" \
    ] || {
      emit_stopped \
        "owner_mismatch" \
        "$STATE_PHASE"

      exit 0
    }

    [ \
      "$REQUEST_ID" \
      = "$STATE_REQUEST_ID" \
    ] || {
      emit_stopped \
        "request_mismatch" \
        "$STATE_PHASE"

      exit 0
    }

    [ \
      "$REQ_INPUT_MESSAGE_ID" \
      = "$STATE_INPUT_ID" \
    ] || {
      emit_stopped \
        "input_message_mismatch" \
        "$STATE_PHASE"

      exit 0
    }

    [ \
      "$REQ_DELEGATE_MESSAGE_ID" \
      = "$STATE_DELEGATE_ID" \
    ] || {
      emit_stopped \
        "delegate_message_mismatch" \
        "$STATE_PHASE"

      exit 0
    }

    [ \
      "$REQ_RESULT_MESSAGE_ID" \
      = "$STATE_RESULT_ID" \
    ] || {
      emit_stopped \
        "result_message_mismatch" \
        "$STATE_PHASE"

      exit 0
    }

    [ -n "$STATE_INPUT_RECEIPT" ] || {
      emit_stopped \
        "input_receipt_missing" \
        "$STATE_PHASE"

      exit 0
    }

    [ -n "$STATE_RESULT_RECEIPT" ] || {
      emit_stopped \
        "result_receipt_missing" \
        "$STATE_PHASE"

      exit 0
    }

    [ -n "$GH_CONFIG_DIR_ARG" ] ||
      protocol_error \
        "gh_config_dir_required"

    ensure_real_directory_lexical \
      "$GH_CONFIG_DIR_ARG"

    require_command gh

    ISSUE_BODY_FILE="$STATE_DIR/.issue-body.$$"

    printf '%s' \
      "$REQ_BODY" \
      > "$ISSUE_BODY_FILE"

    BODY_SIZE="$(
      wc -c < "$ISSUE_BODY_FILE" |
        tr -d '[:space:]'
    )"

    case "$BODY_SIZE" in
      ''|*[!0-9]*)
        protocol_error \
          "invalid_payload"
        ;;
    esac

    [ \
      "$BODY_SIZE" \
      -le "$MAX_TEXT_BYTES" \
    ] ||
      protocol_error \
        "invalid_payload"

    COMMENT_OUTPUT=""

    if ! COMMENT_OUTPUT="$(
      env \
        GH_CONFIG_DIR="$GH_CONFIG_DIR_ARG" \
        gh issue comment \
          "$TEST_ISSUE_NUMBER" \
          --repo "$REPO" \
          --body-file "$ISSUE_BODY_FILE"
    )"; then
      # The remote write may have occurred before the local command became
      # uncertain. Never retry automatically.
      emit_unknown \
        "issue_write_unknown" \
        "$STATE_PHASE"

      exit 0
    fi

    COMMENT_URL=""

    if ! COMMENT_URL="$(
      parse_comment_url \
        "$COMMENT_OUTPUT"
    )"; then
      emit_unknown \
        "issue_write_result_unidentifiable" \
        "$STATE_PHASE"

      exit 0
    fi

    VIEW_OUTPUT=""

    if ! VIEW_OUTPUT="$(
      env \
        GH_CONFIG_DIR="$GH_CONFIG_DIR_ARG" \
        gh issue view \
          "$TEST_ISSUE_NUMBER" \
          --repo "$REPO" \
          --json comments
    )"; then
      emit_unknown \
        "issue_readback_unknown" \
        "$STATE_PHASE"

      exit 0
    fi

    READBACK_RC=0

    verify_issue_readback \
      "$VIEW_OUTPUT" \
      "$COMMENT_URL" \
      "$REQ_BODY" ||
      READBACK_RC="$?"

    case "$READBACK_RC" in
      0)
        ;;

      2)
        emit_unknown \
          "issue_body_mismatch" \
          "$STATE_PHASE"

        exit 0
        ;;

      *)
        emit_unknown \
          "issue_readback_unidentifiable" \
          "$STATE_PHASE"

        exit 0
        ;;
    esac

    write_state \
      "issue_recorded" \
      "$STATE_INPUT_ID" \
      "$OWNER" \
      "$REQUEST_ID" \
      "$STATE_DELEGATE_ID" \
      "$STATE_INPUT_RECEIPT" \
      "$STATE_RESULT_ID" \
      "$STATE_RESULT_RECEIPT" \
      "$COMMENT_URL"

    INPUT_ACK_OUTPUT=""

    if ! INPUT_ACK_OUTPUT="$(
      "$PROVIDER" \
        message-ack \
        "$BIND_TEAM" \
        "$STATE_INPUT_ID" \
        "$OWNER" \
        "$STATE_INPUT_RECEIPT"
    )"; then
      emit_stopped \
        "input_ack_failed" \
        "issue_recorded"

      exit 0
    fi

    if ! load_assignments \
      validate_provider_json \
      "$INPUT_ACK_OUTPUT" \
      ack
    then
      emit_unknown \
        "input_ack_response_unidentifiable" \
        "issue_recorded"

      exit 0
    fi

    if [ \
         "$BACKEND_MESSAGE_ID" \
         != "$STATE_INPUT_ID" \
       ] ||
       [ \
         "$BACKEND_OWNER" \
         != "$OWNER" \
       ] ||
       [ \
         "$BACKEND_RECEIPT_ID" \
         != "$STATE_INPUT_RECEIPT" \
       ]
    then
      emit_unknown \
        "input_ack_response_mismatch" \
        "issue_recorded"

      exit 0
    fi

    # Record the partial boundary before the second ack. If that ack fails,
    # never hide or automatically replay the first acknowledgement.
    write_state \
      "input_acked" \
      "$STATE_INPUT_ID" \
      "$OWNER" \
      "$REQUEST_ID" \
      "$STATE_DELEGATE_ID" \
      "$STATE_INPUT_RECEIPT" \
      "$STATE_RESULT_ID" \
      "$STATE_RESULT_RECEIPT" \
      "$COMMENT_URL"

    RESULT_ACK_OUTPUT=""

    if ! RESULT_ACK_OUTPUT="$(
      "$PROVIDER" \
        message-ack \
        "$BIND_TEAM" \
        "$STATE_RESULT_ID" \
        "$OWNER" \
        "$STATE_RESULT_RECEIPT"
    )"; then
      emit_stopped \
        "result_ack_failed" \
        "input_acked"

      exit 0
    fi

    if ! load_assignments \
      validate_provider_json \
      "$RESULT_ACK_OUTPUT" \
      ack
    then
      emit_unknown \
        "result_ack_response_unidentifiable" \
        "input_acked"

      exit 0
    fi

    if [ \
         "$BACKEND_MESSAGE_ID" \
         != "$STATE_RESULT_ID" \
       ] ||
       [ \
         "$BACKEND_OWNER" \
         != "$OWNER" \
       ] ||
       [ \
         "$BACKEND_RECEIPT_ID" \
         != "$STATE_RESULT_RECEIPT" \
       ]
    then
      emit_unknown \
        "result_ack_response_mismatch" \
        "input_acked"

      exit 0
    fi

    write_state \
      "acked" \
      "$STATE_INPUT_ID" \
      "$OWNER" \
      "$REQUEST_ID" \
      "$STATE_DELEGATE_ID" \
      "$STATE_INPUT_RECEIPT" \
      "$STATE_RESULT_ID" \
      "$STATE_RESULT_RECEIPT" \
      "$COMMENT_URL"

    json_result \
      schemaVersion "__INT__:1" \
      state "acked" \
      runId "$RUN_ID" \
      requestId "$REQUEST_ID" \
      operation "issue-record" \
      team "$BIND_TEAM" \
      actor "$BIND_AGENT" \
      generation "$BIND_GENERATION" \
      inputMessageId "$STATE_INPUT_ID" \
      delegateMessageId "$STATE_DELEGATE_ID" \
      resultMessageId "$STATE_RESULT_ID" \
      issueNumber "__INT__:$TEST_ISSUE_NUMBER" \
      commentUrl "$COMMENT_URL"
    ;;

  observe-owner)
    [ \
      "$REQ_AGENT" \
      = "$PILOT_AGENT" \
    ] || {
      emit_stopped \
        "agent_scope_mismatch" \
        "${STATE_PHASE:-observation}"

      exit 0
    }

    OBS_OUTPUT=""

    if ! OBS_OUTPUT="$(
      "$API" \
        get \
        teams \
        "$BIND_TEAM" \
        actas-owner \
        "$PILOT_AGENT" \
        --schema-version \
        1
    )"; then
      json_result \
        schemaVersion "__INT__:1" \
        state "stopped_for_unknown" \
        reason "owner_observation_failed" \
        runId "$RUN_ID" \
        requestId "$REQUEST_ID" \
        operation "observe-owner" \
        team "$BIND_TEAM" \
        actor "$BIND_AGENT" \
        generation "$BIND_GENERATION"

      exit 0
    fi

    if ! load_assignments \
      validate_owner_observation \
      "$OBS_OUTPUT"
    then
      json_result \
        schemaVersion "__INT__:1" \
        state "stopped_for_unknown" \
        reason "owner_observation_unidentifiable" \
        runId "$RUN_ID" \
        requestId "$REQUEST_ID" \
        operation "observe-owner" \
        team "$BIND_TEAM" \
        actor "$BIND_AGENT" \
        generation "$BIND_GENERATION"

      exit 0
    fi

    case "$OBS_STATUS" in
      unknown|error)
        json_result \
          schemaVersion "__INT__:1" \
          state "stopped_for_unknown" \
          reason "owner_observation_$OBS_STATUS" \
          runId "$RUN_ID" \
          requestId "$REQUEST_ID" \
          operation "observe-owner" \
          team "$BIND_TEAM" \
          actor "$BIND_AGENT" \
          generation "$BIND_GENERATION" \
          ownerStatus "$OBS_STATUS" \
          owner "${OBS_OWNER:-__NULL__}" \
          ownerKind "${OBS_OWNER_KIND:-__NULL__}" \
          liveness "${OBS_LIVENESS:-__NULL__}" \
          consistency "$OBS_CONSISTENCY"
        ;;

      owned|absent|stale|not_found)
        # These are explicit B2 observations, not authorization decisions.
        # In particular, absent/not_found/stale MUST NOT be inferred from
        # unknown/error; they are surfaced only when B2 returned them.
        json_result \
          schemaVersion "__INT__:1" \
          state "observed" \
          runId "$RUN_ID" \
          requestId "$REQUEST_ID" \
          operation "observe-owner" \
          team "$BIND_TEAM" \
          actor "$BIND_AGENT" \
          generation "$BIND_GENERATION" \
          ownerStatus "$OBS_STATUS" \
          owner "${OBS_OWNER:-__NULL__}" \
          ownerKind "${OBS_OWNER_KIND:-__NULL__}" \
          liveness "${OBS_LIVENESS:-__NULL__}" \
          consistency "$OBS_CONSISTENCY"
        ;;

      *)
        json_result \
          schemaVersion "__INT__:1" \
          state "stopped_for_unknown" \
          reason "owner_observation_unidentifiable" \
          runId "$RUN_ID" \
          requestId "$REQUEST_ID" \
          operation "observe-owner" \
          team "$BIND_TEAM" \
          actor "$BIND_AGENT" \
          generation "$BIND_GENERATION"
        ;;
    esac
    ;;

  *)
    # Unreachable after the fixed argument parser. Retain this branch so any
    # future dispatcher expansion remains explicit in code review.
    protocol_error \
      "operation_not_allowed"
    ;;
esac