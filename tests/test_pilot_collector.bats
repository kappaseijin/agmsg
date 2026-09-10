#!/usr/bin/env bats

load test_helper

# tests/test_pilot_collector.bats
#
# G4-C component tests for scripts/pilot-collector.sh.
#
# The fixtures are real JSONL files under an isolated CLAUDE_CONFIG_DIR.
# The fake `claude` executable exists only to make `claude --version`
# deterministic; the collector never receives tool events from the fake CLI.
#
# Required CI suites (Ubuntu/macOS) stay green. Tests are skipped only where
# the host cannot faithfully express the permission failure being tested.

setup() {
  setup_test_env

  export COLLECTOR="$SCRIPTS/pilot-collector.sh"
  chmod +x "$COLLECTOR"

  export TEAM="pilot-team"
  export PILOT_AGENT="agmsg_pm_pilot_claude"
  export PILOT_TYPE="claude-code"
  export SESSION_ID="11111111-1111-4111-8111-111111111111"
  export GENERATION="1"

  export PROJ="$TEST_SKILL_DIR/project"
  mkdir -p "$PROJ"
  export CANONICAL_PROJ
  CANONICAL_PROJ="$(canonical_path "$PROJ")"

  export BINDINGS_DIR="$TEST_SKILL_DIR/run/pilot/bindings"
  mkdir -p "$BINDINGS_DIR"
  export BINDING_FILE="$BINDINGS_DIR/$GENERATION.json"

  export COLLECTOR_STATE_DIR="$TEST_SKILL_DIR/run/pilot-collector"
  mkdir -p "$COLLECTOR_STATE_DIR"

  export CLAUDE_CONFIG_DIR="$TEST_SKILL_DIR/claude-config"
  export CLAUDE_PROJECTS_ROOT="$CLAUDE_CONFIG_DIR/projects"
  mkdir -p "$CLAUDE_PROJECTS_ROOT"

  export FAKE_BIN="$TEST_SKILL_DIR/fake-bin"
  mkdir -p "$FAKE_BIN"
  install_fake_claude

  export PATH="$FAKE_BIN:$PATH"
  export FAKE_CLAUDE_MODE="ok"
  export FAKE_CLAUDE_VERSION="2.1.0 (Claude Code)"

  create_binding

  export AGMSG_PM_BINDING_FILE="$BINDING_FILE"
  export AGMSG_PM_COLLECTOR_STATE_DIR="$COLLECTOR_STATE_DIR"
  export AGMSG_PM_PILOT_SESSION_ID="$SESSION_ID"
  export AGMSG_PM_PROCESS_GENERATION="$GENERATION"
  export AGMSG_PM_TEAM="$TEAM"
  export AGMSG_PM_AGENT="$PILOT_AGENT"
}

teardown() {
  chmod -R u+rwX "$TEST_SKILL_DIR" 2>/dev/null || true
  teardown_test_env
}

canonical_path() {
  python3 - "$1" <<'PY'
import os
import sys

sys.stdout.write(os.path.realpath(sys.argv[1]))
PY
}

sha256_text_json() {
  python3 - "$1" <<'PY'
import hashlib
import json
import sys

value = json.loads(sys.argv[1])
raw = json.dumps(
    value,
    ensure_ascii=False,
    separators=(",", ":"),
    sort_keys=True,
).encode("utf-8")

sys.stdout.write("sha256:" + hashlib.sha256(raw).hexdigest())
PY
}

json_field() {
  python3 - "$1" "$2" <<'PY'
import json
import sys

value = json.loads(sys.argv[1])
result = value[sys.argv[2]]

if result is None:
    sys.stdout.write("null")
elif isinstance(result, bool):
    sys.stdout.write("true" if result else "false")
else:
    sys.stdout.write(str(result))
PY
}

json_file_field() {
  python3 - "$1" "$2" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    value = json.load(fh)

result = value[sys.argv[2]]

if result is None:
    sys.stdout.write("null")
elif isinstance(result, bool):
    sys.stdout.write("true" if result else "false")
else:
    sys.stdout.write(str(result))
PY
}

state_file() {
  printf '%s/generation-%s.state.json' \
    "$COLLECTOR_STATE_DIR" \
    "$GENERATION"
}

ledger_file() {
  printf '%s/generation-%s.observations.jsonl' \
    "$COLLECTOR_STATE_DIR" \
    "$GENERATION"
}

create_binding() {
  cat > "$BINDING_FILE" <<EOF
{"schemaVersion":1,"team":"$TEAM","agent":"$PILOT_AGENT","type":"$PILOT_TYPE","project":"$CANONICAL_PROJ","sessionId":"$SESSION_ID","generation":"$GENERATION","pid":"$$","pidStart":"test-pid-start","profileDigest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","policyVersion":"pm-pilot-pretool-v1","guardDigest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","brokerDigest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","providerCommit":"0b2117c5f91f7950cc196e52edb188748adfa50a"}
EOF
}

install_fake_claude() {
  cat > "$FAKE_BIN/claude" <<'SH'
#!/usr/bin/env bash
set -u

case "${FAKE_CLAUDE_MODE:-ok}" in
  ok)
    printf '%s\n' "${FAKE_CLAUDE_VERSION:-2.1.0 (Claude Code)}"
    exit 0
    ;;
  fail)
    printf '%s\n' 'version unavailable' >&2
    exit 1
    ;;
  multiline)
    printf '%s\n' 'line one' 'line two'
    exit 0
    ;;
  *)
    exit 2
    ;;
esac
SH
  chmod +x "$FAKE_BIN/claude"
}

transcript_dir() {
  printf '%s/%s' \
    "$CLAUDE_PROJECTS_ROOT" \
    "${1:-opaque-project-encoding}"
}

transcript_file() {
  printf '%s/%s.jsonl' \
    "$(transcript_dir "${1:-opaque-project-encoding}")" \
    "$SESSION_ID"
}

make_transcript_dir() {
  mkdir -p "$(transcript_dir "${1:-opaque-project-encoding}")"
}

write_success_transcript() {
  local directory="${1:-opaque-project-encoding}"
  local tool_id="${2:-toolu_001}"
  local command_text="${3:-printf fixture-secret}"
  local file

  make_transcript_dir "$directory"
  file="$(transcript_file "$directory")"

  python3 \
    - "$file" \
    "$tool_id" \
    "$command_text" <<'PY'
import json
import sys

file_name, tool_id, command_text = sys.argv[1:]

records = [
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "working"},
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": "Bash",
                    "input": {
                        "description": "fixture",
                        "command": command_text,
                    },
                },
            ],
        },
    },
    {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": "tool-output-secret",
                }
            ],
        },
    },
]

with open(file_name, "w", encoding="utf-8") as fh:
    for record in records:
        fh.write(
            json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
PY
}

write_failure_transcript() {
  local file

  make_transcript_dir
  file="$(transcript_file)"

  cat > "$file" <<EOF
{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_failure","name":"Bash","input":{"command":"false"}}]}}
{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_failure","is_error":true,"content":"failed-output-secret"}]}}
EOF
}

run_discover() {
  run "$COLLECTOR" discover
}

run_scan() {
  run "$COLLECTOR" scan
}

run_status() {
  run "$COLLECTOR" status
}

@test "discover fixes the unique transcript without assuming project encoding" {
  write_success_transcript "totally-opaque/nested-name"

  run_discover

  [ "$status" -eq 0 ]
  [ "$(json_field "$output" collectorStatus)" = "ok" ]
  [ "$(json_field "$output" transcriptDiscovered)" = "true" ]

  local expected actual
  expected="$(canonical_path "$(transcript_file "totally-opaque/nested-name")")"
  actual="$(json_file_field "$(state_file)" transcriptPath)"

  [ "$actual" = "$expected" ]
}

@test "discover fails closed when no transcript candidate exists" {
  run_discover

  [ "$status" -eq 3 ]
  [ "$(json_field "$output" collectorStatus)" = "audit_unavailable" ]
  [ "$(json_field "$output" reason)" = "transcript_not_found" ]
}

@test "discover fails closed when two transcript candidates exist" {
  write_success_transcript "opaque-one"
  write_success_transcript "opaque-two"

  run_discover

  [ "$status" -eq 3 ]
  [ "$(json_field "$output" collectorStatus)" = "audit_unavailable" ]
  [ "$(json_field "$output" reason)" = "transcript_ambiguous" ]
}

@test "discover rejects a session transcript symlink escaping projects root" {
  local outside link

  outside="$TEST_SKILL_DIR/outside.jsonl"
  printf '%s\n' \
    '{"type":"system","message":{"content":"outside"}}' \
    > "$outside"

  make_transcript_dir
  link="$(transcript_file)"
  ln -s "$outside" "$link"

  run_discover

  [ "$status" -eq 3 ]
  [ "$(json_field "$output" collectorStatus)" = "audit_unavailable" ]
  [ "$(json_field "$output" reason)" = "transcript_outside_projects_root" ]
}

@test "discover fails closed when the unique transcript is unreadable" {
  write_success_transcript
  chmod 000 "$(transcript_file)"

  if cat "$(transcript_file)" >/dev/null 2>&1; then
    skip "host can still read chmod 000 files; unreadable-file semantics unavailable"
  fi

  run_discover

  [ "$status" -eq 3 ]
  [ "$(json_field "$output" collectorStatus)" = "audit_unavailable" ]
  [ "$(json_field "$output" reason)" = "transcript_unreadable" ]
}

@test "collector state directory is mandatory" {
  write_success_transcript
  unset AGMSG_PM_COLLECTOR_STATE_DIR

  run "$COLLECTOR" discover

  [ "$status" -eq 64 ]
  [ "$(json_field "$output" reason)" = "collector_state_dir_required" ]
}

@test "collector state directory may not be empty" {
  write_success_transcript
  export AGMSG_PM_COLLECTOR_STATE_DIR=""

  run "$COLLECTOR" discover

  [ "$status" -eq 64 ]
  [ "$(json_field "$output" reason)" = "collector_state_dir_required" ]
}

@test "collector state directory may not equal bindings directory" {
  write_success_transcript
  export AGMSG_PM_COLLECTOR_STATE_DIR="$BINDINGS_DIR"

  run "$COLLECTOR" discover

  [ "$status" -eq 64 ]
  [ "$(json_field "$output" reason)" = "collector_state_dir_overlaps_bindings" ]
}

@test "collector state directory may not be below bindings directory" {
  write_success_transcript
  export AGMSG_PM_COLLECTOR_STATE_DIR="$BINDINGS_DIR/collector"

  run "$COLLECTOR" discover

  [ "$status" -eq 64 ]
  [ "$(json_field "$output" reason)" = "collector_state_dir_overlaps_bindings" ]
  [ ! -e "$BINDINGS_DIR/collector" ]
}

@test "bindings directory may not be below collector state directory" {
  write_success_transcript

  local parent nested_bindings
  parent="$TEST_SKILL_DIR/shared-root"
  nested_bindings="$parent/bindings"
  mkdir -p "$nested_bindings"

  export COLLECTOR_STATE_DIR="$parent"
  export BINDINGS_DIR="$nested_bindings"
  export BINDING_FILE="$BINDINGS_DIR/$GENERATION.json"

  create_binding

  export AGMSG_PM_BINDING_FILE="$BINDING_FILE"
  export AGMSG_PM_COLLECTOR_STATE_DIR="$COLLECTOR_STATE_DIR"

  run "$COLLECTOR" discover

  [ "$status" -eq 64 ]
  [ "$(json_field "$output" reason)" = "collector_state_dir_overlaps_bindings" ]
}

@test "collector operations never add entries to the G4-A bindings directory" {
  write_success_transcript

  local before after
  before="$(find "$BINDINGS_DIR" -mindepth 1 -maxdepth 1 -print | sort)"

  run_discover
  [ "$status" -eq 0 ]

  run_scan
  [ "$status" -eq 0 ]

  run_status
  [ "$status" -eq 0 ]

  after="$(find "$BINDINGS_DIR" -mindepth 1 -maxdepth 1 -print | sort)"
  [ "$after" = "$before" ]
}

@test "scan writes only reduced metadata and deterministic input digest" {
  write_success_transcript \
    "opaque" \
    "toolu_digest" \
    "printf fixture-secret"

  run_discover
  [ "$status" -eq 0 ]

  run_scan

  [ "$status" -eq 0 ]
  [ "$(json_field "$output" collectorStatus)" = "ok" ]
  [ "$(json_field "$output" newObservations)" = "1" ]
  [ "$(json_field "$output" observations)" = "1" ]

  local ledger expected_digest actual_digest
  ledger="$(ledger_file)"
  [ -f "$ledger" ]

  expected_digest="$(
    sha256_text_json \
      '{"description":"fixture","command":"printf fixture-secret"}'
  )"

  actual_digest="$(
    python3 - "$ledger" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    value = json.loads(fh.readline())

print(value["toolInputDigest"])
PY
  )"

  [ "$actual_digest" = "$expected_digest" ]

  refute grep -Fq 'fixture-secret' "$ledger"
  refute grep -Fq 'tool-output-secret' "$ledger"
  refute grep -Fq '"input"' "$ledger"
  refute grep -Fq '"content"' "$ledger"
}

@test "scan records an explicit failure completion state" {
  write_failure_transcript

  run_discover
  [ "$status" -eq 0 ]

  run_scan
  [ "$status" -eq 0 ]

  [ "$(
    python3 - "$(ledger_file)" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    value = json.loads(fh.readline())

print(value["completionState"])
PY
  )" = "failure" ]
}

@test "unterminated final JSONL record is not consumed until completed" {
  local file first_size

  make_transcript_dir
  file="$(transcript_file)"

  printf '%s\n' \
    '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_partial","name":"Bash","input":{"command":"echo partial"}}]}}' \
    > "$file"

  printf '%s' \
    '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_partial","content":"still-writing"' \
    >> "$file"

  first_size="$(
    python3 - "$file" <<'PY'
import sys

with open(sys.argv[1], "rb") as fh:
    first = fh.readline()

print(len(first))
PY
  )"

  run_discover
  [ "$status" -eq 0 ]

  run_scan
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" newObservations)" = "0" ]
  [ "$(json_field "$output" pending)" = "1" ]
  [ "$(json_field "$output" sourceOffset)" = "$first_size" ]

  printf '%s\n' \
    '}]}}' \
    >> "$file"

  run_scan

  [ "$status" -eq 0 ]
  [ "$(json_field "$output" newObservations)" = "1" ]
  [ "$(json_field "$output" pending)" = "0" ]
  [ "$(json_field "$output" observations)" = "1" ]
}

@test "LF-terminated invalid JSON makes collector status unknown" {
  make_transcript_dir
  printf '%s\n' '{"type":"assistant"' > "$(transcript_file)"

  run_discover
  [ "$status" -eq 0 ]

  run_scan

  [ "$status" -eq 2 ]
  [ "$(json_field "$output" collectorStatus)" = "unknown" ]
  [ "$(json_field "$output" reason)" = "transcript_json_invalid" ]
  [ "$(json_field "$output" sourceOffset)" = "0" ]
}

@test "a tool result with no observed tool use makes status unknown" {
  make_transcript_dir
  cat > "$(transcript_file)" <<EOF
{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_missing","content":"result"}]}}
EOF

  run_discover
  [ "$status" -eq 0 ]

  run_scan

  [ "$status" -eq 2 ]
  [ "$(json_field "$output" collectorStatus)" = "unknown" ]
  [ "$(json_field "$output" reason)" = "tool_result_without_tool_use" ]
}

@test "unknown transcript record type is not silently discarded" {
  make_transcript_dir
  printf '%s\n' \
    '{"type":"future-record","payload":{"possibleTool":"unknown"}}' \
    > "$(transcript_file)"

  run_discover
  [ "$status" -eq 0 ]

  run_scan

  [ "$status" -eq 2 ]
  [ "$(json_field "$output" reason)" = "transcript_record_type_unknown" ]
}

@test "CLI version failure is unknown while transcript remains discovered" {
  write_success_transcript
  export FAKE_CLAUDE_MODE="fail"

  run_discover

  [ "$status" -eq 2 ]
  [ "$(json_field "$output" collectorStatus)" = "unknown" ]
  [ "$(json_field "$output" reason)" = "cli_version_unavailable" ]
  [ "$(json_field "$output" transcriptDiscovered)" = "true" ]
}

@test "scan can recover only the CLI-version unknown after version becomes available" {
  write_success_transcript
  export FAKE_CLAUDE_MODE="fail"

  run_discover
  [ "$status" -eq 2 ]

  export FAKE_CLAUDE_MODE="ok"

  run_scan

  [ "$status" -eq 0 ]
  [ "$(json_field "$output" collectorStatus)" = "ok" ]
  [ "$(json_field "$output" newObservations)" = "1" ]
}

@test "discover never follows a second path after this generation is fixed" {
  write_success_transcript "first-location"

  run_discover
  [ "$status" -eq 0 ]

  local fixed
  fixed="$(json_file_field "$(state_file)" transcriptPath)"

  write_success_transcript "second-location"

  run_discover

  [ "$status" -eq 0 ]
  [ "$(json_file_field "$(state_file)" transcriptPath)" = "$fixed" ]
}

@test "a new generation re-runs transcript discovery" {
  write_success_transcript "generation-one"

  run_discover
  [ "$status" -eq 0 ]

  local old_path new_path
  old_path="$(json_file_field "$(state_file)" transcriptPath)"

  mkdir -p "$(transcript_dir "generation-two")"
  mv \
    "$(transcript_file "generation-one")" \
    "$(transcript_file "generation-two")"

  export GENERATION="2"
  export BINDING_FILE="$BINDINGS_DIR/$GENERATION.json"
  create_binding
  export AGMSG_PM_BINDING_FILE="$BINDING_FILE"
  export AGMSG_PM_PROCESS_GENERATION="$GENERATION"

  run "$COLLECTOR" discover

  [ "$status" -eq 0 ]
  new_path="$(json_file_field "$(state_file)" transcriptPath)"
  [ "$new_path" != "$old_path" ]
  [ "$new_path" = "$(canonical_path "$(transcript_file "generation-two")")" ]
}

@test "scan detects transcript truncation after offset has advanced" {
  write_success_transcript

  run_discover
  [ "$status" -eq 0 ]

  run_scan
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" sourceOffset)" -gt 0 ]

  : > "$(transcript_file)"

  run_scan

  [ "$status" -eq 3 ]
  [ "$(json_field "$output" collectorStatus)" = "audit_unavailable" ]
  [ "$(json_field "$output" reason)" = "transcript_truncated" ]
}

@test "scan detects same-path transcript replacement by device inode identity" {
  write_success_transcript

  run_discover
  [ "$status" -eq 0 ]

  local file old
  file="$(transcript_file)"
  old="$file.old"

  mv "$file" "$old"
  write_success_transcript

  run_scan

  [ "$status" -eq 3 ]
  [ "$(json_field "$output" collectorStatus)" = "audit_unavailable" ]
  [ "$(json_field "$output" reason)" = "transcript_replaced" ]
}

@test "status is read-only" {
  write_success_transcript

  run_discover
  [ "$status" -eq 0 ]

  run_scan
  [ "$status" -eq 0 ]

  local state ledger before_state before_ledger after_state after_ledger
  state="$(state_file)"
  ledger="$(ledger_file)"

  before_state="$(sha256sum_portable "$state")"
  before_ledger="$(sha256sum_portable "$ledger")"

  run_status
  [ "$status" -eq 0 ]

  after_state="$(sha256sum_portable "$state")"
  after_ledger="$(sha256sum_portable "$ledger")"

  [ "$after_state" = "$before_state" ]
  [ "$after_ledger" = "$before_ledger" ]
}

sha256sum_portable() {
  python3 - "$1" <<'PY'
import hashlib
import sys

with open(sys.argv[1], "rb") as fh:
    print(hashlib.sha256(fh.read()).hexdigest())
PY
}

@test "malformed binding fails closed" {
  write_success_transcript
  printf '%s\n' '{"schemaVersion":1}' > "$BINDING_FILE"

  run_discover

  [ "$status" -eq 3 ]
  [ "$(json_field "$output" collectorStatus)" = "audit_unavailable" ]
  [ "$(json_field "$output" reason)" = "binding_schema_invalid" ]
}

@test "session environment mismatch fails closed" {
  write_success_transcript
  export AGMSG_PM_PILOT_SESSION_ID="22222222-2222-4222-8222-222222222222"

  run_discover

  [ "$status" -eq 3 ]
  [ "$(json_field "$output" reason)" = "binding_session_mismatch" ]
}

@test "generation environment mismatch fails closed" {
  write_success_transcript
  export AGMSG_PM_PROCESS_GENERATION="99"

  run_discover

  [ "$status" -eq 3 ]
  [ "$(json_field "$output" reason)" = "binding_generation_mismatch" ]
}