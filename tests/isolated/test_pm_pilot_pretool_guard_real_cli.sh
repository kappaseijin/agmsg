#!/usr/bin/env bash
set -euo pipefail

# Real Claude Code CLI probe for the pilot-only PreToolUse guard (#404).
#
# Contract: docs/decisions/2026-09-11T133016_issue-404-pilot-pretool-guard-contract.md §11.2
#
# Deliberately outside the default Bats suite. It builds a temporary skill
# copy, project, roster, actas claim, binding, and pilot profile (generated
# by scripts/lib/pilot-profile.js), then runs the real CLI three times:
#
#   allowed  form A broker command runs with no permission prompt
#   probe    a plain Bash command is denied and its marker is not created
#   write    Write is denied because the profile matcher covers every tool
#
# The CLI runs in the default permission mode with no --allowedTools, so the
# only thing that can let the broker command run in -p mode is the guard's
# explicit allow. The broker is replaced by a stub in the temporary skill copy
# (the binding digests that stub) so the allowed case writes a marker instead
# of reaching a real queue. Run it from an authenticated verifier seat with:
#
#   bash tests/isolated/test_pm_pilot_pretool_guard_real_cli.sh

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
  printf '%s\n' 'node is required by the isolated pilot hook probe' >&2
  exit 2
}

HEAD="$(git -C "$ROOT_DIR" rev-parse HEAD)"
CLI_VERSION="$($CLAUDE_BIN --version 2>&1 | head -1)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/agmsg-pilot-cli.XXXXXX")"
TMP_ROOT="$(cd "$TMP_ROOT" && pwd -P)"
# Keep the verifier seat's HOME so the real CLI can use its authenticated
# keychain/session. User/project customization is isolated below with an
# explicit project-local settings source, strict MCP config, and no plugin dir.
cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

SKILL="$TMP_ROOT/skill"
PROJECT="$TMP_ROOT/project"
TEAM="isolated-pilot-cli-team"
AGENT="agmsg_pm_pilot_claude"
mkdir -p "$SKILL/scripts" "$SKILL/teams/$TEAM" "$SKILL/run" "$PROJECT/.agmsg-gate/i1-requests"
cp -R "$ROOT_DIR/scripts/." "$SKILL/scripts/"
chmod +x "$SKILL/scripts/"*.sh "$SKILL/scripts/"*.js "$SKILL/scripts/pm-pilot-pretool-guard" \
  "$SKILL/scripts/pm-posttool-record" 2>/dev/null || true

BROKER_MARKER="$TMP_ROOT/broker-executed"
cat > "$SKILL/scripts/p2-consumer-broker.sh" <<EOF
#!/usr/bin/env bash
cat > '$BROKER_MARKER'
printf '%s\n' '{"status":"ok","stub":true}'
EOF
chmod +x "$SKILL/scripts/p2-consumer-broker.sh"

export SKILL_DIR="$SKILL"
export AGMSG_STORAGE_PATH="$TMP_ROOT/db"
export AGMSG_STORAGE_DRIVER=sqlite
export AGMSG_AGENT_PID=''
mkdir -p "$AGMSG_STORAGE_PATH"
bash "$SKILL/scripts/internal/init-db.sh" >/dev/null

GUARD="$SKILL/scripts/pm-pilot-pretool-guard"
BROKER="$SKILL/scripts/p2-consumer-broker.sh"
CONFIG="$PROJECT/.agmsg-gate/i1-run-config.json"
REQUEST="$PROJECT/.agmsg-gate/i1-requests/r1.json"
printf '%s\n' '{"pilot":"config"}' > "$CONFIG"
printf '%s\n' '{"pilot":"request"}' > "$REQUEST"

node - "$SKILL/teams/$TEAM/config.json" "$PROJECT" "$TEAM" "$AGENT" <<'NODE'
const fs = require('fs');
const [file, project, team, agent] = process.argv.slice(2);
fs.writeFileSync(file, JSON.stringify({
  name: team,
  agents: {[agent]: {registrations: [{type: 'claude-code', project}]}},
}) + '\n');
NODE

node "$SKILL/scripts/lib/pilot-profile.js" --project "$PROJECT" --guard "$GUARD" \
  --posttool "$SKILL/scripts/pm-posttool-record" >/dev/null

cat > "$TMP_ROOT/launcher.sh" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
session="$1"; generation="$2"; project="$3"; skill="$4"; binding="$5"; team="$6"; agent="$7"; shift 7
pid="$$"
export SKILL_DIR="$skill"
export AGMSG_PM_PILOT_SESSION_ID="$session"
export AGMSG_PM_BINDING_FILE="$binding"
export AGMSG_PM_PROCESS_PID="$pid"
export AGMSG_PM_PROCESS_GENERATION="$generation"
export AGMSG_PM_TEAM="$team"
export AGMSG_PM_AGENT="$agent"
export AGMSG_PM_TYPE=claude-code
export AGMSG_PM_TEAMS_DIR="$skill/teams"
export AGMSG_PM_GUARD_PATH="$skill/scripts/pm-pilot-pretool-guard"
export AGMSG_PM_BROKER_PATH="$skill/scripts/p2-consumer-broker.sh"
# Like pilot-launcher.sh: both run logs sit next to the binding and exist as
# regular files before the first hook.
export AGMSG_PM_DECISIONS_FILE="${binding%.json}.decisions.jsonl"
export AGMSG_PM_EXECUTIONS_FILE="${binding%.json}.executions.jsonl"
( set -o noclobber; : > "$AGMSG_PM_DECISIONS_FILE"; : > "$AGMSG_PM_EXECUTIONS_FILE" )
pid_start="$(node "$skill/scripts/session-identity.js" --process-start "$pid")"
export AGMSG_PM_PROCESS_START="$pid_start"
. "$skill/scripts/lib/actas-lock.sh"
claim="$(actas_lock_path "$team" "$agent")"
export AGMSG_PM_CLAIM_FILE="$claim"
old_owner="$(actas_lock_owner "$team" "$agent" || true)"
if [ -n "$old_owner" ]; then
  actas_lock_release "$team" "$agent" "$old_owner" >/dev/null 2>&1 || true
fi
actas_lock_claim "$team" "$agent" "$session.$pid" >/dev/null
node - "$binding" "$project" "$team" "$agent" "$session" "$generation" "$pid" "$pid_start" \
  "$AGMSG_PM_GUARD_PATH" "$AGMSG_PM_BROKER_PATH" "$skill/scripts/lib/pilot-binding.js" <<'NODE'
const fs = require('fs');
const path = require('path');
const [file, project, team, agent, sessionId, generation, pid, pidStart, guard, broker, helper] = process.argv.slice(2);
const contract = require(helper);
const canonical = fs.realpathSync.native(project);
fs.writeFileSync(file, JSON.stringify({
  schemaVersion: 1, team, agent, type: 'claude-code',
  project: canonical, sessionId, generation, pid: String(pid), pidStart,
  guardDigest: contract.sha256File(guard),
  brokerDigest: contract.sha256File(broker),
  profileDigest: contract.sha256File(path.join(canonical, '.claude', 'settings.local.json')),
  policyVersion: contract.POLICY_VERSION,
  providerCommit: contract.PROVIDER_COMMIT,
}) + '\n');
NODE
exec "$@"
LAUNCHER
chmod +x "$TMP_ROOT/launcher.sh"

run_case() {
  local label="$1" session="$2" prompt="$3"
  local child_pid watchdog_pid rc
  set +e
  (cd "$PROJECT" && "$TMP_ROOT/launcher.sh" "$session" "$label-generation" "$PROJECT" "$SKILL" \
    "$TMP_ROOT/$label.json" "$TEAM" "$AGENT" "$CLAUDE_BIN" \
    --session-id "$session" --settings "$PROJECT/.claude/settings.local.json" \
    --setting-sources project,local --strict-mcp-config \
    --max-budget-usd 0.50 --no-chrome \
    --output-format json --no-session-persistence -p "$prompt" >"$TMP_ROOT/$label-output.json" 2>&1) &
  child_pid="$!"
  (
    sleep 90
    kill -TERM "$child_pid" 2>/dev/null || true
    sleep 5
    kill -KILL "$child_pid" 2>/dev/null || true
  ) &
  watchdog_pid="$!"
  wait "$child_pid"
  rc="$?"
  kill "$watchdog_pid" 2>/dev/null || true
  wait "$watchdog_pid" 2>/dev/null || true
  set -e
  printf '%s\n' "$rc" > "$TMP_ROOT/$label.rc"
}

dump_case() {
  local label="$1"
  printf '%s_rc=' "$label" >&2; cat "$TMP_ROOT/$label.rc" >&2 2>/dev/null || true
  printf '%s\n' "$label output:" >&2
  cat "$TMP_ROOT/$label-output.json" >&2 2>/dev/null || true
  printf '%s\n' "$label decisions:" >&2
  cat "$TMP_ROOT/$label.decisions.jsonl" >&2 2>/dev/null || true
}

# decision_count <label> <tool> <decision>
decision_count() {
  node - "$TMP_ROOT/$1.decisions.jsonl" "$2" "$3" <<'NODE'
const fs = require('fs');
const [file, tool, decision] = process.argv.slice(2);
const rows = fs.existsSync(file)
  ? fs.readFileSync(file, 'utf8').trim().split(/\r?\n/u).filter(Boolean).map(JSON.parse)
  : [];
process.stdout.write(String(rows.filter((row) => row.tool === tool && row.decision === decision).length));
NODE
}

allowed_command="$BROKER --config $CONFIG receive < $REQUEST"
run_case allowed "55555555-5555-4555-8555-555555555555" \
  "Use the Bash tool exactly once with this literal command, without a wrapper or any change: $allowed_command . Then reply DONE."
[ -e "$BROKER_MARKER" ] && [ "$(decision_count allowed Bash allow)" -ge 1 ] || {
  printf '%s\n' 'form A broker command did not run through an explicit guard allow' >&2
  dump_case allowed
  exit 1
}

probe_marker="$TMP_ROOT/probe-marker"
run_case probe "66666666-6666-4666-8666-666666666666" \
  "Use the Bash tool exactly once to run: printf pilot-probe > $probe_marker . Then reply DONE."
[ ! -e "$probe_marker" ] && [ "$(decision_count probe Bash deny)" -ge 1 ] || {
  printf '%s\n' 'probe Bash command was not denied by the pilot guard' >&2
  dump_case probe
  exit 1
}

write_marker="$TMP_ROOT/write-marker"
run_case write "77777777-7777-4777-8777-777777777777" \
  "Use the Write tool exactly once to create the file $write_marker with the content pilot-write. Do not use any other tool. Then reply DONE."
[ ! -e "$write_marker" ] && [ "$(decision_count write Write deny)" -ge 1 ] || {
  printf '%s\n' 'Write was not routed to the pilot guard and denied' >&2
  dump_case write
  exit 1
}

node - "$HEAD" "$CLI_VERSION" "$PROJECT/.claude/settings.local.json" \
  "$TMP_ROOT/allowed.decisions.jsonl" "$TMP_ROOT/probe.decisions.jsonl" "$TMP_ROOT/write.decisions.jsonl" \
  "$TMP_ROOT/allowed.rc" "$TMP_ROOT/probe.rc" "$TMP_ROOT/write.rc" "$BROKER_MARKER" <<'NODE'
const crypto = require('crypto');
const fs = require('fs');
const [head, cliVersion, profile, allowed, probe, write, allowedRc, probeRc, writeRc, brokerMarker] = process.argv.slice(2);
const read = (file) => (fs.existsSync(file) ? fs.readFileSync(file, 'utf8').trim() : '');
const rows = (file) => read(file).split(/\r?\n/u).filter(Boolean).map(JSON.parse);
const summary = (file) => rows(file).map((row) => ({tool: row.tool, decision: row.decision, reason: row.reason}));
process.stdout.write(JSON.stringify({
  value: {
    allowed: summary(allowed),
    probe: summary(probe),
    write: summary(write),
    probeMarker: 0,
    writeMarker: 0,
    brokerStdin: read(brokerMarker),
    caseExitCodes: {allowed: Number(read(allowedRc)), probe: Number(read(probeRc)), write: Number(read(writeRc))},
  },
  cutoff: `fixed-head:${head}`,
  source: 'tests/isolated/test_pm_pilot_pretool_guard_real_cli.sh',
  command: 'bash tests/isolated/test_pm_pilot_pretool_guard_real_cli.sh',
  cliVersion,
  profileDigest: `sha256:${crypto.createHash('sha256').update(fs.readFileSync(profile)).digest('hex')}`,
}) + '\n');
NODE
