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
  export PM_PROCESS_START="test-start"
  export PM_BINDING="$PM_ROOT/binding.json"
  export PM_DECISIONS="$PM_ROOT/decisions.jsonl"
  export PM_EXECUTIONS="$PM_ROOT/executions.jsonl"
  export PM_HEARTBEAT="$PM_ROOT/heartbeat.json"
  export PM_AUDIT="$PM_ROOT/audit.json"
  export PM_BROKER_ROOT="$PM_ROOT/broker"
  export PM_BROKER_PATH="$SCRIPTS/pm-broker.sh"
  mkdir -p "$PM_PROJECT/.claude" "$PM_ROOT" "$PM_BROKER_ROOT" "$TEST_SKILL_DIR/teams/$PM_TEAM"

  node - "$TEST_SKILL_DIR/teams/$PM_TEAM/config.json" "$PM_PROJECT" <<'NODE'
const fs = require('fs');
const [file, project] = process.argv.slice(2);
fs.writeFileSync(file, JSON.stringify({
  name: 'isolated-team',
  agents: {pm: {registrations: [{type: 'claude-code', project}]}}
}) + '\n');
NODE
  printf '%s\n' "$PM_SESSION.$PM_PID" > "$RUN_DIR/actas.${PM_TEAM}__${PM_AGENT}.session"
  node - "$PM_BINDING" "$PM_PROJECT" "$PM_TEAM" "$PM_AGENT" "$PM_TYPE" "$PM_SESSION" "$PM_GENERATION" "$PM_PID" <<'NODE'
const fs = require('fs');
const [file, project, team, agent, type, sessionId, generation, pid] = process.argv.slice(2);
fs.writeFileSync(file, JSON.stringify({
  schemaVersion: 1, team, agent, type, project: fs.realpathSync.native(project), sessionId, generation,
  pid: String(pid), pidStart: 'test-start', profileDigest: 'sha256:test-profile',
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
  export AGMSG_PM_GUARD_PATH="$SCRIPTS/pm-pretool-guard"
  export AGMSG_PM_BROKER_ROOT="$PM_BROKER_ROOT"
  export AGMSG_PM_DECISIONS_FILE="$PM_DECISIONS"
  export AGMSG_PM_EXECUTIONS_FILE="$PM_EXECUTIONS"
  export AGMSG_PM_AUDIT_FILE="$PM_AUDIT"
  export AGMSG_PM_HEARTBEAT_FILE="$PM_HEARTBEAT"
}

teardown() { teardown_test_env; }

hook_input() {
  local tool="$1" command="${2:-}" input_json
  TOOL_NAME="$tool" COMMAND_TEXT="$command" SESSION_ID="$PM_SESSION" CWD="$PM_PROJECT" \
    node <<'NODE'
const input = {
  hook_event_name: 'PreToolUse',
  session_id: process.env.SESSION_ID,
  cwd: process.env.CWD,
  tool_name: process.env.TOOL_NAME,
  tool_use_id: 'toolu_isolated_1',
  tool_input: {},
};
if (process.env.TOOL_NAME === 'Bash') input.tool_input.command = process.env.COMMAND_TEXT;
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

  rm -f "$RUN_DIR/actas.${PM_TEAM}__${PM_AGENT}.session"
  run_hook "$(hook_input Bash 'git status')"
  [ "$status" -eq 2 ]
}

@test "identity anomalies fail closed: mismatched generation, duplicate roster, and stale claim" {
  node - "$PM_BINDING" <<'NODE'
const fs = require('fs');
const p = process.argv[2];
const value = JSON.parse(fs.readFileSync(p));
value.generation = 'wrong-generation';
fs.writeFileSync(p, JSON.stringify(value) + '\n');
NODE
  run_hook "$(hook_input Bash 'git status')"
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
  run_hook "$(hook_input Bash 'git status')"
  [ "$status" -eq 2 ]

  printf '%s\n' "$PM_SESSION.999999" > "$RUN_DIR/actas.${PM_TEAM}__${PM_AGENT}.session"
  run_hook "$(hook_input Bash 'git status')"
  [ "$status" -eq 2 ]
}

@test "malformed hook input is a blocking internal error" {
  run "$SCRIPTS/pm-pretool-guard" <<< '{not-json'
  [ "$status" -eq 2 ]
  grep -Fq 'permissionDecision' <<<"$output"
}

@test "independent audit detects a denied execution and writes a heartbeat" {
  mkdir -p "$PM_ROOT"
  printf '%s\n' '{"toolUseId":"toolu_isolated_1","decision":"deny","policyVersion":"pm-pretool-v1"}' > "$PM_DECISIONS"
  printf '%s\n' '{"toolUseId":"toolu_isolated_1","tool":"Bash","inputDigest":"sha256:test"}' > "$PM_EXECUTIONS"
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
