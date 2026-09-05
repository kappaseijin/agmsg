#!/usr/bin/env bats

# Issue #236 implementation contract (#240): this is a local, isolated
# contract test for the native Claude PreToolUse guard. It must not install or
# edit a real PM session and it must not modify the older #238 fixture.

load test_helper

setup() {
  setup_test_env
  export PM_ROOT="$TEST_SKILL_DIR/pm-pretool"
  export PM_PROJECT="$PM_ROOT/project"
  export PM_TEAM=isolated-team
  export PM_AGENT=pm
  export PM_TYPE=claude-code
  export PM_SESSION="11111111-1111-4111-8111-111111111111"
  export PM_GENERATION="generation-1"
  export PM_PID="$$"
  export PM_PROCESS_START="$(node "$SCRIPTS/session-identity.js" --process-start "$PM_PID")"
  export PM_BINDING="$PM_ROOT/binding.json"
  export PM_CLAIM_FILE="$RUN_DIR/actas.${PM_TEAM}__${PM_AGENT}.session"
  export PM_DECISIONS="$PM_ROOT/decisions.jsonl"
  export PM_EXECUTIONS="$PM_ROOT/executions.jsonl"
  export PM_HEARTBEAT="$PM_ROOT/heartbeat.json"
  export PM_AUDIT="$PM_ROOT/audit.json"
  export PM_BROKER_ROOT="$PM_ROOT/broker"
  export PM_BROKER_PATH="$(cd "$SCRIPTS" && pwd -P)/pm-broker.sh"
  export PM_WATCH_PATH="$(cd "$SCRIPTS" && pwd -P)/watch.sh"
  export PM_GUARD_DIGEST="$(node - "$SCRIPTS/pm-pretool-guard" <<'NODE'
const crypto = require('crypto');
const fs = require('fs');
process.stdout.write(`sha256:${crypto.createHash('sha256').update(fs.readFileSync(process.argv[2])).digest('hex')}`);
NODE
)"
  export PM_BROKER_DIGEST="$(node - "$PM_BROKER_PATH" <<'NODE'
const crypto = require('crypto');
const fs = require('fs');
process.stdout.write(`sha256:${crypto.createHash('sha256').update(fs.readFileSync(process.argv[2])).digest('hex')}`);
NODE
)"
  mkdir -p "$PM_PROJECT/.claude" "$PM_ROOT" "$PM_BROKER_ROOT" "$TEST_SKILL_DIR/teams/$PM_TEAM"

  node - "$TEST_SKILL_DIR/teams/$PM_TEAM/config.json" "$PM_PROJECT" <<'NODE'
const fs = require('fs');
const [file, project] = process.argv.slice(2);
fs.writeFileSync(file, JSON.stringify({
  name: 'isolated-team',
  agents: {pm: {registrations: [{type: 'claude-code', project}]}}
}) + '\n');
NODE
(
  export SKILL_DIR="$TEST_SKILL_DIR"
  . "$TEST_SKILL_DIR/scripts/lib/actas-lock.sh"
  actas_lock_claim "$PM_TEAM" "$PM_AGENT" "$PM_SESSION.$PM_PID" >/dev/null
)
grep -Fqx "$PM_SESSION.$PM_PID" "$PM_CLAIM_FILE"
node - "$PM_BINDING" "$PM_PROJECT" "$PM_TEAM" "$PM_AGENT" "$PM_TYPE" "$PM_SESSION" "$PM_GENERATION" "$PM_PID" "$PM_PROCESS_START" <<'NODE'
const fs = require('fs');
const [file, project, team, agent, type, sessionId, generation, pid, pidStart] = process.argv.slice(2);
fs.writeFileSync(file, JSON.stringify({
  schemaVersion: 1, team, agent, type, project: fs.realpathSync.native(project), sessionId, generation,
  pid: String(pid), pidStart, guardDigest: process.env.PM_GUARD_DIGEST, brokerDigest: process.env.PM_BROKER_DIGEST,
  profileDigest: 'sha256:0000000000000000000000000000000000000000000000000000000000000000',
  policyVersion: 'pm-pretool-v1'
}) + '\n');
NODE

  export AGMSG_PM_BINDING_FILE="$PM_BINDING"
  export AGMSG_PM_TEAM="$PM_TEAM"
  export AGMSG_PM_AGENT="$PM_AGENT"
  export AGMSG_PM_TYPE="$PM_TYPE"
  export AGMSG_PM_PROCESS_PID="$PM_PID"
  export AGMSG_PM_PROCESS_GENERATION="$PM_GENERATION"
  export AGMSG_PM_PROCESS_START="$PM_PROCESS_START"
  export AGMSG_PM_BROKER_PATH="$PM_BROKER_PATH"
  export AGMSG_PM_GUARD_DIGEST="$PM_GUARD_DIGEST"
  export AGMSG_PM_BROKER_DIGEST="$PM_BROKER_DIGEST"
  export AGMSG_PM_CLAIM_FILE="$PM_CLAIM_FILE"
  export AGMSG_PM_GUARD_PATH="$SCRIPTS/pm-pretool-guard"
  export AGMSG_PM_BROKER_ROOT="$PM_BROKER_ROOT"
  export AGMSG_PM_DECISIONS_FILE="$PM_DECISIONS"
  export AGMSG_PM_EXECUTIONS_FILE="$PM_EXECUTIONS"
  export AGMSG_PM_AUDIT_FILE="$PM_AUDIT"
  export AGMSG_PM_HEARTBEAT_FILE="$PM_HEARTBEAT"
}

teardown() { teardown_test_env; }

hook_input() {
  local tool="$1" command="${2:-}" persistent="${3:-true}" \
    description="${4:-agmsg inbox stream (acting as pm)}" input_json
  TOOL_NAME="$tool" COMMAND_TEXT="$command" MONITOR_PERSISTENT="$persistent" \
    MONITOR_DESCRIPTION="$description" SESSION_ID="$PM_SESSION" CWD="$PM_PROJECT" \
    node <<'NODE'
const input = {
  hook_event_name: 'PreToolUse',
  session_id: process.env.SESSION_ID,
  cwd: process.env.CWD,
  tool_name: process.env.TOOL_NAME,
  tool_use_id: 'toolu_isolated_1',
  tool_input: {},
};
if (process.env.TOOL_NAME === 'Bash' || process.env.TOOL_NAME === 'Monitor') {
  input.tool_input.command = process.env.COMMAND_TEXT;
}
if (process.env.TOOL_NAME === 'Monitor') {
  input.tool_input.description = process.env.MONITOR_DESCRIPTION;
  input.tool_input.persistent = process.env.MONITOR_PERSISTENT === 'true';
}
process.stdout.write(JSON.stringify(input));
NODE
}

run_hook() {
  local input="$1"
  run "$SCRIPTS/pm-pretool-guard" <<<"$input"
}

@test "normal direct Bash work is denied before execution" {
  run_hook "$(hook_input Bash 'git fetch origin')"
  [ "$status" -eq 2 ]
  grep -Fq 'permissionDecision' <<<"$output"
  [ ! -e "$PM_BROKER_ROOT/executed" ]
}

@test "a fixed structured broker operation is permitted without blanket allow output" {
  payload="eyJvcGVyYXRpb24iOiJhZ21zZ19zZW5kIiwicmVjaXBpZW50IjoicG0ifQ"
  run "$SCRIPTS/session-identity.sh" <<<"$(hook_input Bash 'placeholder')"
  [ "$status" -eq 0 ] || { printf '%s\n' "$output" >&2; false; }
  run_hook "$(hook_input Bash "$PM_BROKER_PATH agmsg_send $payload")"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -s "$PM_DECISIONS" ]
}

@test "shell syntax, wrapper, alternate helper, and unknown operation are denied" {
  payload="eyJvcGVyYXRpb24iOiJhZ21zZ19zZW5kIn0"
  for command in \
    "$PM_BROKER_PATH agmsg_send $payload | tee x" \
    "/bin/sh -c '$PM_BROKER_PATH agmsg_send $payload'" \
    "$PM_BROKER_PATH agmsg_send \$PAYLOAD" \
    "$PM_BROKER_PATH unknown $payload" \
    "$PM_ROOT/other-helper agmsg_send $payload"; do
    run_hook "$(hook_input Bash "$command")"
    [ "$status" -eq 2 ]
  done
}

@test "changing the broker path cannot create an allow" {
  payload="eyJvcGVyYXRpb24iOiJhZ21zZ19zZW5kIn0"
  run env AGMSG_PM_BROKER_PATH=/bin/echo "$SCRIPTS/pm-pretool-guard" \
    <<<"$(hook_input Bash "/bin/echo agmsg_send $payload")"
  [ "$status" -eq 2 ]
}

@test "role-limited Monitor permits only the fixed current-session watch command" {
  monitor_command="$(printf '%q %q %q %q %q' "$PM_WATCH_PATH" "$PM_SESSION.$PM_PID" "$PM_PROJECT" "$PM_TYPE" "$PM_AGENT")"
  run_hook "$(hook_input Monitor "$monitor_command")"
  [ "$status" -eq 0 ]
  grep -Fq '"tool":"Monitor"' "$PM_DECISIONS"

  for command in \
    "$(printf '%q %q %q %q' "$PM_WATCH_PATH" "$PM_SESSION.$PM_PID" "$PM_PROJECT" "$PM_TYPE")" \
    "$(printf '%q %q %q %q %q' "$PM_WATCH_PATH" "$PM_SESSION.$PM_PID" "$PM_PROJECT" "$PM_TYPE" other-role)" \
    "$monitor_command | tee monitor.log"; do
    run_hook "$(hook_input Monitor "$command")"
    [ "$status" -eq 2 ]
  done

  run_hook "$(hook_input Monitor "$monitor_command" false)"
  [ "$status" -eq 2 ]
  run_hook "$(hook_input Monitor "$monitor_command" true 'agmsg inbox stream (acting as another-role)')"
  [ "$status" -eq 2 ]
}

@test "the complete PM operation table uses only structured broker arguments" {
  for operation in agmsg_send agmsg_receive agmsg_delegate monitor actas drop \
    rule_load rule_record git_maintenance sync_origin_clone proxy_git_write \
    team_provision bot_collaborator apply_decision stale_runtime_cleanup \
    issue_view issue_comment pr_view pr_comment seat_start seat_stop; do
    payload="$(OPERATION="$operation" node <<'NODE'
const value = {operation: process.env.OPERATION, target: 'fixed'};
if (process.env.OPERATION.startsWith('agmsg_')) value.recipient = 'pm';
process.stdout.write(Buffer.from(JSON.stringify(value)).toString('base64url'));
NODE
)"
    run_hook "$(hook_input Bash "$PM_BROKER_PATH $operation $payload")"
    [ "$status" -eq 0 ]
  done
}

@test "native question remains available while an unidentified session cannot do work" {
  run_hook "$(hook_input AskUserQuestion)"
  [ "$status" -eq 0 ]

  run env -u AGMSG_PM_DECISIONS_FILE "$SCRIPTS/pm-pretool-guard" <<<"$(hook_input AskUserQuestion)"
  [ "$status" -eq 0 ]

  rm -f "$RUN_DIR/actas.${PM_TEAM}__${PM_AGENT}.session"
  run_hook "$(hook_input Bash 'git status')"
  [ "$status" -eq 2 ]
}

@test "identity anomalies fail closed: mismatched generation, duplicate roster, and stale claim" {
  payload="eyJvcGVyYXRpb24iOiJhZ21zZ19zZW5kIiwicmVjaXBpZW50IjoicG0ifQ"
  broker_command="$PM_BROKER_PATH agmsg_send $payload"
  node - "$PM_BINDING" <<'NODE'
const fs = require('fs');
const p = process.argv[2];
const value = JSON.parse(fs.readFileSync(p));
value.generation = 'wrong-generation';
fs.writeFileSync(p, JSON.stringify(value) + '\n');
NODE
  run_hook "$(hook_input Bash "$broker_command")"
  [ "$status" -eq 2 ]

  node - "$TEST_SKILL_DIR/teams/$PM_TEAM/config.json" "$PM_PROJECT" <<'NODE'
const fs = require('fs');
const [p, project] = process.argv.slice(2);
const c = JSON.parse(fs.readFileSync(p));
c.agents.pm.registrations.push({type: 'claude-code', project});
fs.writeFileSync(p, JSON.stringify(c) + '\n');
NODE
  node - "$PM_BINDING" <<'NODE'
const fs = require('fs');
const p = process.argv[2];
const value = JSON.parse(fs.readFileSync(p));
value.generation = process.env.PM_GENERATION;
fs.writeFileSync(p, JSON.stringify(value) + '\n');
NODE
  run_hook "$(hook_input Bash "$broker_command")"
  [ "$status" -eq 2 ]

  printf '%s\n' "$PM_SESSION.999999" > "$PM_CLAIM_FILE"
  run_hook "$(hook_input Bash "$broker_command")"
  [ "$status" -eq 2 ]
}

@test "identity rejects a forged process start token" {
  payload="eyJvcGVyYXRpb24iOiJhZ21zZ19zZW5kIiwicmVjaXBpZW50IjoicG0ifQ"
  forged_start=forged-process-start
  node - "$PM_BINDING" "$forged_start" <<'NODE'
const fs = require('fs');
const [bindingFile, forgedStart] = process.argv.slice(2);
const value = JSON.parse(fs.readFileSync(bindingFile));
value.pidStart = forgedStart;
fs.writeFileSync(bindingFile, JSON.stringify(value) + '\n');
NODE
  export AGMSG_PM_PROCESS_START="$forged_start"
  run_hook "$(hook_input Bash "$PM_BROKER_PATH agmsg_send $payload")"
  [ "$status" -eq 2 ]
  grep -Fq 'process_start_mismatch' <<<"$output"
}

@test "identity rejects a live process outside the current process ancestry" {
  payload="eyJvcGVyYXRpb24iOiJhZ21zZ19zZW5kIiwicmVjaXBpZW50IjoicG0ifQ"
  sleep 60 &
  other_pid=$!
  other_start="$(node "$SCRIPTS/session-identity.js" --process-start "$other_pid")"
  node - "$PM_BINDING" "$other_pid" "$other_start" <<'NODE'
const fs = require('fs');
const [bindingFile, pid, pidStart] = process.argv.slice(2);
const value = JSON.parse(fs.readFileSync(bindingFile));
value.pid = pid;
value.pidStart = pidStart;
fs.writeFileSync(bindingFile, JSON.stringify(value) + '\n');
NODE
  export AGMSG_PM_PROCESS_PID="$other_pid"
  export AGMSG_PM_PROCESS_START="$other_start"
  run_hook "$(hook_input Bash "$PM_BROKER_PATH agmsg_send $payload")"
  hook_status="$status"
  hook_output="$output"
  kill "$other_pid" 2>/dev/null || true
  wait "$other_pid" 2>/dev/null || true
  [ "$hook_status" -eq 2 ]
  grep -Fq 'process_parent_mismatch' <<<"$hook_output"
}

@test "formal claim is consumed by existing owner and state readers" {
  owner="$PM_SESSION.$PM_PID"
  run bash -c '. "$1/scripts/lib/actas-lock.sh"; actas_lock_owner "$2" "$3"' \
    _ "$TEST_SKILL_DIR" "$PM_TEAM" "$PM_AGENT"
  [ "$status" -eq 0 ]
  [ "$output" = "$owner" ]

  run bash -c '. "$1/scripts/lib/actas-lock.sh"; actas_lock_state "$2" "$3" "$4"' \
    _ "$TEST_SKILL_DIR" "$PM_TEAM" "$PM_AGENT" "$owner"
  [ "$status" -eq 0 ]
  [ "$output" = 'mine' ]
}

@test "identity and audit reject every non-canonical claim form" {
  payload="eyJvcGVyYXRpb24iOiJhZ21zZ19zZW5kIiwicmVjaXBpZW50IjoicG0ifQ"
  broker_command="$PM_BROKER_PATH agmsg_send $payload"
  owner="$PM_SESSION.$PM_PID"
  for form in empty json extra-line extra-space no-final-lf carriage; do
    case "$form" in
      empty) : > "$PM_CLAIM_FILE" ;;
      json) printf '%s\n' '{"schemaVersion":1}' > "$PM_CLAIM_FILE" ;;
      extra-line) printf '%s\nextra\n' "$owner" > "$PM_CLAIM_FILE" ;;
      extra-space) printf '%s \n' "$owner" > "$PM_CLAIM_FILE" ;;
      no-final-lf) printf '%s' "$owner" > "$PM_CLAIM_FILE" ;;
      carriage) printf '%s\r\n' "$owner" > "$PM_CLAIM_FILE" ;;
    esac
    run_hook "$(hook_input Bash "$broker_command")"
    [ "$status" -eq 2 ]
    grep -Fq 'claim_' <<<"$output"
    run env AGMSG_PM_GUARD_PATH="$SCRIPTS/pm-pretool-guard" \
      bash "$SCRIPTS/pm-audit.sh" --once
    [ "$status" -eq 1 ]
    grep -Fq 'claim_' <<<"$output"
  done

  printf '%s\n' "$owner" > "$PM_CLAIM_FILE"
  chmod 000 "$PM_CLAIM_FILE"
  run_hook "$(hook_input Bash "$broker_command")"
  unreadable_status="$status"
  unreadable_output="$output"
  chmod 600 "$PM_CLAIM_FILE"
  [ "$unreadable_status" -eq 2 ]
  grep -Fq 'claim_unreadable' <<<"$unreadable_output"

  AGMSG_PM_CLAIM_FILE="$PM_ROOT/not-derived.session" \
    run_hook "$(hook_input Bash "$broker_command")"
  [ "$status" -eq 2 ]
  grep -Fq 'claim_path_mismatch' <<<"$output"
  run env AGMSG_PM_CLAIM_FILE="$PM_ROOT/not-derived.session" \
    AGMSG_PM_GUARD_PATH="$SCRIPTS/pm-pretool-guard" bash "$SCRIPTS/pm-audit.sh" --once
  [ "$status" -eq 1 ]
  grep -Fq 'claim_path_mismatch' <<<"$output"
}

@test "claim path encoding matches actas for non-ASCII and punctuation" {
  team='日本 team_%'
  agent='alice%_pm'
  node_path="$(node - "$SCRIPTS/lib/pm-claim.js" "$TEST_SKILL_DIR" "$team" "$agent" <<'NODE'
const {claimPath} = require(process.argv[2]);
process.stdout.write(claimPath(process.argv[3], process.argv[4], process.argv[5]));
NODE
)"
  shell_path="$(bash -c '. "$1/scripts/lib/actas-lock.sh"; actas_lock_path "$2" "$3"' \
    _ "$TEST_SKILL_DIR" "$team" "$agent")"
  [ "$node_path" = "$shell_path" ]

  space_path="$(bash -c '. "$1/scripts/lib/actas-lock.sh"; actas_lock_path "$2" "$3"' \
    _ "$TEST_SKILL_DIR" 'foo bar' pm)"
  underscore_path="$(bash -c '. "$1/scripts/lib/actas-lock.sh"; actas_lock_path "$2" "$3"' \
    _ "$TEST_SKILL_DIR" foo_bar pm)"
  [ "$space_path" != "$underscore_path" ]
}

@test "identity rejects a claim exchanged between its two reads" {
  owner="$PM_SESSION.$PM_PID"
  rm -f "$PM_CLAIM_FILE"
  mkfifo "$PM_CLAIM_FILE"
  (
    printf '%s\n' "$owner" > "$PM_CLAIM_FILE"
    printf '%s\n' "$owner-replaced" > "$PM_CLAIM_FILE"
  ) &
  feeder_pid=$!
  set +e
  node "$SCRIPTS/session-identity.js" <<<"$(hook_input Bash placeholder)" \
    >"$PM_ROOT/claim-exchange.out" 2>&1
  identity_status=$?
  set -e
  wait "$feeder_pid"
  rm -f "$PM_CLAIM_FILE"
  printf '%s\n' "$owner" > "$PM_CLAIM_FILE"
  [ "$identity_status" -eq 1 ]
  grep -Fq 'claim_mismatch' "$PM_ROOT/claim-exchange.out"
}

@test "identity rejects a binding exchanged before the final read" {
  first_binding="$PM_ROOT/binding-first.json"
  second_binding="$PM_ROOT/binding-second.json"
  cp "$PM_BINDING" "$first_binding"
  node - "$second_binding" "$first_binding" <<'NODE'
const fs = require('fs');
const [out, input] = process.argv.slice(2);
const value = JSON.parse(fs.readFileSync(input));
value.generation = 'exchanged-generation';
fs.writeFileSync(out, JSON.stringify(value) + '\n');
NODE
  rm -f "$PM_BINDING"
  mkfifo "$PM_BINDING"
  (
    cat "$first_binding" > "$PM_BINDING"
    cat "$second_binding" > "$PM_BINDING"
  ) &
  feeder_pid=$!
  set +e
  node "$SCRIPTS/session-identity.js" <<<"$(hook_input Bash placeholder)" \
    >"$PM_ROOT/binding-exchange.out" 2>&1
  identity_status=$?
  set -e
  wait "$feeder_pid"
  rm -f "$PM_BINDING"
  cp "$first_binding" "$PM_BINDING"
  [ "$identity_status" -eq 1 ]
  grep -Fq 'binding_changed' "$PM_ROOT/binding-exchange.out"
}

@test "malformed hook input is a blocking internal error" {
  run "$SCRIPTS/pm-pretool-guard" <<< '{not-json'
  [ "$status" -eq 2 ]
  grep -Fq 'permissionDecision' <<<"$output"
}

@test "independent audit detects a denied execution and writes a heartbeat" {
  mkdir -p "$PM_ROOT"
  printf '%s\n' '{"schemaVersion":1,"sessionId":"11111111-1111-4111-8111-111111111111","generation":"generation-1","toolUseId":"toolu_isolated_1","tool":"Bash","inputDigest":"sha256:0000000000000000000000000000000000000000000000000000000000000000","decision":"deny","reason":"test","policyVersion":"pm-pretool-v1"}' > "$PM_DECISIONS"
  printf '%s\n' '{"schemaVersion":1,"sessionId":"11111111-1111-4111-8111-111111111111","generation":"generation-1","toolUseId":"toolu_isolated_1","tool":"Bash","inputDigest":"sha256:0000000000000000000000000000000000000000000000000000000000000000","policyVersion":"pm-pretool-v1"}' > "$PM_EXECUTIONS"
  run env AGMSG_PM_GUARD_PATH="$BATS_TEST_DIRNAME/../scripts/pm-pretool-guard" \
    bash "$BATS_TEST_DIRNAME/../scripts/pm-audit.sh" --once
  [ "$status" -eq 1 ]
  grep -Fq 'denied_tool_executed' <<<"$output"
  [ -s "$PM_HEARTBEAT" ]
}

@test "independent audit is healthy when each execution has a pretool decision" {
  payload="eyJvcGVyYXRpb24iOiJhZ21zZ19zZW5kIiwicmVjaXBpZW50IjoicG0ifQ"
  run_hook "$(hook_input Bash "$PM_BROKER_PATH agmsg_send $payload")"
  [ "$status" -eq 0 ]
  run "$SCRIPTS/pm-broker.sh" agmsg_send "$payload"
  [ "$status" -eq 0 ]
  hook_input Bash "$PM_BROKER_PATH agmsg_send $payload" | \
    "$SCRIPTS/pm-posttool-record"
  run env AGMSG_PM_GUARD_PATH="$SCRIPTS/pm-pretool-guard" \
    "$SCRIPTS/pm-audit.sh" --once
  [ "$status" -eq 0 ]
  grep -Fq '"status":"healthy"' <<<"$output"
  run env AGMSG_PM_AUDIT_CYCLES=1 AGMSG_PM_AUDIT_INTERVAL_S=0 \
    AGMSG_PM_GUARD_PATH="$SCRIPTS/pm-pretool-guard" \
    "$SCRIPTS/pm-audit-loop.sh"
  [ "$status" -eq 0 ]
}

@test "outer guard failure leaves a known hole and independent audit reports it" {
  stub="$PM_ROOT/harmless-stub"
  marker="$PM_ROOT/outer-marker"
  printf '#!/bin/sh\nprintf outer-ran > "$1"\n' > "$stub"
  chmod +x "$stub"
  run "$stub" "$marker"
  [ "$status" -eq 0 ]
  [ -s "$marker" ]

  rm -f "$AGMSG_PM_GUARD_PATH"
  hook_input Bash "$stub $marker" | "$SCRIPTS/pm-posttool-record"
  run env AGMSG_PM_GUARD_PATH="$AGMSG_PM_GUARD_PATH" \
    "$SCRIPTS/pm-audit.sh" --once
  [ "$status" -eq 1 ]
  grep -Fq 'guard_unavailable' <<<"$output"
  grep -Fq 'execution_without_decision' <<<"$output"
  [ -s "$PM_HEARTBEAT" ]
}

@test "missing heartbeat is detected by the independent watchdog" {
  run bash "$BATS_TEST_DIRNAME/../scripts/pm-audit-watchdog.sh" \
    --heartbeat "$PM_HEARTBEAT" --now 1000 --stale-seconds 180
  [ "$status" -eq 1 ]
  grep -Fq 'heartbeat_unavailable' <<<"$output"
}

@test "the watchdog treats the exact stale threshold as stale" {
  printf '%s\n' '{"schemaVersion":1,"status":"healthy","observedAtEpoch":1000}' > "$PM_HEARTBEAT"
  run bash "$BATS_TEST_DIRNAME/../scripts/pm-audit-watchdog.sh" \
    --heartbeat "$PM_HEARTBEAT" --now 1180 --stale-seconds 180
  [ "$status" -eq 1 ]
  grep -Fq 'heartbeat_stale' <<<"$output"
}

@test "audit rejects malformed binding and unknown execution records" {
  printf '%s\n' '{}' > "$PM_BINDING"
  run env AGMSG_PM_BINDING_FILE="$PM_BINDING" "$SCRIPTS/pm-audit.sh" --once
  [ "$status" -eq 1 ]
  grep -Fq 'binding_schema_invalid' <<<"$output"

  node - "$PM_EXECUTIONS" <<'NODE'
const fs = require('fs');
const file = process.argv[2];
fs.writeFileSync(file, JSON.stringify({
  schemaVersion: 1, sessionId: process.env.PM_SESSION, generation: process.env.PM_GENERATION,
  toolUseId: 'unknown-tool-use', tool: 'UnknownTool', inputDigest: 'sha256:0000000000000000000000000000000000000000000000000000000000000000',
  policyVersion: 'pm-pretool-v1',
}) + '\n');
NODE
  run "$SCRIPTS/pm-audit.sh" --once
  [ "$status" -eq 1 ]
  grep -Fq 'execution_unknown_tool' <<<"$output"
}

@test "audit failure does not mint a fresh heartbeat" {
  mkdir -p "$PM_ROOT/audit-output-dir"
  run env AGMSG_PM_AUDIT_FILE="$PM_ROOT/audit-output-dir" "$SCRIPTS/pm-audit.sh" --once
  [ "$status" -eq 1 ]
  grep -Fq 'EISDIR' <<<"$output"
  [ ! -e "$PM_HEARTBEAT" ]
}
