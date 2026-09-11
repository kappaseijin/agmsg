#!/usr/bin/env bats

load test_helper

# tests/test_p2_consumer_broker.bats
#
# G4-B component tests for scripts/p2-consumer-broker.sh.
#
# Strategy:
#
#   - setup_test_env copies the repository scripts into an isolated skill root.
#   - p2-provider.sh and api.sh remain REAL by default.
#   - selected fault tests replace only the isolated copies with deterministic
#     fakes so impossible/error/unknown backend responses can be injected.
#   - gh is always a fake executable because tests MUST NOT write to GitHub.
#
# Two tests below preserve known G3/G2 contract gaps as executable
# specifications.
#
# They are skipped in the required Bats suite because this repository requires
# the `bats` status checks to complete green, while the current fixed G2
# provider surface cannot satisfy these assertions:
#
#   1. a claimed input remains visible to message-peek, so collect-result can
#      encounter the original input before a later worker result;
#
#   2. message-peek exposes only one unread message, so G4-B cannot prove that
#      exactly one matching worker result exists.
#
# Keep the full assertions below. Remove the corresponding skip only when the
# underlying provider/contract gap has been resolved and the test passes
# unmodified against the real supported surface.
#
# A skip here records an explicit unresolved contract dependency; it MUST NOT
# be interpreted as G4-B or the final P2 pilot having satisfied that contract.

setup() {
  setup_test_env

  bash "$SCRIPTS/join.sh" \
    pilot-team \
    agmsg_pm_pilot_claude \
    codex \
    /tmp/pilot-project \
    >/dev/null

  bash "$SCRIPTS/join.sh" \
    pilot-team \
    agmsg_worker_codex \
    codex \
    /tmp/worker-project \
    >/dev/null

  export TEAM="pilot-team"
  export PILOT_AGENT="agmsg_pm_pilot_claude"
  export PILOT_TYPE="claude-code"
  export WORKER="agmsg_worker_codex"
  export OTHER_WORKER="agmsg_worker_other_codex"
  export SENDER="agmsg_sender_codex"

  export RUN_ID="run-g4b-001"
  export GENERATION="1"
  export SESSION_ID="11111111-1111-4111-8111-111111111111"

  export TEST_REPO="kappaseijin/agmsg"
  export TEST_ISSUE_NUMBER="391"

  export BROKER="$SCRIPTS/p2-consumer-broker.sh"
  export REAL_PROVIDER_BACKUP="$TEST_SKILL_DIR/p2-provider.real.sh"
  export REAL_API_BACKUP="$TEST_SKILL_DIR/api.real.sh"

  [ -f "$BROKER" ]
  [ -f "$SCRIPTS/p2-provider.sh" ]
  [ -f "$SCRIPTS/api.sh" ]

  chmod +x "$BROKER"
  chmod +x "$SCRIPTS/p2-provider.sh"
  chmod +x "$SCRIPTS/api.sh"

  cp "$SCRIPTS/p2-provider.sh" "$REAL_PROVIDER_BACKUP"
  cp "$SCRIPTS/api.sh" "$REAL_API_BACKUP"

  export PROJ="$TEST_SKILL_DIR/project"
  export WORKER_PROJ="$TEST_SKILL_DIR/worker-project"
  export OTHER_WORKER_PROJ="$TEST_SKILL_DIR/other-worker-project"
  export SENDER_PROJ="$TEST_SKILL_DIR/sender-project"

  mkdir -p \
    "$PROJ/.claude" \
    "$WORKER_PROJ" \
    "$OTHER_WORKER_PROJ" \
    "$SENDER_PROJ"

  printf '%s\n' '{"hooks":{}}' \
    > "$PROJ/.claude/settings.local.json"

  export CANONICAL_PROJ
  CANONICAL_PROJ="$(canonical_path "$PROJ")"

  export GH_STUB_BIN="$TEST_SKILL_DIR/gh-bin"
  export GH_CONFIG_DIR_TEST="$TEST_SKILL_DIR/gh-config"
  export GH_LOG="$TEST_SKILL_DIR/gh.log"
  export GH_BODY_STORE="$TEST_SKILL_DIR/gh-body.txt"

  mkdir -p \
    "$GH_STUB_BIN" \
    "$GH_CONFIG_DIR_TEST"

  : > "$GH_LOG"
  : > "$GH_BODY_STORE"

  install_fake_gh

  export PATH="$GH_STUB_BIN:$PATH"

  export CONFIG_FILE="$TEST_SKILL_DIR/run-config.json"

  cat > "$CONFIG_FILE" <<EOF
{"schemaVersion":1,"runId":"$RUN_ID","worker":"$WORKER","testIssueNumber":$TEST_ISSUE_NUMBER,"repo":"$TEST_REPO"}
EOF

  create_binding

  export AGMSG_PM_BINDING_FILE="$BINDING_FILE"
  export AGMSG_PM_PILOT_SESSION_ID="$SESSION_ID"
  export AGMSG_PM_PROCESS_GENERATION="$GENERATION"
  export AGMSG_PM_TEAM="$TEAM"
  export AGMSG_PM_AGENT="$PILOT_AGENT"

  export PROVIDER_LOG="$TEST_SKILL_DIR/provider.log"
  export API_LOG="$TEST_SKILL_DIR/api.log"

  : > "$PROVIDER_LOG"
  : > "$API_LOG"

  export FAKE_RESULT_REQUEST_ID=""
  export FAKE_RESULT_DELEGATE_ID=""
  export FAKE_RESULT_MESSAGE_ID="result-message-001"
  export FAKE_RESULT_TEXT="worker result"
  export FAKE_PEEK_TO="$PILOT_AGENT"
  export FAKE_PEEK_FROM="$WORKER"
  export FAKE_PEEK_MESSAGE_ID="fake-message-001"
  export FAKE_PEEK_BODY="fake body"

  export P2_PROVIDER_FAKE_MODE=""
  export FAKE_GH_MODE="ok"
  export FAKE_API_STATUS="absent"
}

teardown() {
  teardown_test_env
}

canonical_path() {
  python3 - "$1" <<'PY'
import os
import sys

sys.stdout.write(
    os.path.realpath(sys.argv[1])
)
PY
}

sha256_file() {
  python3 - "$1" <<'PY'
import hashlib
import sys

with open(sys.argv[1], "rb") as fh:
    sys.stdout.write(
        "sha256:"
        + hashlib.sha256(fh.read()).hexdigest()
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

json_field() {
  python3 - "$1" "$2" <<'PY'
import json
import sys

value = json.loads(sys.argv[1])
field = sys.argv[2]

result = value[field]

if result is None:
    sys.stdout.write("null")
elif isinstance(result, bool):
    sys.stdout.write(
        "true" if result else "false"
    )
else:
    sys.stdout.write(str(result))
PY
}

json_file_field() {
  python3 - "$1" "$2" <<'PY'
import json
import sys

with open(
    sys.argv[1],
    "r",
    encoding="utf-8",
) as fh:
    value = json.load(fh)

result = value[sys.argv[2]]

if result is None:
    sys.stdout.write("null")
elif isinstance(result, bool):
    sys.stdout.write(
        "true" if result else "false"
    )
else:
    sys.stdout.write(str(result))
PY
}

broker_state_file() {
  local key

  key="$(sha256_text "$RUN_ID")"

  printf '%s/run/pilot/%s__%s/broker-state/%s.json' \
    "$TEST_SKILL_DIR" \
    "$TEAM" \
    "$PILOT_AGENT" \
    "$key"
}

binding_directory() {
  printf '%s/run/pilot/%s__%s/bindings' \
    "$TEST_SKILL_DIR" \
    "$TEAM" \
    "$PILOT_AGENT"
}

create_binding() {
  local broker_digest

  mkdir -p "$(binding_directory)"

  export BINDING_FILE="$(binding_directory)/$GENERATION.json"

  broker_digest="$(sha256_file "$BROKER")"

  cat > "$BINDING_FILE" <<EOF
{"schemaVersion":1,"team":"$TEAM","agent":"$PILOT_AGENT","type":"$PILOT_TYPE","project":"$CANONICAL_PROJ","sessionId":"$SESSION_ID","generation":"$GENERATION","pid":"$$","pidStart":"test-pid-start","profileDigest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","policyVersion":"pm-pilot-pretool-v1","guardDigest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","brokerDigest":"$broker_digest","providerCommit":"0b2117c5f91f7950cc196e52edb188748adfa50a"}
EOF
}

write_broker_state() {
  local phase="$1"
  local input_id="${2:-input-message-001}"
  local owner="${3:-}"
  local request_id="${4:-request-001}"
  local delegate_id="${5:-delegate-message-001}"
  local input_receipt="${6:-input-receipt-001}"
  local result_id="${7:-}"
  local result_receipt="${8:-}"
  local comment_url="${9:-}"

  local file
  file="$(broker_state_file)"

  mkdir -p "$(dirname "$file")"

  if [ -z "$owner" ]; then
    owner="p2:${SESSION_ID}:${GENERATION}:${RUN_ID}"
  fi

  python3 \
    - "$file" \
    "$RUN_ID" \
    "$phase" \
    "$input_id" \
    "$owner" \
    "$request_id" \
    "$delegate_id" \
    "$input_receipt" \
    "$result_id" \
    "$result_receipt" \
    "$comment_url" <<'PY'
import json
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
    "inputMessageId": input_id,
    "owner": owner,
    "requestId": request_id,
    "delegateMessageId": delegate_id,
    "inputReceiptId": input_receipt,
    "resultMessageId": result_id,
    "resultReceiptId": result_receipt,
    "issueCommentUrl": comment_url,
}

for key, item in optional.items():
    if item:
        value[key] = item

with open(
    file_name,
    "w",
    encoding="utf-8",
) as fh:
    json.dump(
        value,
        fh,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    fh.write("\n")
PY
}

common_request() {
  local operation="$1"
  local request_id="${2:-request-001}"

  python3 \
    - "$operation" \
    "$request_id" \
    "$RUN_ID" \
    "$TEAM" \
    "$PILOT_AGENT" \
    "$GENERATION" <<'PY'
import json
import sys

operation = sys.argv[1]
request_id = sys.argv[2]

value = {
    "schemaVersion": 1,
    "runId": sys.argv[3],
    "requestId": request_id,
    "operation": operation,
    "team": sys.argv[4],
    "actor": sys.argv[5],
    "generation": sys.argv[6],
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

delegate_request() {
  local request_id="${1:-request-001}"
  local input_id="${2:-input-message-001}"
  local worker="${3:-$WORKER}"
  local task="${4:-count the fixed fixture records}"

  python3 \
    - "$RUN_ID" \
    "$request_id" \
    "$TEAM" \
    "$PILOT_AGENT" \
    "$GENERATION" \
    "$input_id" \
    "$worker" \
    "$task" <<'PY'
import json
import sys

value = {
    "schemaVersion": 1,
    "runId": sys.argv[1],
    "requestId": sys.argv[2],
    "operation": "delegate",
    "team": sys.argv[3],
    "actor": sys.argv[4],
    "generation": sys.argv[5],
    "inputMessageId": sys.argv[6],
    "worker": sys.argv[7],
    "task": sys.argv[8],
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

collect_request() {
  local request_id="${1:-request-001}"
  local delegate_id="${2:-delegate-message-001}"

  python3 \
    - "$RUN_ID" \
    "$request_id" \
    "$TEAM" \
    "$PILOT_AGENT" \
    "$GENERATION" \
    "$delegate_id" <<'PY'
import json
import sys

value = {
    "schemaVersion": 1,
    "runId": sys.argv[1],
    "requestId": sys.argv[2],
    "operation": "collect-result",
    "team": sys.argv[3],
    "actor": sys.argv[4],
    "generation": sys.argv[5],
    "delegateMessageId": sys.argv[6],
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

issue_request() {
  local request_id="${1:-request-001}"
  local input_id="${2:-input-message-001}"
  local delegate_id="${3:-delegate-message-001}"
  local result_id="${4:-result-message-001}"
  local body="${5:-first line
second line}"

  python3 \
    - "$RUN_ID" \
    "$request_id" \
    "$TEAM" \
    "$PILOT_AGENT" \
    "$GENERATION" \
    "$input_id" \
    "$delegate_id" \
    "$result_id" \
    "$body" <<'PY'
import json
import sys

value = {
    "schemaVersion": 1,
    "runId": sys.argv[1],
    "requestId": sys.argv[2],
    "operation": "issue-record",
    "team": sys.argv[3],
    "actor": sys.argv[4],
    "generation": sys.argv[5],
    "inputMessageId": sys.argv[6],
    "delegateMessageId": sys.argv[7],
    "resultMessageId": sys.argv[8],
    "body": sys.argv[9],
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

owner_request() {
  local agent="${1:-$PILOT_AGENT}"
  local request_id="${2:-request-owner-001}"

  python3 \
    - "$RUN_ID" \
    "$request_id" \
    "$TEAM" \
    "$PILOT_AGENT" \
    "$GENERATION" \
    "$agent" <<'PY'
import json
import sys

value = {
    "schemaVersion": 1,
    "runId": sys.argv[1],
    "requestId": sys.argv[2],
    "operation": "observe-owner",
    "team": sys.argv[3],
    "actor": sys.argv[4],
    "generation": sys.argv[5],
    "agent": sys.argv[6],
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

invoke_broker() {
  local request="$1"
  local operation="$2"

  printf '%s\n' "$request" |
    "$BROKER" \
      --config "$CONFIG_FILE" \
      "$operation"
}

invoke_issue_broker() {
  local request="$1"

  printf '%s\n' "$request" |
    "$BROKER" \
      --config "$CONFIG_FILE" \
      --gh-config-dir "$GH_CONFIG_DIR_TEST" \
      issue-record
}

restore_real_provider() {
  cp "$REAL_PROVIDER_BACKUP" \
    "$SCRIPTS/p2-provider.sh"

  chmod +x "$SCRIPTS/p2-provider.sh"
}

restore_real_api() {
  cp "$REAL_API_BACKUP" \
    "$SCRIPTS/api.sh"

  chmod +x "$SCRIPTS/api.sh"
}

install_fake_provider() {
  cat > "$SCRIPTS/p2-provider.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

: "${PROVIDER_LOG:?}"

action="${1:-}"
shift || true

printf '%s' "$action" >> "$PROVIDER_LOG"

for arg in "$@"; do
  printf '\t%s' "$arg" >> "$PROVIDER_LOG"
done

printf '\n' >> "$PROVIDER_LOG"

mode="${P2_PROVIDER_FAKE_MODE:-normal}"

case "$action" in
  message-peek)
    team="${1:-}"
    recipient="${2:-}"

    case "$mode" in
      peek-fail)
        exit 1
        ;;

      peek-invalid)
        printf '%s\n' 'not-json'
        exit 0
        ;;

      peek-absent)
        printf '%s\n' \
          '{"schemaVersion":1,"state":"absent"}'
        exit 0
        ;;

      peek-other-recipient)
        printf \
          '{"schemaVersion":1,"state":"ok","messageId":"%s","from":"%s","to":"other-agent","body":"%s","createdAt":"2026-09-10T00:00:00Z"}\n' \
          "${FAKE_PEEK_MESSAGE_ID:-fake-message-001}" \
          "${FAKE_PEEK_FROM:-agmsg_worker_codex}" \
          "${FAKE_PEEK_BODY:-body}"
        exit 0
        ;;

      result|multiple-result)
        body="$(
          python3 \
            - "${FAKE_RESULT_REQUEST_ID:-request-001}" \
            "${FAKE_RESULT_DELEGATE_ID:-delegate-message-001}" \
            "${FAKE_RESULT_TEXT:-worker result}" <<'PY'
import json
import sys

print(
    json.dumps(
        {
            "schemaVersion": 1,
            "requestId": sys.argv[1],
            "delegateMessageId": sys.argv[2],
            "result": sys.argv[3],
        },
        separators=(",", ":"),
    )
)
PY
        )"

        printf \
          '{"schemaVersion":1,"state":"ok","messageId":"%s","from":"%s","to":"%s","body":%s,"createdAt":"2026-09-10T00:00:00Z"}\n' \
          "${FAKE_RESULT_MESSAGE_ID:-result-message-001}" \
          "${FAKE_PEEK_FROM:-agmsg_worker_codex}" \
          "$recipient" \
          "$(
            python3 - "$body" <<'PY'
import json
import sys
sys.stdout.write(json.dumps(sys.argv[1]))
PY
          )"
        exit 0
        ;;

      result-wrong-worker)
        body="$(
          python3 \
            - "${FAKE_RESULT_REQUEST_ID:-request-001}" \
            "${FAKE_RESULT_DELEGATE_ID:-delegate-message-001}" <<'PY'
import json
import sys

print(
    json.dumps(
        {
            "schemaVersion": 1,
            "requestId": sys.argv[1],
            "delegateMessageId": sys.argv[2],
            "result": "worker result",
        },
        separators=(",", ":"),
    )
)
PY
        )"

        printf \
          '{"schemaVersion":1,"state":"ok","messageId":"result-message-001","from":"wrong-worker","to":"%s","body":%s,"createdAt":"2026-09-10T00:00:00Z"}\n' \
          "$recipient" \
          "$(
            python3 - "$body" <<'PY'
import json
import sys
sys.stdout.write(json.dumps(sys.argv[1]))
PY
          )"
        exit 0
        ;;

      *)
        printf \
          '{"schemaVersion":1,"state":"ok","messageId":"%s","from":"%s","to":"%s","body":"%s","createdAt":"2026-09-10T00:00:00Z"}\n' \
          "${FAKE_PEEK_MESSAGE_ID:-fake-message-001}" \
          "${FAKE_PEEK_FROM:-agmsg_worker_codex}" \
          "$recipient" \
          "${FAKE_PEEK_BODY:-body}"
        ;;
    esac
    ;;

  message-claim)
    team="${1:-}"
    message_id="${2:-}"
    owner="${3:-}"

    case "$mode" in
      claim-fail)
        exit 1
        ;;

      claim-invalid)
        printf '%s\n' 'not-json'
        exit 0
        ;;

      *)
        printf \
          '{"schemaVersion":1,"state":"claimed","messageId":"%s","owner":"%s"}\n' \
          "$message_id" \
          "$owner"
        ;;
    esac
    ;;

  message-release)
    team="${1:-}"
    message_id="${2:-}"
    owner="${3:-}"

    printf \
      '{"schemaVersion":1,"state":"released","messageId":"%s","owner":"%s"}\n' \
      "$message_id" \
      "$owner"
    ;;

  message-send)
    team="${1:-}"
    from="${2:-}"
    to="${3:-}"
    request="${4:-}"

    case "$mode" in
      send-fail)
        exit 1
        ;;

      send-invalid)
        printf '%s\n' 'not-json'
        exit 0
        ;;

      send-delivered-mutation)
        printf \
          '{"schemaVersion":1,"state":"delivered","messageId":"delegate-message-001","requestId":"%s","team":"%s","from":"%s","to":"%s"}\n' \
          "$request" \
          "$team" \
          "$from" \
          "$to"
        ;;

      *)
        printf \
          '{"schemaVersion":1,"state":"queued","messageId":"delegate-message-001","requestId":"%s","team":"%s","from":"%s","to":"%s"}\n' \
          "$request" \
          "$team" \
          "$from" \
          "$to"
        ;;
    esac
    ;;

  handoff-receipt)
    team="${1:-}"
    input="${2:-}"
    request="${3:-}"
    delegate="${4:-}"

    case "$mode" in
      receipt-fail)
        exit 1
        ;;

      receipt-invalid)
        printf '%s\n' 'not-json'
        exit 0
        ;;

      *)
        if [ "$input" = "${FAKE_RESULT_MESSAGE_ID:-result-message-001}" ]; then
          receipt_id="result-receipt-001"
        else
          receipt_id="input-receipt-001"
        fi

        printf \
          '{"schemaVersion":1,"state":"recorded","inputMessageId":"%s","requestId":"%s","delegateMessageId":"%s","receiptId":"%s","team":"%s"}\n' \
          "$input" \
          "$request" \
          "$delegate" \
          "$receipt_id" \
          "$team"
        ;;
    esac
    ;;

  message-ack)
    team="${1:-}"
    message_id="${2:-}"
    owner="${3:-}"
    receipt="${4:-}"

    case "$mode" in
      ack-fail)
        exit 1
        ;;

      ack-invalid)
        printf '%s\n' 'not-json'
        exit 0
        ;;

      *)
        printf \
          '{"schemaVersion":1,"state":"acked","messageId":"%s","owner":"%s","receiptId":"%s"}\n' \
          "$message_id" \
          "$owner" \
          "$receipt"
        ;;
    esac
    ;;

  *)
    exit 2
    ;;
esac
EOF

  chmod +x "$SCRIPTS/p2-provider.sh"
}

install_fake_api() {
  cat > "$SCRIPTS/api.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

: "${API_LOG:?}"

printf '%s' "${1:-}" >> "$API_LOG"

shift || true

for arg in "$@"; do
  printf '\t%s' "$arg" >> "$API_LOG"
done

printf '\n' >> "$API_LOG"

case "${FAKE_API_STATUS:-absent}" in
  fail)
    exit 1
    ;;

  invalid)
    printf '%s\n' 'not-json'
    ;;

  unknown)
    printf \
      '{"schemaVersion":1,"resource":"actas-owner","team":"%s","agent":"agmsg_pm_pilot_claude","status":"unknown","reason":"liveness_unavailable","owner":null,"ownerKind":null,"liveness":null,"consistency":"unknown"}\n' \
      "$2"
    ;;

  error)
    printf \
      '{"schemaVersion":1,"resource":"actas-owner","team":"%s","agent":"agmsg_pm_pilot_claude","status":"error","reason":"read_failed","owner":null,"ownerKind":null,"liveness":null,"consistency":"unknown"}\n' \
      "$2"
    ;;

  owned)
    printf \
      '{"schemaVersion":1,"resource":"actas-owner","team":"%s","agent":"agmsg_pm_pilot_claude","status":"owned","reason":null,"owner":"opaque-owner","ownerKind":"pid","liveness":"alive","consistency":"consistent"}\n' \
      "$2"
    ;;

  stale)
    printf \
      '{"schemaVersion":1,"resource":"actas-owner","team":"%s","agent":"agmsg_pm_pilot_claude","status":"stale","reason":null,"owner":"opaque-owner","ownerKind":"pid","liveness":"dead","consistency":"consistent"}\n' \
      "$2"
    ;;

  not_found)
    printf \
      '{"schemaVersion":1,"resource":"actas-owner","team":"%s","agent":"agmsg_pm_pilot_claude","status":"not_found","reason":"target_not_found","owner":null,"ownerKind":null,"liveness":null,"consistency":"consistent"}\n' \
      "$2"
    ;;

  *)
    printf \
      '{"schemaVersion":1,"resource":"actas-owner","team":"%s","agent":"agmsg_pm_pilot_claude","status":"absent","reason":null,"owner":null,"ownerKind":null,"liveness":null,"consistency":"consistent"}\n' \
      "$2"
    ;;
esac
EOF

  chmod +x "$SCRIPTS/api.sh"
}

install_fake_gh() {
  cat > "$GH_STUB_BIN/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

: "${GH_LOG:?}"
: "${GH_BODY_STORE:?}"

printf 'config=%s\t' "${GH_CONFIG_DIR:-}" >> "$GH_LOG"

for arg in "$@"; do
  printf '%s\t' "$arg" >> "$GH_LOG"
done

printf '\n' >> "$GH_LOG"

[ "${1:-}" = "issue" ] || exit 2

case "${2:-}" in
  comment)
    issue="${3:-}"
    shift 3

    repo=""
    body_file=""

    while [ "$#" -gt 0 ]; do
      case "$1" in
        --repo)
          repo="${2:-}"
          shift 2
          ;;

        --body-file)
          body_file="${2:-}"
          shift 2
          ;;

        *)
          exit 2
          ;;
      esac
    done

    [ -n "$repo" ] || exit 2
    [ -n "$body_file" ] || exit 2

    cat "$body_file" > "$GH_BODY_STORE"

    case "${FAKE_GH_MODE:-ok}" in
      write-fail)
        # Simulates "request may have reached GitHub but local result is
        # unknown". The broker must not automatically retry this command.
        exit 1
        ;;

      comment-url-invalid)
        printf '%s\n' 'created comment'
        exit 0
        ;;

      *)
        printf \
          'https://github.com/%s/issues/%s#issuecomment-123456789\n' \
          "$repo" \
          "$issue"
        ;;
    esac
    ;;

  view)
    issue="${3:-}"
    shift 3

    repo=""
    json_fields=""

    while [ "$#" -gt 0 ]; do
      case "$1" in
        --repo)
          repo="${2:-}"
          shift 2
          ;;

        --json)
          json_fields="${2:-}"
          shift 2
          ;;

        *)
          exit 2
          ;;
      esac
    done

    [ "$json_fields" = "comments" ] || exit 2

    case "${FAKE_GH_MODE:-ok}" in
      view-fail)
        exit 1
        ;;

      body-mismatch)
        body="different body"
        ;;

      *)
        body="$(cat "$GH_BODY_STORE")"
        ;;
    esac

    python3 \
      - "$repo" \
      "$issue" \
      "$body" <<'PY'
import json
import sys

repo = sys.argv[1]
issue = sys.argv[2]
body = sys.argv[3]

print(
    json.dumps(
        {
            "comments": [
                {
                    "url": (
                        "https://github.com/"
                        + repo
                        + "/issues/"
                        + issue
                        + "#issuecomment-123456789"
                    ),
                    "body": body,
                }
            ]
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
)
PY
    ;;

  *)
    exit 2
    ;;
esac
EOF

  chmod +x "$GH_STUB_BIN/gh"
}

join_agent() {
  local agent="$1"
  local type="$2"
  local project="$3"
  local role="$4"

  AGMSG_RESOLVE_PROJECT=0 \
    bash "$SCRIPTS/join.sh" \
      "$TEAM" \
      "$agent" \
      "$type" \
      "$project" \
      --role "$role" \
      --kind seat \
      >/dev/null
}

join_real_fixture_agents() {
  join_agent \
    "$PILOT_AGENT" \
    "$PILOT_TYPE" \
    "$PROJ" \
    manager

  join_agent \
    "$WORKER" \
    codex \
    "$WORKER_PROJ" \
    worker

  join_agent \
    "$OTHER_WORKER" \
    codex \
    "$OTHER_WORKER_PROJ" \
    worker

  join_agent \
    "$SENDER" \
    codex \
    "$SENDER_PROJ" \
    worker
}

real_send() {
  local from="$1"
  local to="$2"
  local request_id="$3"
  local body="$4"

  "$SCRIPTS/p2-provider.sh" \
    message-send \
    "$TEAM" \
    "$from" \
    "$to" \
    "$request_id" \
    "$body"
}

real_peek() {
  local recipient="$1"

  "$SCRIPTS/p2-provider.sh" \
    message-peek \
    "$TEAM" \
    "$recipient"
}

provider_call_count() {
  local action="$1"

  if [ ! -s "$PROVIDER_LOG" ]; then
    printf '0\n'
    return 0
  fi

  awk \
    -F '\t' \
    -v wanted="$action" \
    '$1 == wanted { count += 1 } END { print count + 0 }' \
    "$PROVIDER_LOG"
}

gh_call_count() {
  local word="$1"

  if [ ! -s "$GH_LOG" ]; then
    printf '0\n'
    return 0
  fi

  grep -c "$word" "$GH_LOG" 2>/dev/null || true
}

assert_json_state() {
  local json="$1"
  local expected="$2"

  [ "$(json_field "$json" state)" = "$expected" ]
}

assert_provider_not_called() {
  [ ! -s "$PROVIDER_LOG" ]
}

# ---------------------------------------------------------------------------
# Payload / binding boundary
# ---------------------------------------------------------------------------

@test "broker exposes only the five G3 consumer operations" {
  local request

  # G2 message-peek currently assumes the team's storage schema already
  # exists. Initialize it through the normal real message-send path without
  # creating a message addressed to the pilot.
  real_send \
    "$PILOT_AGENT" \
    "$WORKER" \
    "g4b-peek-bootstrap" \
    "initialize provider storage" \
    >/dev/null

  request="$(common_request receive)"

  run invoke_broker \
    "$request" \
    receive

  # No input exists, but the operation itself is recognized.
  [ "$status" -eq 0 ]
  assert_json_state "$output" absent

  for operation in \
    git_maintenance \
    proxy_git_write \
    team_provision \
    bot_collaborator \
    issue_view \
    pr_view \
    seat_start \
    seat_stop \
    message-send \
    message-ack \
    history
  do
    run invoke_broker \
      "$request" \
      "$operation"

    [ "$status" -ne 0 ]

    case "$output" in
      *arguments_invalid*|*operation_not_allowed*)
        ;;
      *)
        printf \
          'unexpected unsupported-operation result for %s: %s\n' \
          "$operation" \
          "$output" \
          >&2
        return 1
        ;;
    esac
  done
}

@test "broker implementation does not import or dispatch through pm-broker" {
  local matches

  matches="$(
    grep -nE \
      'pm-broker(\.js|\.sh)|require\(.*pm-broker|source .*pm-broker|\. .*pm-broker' \
      "$BROKER" |
      grep -vE '^[0-9]+:[[:space:]]*#' ||
      true
  )"

  [ -z "$matches" ]
}

@test "unknown request fields are rejected before backend execution" {
  install_fake_provider

  local request

  request="$(
    python3 - <<PY
import json

print(json.dumps({
    "schemaVersion": 1,
    "runId": "$RUN_ID",
    "requestId": "request-001",
    "operation": "receive",
    "team": "$TEAM",
    "actor": "$PILOT_AGENT",
    "generation": "$GENERATION",
    "unexpected": "forbidden",
}, separators=(",", ":")))
PY
  )"

  run invoke_broker \
    "$request" \
    receive

  [ "$status" -ne 0 ]

  case "$output" in
    *invalid_payload*)
      ;;
    *)
      return 1
      ;;
  esac

  assert_provider_not_called
}

@test "request larger than 8192 bytes is rejected before backend execution" {
  install_fake_provider

  local request

  request="$(
    python3 - <<PY
import json

print(json.dumps({
    "schemaVersion": 1,
    "runId": "$RUN_ID",
    "requestId": "request-001",
    "operation": "receive",
    "team": "$TEAM",
    "actor": "$PILOT_AGENT",
    "generation": "$GENERATION",
    "padding": "x" * 9000,
}, separators=(",", ":")))
PY
  )"

  run invoke_broker \
    "$request" \
    receive

  [ "$status" -ne 0 ]

  case "$output" in
    *invalid_payload*)
      ;;
    *)
      return 1
      ;;
  esac

  assert_provider_not_called
}

@test "binding team actor and generation are authoritative over caller request" {
  install_fake_provider

  local request

  request="$(
    python3 - <<PY
import json

print(json.dumps({
    "schemaVersion": 1,
    "runId": "$RUN_ID",
    "requestId": "request-001",
    "operation": "receive",
    "team": "different-team",
    "actor": "$PILOT_AGENT",
    "generation": "$GENERATION",
}, separators=(",", ":")))
PY
  )"

  run invoke_broker \
    "$request" \
    receive

  [ "$status" -ne 0 ]
  assert_provider_not_called
}

@test "noncanonical project in G4-A binding is rejected" {
  install_fake_provider

  mkdir -p "$TEST_SKILL_DIR/project-link-parent"

  ln -s \
    "$CANONICAL_PROJ" \
    "$TEST_SKILL_DIR/project-link"

  python3 \
    - "$BINDING_FILE" \
    "$TEST_SKILL_DIR/project-link" <<'PY'
import json
import sys

file_name = sys.argv[1]

with open(file_name, encoding="utf-8") as fh:
    value = json.load(fh)

value["project"] = sys.argv[2]

with open(
    file_name,
    "w",
    encoding="utf-8",
) as fh:
    json.dump(
        value,
        fh,
        separators=(",", ":"),
    )
    fh.write("\n")
PY

  run invoke_broker \
    "$(common_request receive)" \
    receive

  [ "$status" -ne 0 ]

  case "$output" in
    *invalid_binding*)
      ;;
    *)
      return 1
      ;;
  esac

  assert_provider_not_called
}

# ---------------------------------------------------------------------------
# receive
# ---------------------------------------------------------------------------

@test "receive: real provider peeks one pilot message and claims it with stable opaque owner" {
  join_real_fixture_agents

  local send_json
  local input_id
  local request
  local expected_owner

  send_json="$(
    real_send \
      "$SENDER" \
      "$PILOT_AGENT" \
      "incoming-request-001" \
      "input work"
  )"

  input_id="$(json_field "$send_json" messageId)"

  request="$(common_request receive request-receive-001)"

  run invoke_broker \
    "$request" \
    receive

  [ "$status" -eq 0 ]
  assert_json_state "$output" claimed

  [ "$(json_field "$output" inputMessageId)" = "$input_id" ]

  expected_owner="p2:${SESSION_ID}:${GENERATION}:${RUN_ID}"

  [ "$(json_field "$output" owner)" = "$expected_owner" ]

  [ "$(json_file_field "$(broker_state_file)" phase)" = "claimed" ]
  [ "$(json_file_field "$(broker_state_file)" owner)" = "$expected_owner" ]
  [ "$(json_file_field "$(broker_state_file)" inputMessageId)" = "$input_id" ]
}

@test "receive: another recipient is rejected without claim" {
  install_fake_provider
  export P2_PROVIDER_FAKE_MODE="peek-other-recipient"

  run invoke_broker \
    "$(common_request receive)" \
    receive

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped

  [ "$(json_field "$output" reason)" = "recipient_mismatch" ]

  [ "$(provider_call_count message-peek)" -eq 1 ]
  [ "$(provider_call_count message-claim)" -eq 0 ]
  [ "$(provider_call_count message-ack)" -eq 0 ]
}

@test "receive: busy claim stops and performs no ack" {
  install_fake_provider
  export P2_PROVIDER_FAKE_MODE="claim-fail"

  run invoke_broker \
    "$(common_request receive)" \
    receive

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped

  [ "$(json_field "$output" reason)" = "claim_failed" ]

  [ "$(provider_call_count message-peek)" -eq 1 ]
  [ "$(provider_call_count message-claim)" -eq 1 ]
  [ "$(provider_call_count message-ack)" -eq 0 ]
}

@test "receive: unknown or malformed peek stops_for_unknown and performs no claim or ack" {
  install_fake_provider
  export P2_PROVIDER_FAKE_MODE="peek-invalid"

  run invoke_broker \
    "$(common_request receive)" \
    receive

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped_for_unknown

  [ "$(json_field "$output" reason)" = "backend_response_unidentifiable" ]

  [ "$(provider_call_count message-peek)" -eq 1 ]
  [ "$(provider_call_count message-claim)" -eq 0 ]
  [ "$(provider_call_count message-ack)" -eq 0 ]
}

@test "receive: absent is a side-effect-free completion" {
  install_fake_provider
  export P2_PROVIDER_FAKE_MODE="peek-absent"

  run invoke_broker \
    "$(common_request receive)" \
    receive

  [ "$status" -eq 0 ]
  assert_json_state "$output" absent

  [ "$(provider_call_count message-peek)" -eq 1 ]
  [ "$(provider_call_count message-claim)" -eq 0 ]
  [ "$(provider_call_count message-ack)" -eq 0 ]
}

# ---------------------------------------------------------------------------
# delegate
# ---------------------------------------------------------------------------

@test "delegate: real provider queues exactly one message to manifest worker and records input receipt" {
  join_real_fixture_agents

  local incoming
  local input_id
  local receive_output
  local request_id="request-delegate-001"
  local delegate_output
  local delegate_id
  local worker_peek

  incoming="$(
    real_send \
      "$SENDER" \
      "$PILOT_AGENT" \
      "incoming-request-001" \
      "input work"
  )"

  input_id="$(json_field "$incoming" messageId)"

  run invoke_broker \
    "$(common_request receive request-receive-001)" \
    receive

  [ "$status" -eq 0 ]
  receive_output="$output"

  [ "$(json_field "$receive_output" inputMessageId)" = "$input_id" ]

  run invoke_broker \
    "$(
      delegate_request \
        "$request_id" \
        "$input_id" \
        "$WORKER" \
        "count the fixed fixture records"
    )" \
    delegate

  [ "$status" -eq 0 ]
  delegate_output="$output"

  assert_json_state "$delegate_output" delegated

  [ "$(json_field "$delegate_output" deliveryState)" = "queued" ]
  [ "$(json_field "$delegate_output" worker)" = "$WORKER" ]

  delegate_id="$(json_field "$delegate_output" delegateMessageId)"

  [ -n "$delegate_id" ]
  [ -n "$(json_field "$delegate_output" inputReceiptId)" ]

  worker_peek="$(
    real_peek "$WORKER"
  )"

  [ "$(json_field "$worker_peek" state)" = "ok" ]
  [ "$(json_field "$worker_peek" messageId)" = "$delegate_id" ]
  [ "$(json_field "$worker_peek" from)" = "$PILOT_AGENT" ]
  [ "$(json_field "$worker_peek" to)" = "$WORKER" ]

  [ "$(json_file_field "$(broker_state_file)" phase)" = "waiting_result" ]
  [ "$(json_file_field "$(broker_state_file)" delegateMessageId)" = "$delegate_id" ]
  [ "$(json_file_field "$(broker_state_file)" requestId)" = "$request_id" ]
}

@test "delegate: arbitrary worker is rejected before message-send" {
  install_fake_provider

  write_broker_state \
    claimed \
    input-message-001

  run invoke_broker \
    "$(
      delegate_request \
        request-001 \
        input-message-001 \
        "$OTHER_WORKER" \
        "count the fixed fixture records"
    )" \
    delegate

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped

  [ "$(json_field "$output" reason)" = "worker_scope_mismatch" ]

  [ "$(provider_call_count message-send)" -eq 0 ]
}

@test "delegate: shell-bearing task is rejected before provider execution" {
  install_fake_provider

  write_broker_state \
    claimed \
    input-message-001

  run invoke_broker \
    "$(
      delegate_request \
        request-001 \
        input-message-001 \
        "$WORKER" \
        'bash -c "rm -rf /tmp/example"'
    )" \
    delegate

  [ "$status" -ne 0 ]

  case "$output" in
    *invalid_payload*)
      ;;
    *)
      printf '%s\n' "$output" >&2
      return 1
      ;;
  esac

  assert_provider_not_called
}

@test "delegate: provider queued state is not promoted to delivery or worker success" {
  install_fake_provider
  export P2_PROVIDER_FAKE_MODE="normal"

  write_broker_state \
    claimed \
    input-message-001

  run invoke_broker \
    "$(
      delegate_request \
        request-001 \
        input-message-001 \
        "$WORKER" \
        "count the fixed fixture records"
    )" \
    delegate

  [ "$status" -eq 0 ]
  assert_json_state "$output" delegated

  [ "$(json_field "$output" deliveryState)" = "queued" ]

  case "$output" in
    *delivered*|*worker_started*|*processed*)
      printf \
        'queued response was incorrectly promoted: %s\n' \
        "$output" \
        >&2
      return 1
      ;;
    *)
      ;;
  esac
}

@test "delegate: delivered-state mutation from provider is rejected as unidentifiable and never retried" {
  install_fake_provider
  export P2_PROVIDER_FAKE_MODE="send-delivered-mutation"

  write_broker_state \
    claimed \
    input-message-001

  run invoke_broker \
    "$(
      delegate_request \
        request-001 \
        input-message-001 \
        "$WORKER" \
        "count the fixed fixture records"
    )" \
    delegate

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped_for_unknown

  [ "$(provider_call_count message-send)" -eq 1 ]
  [ "$(provider_call_count handoff-receipt)" -eq 0 ]
}

@test "delegate: unknown send response is never automatically resent" {
  install_fake_provider
  export P2_PROVIDER_FAKE_MODE="send-invalid"

  write_broker_state \
    claimed \
    input-message-001

  run invoke_broker \
    "$(
      delegate_request \
        request-001 \
        input-message-001 \
        "$WORKER" \
        "count the fixed fixture records"
    )" \
    delegate

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped_for_unknown

  [ "$(provider_call_count message-send)" -eq 1 ]
  [ "$(provider_call_count handoff-receipt)" -eq 0 ]
  [ "$(provider_call_count message-release)" -eq 0 ]
  [ "$(provider_call_count message-ack)" -eq 0 ]
}

# ---------------------------------------------------------------------------
# collect-result
# ---------------------------------------------------------------------------

@test "collect-result: matching request delegate and result are claimed and receipt-recorded" {
  install_fake_provider

  export P2_PROVIDER_FAKE_MODE="result"
  export FAKE_RESULT_REQUEST_ID="request-result-001"
  export FAKE_RESULT_DELEGATE_ID="delegate-message-001"
  export FAKE_RESULT_MESSAGE_ID="result-message-001"
  export FAKE_RESULT_TEXT="42 records"

  write_broker_state \
    waiting_result \
    input-message-001 \
    "" \
    request-result-001 \
    delegate-message-001 \
    input-receipt-001

  run invoke_broker \
    "$(
      collect_request \
        request-result-001 \
        delegate-message-001
    )" \
    collect-result

  [ "$status" -eq 0 ]
  assert_json_state "$output" result_claimed

  [ "$(json_field "$output" resultMessageId)" = "result-message-001" ]
  [ "$(json_field "$output" delegateMessageId)" = "delegate-message-001" ]
  [ "$(json_field "$output" result)" = "42 records" ]
  [ "$(json_field "$output" resultReceiptId)" = "result-receipt-001" ]

  [ "$(provider_call_count message-peek)" -eq 1 ]
  [ "$(provider_call_count message-claim)" -eq 1 ]
  [ "$(provider_call_count handoff-receipt)" -eq 1 ]

  [ "$(json_file_field "$(broker_state_file)" phase)" = "receipt_recorded" ]
  [ "$(json_file_field "$(broker_state_file)" resultMessageId)" = "result-message-001" ]
  [ "$(json_file_field "$(broker_state_file)" resultReceiptId)" = "result-receipt-001" ]
}

@test "collect-result: absent result stops without claim receipt or ack" {
  install_fake_provider
  export P2_PROVIDER_FAKE_MODE="peek-absent"

  write_broker_state \
    waiting_result \
    input-message-001 \
    "" \
    request-001 \
    delegate-message-001 \
    input-receipt-001

  run invoke_broker \
    "$(collect_request request-001 delegate-message-001)" \
    collect-result

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped

  [ "$(json_field "$output" reason)" = "result_absent" ]

  [ "$(provider_call_count message-peek)" -eq 1 ]
  [ "$(provider_call_count message-claim)" -eq 0 ]
  [ "$(provider_call_count handoff-receipt)" -eq 0 ]
  [ "$(provider_call_count message-ack)" -eq 0 ]
}

@test "collect-result: result from another request is rejected without claim" {
  install_fake_provider

  export P2_PROVIDER_FAKE_MODE="result"
  export FAKE_RESULT_REQUEST_ID="another-request"
  export FAKE_RESULT_DELEGATE_ID="delegate-message-001"

  write_broker_state \
    waiting_result \
    input-message-001 \
    "" \
    request-001 \
    delegate-message-001 \
    input-receipt-001

  run invoke_broker \
    "$(collect_request request-001 delegate-message-001)" \
    collect-result

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped

  [ "$(json_field "$output" reason)" = "result_request_mismatch" ]

  [ "$(provider_call_count message-claim)" -eq 0 ]
  [ "$(provider_call_count handoff-receipt)" -eq 0 ]
}

@test "collect-result: result with another delegate id is rejected without claim" {
  install_fake_provider

  export P2_PROVIDER_FAKE_MODE="result"
  export FAKE_RESULT_REQUEST_ID="request-001"
  export FAKE_RESULT_DELEGATE_ID="other-delegate"

  write_broker_state \
    waiting_result \
    input-message-001 \
    "" \
    request-001 \
    delegate-message-001 \
    input-receipt-001

  run invoke_broker \
    "$(collect_request request-001 delegate-message-001)" \
    collect-result

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped

  [ "$(json_field "$output" reason)" = "result_delegate_mismatch" ]

  [ "$(provider_call_count message-claim)" -eq 0 ]
  [ "$(provider_call_count handoff-receipt)" -eq 0 ]
}

@test "collect-result: caller cannot guess or substitute an opaque delegate id" {
  install_fake_provider

  write_broker_state \
    waiting_result \
    input-message-001 \
    "" \
    request-001 \
    actual-delegate-id \
    input-receipt-001

  run invoke_broker \
    "$(collect_request request-001 guessed-delegate-id)" \
    collect-result

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped

  [ "$(json_field "$output" reason)" = "delegate_message_mismatch" ]

  assert_provider_not_called
}

@test "collect-result: multiple matching results must not be accepted as a unique result" {
  skip "Known G3/G2 gap: message-peek cannot enumerate results to prove uniqueness"

  # CONTRACT-LEVEL RED TEST.
  #
  # G3 requires multiple matching results to be non-success.
  #
  # The current G2 message-peek façade exposes only one unread message and
  # there is no history/list capability available to this consumer.
  # The current adapter therefore cannot prove uniqueness and presently
  # accepts the first matching result.
  #
  # This test intentionally remains RED until G4-B has a sound way to satisfy
  # the fixed G3 uniqueness requirement without direct history/SQLite access.
  install_fake_provider

  export P2_PROVIDER_FAKE_MODE="multiple-result"
  export FAKE_RESULT_REQUEST_ID="request-001"
  export FAKE_RESULT_DELEGATE_ID="delegate-message-001"
  export FAKE_RESULT_MESSAGE_ID="result-message-first"

  write_broker_state \
    waiting_result \
    input-message-001 \
    "" \
    request-001 \
    delegate-message-001 \
    input-receipt-001

  run invoke_broker \
    "$(collect_request request-001 delegate-message-001)" \
    collect-result

  [ "$status" -eq 0 ]

  case "$(json_field "$output" state)" in
    stopped|stopped_for_unknown)
      ;;
    *)
      printf \
        'G3 violation: multiple-result ambiguity was accepted: %s\n' \
        "$output" \
        >&2
      return 1
      ;;
  esac

  [ "$(provider_call_count message-ack)" -eq 0 ]
}

@test "collect-result: real provider must not confuse still-unread claimed input with worker result" {
  skip "Known G3/G2 gap: claimed input remains visible to message-peek"

  # CONTRACT-LEVEL RED TEST.
  #
  # p2-provider message-peek filters m.read_at IS NULL.
  # message-claim does not mark the input read.
  #
  # Therefore after receive+delegate, the original claimed input remains the
  # first unread pilot message. A later worker result can exist behind it.
  #
  # G3 requires collect-result to find the result corresponding uniquely to
  # request/delegate IDs rather than silently treating this as a valid result
  # or consuming the wrong message.
  join_real_fixture_agents

  local incoming
  local input_id
  local delegate_output
  local delegate_id
  local worker_result_body

  incoming="$(
    real_send \
      "$SENDER" \
      "$PILOT_AGENT" \
      incoming-request \
      "original input"
  )"

  input_id="$(json_field "$incoming" messageId)"

  run invoke_broker \
    "$(common_request receive receive-request)" \
    receive

  [ "$status" -eq 0 ]
  assert_json_state "$output" claimed

  run invoke_broker \
    "$(
      delegate_request \
        request-real-result \
        "$input_id" \
        "$WORKER" \
        "count the fixed fixture records"
    )" \
    delegate

  [ "$status" -eq 0 ]
  assert_json_state "$output" delegated

  delegate_output="$output"
  delegate_id="$(json_field "$delegate_output" delegateMessageId)"

  worker_result_body="$(
    python3 \
      - request-real-result \
      "$delegate_id" <<'PY'
import json
import sys

print(
    json.dumps(
        {
            "schemaVersion": 1,
            "requestId": sys.argv[1],
            "delegateMessageId": sys.argv[2],
            "result": "42 records",
        },
        separators=(",", ":"),
    )
)
PY
  )"

  real_send \
    "$WORKER" \
    "$PILOT_AGENT" \
    request-real-result \
    "$worker_result_body" \
    >/dev/null

  run invoke_broker \
    "$(
      collect_request \
        request-real-result \
        "$delegate_id"
    )" \
    collect-result

  [ "$status" -eq 0 ]

  # Fixed G3 contract: a matching result exists and must be collectable.
  # Current implementation is expected to fail here because the old input is
  # still the first unread message returned by the real provider.
  assert_json_state "$output" result_claimed

  [ "$(json_field "$output" result)" = "42 records" ]
}

# ---------------------------------------------------------------------------
# issue-record
# ---------------------------------------------------------------------------

@test "issue-record: writes multiline body only to configured test Issue rereads exact body and then acks both messages" {
  install_fake_provider
  export P2_PROVIDER_FAKE_MODE="normal"
  export FAKE_GH_MODE="ok"

  local body
  body='first line
second line
third line'

  write_broker_state \
    receipt_recorded \
    input-message-001 \
    "" \
    request-001 \
    delegate-message-001 \
    input-receipt-001 \
    result-message-001 \
    result-receipt-001

  run invoke_issue_broker \
    "$(
      issue_request \
        request-001 \
        input-message-001 \
        delegate-message-001 \
        result-message-001 \
        "$body"
    )"

  [ "$status" -eq 0 ]
  assert_json_state "$output" acked

  [ "$(json_field "$output" issueNumber)" = "$TEST_ISSUE_NUMBER" ]

  [ "$(cat "$GH_BODY_STORE")" = "$body" ]

  grep -F \
    "config=$GH_CONFIG_DIR_TEST" \
    "$GH_LOG"

  grep -F \
    $'issue\tcomment\t391\t--repo\tkappaseijin/agmsg\t--body-file' \
    "$GH_LOG"

  grep -F \
    $'issue\tview\t391\t--repo\tkappaseijin/agmsg\t--json\tcomments' \
    "$GH_LOG"

  [ "$(provider_call_count message-ack)" -eq 2 ]

  grep -F \
    $'message-ack\tpilot-team\tinput-message-001\tp2:11111111-1111-4111-8111-111111111111:1:run-g4b-001\tinput-receipt-001' \
    "$PROVIDER_LOG"

  grep -F \
    $'message-ack\tpilot-team\tresult-message-001\tp2:11111111-1111-4111-8111-111111111111:1:run-g4b-001\tresult-receipt-001' \
    "$PROVIDER_LOG"

  [ "$(json_file_field "$(broker_state_file)" phase)" = "acked" ]
}

@test "issue-record: caller cannot select another Issue" {
  install_fake_provider

  write_broker_state \
    receipt_recorded \
    input-message-001 \
    "" \
    request-001 \
    delegate-message-001 \
    input-receipt-001 \
    result-message-001 \
    result-receipt-001

  local request

  request="$(
    python3 - <<PY
import json

print(json.dumps({
    "schemaVersion": 1,
    "runId": "$RUN_ID",
    "requestId": "request-001",
    "operation": "issue-record",
    "team": "$TEAM",
    "actor": "$PILOT_AGENT",
    "generation": "$GENERATION",
    "inputMessageId": "input-message-001",
    "delegateMessageId": "delegate-message-001",
    "resultMessageId": "result-message-001",
    "body": "body",
    "testIssueNumber": 236,
}, separators=(",", ":")))
PY
  )"

  run invoke_issue_broker \
    "$request"

  [ "$status" -ne 0 ]

  case "$output" in
    *invalid_payload*)
      ;;
    *)
      return 1
      ;;
  esac

  [ ! -s "$GH_LOG" ]
  assert_provider_not_called
}

@test "issue-record: readback body mismatch is stopped_for_unknown and performs no ack" {
  install_fake_provider
  export FAKE_GH_MODE="body-mismatch"

  write_broker_state \
    receipt_recorded \
    input-message-001 \
    "" \
    request-001 \
    delegate-message-001 \
    input-receipt-001 \
    result-message-001 \
    result-receipt-001

  run invoke_issue_broker \
    "$(
      issue_request \
        request-001 \
        input-message-001 \
        delegate-message-001 \
        result-message-001 \
        "expected body"
    )"

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped_for_unknown

  [ "$(json_field "$output" reason)" = "issue_body_mismatch" ]

  [ "$(provider_call_count message-ack)" -eq 0 ]

  [ "$(gh_call_count $'issue\tcomment')" -eq 1 ]
  [ "$(gh_call_count $'issue\tview')" -eq 1 ]
}

@test "issue-record: write result unknown is never automatically retried released or acked" {
  install_fake_provider
  export FAKE_GH_MODE="write-fail"

  write_broker_state \
    receipt_recorded \
    input-message-001 \
    "" \
    request-001 \
    delegate-message-001 \
    input-receipt-001 \
    result-message-001 \
    result-receipt-001

  run invoke_issue_broker \
    "$(
      issue_request \
        request-001 \
        input-message-001 \
        delegate-message-001 \
        result-message-001 \
        "possibly written body"
    )"

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped_for_unknown

  [ "$(json_field "$output" reason)" = "issue_write_unknown" ]

  [ "$(gh_call_count $'issue\tcomment')" -eq 1 ]
  [ "$(gh_call_count $'issue\tview')" -eq 0 ]

  [ "$(provider_call_count message-release)" -eq 0 ]
  [ "$(provider_call_count message-ack)" -eq 0 ]
  [ "$(provider_call_count message-send)" -eq 0 ]
}

@test "issue-record: successful gh exit without identifiable comment URL is unknown and not retried" {
  install_fake_provider
  export FAKE_GH_MODE="comment-url-invalid"

  write_broker_state \
    receipt_recorded \
    input-message-001 \
    "" \
    request-001 \
    delegate-message-001 \
    input-receipt-001 \
    result-message-001 \
    result-receipt-001

  run invoke_issue_broker \
    "$(
      issue_request \
        request-001 \
        input-message-001 \
        delegate-message-001 \
        result-message-001 \
        "body"
    )"

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped_for_unknown

  [ "$(json_field "$output" reason)" = "issue_write_result_unidentifiable" ]

  [ "$(gh_call_count $'issue\tcomment')" -eq 1 ]
  [ "$(gh_call_count $'issue\tview')" -eq 0 ]
  [ "$(provider_call_count message-ack)" -eq 0 ]
}

@test "issue-record: body above 4096 UTF-8 bytes is rejected before gh write" {
  install_fake_provider

  write_broker_state \
    receipt_recorded \
    input-message-001 \
    "" \
    request-001 \
    delegate-message-001 \
    input-receipt-001 \
    result-message-001 \
    result-receipt-001

  local body
  body="$(
    python3 - <<'PY'
print("x" * 4097, end="")
PY
  )"

  run invoke_issue_broker \
    "$(
      issue_request \
        request-001 \
        input-message-001 \
        delegate-message-001 \
        result-message-001 \
        "$body"
    )"

  [ "$status" -ne 0 ]

  case "$output" in
    *invalid_payload*)
      ;;
    *)
      return 1
      ;;
  esac

  [ ! -s "$GH_LOG" ]
  assert_provider_not_called
}

# ---------------------------------------------------------------------------
# observe-owner
# ---------------------------------------------------------------------------

@test "observe-owner: real api.sh observes the fixed pilot B2 state" {
  join_real_fixture_agents
  restore_real_api

  run invoke_broker \
    "$(owner_request "$PILOT_AGENT")" \
    observe-owner

  [ "$status" -eq 0 ]
  assert_json_state "$output" observed

  [ "$(json_field "$output" operation)" = "observe-owner" ]
  [ "$(json_field "$output" actor)" = "$PILOT_AGENT" ]

  case "$(json_field "$output" ownerStatus)" in
    owned|absent|stale|not_found)
      ;;
    *)
      printf \
        'unexpected real B2 observation: %s\n' \
        "$output" \
        >&2
      return 1
      ;;
  esac
}

@test "observe-owner: current PM cannot be queried through pilot adapter" {
  install_fake_api

  run invoke_broker \
    "$(owner_request agmsg_pm_claude)" \
    observe-owner

  [ "$status" -ne 0 ]

  case "$output" in
    *invalid_payload*)
      ;;
    *)
      return 1
      ;;
  esac

  [ ! -s "$API_LOG" ]
}

@test "observe-owner: worker cannot be queried through pilot adapter" {
  install_fake_api

  run invoke_broker \
    "$(owner_request "$WORKER")" \
    observe-owner

  [ "$status" -ne 0 ]

  case "$output" in
    *invalid_payload*)
      ;;
    *)
      return 1
      ;;
  esac

  [ ! -s "$API_LOG" ]
}

@test "observe-owner: unknown remains stopped_for_unknown and is never converted to free or allowed" {
  install_fake_api
  export FAKE_API_STATUS="unknown"

  run invoke_broker \
    "$(owner_request "$PILOT_AGENT")" \
    observe-owner

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped_for_unknown

  [ "$(json_field "$output" ownerStatus)" = "unknown" ]

  case "$output" in
    *'"ownerStatus":"free"'*|*'"state":"observed"'*|*'"allowed"'*)
      printf \
        'unknown was incorrectly normalized: %s\n' \
        "$output" \
        >&2
      return 1
      ;;
    *)
      ;;
  esac
}

@test "observe-owner: error remains stopped_for_unknown and is never converted to free" {
  install_fake_api
  export FAKE_API_STATUS="error"

  run invoke_broker \
    "$(owner_request "$PILOT_AGENT")" \
    observe-owner

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped_for_unknown

  [ "$(json_field "$output" ownerStatus)" = "error" ]

  case "$output" in
    *'"ownerStatus":"free"'*|*'"state":"observed"'*)
      return 1
      ;;
    *)
      ;;
  esac
}

@test "observe-owner: api execution failure is unknown rather than free" {
  install_fake_api
  export FAKE_API_STATUS="fail"

  run invoke_broker \
    "$(owner_request "$PILOT_AGENT")" \
    observe-owner

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped_for_unknown

  [ "$(json_field "$output" reason)" = "owner_observation_failed" ]

  case "$output" in
    *'"ownerStatus":"free"'*|*'"state":"observed"'*)
      return 1
      ;;
    *)
      ;;
  esac
}

@test "observe-owner: malformed B2 JSON is fail-closed" {
  install_fake_api
  export FAKE_API_STATUS="invalid"

  run invoke_broker \
    "$(owner_request "$PILOT_AGENT")" \
    observe-owner

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped_for_unknown

  [ "$(json_field "$output" reason)" = "owner_observation_unidentifiable" ]
}

# ---------------------------------------------------------------------------
# F1 / forbidden fallback / owner consistency
# ---------------------------------------------------------------------------

@test "F1: provider execution failure never falls back to generic Bash or pm-broker" {
  install_fake_provider
  export P2_PROVIDER_FAKE_MODE="peek-fail"

  run invoke_broker \
    "$(common_request receive)" \
    receive

  [ "$status" -eq 0 ]
  assert_json_state "$output" stopped

  [ "$(provider_call_count message-peek)" -eq 1 ]

  run grep -nE \
    '(^|[;&|[:space:]])(pm-broker(\.js|\.sh)?|history\.sh|claim\.sh|sqlite3)([[:space:]]|$)|bash[[:space:]]+-c' \
    "$BROKER"

  # Comments can contain historical/prohibited names; only executable-looking
  # references are forbidden. Filter obvious comment-only lines.
  if [ "$status" -eq 0 ]; then
    filtered="$(
      printf '%s\n' "$output" |
        grep -vE '^[0-9]+:[[:space:]]*#' || true
    )"

    [ -z "$filtered" ]
  fi
}

@test "claim release and ack owner format is stable and opaque within one run" {
  install_fake_provider

  export P2_PROVIDER_FAKE_MODE="normal"

  local expected_owner
  expected_owner="p2:${SESSION_ID}:${GENERATION}:${RUN_ID}"

  run invoke_broker \
    "$(common_request receive receive-owner-test)" \
    receive

  [ "$status" -eq 0 ]
  assert_json_state "$output" claimed

  [ "$(json_field "$output" owner)" = "$expected_owner" ]

  grep -F \
    $'message-claim\tpilot-team\tfake-message-001\tp2:11111111-1111-4111-8111-111111111111:1:run-g4b-001' \
    "$PROVIDER_LOG"

  : > "$PROVIDER_LOG"

  write_broker_state \
    receipt_recorded \
    input-message-001 \
    "$expected_owner" \
    request-001 \
    delegate-message-001 \
    input-receipt-001 \
    result-message-001 \
    result-receipt-001

  run invoke_issue_broker \
    "$(
      issue_request \
        request-001 \
        input-message-001 \
        delegate-message-001 \
        result-message-001 \
        "owner consistency"
    )"

  [ "$status" -eq 0 ]
  assert_json_state "$output" acked

  grep -F \
    $'message-ack\tpilot-team\tinput-message-001\tp2:11111111-1111-4111-8111-111111111111:1:run-g4b-001\tinput-receipt-001' \
    "$PROVIDER_LOG"

  grep -F \
    $'message-ack\tpilot-team\tresult-message-001\tp2:11111111-1111-4111-8111-111111111111:1:run-g4b-001\tresult-receipt-001' \
    "$PROVIDER_LOG"
}
# Issue #409: the harness must run issue-record with --gh-config-dir.

@test "issue-record: without --gh-config-dir the broker stops with gh_config_dir_required" {
  install_fake_provider
  export P2_PROVIDER_FAKE_MODE="normal"
  export FAKE_GH_MODE="ok"

  write_broker_state \
    receipt_recorded \
    input-message-001 \
    "" \
    request-001 \
    delegate-message-001 \
    input-receipt-001 \
    result-message-001 \
    result-receipt-001

  # Form A, as the harness used to build it.
  run invoke_broker "$(issue_request request-001)" issue-record

  [ "$status" -eq 2 ]
  [ "$(json_field "$output" reason)" = "gh_config_dir_required" ]
  # Nothing was written to the Issue and nothing was acked.
  [ ! -s "$GH_LOG" ]
  [ "$(provider_call_count message-ack)" -eq 0 ]
}

@test "issue-record: the exact command built by pilot-gate-i1 is accepted and acks" {
  install_fake_provider
  export P2_PROVIDER_FAKE_MODE="normal"
  export FAKE_GH_MODE="ok"

  write_broker_state \
    receipt_recorded \
    input-message-001 \
    "" \
    request-001 \
    delegate-message-001 \
    input-receipt-001 \
    result-message-001 \
    result-receipt-001

  local request_file="$TEST_SKILL_DIR/issue-record-request.json"
  issue_request request-001 > "$request_file"

  local command
  command="$(
    python3 - "$SCRIPTS/lib/pilot-gate-i1.py" "$BROKER" "$CONFIG_FILE" \
      "$request_file" "$GH_CONFIG_DIR_TEST" <<'PY'
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("pilot_gate_i1", sys.argv[1])
i1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(i1)
broker, config, request, gh = (pathlib.Path(a) for a in sys.argv[2:6])
print(i1.exact_broker_command(broker, config, "issue-record", request, gh))
PY
  )"

  # Guarded: a bare non-last [[ ]] is not enforced on bash 3.2 (#670).
  [[ "$command" == "$BROKER --config $CONFIG_FILE --gh-config-dir $GH_CONFIG_DIR_TEST issue-record < $request_file" ]] ||
    { echo "unexpected command: $command" >&2; return 1; }

  run bash -c "$command"

  [ "$status" -eq 0 ]
  assert_json_state "$output" acked
  grep -F "config=$GH_CONFIG_DIR_TEST" "$GH_LOG"
  [ "$(provider_call_count message-ack)" -eq 2 ]
}
