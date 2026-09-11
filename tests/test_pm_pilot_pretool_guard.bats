#!/usr/bin/env bats

# Component tests for scripts/pm-pilot-pretool-guard (#404).
#
# Contract: docs/decisions/2026-09-11T133016_issue-404-pilot-pretool-guard-contract.md
#
# The real guard runs from the fixture copy of scripts/. Only the identity
# helper (session-identity.js) is replaced by a stub, because the real helper
# needs a live launcher process, claim, and roster. The stub is selected by
# STUB_IDENTITY so each identity branch can be broken on its own. One test
# keeps the real helper to show that its failure denies.
#
# Every deny case starts from a fixture in which the allow case passes and
# breaks exactly one condition.

bats_require_minimum_version 1.5.0

load test_helper

setup() {
  setup_test_env

  export GUARD="$SCRIPTS/pm-pilot-pretool-guard"
  export BROKER="$SCRIPTS/p2-consumer-broker.sh"
  [ -f "$GUARD" ]
  [ -x "$GUARD" ]
  [ -x "$BROKER" ]

  export PILOT_SESSION="33333333-3333-4333-8333-333333333333"
  export PROJ="$TEST_SKILL_DIR/project"
  mkdir -p "$PROJ/.agmsg-gate/i1-requests" "$PROJ/gh-config"
  export CANONICAL_PROJ
  CANONICAL_PROJ="$(node -e 'process.stdout.write(require("fs").realpathSync.native(process.argv[1]))' "$PROJ")"
  export CONFIG="$CANONICAL_PROJ/.agmsg-gate/i1-run-config.json"
  export REQUEST="$CANONICAL_PROJ/.agmsg-gate/i1-requests/r1.json"
  export GH_DIR="$CANONICAL_PROJ/gh-config"
  printf '%s\n' '{}' > "$CONFIG"
  printf '%s\n' '{}' > "$REQUEST"

  node "$SCRIPTS/lib/pilot-profile.js" --project "$CANONICAL_PROJ" --guard "$GUARD" > /dev/null

  # Like the launcher: the binding, decision log, and execution log share one
  # directory, and both logs exist as regular files before the first hook.
  export BINDINGS_DIR="$TEST_SKILL_DIR/bindings"
  mkdir -p "$BINDINGS_DIR"
  : > "$BINDINGS_DIR/1.decisions.jsonl"
  : > "$BINDINGS_DIR/1.executions.jsonl"

  install_identity_stub
  write_binding

  export AGMSG_PM_PILOT_SESSION_ID="$PILOT_SESSION"
  export AGMSG_PM_BINDING_FILE="$BINDINGS_DIR/1.json"
  export AGMSG_PM_GUARD_PATH="$GUARD"
  export AGMSG_PM_BROKER_PATH="$BROKER"
  export AGMSG_PM_DECISIONS_FILE="$BINDINGS_DIR/1.decisions.jsonl"
  export AGMSG_PM_EXECUTIONS_FILE="$BINDINGS_DIR/1.executions.jsonl"
  export AGMSG_PM_TEAM="pilot-team"
  export AGMSG_PM_AGENT="agmsg_pm_pilot_claude"
  export AGMSG_PM_TYPE="claude-code"
  export AGMSG_PM_PROCESS_PID="4242"
  export AGMSG_PM_PROCESS_GENERATION="1"
  export AGMSG_PM_PROCESS_START="Thu Sep 11 00:00:00 2026"
  export AGMSG_PM_TEAMS_DIR="$TEST_SKILL_DIR/teams"
  export AGMSG_PM_CLAIM_FILE="$TEST_SKILL_DIR/run/claim"
  export STUB_IDENTITY=ok

  export PM_GUARD_DIGEST_BEFORE
  PM_GUARD_DIGEST_BEFORE="$(digest "$BATS_TEST_DIRNAME/../scripts/pm-pretool-guard")"
}

teardown() {
  # E10: the current PM guard must not change.
  [ "$(digest "$BATS_TEST_DIRNAME/../scripts/pm-pretool-guard")" = "$PM_GUARD_DIGEST_BEFORE" ]
  teardown_test_env
}

digest() {
  node -e '
    const crypto = require("crypto");
    const fs = require("fs");
    process.stdout.write("sha256:" + crypto.createHash("sha256").update(fs.readFileSync(process.argv[1])).digest("hex"));
  ' "$1"
}

install_identity_stub() {
  cat > "$SCRIPTS/session-identity.js" <<'EOF'
#!/usr/bin/env node
'use strict';
require('fs').readFileSync(0);
const mode = process.env.STUB_IDENTITY || 'ok';
const value = {
  status: 'ok',
  agent: 'agmsg_pm_pilot_claude',
  type: 'claude-code',
  sessionId: process.env.STUB_IDENTITY_SESSION || process.env.AGMSG_PM_PILOT_SESSION_ID,
};
if (mode === 'fail') process.exit(1);
if (mode === 'sleep') { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5000); }
if (mode === 'garbage') { process.stdout.write('not json\n'); process.exit(0); }
if (mode === 'status') value.status = 'unidentifiable';
if (mode === 'agent') value.agent = 'agmsg_pm_claude';
if (mode === 'type') value.type = 'codex';
process.stdout.write(JSON.stringify(value) + '\n');
EOF
  chmod +x "$SCRIPTS/session-identity.js"
}

# binding.json with digests of the current fixture files. Extra args are
# "field=value" overrides.
write_binding() {
  node - "$BINDINGS_DIR/1.json" "$CANONICAL_PROJ" "$GUARD" "$BROKER" "$SCRIPTS/lib/pilot-binding.js" "$@" <<'NODE'
'use strict';
const fs = require('fs');
const path = require('path');
const [file, project, guard, broker, helper, ...overrides] = process.argv.slice(2);
const contract = require(helper);
const binding = {
  schemaVersion: 1,
  policyVersion: contract.POLICY_VERSION,
  providerCommit: contract.PROVIDER_COMMIT,
  project,
  guardDigest: contract.sha256File(guard),
  profileDigest: contract.sha256File(path.join(project, '.claude', 'settings.local.json')),
  brokerDigest: contract.sha256File(broker),
};
for (const override of overrides) {
  const at = override.indexOf('=');
  binding[override.slice(0, at)] = override.slice(at + 1);
}
fs.writeFileSync(file, JSON.stringify(binding) + '\n');
NODE
}

# hook_json <tool> <command>
hook_json() {
  node -e '
    const [tool, command] = process.argv.slice(1);
    const toolInput = tool === "Bash" ? {command} : {file_path: "/etc/hosts", content: "x"};
    process.stdout.write(JSON.stringify({
      session_id: process.env.PILOT_SESSION,
      transcript_path: "/dev/null",
      cwd: process.env.CANONICAL_PROJ,
      hook_event_name: "PreToolUse",
      tool_name: tool,
      tool_use_id: "toolu_test_1",
      tool_input: toolInput,
    }));
  ' "$1" "$2"
}

guard_raw() {
  run --separate-stderr "$GUARD" <<<"$1"
}

guard_bash() {
  guard_raw "$(hook_json Bash "$1")"
}

form_a() {
  printf '%s --config %s %s < %s' "$BROKER" "$CONFIG" "$1" "$REQUEST"
}

form_b() {
  printf '%s --config %s --gh-config-dir %s %s < %s' "$BROKER" "$CONFIG" "$GH_DIR" "$1" "$REQUEST"
}

last_decision_field() {
  tail -n 1 "$AGMSG_PM_DECISIONS_FILE" | node -e '
    const line = require("fs").readFileSync(0, "utf8");
    const value = JSON.parse(line)[process.argv[1]];
    process.stdout.write(value === null ? "null" : String(value));
  ' "$1"
}

assert_allow() {
  [ "$status" -eq 0 ] || { printf 'status=%s stdout=%s stderr=%s\n' "$status" "$output" "$stderr"; return 1; }
  [ "$output" = '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"pm-pilot-pretool-v1: fixed broker contract"}}' ]
  [ -z "$stderr" ]
  [ "$(last_decision_field decision)" = allow ]
  [ "$(last_decision_field reason)" = null ]
}

# assert_deny <reason> [recorded]
assert_deny() {
  [ "$status" -eq 2 ] || { printf 'status=%s stdout=%s stderr=%s\n' "$status" "$output" "$stderr"; return 1; }
  [ "$output" = "{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"$1; pilot accepts only the fixed p2-consumer-broker contract\"}}" ] ||
    { printf 'stdout=%s\n' "$output"; return 1; }
  [ "$stderr" = "pm-pilot-pretool-guard: deny $1" ]
  if [ "${2:-recorded}" = recorded ]; then
    [ "$(last_decision_field decision)" = deny ]
    [ "$(last_decision_field reason)" = "$1" ]
  fi
}

# --- allow -----------------------------------------------------------------

@test "pilot guard: form A allows each of the five operations" {
  local op
  for op in receive delegate collect-result issue-record observe-owner; do
    guard_bash "$(form_a "$op")"
    assert_allow
  done
  [ "$(wc -l < "$AGMSG_PM_DECISIONS_FILE" | tr -d ' ')" = 5 ]
}

@test "pilot guard: form B allows issue-record" {
  guard_bash "$(form_b issue-record)"
  assert_allow
}

@test "pilot guard: allow record satisfies the decision schema" {
  guard_bash "$(form_a receive)"
  assert_allow
  tail -n 1 "$AGMSG_PM_DECISIONS_FILE" | node -e '
    const record = JSON.parse(require("fs").readFileSync(0, "utf8"));
    const keys = ["schemaVersion", "observedAt", "guard", "policyVersion", "sessionId", "generation",
      "toolUseId", "tool", "inputDigest", "decision", "reason"];
    if (JSON.stringify(Object.keys(record).sort()) !== JSON.stringify([...keys].sort())) process.exit(10);
    if (record.schemaVersion !== 1 || record.guard !== "pm-pilot-pretool-guard") process.exit(11);
    if (record.policyVersion !== "pm-pilot-pretool-v1") process.exit(12);
    if (record.sessionId !== process.env.PILOT_SESSION || record.generation !== "1") process.exit(13);
    if (record.toolUseId !== "toolu_test_1" || record.tool !== "Bash") process.exit(14);
    if (!/^sha256:[0-9a-f]{64}$/.test(record.inputDigest)) process.exit(15);
    if (Number.isNaN(Date.parse(record.observedAt))) process.exit(16);
  '
}

# --- grammar (stage 3) -------------------------------------------------------

@test "pilot guard: form B with an operation other than issue-record is denied" {
  local op
  for op in receive delegate collect-result observe-owner; do
    guard_bash "$(form_b "$op")"
    assert_deny command_not_in_pilot_contract
  done
}

@test "pilot guard: shell syntax and malformed tokens are denied" {
  local command
  local -a commands=(
    "$(form_a receive) | cat"
    "$(form_a receive); true"
    "$(form_a receive) && true"
    "$BROKER --config $CONFIG receive > $REQUEST"
    "$BROKER --config '$CONFIG' receive < $REQUEST"
    "$BROKER --config \"$CONFIG\" receive < $REQUEST"
    "$BROKER --config \$HOME/x receive < $REQUEST"
    "$BROKER --config \`id\` receive < $REQUEST"
    "$(form_a receive)
true"
    "$BROKER  --config $CONFIG receive < $REQUEST"
    " $(form_a receive)"
    "$(form_a receive) "
    "scripts/p2-consumer-broker.sh --config $CONFIG receive < $REQUEST"
    "$BROKER --config $CANONICAL_PROJ/../x receive < $REQUEST"
    "$BROKER --config $CANONICAL_PROJ/./x receive < $REQUEST"
    "$BROKER --config $CANONICAL_PROJ//x receive < $REQUEST"
    "$BROKER --config $CANONICAL_PROJ/.. receive < $REQUEST"
    "$BROKER --config $CONFIG receive"
    "$BROKER --config $CONFIG receive < $REQUEST extra"
    "$BROKER --config $CONFIG send < $REQUEST"
    "$BROKER --config $CONFIG receive <$REQUEST"
    "$BROKER --cfg $CONFIG receive < $REQUEST"
    "$BROKER --config $CONFIG --gh $GH_DIR issue-record < $REQUEST"
    "$BROKER --config $CONFIG receive < $CANONICAL_PROJ/r*.json"
    ""
  )
  for command in "${commands[@]}"; do
    guard_bash "$command"
    assert_deny command_not_in_pilot_contract || { printf 'command=%q\n' "$command"; return 1; }
  done
}

@test "pilot guard: grammar is checked before identity" {
  export STUB_IDENTITY=fail
  guard_bash "$(form_a receive) | cat"
  assert_deny command_not_in_pilot_contract
}

# --- tool (stage 2) ----------------------------------------------------------

@test "pilot guard: every tool other than Bash is denied" {
  local tool
  for tool in Write Edit Read Monitor AskUserQuestion EnterPlanMode ExitPlanMode; do
    guard_raw "$(hook_json "$tool" '')"
    assert_deny tool_not_allowed
  done
}

# --- identity (stages 4, 5) --------------------------------------------------

@test "pilot guard: identity helper failure, timeout, and bad output are denied" {
  export STUB_IDENTITY=fail
  guard_bash "$(form_a receive)"
  assert_deny session_identity_unavailable

  export STUB_IDENTITY=sleep
  guard_bash "$(form_a receive)"
  assert_deny session_identity_unavailable

  export STUB_IDENTITY=garbage
  guard_bash "$(form_a receive)"
  assert_deny session_identity_invalid

  export STUB_IDENTITY=status
  guard_bash "$(form_a receive)"
  assert_deny session_identity_invalid
}

@test "pilot guard: the real identity helper denies without a live launcher" {
  cp "$BATS_TEST_DIRNAME/../scripts/session-identity.js" "$SCRIPTS/session-identity.js"
  guard_bash "$(form_a receive)"
  assert_deny session_identity_unavailable
}

@test "pilot guard: identity agent, type, and session mismatches are denied" {
  export STUB_IDENTITY=agent
  guard_bash "$(form_a receive)"
  assert_deny pilot_identity_mismatch

  export STUB_IDENTITY=type
  guard_bash "$(form_a receive)"
  assert_deny pilot_identity_mismatch

  export STUB_IDENTITY=ok
  export STUB_IDENTITY_SESSION="44444444-4444-4444-8444-444444444444"
  guard_bash "$(form_a receive)"
  assert_deny pilot_identity_mismatch
  unset STUB_IDENTITY_SESSION

  export AGMSG_PM_PILOT_SESSION_ID="44444444-4444-4444-8444-444444444444"
  export STUB_IDENTITY_SESSION="$AGMSG_PM_PILOT_SESSION_ID"
  guard_bash "$(form_a receive)"
  assert_deny pilot_identity_mismatch
}

# --- binding (stages 6-9) ----------------------------------------------------

@test "pilot guard: binding policy and provider mismatches are denied" {
  write_binding policyVersion=pm-pretool-v1
  guard_bash "$(form_a receive)"
  assert_deny binding_policy_mismatch

  write_binding providerCommit=0000000000000000000000000000000000000000
  guard_bash "$(form_a receive)"
  assert_deny binding_provider_mismatch
}

@test "pilot guard: guard path and digest mismatches are denied" {
  cp "$GUARD" "$TEST_SKILL_DIR/pm-pilot-pretool-guard"
  export AGMSG_PM_GUARD_PATH="$TEST_SKILL_DIR/pm-pilot-pretool-guard"
  guard_bash "$(form_a receive)"
  assert_deny guard_path_mismatch

  ln -s "$GUARD" "$TEST_SKILL_DIR/link/pm-pilot-pretool-guard" 2>/dev/null || {
    mkdir -p "$TEST_SKILL_DIR/link"
    ln -s "$GUARD" "$TEST_SKILL_DIR/link/pm-pilot-pretool-guard"
  }
  export AGMSG_PM_GUARD_PATH="$TEST_SKILL_DIR/link/pm-pilot-pretool-guard"
  guard_bash "$(form_a receive)"
  assert_deny guard_path_mismatch

  # One byte appended to a copy: the binding pins the digest of the original.
  export AGMSG_PM_GUARD_PATH="$GUARD"
  cp "$GUARD" "$TEST_SKILL_DIR/guard.orig"
  printf '\n' >> "$GUARD"
  guard_bash "$(form_a receive)"
  assert_deny guard_digest_mismatch
  cp "$TEST_SKILL_DIR/guard.orig" "$GUARD"
}

@test "pilot guard: profile digest mismatch is denied" {
  printf '\n' >> "$CANONICAL_PROJ/.claude/settings.local.json"
  guard_bash "$(form_a receive)"
  assert_deny profile_digest_mismatch
}

@test "pilot guard: broker digest, symlink, non-executable, and path mismatches are denied" {
  cp "$BROKER" "$TEST_SKILL_DIR/broker.orig"
  printf '\n# changed\n' >> "$BROKER"
  guard_bash "$(form_a receive)"
  assert_deny broker_digest_mismatch
  cp "$TEST_SKILL_DIR/broker.orig" "$BROKER"

  chmod -x "$BROKER"
  guard_bash "$(form_a receive)"
  assert_deny broker_path_mismatch
  chmod +x "$BROKER"

  mkdir -p "$TEST_SKILL_DIR/link"
  ln -s "$BROKER" "$TEST_SKILL_DIR/link/p2-consumer-broker.sh"
  export AGMSG_PM_BROKER_PATH="$TEST_SKILL_DIR/link/p2-consumer-broker.sh"
  guard_bash "$TEST_SKILL_DIR/link/p2-consumer-broker.sh --config $CONFIG receive < $REQUEST"
  assert_deny broker_path_mismatch

  # The command names a broker other than the exported one.
  export AGMSG_PM_BROKER_PATH="$BROKER"
  cp "$BROKER" "$TEST_SKILL_DIR/p2-consumer-broker.sh"
  guard_bash "$TEST_SKILL_DIR/p2-consumer-broker.sh --config $CONFIG receive < $REQUEST"
  assert_deny broker_path_mismatch

  # The exported path is another spelling of the same broker file.
  export AGMSG_PM_BROKER_PATH="$SCRIPTS/./p2-consumer-broker.sh"
  guard_bash "$(form_a receive)"
  assert_deny broker_path_mismatch

  # The exported and command paths agree but are not next to the guard.
  export AGMSG_PM_BROKER_PATH="$TEST_SKILL_DIR/p2-consumer-broker.sh"
  guard_bash "$TEST_SKILL_DIR/p2-consumer-broker.sh --config $CONFIG receive < $REQUEST"
  assert_deny broker_path_mismatch
}

# --- argument paths (stage 10) -----------------------------------------------

@test "pilot guard: argument path violations are denied" {
  ln -s "$CONFIG" "$CANONICAL_PROJ/.agmsg-gate/config-link.json"
  guard_bash "$BROKER --config $CANONICAL_PROJ/.agmsg-gate/config-link.json receive < $REQUEST"
  assert_deny argument_path_invalid

  ln -s "$REQUEST" "$CANONICAL_PROJ/.agmsg-gate/request-link.json"
  guard_bash "$BROKER --config $CONFIG receive < $CANONICAL_PROJ/.agmsg-gate/request-link.json"
  assert_deny argument_path_invalid

  printf '{}\n' > "$TEST_SKILL_DIR/outside.json"
  local outside
  outside="$(node -e 'process.stdout.write(require("fs").realpathSync.native(process.argv[1]))' "$TEST_SKILL_DIR/outside.json")"
  guard_bash "$BROKER --config $outside receive < $REQUEST"
  assert_deny argument_path_invalid
  guard_bash "$BROKER --config $CONFIG receive < $outside"
  assert_deny argument_path_invalid

  guard_bash "$BROKER --config $CONFIG --gh-config-dir $CONFIG issue-record < $REQUEST"
  assert_deny argument_path_invalid

  guard_bash "$BROKER --config $CANONICAL_PROJ/.agmsg-gate receive < $REQUEST"
  assert_deny argument_path_invalid

  guard_bash "$BROKER --config $CANONICAL_PROJ/missing.json receive < $REQUEST"
  assert_deny argument_path_invalid
}

# --- environment -------------------------------------------------------------

@test "pilot guard: each missing AGMSG_PM_* variable is denied" {
  local name reason saved
  for name in AGMSG_PM_PILOT_SESSION_ID AGMSG_PM_BINDING_FILE AGMSG_PM_GUARD_PATH AGMSG_PM_BROKER_PATH \
    AGMSG_PM_EXECUTIONS_FILE \
    AGMSG_PM_TEAM AGMSG_PM_AGENT AGMSG_PM_TYPE AGMSG_PM_PROCESS_PID AGMSG_PM_PROCESS_GENERATION \
    AGMSG_PM_PROCESS_START AGMSG_PM_TEAMS_DIR AGMSG_PM_CLAIM_FILE
  do
    saved="${!name}"
    reason="env_$(printf '%s' "${name#AGMSG_PM_}" | tr '[:upper:]' '[:lower:]')_invalid"
    unset "$name"
    guard_bash "$(form_a receive)"
    if [ "$name" = AGMSG_PM_BINDING_FILE ]; then
      # Without a binding the decision log cannot be located, so the deny
      # happens before any stage and is not written anywhere.
      assert_deny decision_log_location_invalid unrecorded || { printf 'name=%s\n' "$name"; return 1; }
    else
      assert_deny "$reason" || { printf 'name=%s\n' "$name"; return 1; }
    fi
    export "$name=$saved"
  done
}

@test "pilot guard: a control character in an environment value is denied" {
  export AGMSG_PM_TEAM=$'pilot\tteam'
  guard_bash "$(form_a receive)"
  assert_deny env_team_invalid
}

@test "pilot guard: missing or relative decisions file denies without a record" {
  unset AGMSG_PM_DECISIONS_FILE
  guard_bash "$(form_a receive)"
  assert_deny decision_log_unconfigured unrecorded

  export AGMSG_PM_DECISIONS_FILE="relative/decisions.jsonl"
  guard_bash "$(form_a receive)"
  assert_deny decision_log_unconfigured unrecorded
  [ ! -e relative/decisions.jsonl ]
}

@test "pilot guard: a failed allow record turns the decision into deny" {
  chmod 0444 "$AGMSG_PM_DECISIONS_FILE"
  guard_bash "$(form_a receive)"
  assert_deny decision_log_unavailable unrecorded
  [ ! -s "$AGMSG_PM_DECISIONS_FILE" ]
}

# --- run logs next to the binding (#404, inherited live PM environment) ----

@test "pilot guard: a decision log outside the binding directory denies without writing it" {
  mkdir -p "$TEST_SKILL_DIR/live-pm"
  : > "$TEST_SKILL_DIR/live-pm/decisions.jsonl"
  export AGMSG_PM_DECISIONS_FILE="$TEST_SKILL_DIR/live-pm/decisions.jsonl"
  guard_bash "$(form_a receive)"
  assert_deny decision_log_location_invalid unrecorded
  [ ! -s "$TEST_SKILL_DIR/live-pm/decisions.jsonl" ]
}

@test "pilot guard: a decision log symlink inside the binding directory is denied" {
  : > "$TEST_SKILL_DIR/target.jsonl"
  ln -s "$TEST_SKILL_DIR/target.jsonl" "$BINDINGS_DIR/1.link.jsonl"
  export AGMSG_PM_DECISIONS_FILE="$BINDINGS_DIR/1.link.jsonl"
  guard_bash "$(form_a receive)"
  assert_deny decision_log_location_invalid unrecorded
  [ ! -s "$TEST_SKILL_DIR/target.jsonl" ]
}

@test "pilot guard: a missing or non-normalized decision log is denied" {
  export AGMSG_PM_DECISIONS_FILE="$BINDINGS_DIR/2.decisions.jsonl"
  guard_bash "$(form_a receive)"
  assert_deny decision_log_location_invalid unrecorded
  [ ! -e "$BINDINGS_DIR/2.decisions.jsonl" ]

  export AGMSG_PM_DECISIONS_FILE="$BINDINGS_DIR/../bindings/1.decisions.jsonl"
  guard_bash "$(form_a receive)"
  assert_deny decision_log_location_invalid unrecorded

  # Both paths share the same non-normalized directory spelling, so only the
  # normalization check can tell them apart from the launcher's paths.
  export AGMSG_PM_BINDING_FILE="$BINDINGS_DIR//1.json"
  export AGMSG_PM_DECISIONS_FILE="$BINDINGS_DIR//1.decisions.jsonl"
  guard_bash "$(form_a receive)"
  assert_deny decision_log_location_invalid unrecorded
}

@test "pilot guard: an execution log outside the binding directory, missing, or symlinked is denied" {
  mkdir -p "$TEST_SKILL_DIR/live-pm"
  : > "$TEST_SKILL_DIR/live-pm/executions.jsonl"
  export AGMSG_PM_EXECUTIONS_FILE="$TEST_SKILL_DIR/live-pm/executions.jsonl"
  guard_bash "$(form_a receive)"
  assert_deny executions_log_location_invalid

  export AGMSG_PM_EXECUTIONS_FILE="$BINDINGS_DIR/2.executions.jsonl"
  guard_bash "$(form_a receive)"
  assert_deny executions_log_location_invalid

  ln -s "$TEST_SKILL_DIR/live-pm/executions.jsonl" "$BINDINGS_DIR/1.link-exec.jsonl"
  export AGMSG_PM_EXECUTIONS_FILE="$BINDINGS_DIR/1.link-exec.jsonl"
  guard_bash "$(form_a receive)"
  assert_deny executions_log_location_invalid
}

# --- input (stage 1) ---------------------------------------------------------

@test "pilot guard: invalid hook input is denied and recorded with unknown fields" {
  guard_raw 'not json'
  assert_deny hook_input_invalid
  [ "$(last_decision_field sessionId)" = unknown ]
  [ "$(last_decision_field toolUseId)" = unknown ]

  guard_raw '[]'
  assert_deny hook_input_invalid

  local field
  for field in session_id cwd tool_name tool_use_id; do
    guard_raw "$(hook_json Bash "$(form_a receive)" | node -e '
      const value = JSON.parse(require("fs").readFileSync(0, "utf8"));
      delete value[process.argv[1]];
      process.stdout.write(JSON.stringify(value));
    ' "$field")"
    assert_deny "${field}_invalid"
  done

  guard_raw "$(hook_json Bash "$(form_a receive)" | node -e '
    const value = JSON.parse(require("fs").readFileSync(0, "utf8"));
    value.tool_input = "x";
    process.stdout.write(JSON.stringify(value));
  ')"
  assert_deny tool_input_invalid

  guard_raw "$(hook_json Bash "$(form_a receive)" | node -e '
    const value = JSON.parse(require("fs").readFileSync(0, "utf8"));
    value.hook_event_name = "PostToolUse";
    process.stdout.write(JSON.stringify(value));
  ')"
  assert_deny hook_event_invalid
}

@test "pilot guard: hook input over 1 MiB is denied" {
  node -e 'process.stdout.write(JSON.stringify({pad: "x".repeat(1024 * 1024)}))' > "$TEST_SKILL_DIR/big.json"
  run --separate-stderr "$GUARD" < "$TEST_SKILL_DIR/big.json"
  assert_deny hook_input_too_large
}

# --- profile (§9) ------------------------------------------------------------

# Claude Code matcher semantics: omitted, empty, or "*" matches every tool;
# otherwise the matcher is an exact name or a regular expression.
profile_routes_tool_to_guard() {
  node - "$CANONICAL_PROJ/.claude/settings.local.json" "$GUARD" "$1" <<'NODE'
'use strict';
const fs = require('fs');
const [file, guard, tool] = process.argv.slice(2);
const profile = JSON.parse(fs.readFileSync(file, 'utf8'));
const groups = profile.hooks.PreToolUse;
const handlers = groups.flatMap((group) => group.hooks.map((hook) => ({group, hook})));
if (handlers.length !== 1) process.exit(10);
const {group, hook} = handlers[0];
if (hook.type !== 'command' || hook.command !== guard || hook.args !== undefined) process.exit(11);
if (!(hook.timeout >= 10)) process.exit(12);
const matcher = group.matcher;
const matches = matcher === undefined || matcher === '' || matcher === '*' ||
  new RegExp(`^(?:${matcher})$`).test(tool);
process.exit(matches ? 0 : 1);
NODE
}

@test "pilot profile: the single PreToolUse handler routes every tool to the guard" {
  local tool
  for tool in Bash Write Edit Read Monitor; do
    run profile_routes_tool_to_guard "$tool"
    [ "$status" -eq 0 ] || { printf 'tool=%s status=%s\n' "$tool" "$status"; return 1; }
    if [ "$tool" != Bash ]; then
      guard_raw "$(hook_json "$tool" '')"
      assert_deny tool_not_allowed
    fi
  done
}

@test "pilot profile: generator refuses a relative, symlinked, or non-executable guard" {
  run node "$SCRIPTS/lib/pilot-profile.js" --project "$CANONICAL_PROJ" --guard scripts/pm-pilot-pretool-guard
  [ "$status" -eq 1 ]

  mkdir -p "$TEST_SKILL_DIR/link"
  ln -s "$GUARD" "$TEST_SKILL_DIR/link/pm-pilot-pretool-guard"
  run node "$SCRIPTS/lib/pilot-profile.js" --project "$CANONICAL_PROJ" --guard "$TEST_SKILL_DIR/link/pm-pilot-pretool-guard"
  [ "$status" -eq 1 ]

  chmod -x "$GUARD"
  run node "$SCRIPTS/lib/pilot-profile.js" --project "$CANONICAL_PROJ" --guard "$GUARD"
  [ "$status" -eq 1 ]
  chmod +x "$GUARD"

  run node "$SCRIPTS/lib/pilot-profile.js" --project "$CANONICAL_PROJ"
  [ "$status" -eq 1 ]

  run node "$SCRIPTS/lib/pilot-profile.js" --project "$CANONICAL_PROJ" --guard "$GUARD" \
    --posttool "$SCRIPTS/pm-pretool-guard"
  [ "$status" -eq 1 ]
}

@test "pilot profile: --posttool adds exactly one PostToolUse handler and keeps PreToolUse unchanged" {
  local pre_only
  pre_only="$(cat "$CANONICAL_PROJ/.claude/settings.local.json")"
  node "$SCRIPTS/lib/pilot-profile.js" --project "$CANONICAL_PROJ" --guard "$GUARD" \
    --posttool "$SCRIPTS/pm-posttool-record" > /dev/null
  node - "$CANONICAL_PROJ/.claude/settings.local.json" "$GUARD" "$SCRIPTS/pm-posttool-record" "$pre_only" <<'NODE'
'use strict';
const fs = require('fs');
const [file, guard, posttool, preOnly] = process.argv.slice(2);
const profile = JSON.parse(fs.readFileSync(file, 'utf8'));
if (JSON.stringify(Object.keys(profile.hooks).sort()) !== '["PostToolUse","PreToolUse"]') process.exit(10);
if (JSON.stringify(profile.hooks.PreToolUse) !== JSON.stringify(JSON.parse(preOnly).hooks.PreToolUse)) process.exit(11);
const post = profile.hooks.PostToolUse.flatMap((group) => group.hooks);
if (post.length !== 1) process.exit(12);
if (post[0].type !== 'command' || post[0].command !== posttool || post[0].args !== undefined) process.exit(13);
if (guard === posttool) process.exit(14);
NODE
}

# --- non-sharing (§3) --------------------------------------------------------

@test "pilot guard: does not reference the current PM guard, PM broker, or session-identity.sh" {
  local source="$BATS_TEST_DIRNAME/../scripts/pm-pilot-pretool-guard"
  # Positive control: a word known to be present is found.
  grep -q 'session-identity.js' "$source"
  grep -q 'pm-pretool-guard' "$source" && return 1
  grep -q 'pm-broker' "$source" && return 1
  grep -q 'session-identity.sh' "$source" && return 1
  ! grep -q "BROKER_OPERATIONS\|parseLiteralCommand\|monitorCommand" "$source"
}
