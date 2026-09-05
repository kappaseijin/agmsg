#!/usr/bin/env bash
set -euo pipefail

# Real Claude Code CLI probe for Issue #236/#240.
#
# This file is deliberately outside the default Bats suite. It is an isolated
# acceptance probe: it creates a temporary project, roster, actas claim,
# binding, and settings file, then runs the real CLI twice. It never edits a
# real PM project or installs a hook. Run it from an authenticated verifier
# seat with:
#
#   bash tests/isolated/test_pm_pretool_guard_real_cli.sh

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd -P)"
CLAUDE_BIN="${CLAUDE_BIN:-}"
if [ -z "$CLAUDE_BIN" ]; then
  CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
fi
[ -x "$CLAUDE_BIN" ] || {
  printf '%s\n' 'real Claude CLI is unavailable; this isolated probe cannot be accepted' >&2
  exit 2
}
command -v node >/dev/null 2>&1 || {
  printf '%s\n' 'node is required by the isolated PM hook probe' >&2
  exit 2
}

START_MS="$(node -e 'process.stdout.write(String(Date.now()))')"
HEAD="$(git -C "$ROOT_DIR" rev-parse HEAD)"
CLI_VERSION="$($CLAUDE_BIN --version 2>&1 | head -1)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/agmsg-pm-cli.XXXXXX")"
cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

SKILL="$TMP_ROOT/skill"
PROJECT="$TMP_ROOT/project"
TEAM="isolated-cli-team"
AGENT="pm"
SESSION="22222222-2222-4222-8222-222222222222"
GENERATION="real-cli-generation"
mkdir -p "$SKILL/scripts" "$SKILL/teams/$TEAM" "$SKILL/run" "$PROJECT/.claude" "$TMP_ROOT/broker"
cp -R "$ROOT_DIR/scripts/." "$SKILL/scripts/"
chmod +x "$SKILL/scripts/"*.sh "$SKILL/scripts/"*.js "$SKILL/scripts/pm-pretool-guard" \
  "$SKILL/scripts/pm-posttool-record" 2>/dev/null || true

export SKILL_DIR="$SKILL"
export AGMSG_PM_BROKER_ROOT="$TMP_ROOT/broker"
export AGMSG_PM_BINDING_FILE="$TMP_ROOT/binding.json"
export AGMSG_PM_DECISIONS_FILE="$TMP_ROOT/decisions.jsonl"
export AGMSG_PM_EXECUTIONS_FILE="$TMP_ROOT/executions.jsonl"
export AGMSG_PM_AUDIT_FILE="$TMP_ROOT/audit.json"
export AGMSG_PM_HEARTBEAT_FILE="$TMP_ROOT/heartbeat.json"
export AGMSG_PM_GUARD_PATH="$SKILL/scripts/pm-pretool-guard"
export AGMSG_PM_BROKER_PATH="$SKILL/scripts/pm-broker.sh"
export AGMSG_PM_TEAMS_DIR="$SKILL/teams"
export AGMSG_PM_TEAM="$TEAM"
export AGMSG_PM_AGENT="$AGENT"
export AGMSG_PM_TYPE=claude-code

node - "$SKILL/teams/$TEAM/config.json" "$PROJECT" <<'NODE'
const fs = require('fs');
const [file, project] = process.argv.slice(2);
fs.writeFileSync(file, JSON.stringify({
  name: 'isolated-cli-team',
  agents: {pm: {registrations: [{type: 'claude-code', project}]}}
}) + '\n');
NODE

node - "$PROJECT/.claude/settings.local.json" "$AGMSG_PM_GUARD_PATH" "$SKILL/scripts/pm-posttool-record" <<'NODE'
const fs = require('fs');
const [file, guard, post] = process.argv.slice(2);
fs.writeFileSync(file, JSON.stringify({
  hooks: {
    PreToolUse: [{matcher: '*', hooks: [{type: 'command', command: guard}]}],
    PostToolUse: [{matcher: '*', hooks: [{type: 'command', command: post}]}]
  }
}) + '\n');
NODE

cat > "$TMP_ROOT/launcher.sh" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
case "$#" in
  [7-9]|[1-9][0-9]*) ;;
  *) echo 'launcher: expected session generation project skill binding claim command...' >&2; exit 2 ;;
esac
session="$1"; generation="$2"; project="$3"; skill="$4"; binding="$5"; claim="$6"; shift 6
pid="$$"
export SKILL_DIR="$skill"
export AGMSG_PM_BINDING_FILE="$binding"
export AGMSG_PM_PROCESS_PID="$pid"
export AGMSG_PM_PROCESS_GENERATION="$generation"
export AGMSG_PM_PROCESS_START="$generation"
export AGMSG_PM_TEAM=isolated-cli-team
export AGMSG_PM_AGENT=pm
export AGMSG_PM_TYPE=claude-code
export AGMSG_PM_TEAMS_DIR="$skill/teams"
export AGMSG_PM_BROKER_PATH="$skill/scripts/pm-broker.sh"
export AGMSG_PM_BROKER_ROOT="${AGMSG_PM_BROKER_ROOT:?}"
export AGMSG_PM_DECISIONS_FILE="${AGMSG_PM_DECISIONS_FILE:?}"
export AGMSG_PM_EXECUTIONS_FILE="${AGMSG_PM_EXECUTIONS_FILE:?}"
export AGMSG_PM_AUDIT_FILE="${AGMSG_PM_AUDIT_FILE:?}"
export AGMSG_PM_HEARTBEAT_FILE="${AGMSG_PM_HEARTBEAT_FILE:?}"
mkdir -p "$skill/run"
printf '%s\n' "$session.$pid" > "$claim"
node - "$binding" "$project" "$session" "$generation" "$pid" <<'NODE'
const fs = require('fs');
const [file, project, sessionId, generation, pid] = process.argv.slice(2);
fs.writeFileSync(file, JSON.stringify({
  schemaVersion: 1, team: 'isolated-cli-team', agent: 'pm', type: 'claude-code',
  project: fs.realpathSync.native(project), sessionId, generation, pid: String(pid),
  pidStart: generation, profileDigest: 'sha256:isolated-cli-profile',
  policyVersion: 'pm-pretool-v1'
}) + '\n');
NODE
exec "$@"
LAUNCHER
chmod +x "$TMP_ROOT/launcher.sh"

payload="$(node <<'NODE'
process.stdout.write(Buffer.from(JSON.stringify({operation: 'agmsg_send', recipient: 'pm'})).toString('base64url'));
NODE
)"
direct_marker="$TMP_ROOT/direct-marker"
allowed_command="$AGMSG_PM_BROKER_PATH agmsg_send $payload"

run_case() {
  local label="$1" session="$2" generation="$3" prompt="$4" output="$5" claim="$6"
  local binding="$TMP_ROOT/$label-binding.json"
  set +e
  (cd "$PROJECT" && "$TMP_ROOT/launcher.sh" "$session" "$generation" "$PROJECT" "$SKILL" \
    "$binding" "$claim" "$CLAUDE_BIN" \
    --session-id "$session" --settings "$PROJECT/.claude/settings.local.json" \
    --permission-mode bypassPermissions --allowedTools Bash --output-format json \
    --no-session-persistence -p "$prompt" >"$output" 2>&1)
  local rc="$?"
  set -e
  printf '%s\n' "$rc" > "$TMP_ROOT/$label.rc"
}

run_case direct "$SESSION" "$GENERATION" \
  "Use the Bash tool exactly once to run: printf cli-direct > $direct_marker . Then reply DONE." \
  "$TMP_ROOT/direct-output.json" "$SKILL/run/actas.${TEAM}__${AGENT}.session"

direct_marker_count=0
[ -e "$direct_marker" ] && direct_marker_count=1
[ "$direct_marker_count" -eq 0 ] || {
  printf '%s\n' 'direct Bash marker was executed; PreToolUse denial did not hold' >&2
  exit 1
}

run_case allowed "33333333-3333-4333-8333-333333333333" allowed-generation \
  "Use the Bash tool exactly once with this literal command, without a wrapper or shell syntax: $allowed_command . Then reply DONE." \
  "$TMP_ROOT/allowed-output.json" "$SKILL/run/actas.${TEAM}__${AGENT}.session"

[ -e "$TMP_ROOT/broker/executed" ] || {
  printf '%s\n' 'fixed broker marker was not executed by the real CLI' >&2
  exit 1
}

export AGMSG_PM_BINDING_FILE="$TMP_ROOT/allowed-binding.json"
"$SKILL/scripts/pm-audit.sh" --once >/dev/null 2>&1 || {
  printf '%s\n' 'independent audit did not report a healthy real CLI run' >&2
  cat "$TMP_ROOT/audit.json" >&2 2>/dev/null || true
  exit 1
}

# Deliberately remove the outer hook. The design accepts that Claude Code can
# run a harmless tool when the command hook is absent; the independent audit
# must expose that hole instead of manufacturing a marker=0 result.
outer_marker="$TMP_ROOT/outer-marker"
rm -f "$AGMSG_PM_GUARD_PATH"
run_case outer "44444444-4444-4444-8444-444444444444" outer-generation \
  "Use the Bash tool exactly once to run: printf cli-outer > $outer_marker . Then reply DONE." \
  "$TMP_ROOT/outer-output.json" "$SKILL/run/actas.${TEAM}__${AGENT}.session"
[ -e "$outer_marker" ] || {
  printf '%s\n' 'outer hook failure did not produce the expected known hole' >&2
  exit 1
}
export AGMSG_PM_BINDING_FILE="$TMP_ROOT/outer-binding.json"
set +e
"$SKILL/scripts/pm-audit.sh" --once > "$TMP_ROOT/outer-audit-output.json" 2>&1
outer_audit_status="$?"
set -e
[ "$outer_audit_status" -eq 1 ] || {
  printf '%s\n' 'independent audit did not detect the missing outer hook' >&2
  cat "$TMP_ROOT/outer-audit-output.json" >&2
  exit 1
}
grep -q 'guard_unavailable' "$TMP_ROOT/audit.json"
grep -q 'execution_without_decision' "$TMP_ROOT/audit.json"

node - "$TMP_ROOT/decisions.jsonl" "$TMP_ROOT/executions.jsonl" <<'NODE'
const fs = require('fs');
const [decisionsFile, executionsFile] = process.argv.slice(2);
const lines = (file) => fs.readFileSync(file, 'utf8').trim().split(/\r?\n/u).filter(Boolean).map(JSON.parse);
const decisions = lines(decisionsFile);
const executions = lines(executionsFile);
if (!decisions.some((value) => value.tool === 'Bash' && value.decision === 'deny')) {
  throw new Error('missing real PreToolUse deny decision');
}
if (!decisions.some((value) => value.tool === 'Bash' && value.decision === 'allow')) {
  throw new Error('missing real PreToolUse allow decision');
}
if (!executions.some((value) => value.tool === 'Bash')) {
  throw new Error('missing real PostToolUse execution record');
}
NODE

END_MS="$(node -e 'process.stdout.write(String(Date.now()))')"
elapsed_ms=$((END_MS - START_MS))
profile_digest="$(node - "$PROJECT/.claude/settings.local.json" <<'NODE'
const crypto = require('crypto');
const fs = require('fs');
process.stdout.write('sha256:' + crypto.createHash('sha256').update(fs.readFileSync(process.argv[2])).digest('hex'));
NODE
)"
node - "$TMP_ROOT/decisions.jsonl" "$TMP_ROOT/executions.jsonl" "$TMP_ROOT/broker/requests.jsonl" \
  "$TMP_ROOT/audit.json" "$HEAD" "$CLI_VERSION" "$profile_digest" "$elapsed_ms" <<'NODE'
const fs = require('fs');
const [decisionsFile, executionsFile, requestsFile, auditFile, head, cliVersion, profileDigest, elapsedMs] = process.argv.slice(2);
const read = (file) => fs.existsSync(file) ? fs.readFileSync(file, 'utf8').trim() : '';
const rows = (file) => read(file).split(/\r?\n/u).filter(Boolean).map(JSON.parse);
const decisions = rows(decisionsFile);
const executions = rows(executionsFile);
const requests = rows(requestsFile);
const audit = JSON.parse(read(auditFile));
const packet = {
  value: {
    directMarker: 0,
    brokerMarker: requests.length,
    outerMarker: 1,
    denyDecisions: decisions.filter((value) => value.decision === 'deny').length,
    allowDecisions: decisions.filter((value) => value.decision === 'allow').length,
    executionRecords: executions.length,
    auditStatus: 'healthy_before_outer_failure',
    outerAuditStatus: audit.status,
    outerAuditAlerts: audit.alerts,
  },
  cutoff: `fixed-head:${head}`,
  source: 'tests/isolated/test_pm_pretool_guard_real_cli.sh',
  command: 'bash tests/isolated/test_pm_pretool_guard_real_cli.sh',
  cliVersion,
  profileDigest,
  elapsedMs: Number(elapsedMs),
  toolIds: [...new Set([...decisions, ...executions].map((value) => value.toolUseId).filter(Boolean))],
  rawMarkerOutput: read(requestsFile),
};
process.stdout.write(JSON.stringify(packet) + '\n');
NODE
