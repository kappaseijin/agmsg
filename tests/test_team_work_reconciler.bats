#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
  bash "$SCRIPTS/join.sh" demo owner codex /tmp/demo-owner --role programmer --kind seat >/dev/null
  bash "$SCRIPTS/join.sh" demo dispatch codex /tmp/demo-dispatch --role manager --kind seat >/dev/null

  export AUDIT_FIXTURES="$BATS_TEST_DIRNAME/fixtures/team-work-audit"
  export FAKE_GH_BIN="$BATS_TEST_TMPDIR/fake-gh-bin"
  export TEAM_WORK_GH_LOG="$BATS_TEST_TMPDIR/gh-requests.jsonl"
  export FAKE_DELIVERY_BIN="$BATS_TEST_TMPDIR/fake-delivery.sh"
  mkdir -p "$FAKE_GH_BIN"
  cp "$AUDIT_FIXTURES/gh" "$FAKE_GH_BIN/gh"
  chmod +x "$FAKE_GH_BIN/gh"
  : > "$TEAM_WORK_GH_LOG"

  cat > "$FAKE_DELIVERY_BIN" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

[ "$1" = "status" ]
type="$2"
project="$3"
case "${TEAM_WORK_FAKE_DELIVERY:-false}" in
  true)
    runtime="alive"; liveness="alive"; deliverable="true" ;;
  false)
    runtime="missing"; liveness="missing"; deliverable="false" ;;
  *)
    runtime="unknown"; liveness="unknown"; deliverable='"unknown"' ;;
esac
printf '{"schemaVersion":1,"type":"%s","project":"%s","runtime":"%s","sessionId":"session-owner","deliverable":%s,"liveness":"%s","receipt":{"state":"none","queued":0,"claimed":0,"handedOff":0,"unknown":0},"evidence":[],"seats":[{"team":"demo","name":"owner","runtime":"%s","sessionId":"session-owner","deliverable":%s,"liveness":"%s","receipt":{"state":"none","queued":0,"claimed":0,"handedOff":0,"unknown":0},"evidence":[]}]}' "$type" "$project" "$runtime" "$deliverable" "$liveness" "$runtime" "$deliverable" "$liveness"
SH
  chmod +x "$FAKE_DELIVERY_BIN"
}

teardown() {
  teardown_test_env
}

write_pack() {
  local writeback_required="${2:-false}"
  printf '%s\n' "{\"schemaVersion\":1,\"team\":\"demo\",\"workItems\":[{\"schemaVersion\":1,\"workItem\":{\"id\":\"issue:42\",\"source\":{\"kind\":\"issue\",\"repository\":\"kappaseijin/agmsg\",\"number\":42}},\"ownerSeat\":\"owner\",\"workKinds\":[\"implementation\"],\"relations\":[],\"revision\":1,\"classificationBasis\":{\"contentDigest\":\"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\",\"refs\":[{\"kind\":\"issue\",\"repository\":\"kappaseijin/agmsg\",\"number\":42}]},\"writebackRequired\":$writeback_required}]}" > "$1"
}

json_value() {
  JSON_INPUT="$1" JSON_SELECTOR="$2" node -e '
let value = JSON.parse(process.env.JSON_INPUT);
for (const part of process.env.JSON_SELECTOR.split(".")) {
  if (part.length === 0) continue;
  const match = /^(.+)\[([0-9]+\])$/.exec(part);
  value = match ? value[match[1]][Number(match[2])] : value[part];
}
process.stdout.write(typeof value === "string" ? value : JSON.stringify(value));
'
}

sha256_file() {
  FILE_PATH="$1" node -e 'const crypto = require("crypto"); const fs = require("fs"); process.stdout.write(crypto.createHash("sha256").update(fs.readFileSync(process.env.FILE_PATH)).digest("hex"));'
}

assert_finding() {
  JSON_INPUT="$1" EXPECTED="$2" node -e '
const value = JSON.parse(process.env.JSON_INPUT);
if (!value.findings.some((finding) => finding.code === process.env.EXPECTED)) process.exit(1);
'
}

assert_no_finding() {
  JSON_INPUT="$1" UNEXPECTED="$2" node -e '
const value = JSON.parse(process.env.JSON_INPUT);
if (value.findings.some((finding) => finding.code === process.env.UNEXPECTED)) process.exit(1);
'
}

assert_canonical_json() {
  JSON_INPUT="$1" node -e '
const input = process.env.JSON_INPUT;
const value = JSON.parse(input);
function sort(item) {
  if (Array.isArray(item)) return item.map(sort);
  if (item && typeof item === "object") {
    const result = {};
    for (const key of Object.keys(item).sort()) result[key] = sort(item[key]);
    return result;
  }
  return item;
}
if (input !== JSON.stringify(sort(value))) process.exit(1);
'
}

run_reconciler() {
  local fixture="$1" command="$2" pack="$3"
  shift 3
  local -a environment=()
  while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
    environment+=("$1")
    shift
  done
  if [ "${1:-}" = "--" ]; then
    shift
  fi
  run env PATH="$FAKE_GH_BIN:$PATH" TEAM_WORK_GH_FIXTURE="$fixture" TEAM_WORK_GH_LOG="$TEAM_WORK_GH_LOG" \
    AGMSG_TEAM_WORK_DELIVERY_BIN="$FAKE_DELIVERY_BIN" "${environment[@]}" \
    bash "$SCRIPTS/team-work.sh" "$command" demo "$pack" "$@"
}

@test "team-work reconciler: dispatch ledger has immutable history" {
  local database="$TEST_SKILL_DIR/db/messages.db"

  run sqlite3 "$database" "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'team_work_dispatch_current';"
  [ "$status" -eq 0 ]
  [ "$output" = "team_work_dispatch_current" ]

  run sqlite3 "$database" "
INSERT INTO team_work_dispatch_current(
  team, work_item_id, contract_digest, envelope_digest, owner_seat, state,
  lease_epoch, lease_expires_at, queue_digest, delivery_evidence_json,
  ack_evidence, last_action, last_actor, created_at, updated_at
) VALUES (
  'demo', 'issue:43', 'sha256:contract', 'sha256:envelope', 'owner', 'dispatching',
  'epoch-1', 200, 'sha256:queue', '{}', NULL, 'dispatch', 'manager', 100, 100
);
"
  [ "$status" -eq 0 ]

  run sqlite3 "$database" "SELECT revision || ':' || state FROM team_work_dispatch_revisions WHERE team = 'demo' AND work_item_id = 'issue:43';"
  [ "$status" -eq 0 ]
  [ "$output" = "1:dispatching" ]

  run sqlite3 "$database" "UPDATE team_work_dispatch_revisions SET state = 'claimed' WHERE team = 'demo' AND work_item_id = 'issue:43' AND revision = 1;"
  [ "$status" -ne 0 ]
  [[ "$output" == *"append-only"* ]]
}

@test "team-work reconcile: detects an expired G2 lease" {
  local pack="$BATS_TEST_TMPDIR/expired.json"
  local digest_values contract_digest envelope_digest
  write_pack "$pack"
  digest_values="$(PACK_PATH="$pack" TEAM_WORK_MODULE="$SCRIPTS/lib/team-work.js" node -e '
const fs = require("fs");
const work = require(process.env.TEAM_WORK_MODULE);
const pack = JSON.parse(fs.readFileSync(process.env.PACK_PATH, "utf8"));
process.stdout.write(`${work.sha256Digest(pack)}\t${work.envelopeDigest(pack.workItems[0])}`);
')"
  IFS=$'\t' read -r contract_digest envelope_digest <<< "$digest_values"
  sqlite3 "$TEST_SKILL_DIR/db/messages.db" "
INSERT INTO team_work_current(
  team, work_item_id, contract_digest, envelope_digest, owner_seat,
  source_repository, source_number, revision, state, lease_owner,
  lease_expires_at, ack_evidence, pr_links_json, writebacks_json,
  last_action, last_actor, created_at, updated_at
) VALUES (
  'demo', 'issue:42', '$contract_digest', '$envelope_digest', 'owner',
  'kappaseijin/agmsg', 42, 1, 'claimed', 'owner',
  100, NULL, '[]', '[]', 'claim', 'owner', 100, 100
);
"

  run_reconciler "$AUDIT_FIXTURES/open.json" reconcile "$pack" TEAM_WORK_NOW=101 TEAM_WORK_FAKE_DELIVERY=false

  [ "$status" -eq 0 ]
  assert_finding "$output" expired_lease
}

@test "team-work reconcile: detects an active lease after upstream close" {
  local pack="$BATS_TEST_TMPDIR/upstream-closed.json"
  write_pack "$pack"
  run env TEAM_WORK_NOW=100 bash "$SCRIPTS/team-work.sh" claim demo "$pack" issue:42 owner 3600
  [ "$status" -eq 0 ]

  run_reconciler "$AUDIT_FIXTURES/closed.json" reconcile "$pack" TEAM_WORK_NOW=101 TEAM_WORK_FAKE_DELIVERY=true

  [ "$status" -eq 0 ]
  assert_finding "$output" upstream_closed
}

@test "team-work reconcile: detects ready work without a live owner seat" {
  local pack="$BATS_TEST_TMPDIR/orphan.json"
  write_pack "$pack"

  run_reconciler "$AUDIT_FIXTURES/open.json" reconcile "$pack" TEAM_WORK_NOW=101 TEAM_WORK_FAKE_DELIVERY=false

  [ "$status" -eq 0 ]
  assert_finding "$output" orphan_ready
}

@test "team-work reconcile: blocked work is not reported as orphan ready" {
  local pack="$BATS_TEST_TMPDIR/reconcile-blocked.json"
  write_pack "$pack"
  run env TEAM_WORK_NOW=4102444800 bash "$SCRIPTS/team-work.sh" claim demo "$pack" issue:42 owner 0
  [ "$status" -eq 0 ]
  run env TEAM_WORK_NOW=4102444800 bash "$SCRIPTS/team-work.sh" set-state demo "$pack" issue:42 dispatch blocked
  [ "$status" -eq 0 ]

  run_reconciler "$AUDIT_FIXTURES/open.json" reconcile "$pack" TEAM_WORK_NOW=4102444800 TEAM_WORK_FAKE_DELIVERY=false

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" auditStatus)" = "unknown" ]
  assert_no_finding "$output" orphan_ready
}

@test "team-work reconcile: detects required writeback without local evidence" {
  local pack="$BATS_TEST_TMPDIR/writeback.json"
  write_pack "$pack" true

  run_reconciler "$AUDIT_FIXTURES/open.json" reconcile "$pack" TEAM_WORK_NOW=101 TEAM_WORK_FAKE_DELIVERY=true

  [ "$status" -eq 0 ]
  assert_finding "$output" writeback_required
}

@test "team-work reconcile: carries forward G2 stale state as remediation" {
  local pack="$BATS_TEST_TMPDIR/stale.json"
  write_pack "$pack"
  run env TEAM_WORK_NOW=100 bash "$SCRIPTS/team-work.sh" claim demo "$pack" issue:42 owner 3600
  [ "$status" -eq 0 ]
  write_pack "$pack" true

  run_reconciler "$AUDIT_FIXTURES/open.json" reconcile "$pack" TEAM_WORK_NOW=101 TEAM_WORK_FAKE_DELIVERY=true

  [ "$status" -eq 0 ]
  assert_finding "$output" stale_state
}

@test "team-work reconcile: an uninitialized store reports healthy, not stale_state" {
  local pack="$BATS_TEST_TMPDIR/uninitialized.json"
  write_pack "$pack"
  rm -f "$DBPATH"

  run_reconciler "$AUDIT_FIXTURES/open.json" reconcile "$pack" TEAM_WORK_NOW=101 TEAM_WORK_FAKE_DELIVERY=true

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" result)" = "healthy" ]
  [ "$(json_value "$output" findings)" = "[]" ]
}

@test "team-work reconcile: emits canonical JSON and does not mutate the local store" {
  local pack="$BATS_TEST_TMPDIR/read-only.json"
  local database="$TEST_SKILL_DIR/db/messages.db"
  local before
  write_pack "$pack"
  before="$(sha256_file "$database")"

  run_reconciler "$AUDIT_FIXTURES/open.json" reconcile "$pack" TEAM_WORK_NOW=101 TEAM_WORK_FAKE_DELIVERY=true

  [ "$status" -eq 0 ]
  assert_canonical_json "$output"
  [[ "$(json_value "$output" reconcileDigest)" =~ ^sha256:[0-9a-f]{64}$ ]]
  [ "$before" = "$(sha256_file "$database")" ]
}

@test "team-work watchdog: reports a stale heartbeat without local mutation" {
  local pack="$BATS_TEST_TMPDIR/watchdog.json"
  local heartbeat="$BATS_TEST_TMPDIR/reconciler-heartbeat.json"
  local database="$TEST_SKILL_DIR/db/messages.db"
  local before
  write_pack "$pack"
  printf '%s\n' '{"schemaVersion":1,"team":"demo","cycleId":"cycle-1","startedAt":90,"finishedAt":100,"result":"quiescent","sourceDigest":"sha256:source"}' > "$heartbeat"
  before="$(sha256_file "$database")"

  run env TEAM_WORK_NOW=200 bash "$SCRIPTS/team-work.sh" watchdog demo "$pack" "$heartbeat" 50

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" status)" = "stale" ]
  [ "$before" = "$(sha256_file "$database")" ]
}

@test "team-work watchdog: treats a fresh quiescent heartbeat as healthy" {
  local pack="$BATS_TEST_TMPDIR/watchdog-healthy.json"
  local heartbeat="$BATS_TEST_TMPDIR/reconciler-heartbeat.json"
  write_pack "$pack"
  printf '%s\n' '{"schemaVersion":1,"team":"demo","cycleId":"cycle-2","startedAt":100,"finishedAt":100,"result":"quiescent","sourceDigest":"sha256:source"}' > "$heartbeat"

  run env TEAM_WORK_NOW=101 bash "$SCRIPTS/team-work.sh" watchdog demo "$pack" "$heartbeat" 50

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" status)" = "healthy" ]
  [ "$(json_value "$output" alarm)" = "false" ]
}

@test "team-work reconcile: writes only the requested heartbeat artifact" {
  local pack="$BATS_TEST_TMPDIR/reconcile-heartbeat.json"
  local heartbeat="$BATS_TEST_TMPDIR/reconcile-heartbeat.out"
  local database="$TEST_SKILL_DIR/db/messages.db"
  local before
  write_pack "$pack"
  before="$(sha256_file "$database")"

  run_reconciler "$AUDIT_FIXTURES/open.json" reconcile "$pack" TEAM_WORK_NOW=101 TEAM_WORK_FAKE_DELIVERY=true -- "$heartbeat"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" heartbeatPath)" = "$heartbeat" ]
  [ -f "$heartbeat" ]
  [ "$(json_value "$(cat "$heartbeat")" team)" = "demo" ]
  [ "$before" = "$(sha256_file "$database")" ]
}

@test "team-work G3 wrapper: rejects incomplete command arguments" {
  local pack="$BATS_TEST_TMPDIR/usage.json"
  write_pack "$pack"

  run bash "$SCRIPTS/team-work.sh" reconcile demo "$pack" first second
  [ "$status" -ne 0 ]
  [[ "$output" == *"Usage: team-work.sh reconcile"* ]]

  run bash "$SCRIPTS/team-work.sh" watchdog demo "$pack"
  [ "$status" -ne 0 ]
  [[ "$output" == *"Usage: team-work.sh watchdog"* ]]

  run bash "$SCRIPTS/team-work.sh" dispatch demo "$pack" issue:42
  [ "$status" -ne 0 ]
  [[ "$output" == *"Usage: team-work.sh dispatch"* ]]

  run bash "$SCRIPTS/team-work.sh" dispatch-ack demo "$pack" issue:42 owner
  [ "$status" -ne 0 ]
  [[ "$output" == *"Usage: team-work.sh dispatch-ack"* ]]
}

@test "team-work dispatch: denies a ready owner that is not allowlisted or live" {
  local pack="$BATS_TEST_TMPDIR/dispatch-denied.json"
  local database="$TEST_SKILL_DIR/db/messages.db"
  local before
  write_pack "$pack"
  before="$(sha256_file "$database")"

  run_reconciler "$AUDIT_FIXTURES/open.json" dispatch "$pack" TEAM_WORK_NOW=101 TEAM_WORK_FAKE_DELIVERY=false -- issue:42 dispatch

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" dispatched)" = "false" ]
  [ "$(json_value "$output" state)" = "not_dispatchable" ]
  [ "$before" = "$(sha256_file "$database")" ]
  [ "$(sqlite3 "$database" "SELECT count(*) FROM team_work_dispatch_current;")" = "0" ]
}

@test "team-work dispatch: blocked work does not mutate the dispatch ledger" {
  local pack="$BATS_TEST_TMPDIR/dispatch-blocked.json"
  local database="$TEST_SKILL_DIR/db/messages.db"
  write_pack "$pack"
  run env TEAM_WORK_NOW=4102444800 bash "$SCRIPTS/team-work.sh" claim demo "$pack" issue:42 owner 0
  [ "$status" -eq 0 ]
  run env TEAM_WORK_NOW=4102444800 bash "$SCRIPTS/team-work.sh" set-state demo "$pack" issue:42 dispatch blocked
  [ "$status" -eq 0 ]

  run_reconciler "$AUDIT_FIXTURES/open.json" dispatch "$pack" TEAM_WORK_NOW=4102444800 TEAM_WORK_FAKE_DELIVERY=true TEAM_WORK_DISPATCH_ALLOWLIST='["owner"]' -- issue:42 dispatch 120

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" dispatched)" = "false" ]
  [ "$(json_value "$output" state)" = "not_dispatchable" ]
  [ "$(sqlite3 "$database" "SELECT count(*) FROM team_work_dispatch_current;")" = "0" ]
  [ "$(sqlite3 "$database" "SELECT count(*) FROM team_work_dispatch_revisions;")" = "0" ]
}

@test "team-work dispatch: records dispatching without claiming before ACK" {
  local pack="$BATS_TEST_TMPDIR/dispatch.json"
  local database="$TEST_SKILL_DIR/db/messages.db"
  local lease_epoch
  write_pack "$pack"

  run_reconciler "$AUDIT_FIXTURES/open.json" dispatch "$pack" TEAM_WORK_NOW=101 TEAM_WORK_FAKE_DELIVERY=true TEAM_WORK_DISPATCH_ALLOWLIST='["owner"]' -- issue:42 dispatch 120

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" dispatched)" = "true" ]
  [ "$(json_value "$output" state)" = "dispatching" ]
  [ "$(json_value "$output" sendInvoked)" = "false" ]
  lease_epoch="$(json_value "$output" leaseEpoch)"
  [[ "$lease_epoch" =~ ^[0-9a-f-]{36}$ ]]
  [ "$(sqlite3 "$database" "SELECT state FROM team_work_dispatch_current WHERE team = 'demo' AND work_item_id = 'issue:42';")" = "dispatching" ]
  [ "$(sqlite3 "$database" "SELECT count(*) FROM team_work_current WHERE team = 'demo' AND work_item_id = 'issue:42';")" = "0" ]

  run_reconciler "$AUDIT_FIXTURES/open.json" dispatch-ack "$pack" TEAM_WORK_NOW=102 TEAM_WORK_FAKE_DELIVERY=true TEAM_WORK_DISPATCH_ALLOWLIST='["owner"]' -- issue:42 owner "$lease_epoch" received

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" acknowledged)" = "true" ]
  [ "$(json_value "$output" state)" = "claimed" ]
  [ "$(sqlite3 "$database" "SELECT state FROM team_work_dispatch_current WHERE team = 'demo' AND work_item_id = 'issue:42';")" = "claimed" ]
  [ "$(sqlite3 "$database" "SELECT state FROM team_work_current WHERE team = 'demo' AND work_item_id = 'issue:42';")" = "claimed" ]
  [ "$(sqlite3 "$database" "SELECT count(*) FROM team_work_dispatch_revisions WHERE team = 'demo' AND work_item_id = 'issue:42';")" = "2" ]
}

@test "team-work dispatch: refuses an owner whose delivery capability is unknown" {
  local pack="$BATS_TEST_TMPDIR/dispatch-unknown.json"
  local database="$TEST_SKILL_DIR/db/messages.db"
  local before
  write_pack "$pack"
  before="$(sha256_file "$database")"

  run_reconciler "$AUDIT_FIXTURES/open.json" dispatch "$pack" TEAM_WORK_NOW=101 TEAM_WORK_FAKE_DELIVERY=unknown TEAM_WORK_DISPATCH_ALLOWLIST='["owner"]' -- issue:42 dispatch

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" dispatched)" = "false" ]
  [ "$(json_value "$output" state)" = "not_dispatchable" ]
  [ "$(json_value "$output" delivery.status)" = "unknown" ]
  [ "$before" = "$(sha256_file "$database")" ]
}

@test "team-work dispatch ACK: rejects a wrong or expired epoch without claiming" {
  local pack="$BATS_TEST_TMPDIR/dispatch-ack-reject.json"
  local database="$TEST_SKILL_DIR/db/messages.db"
  local lease_epoch before
  write_pack "$pack"

  run_reconciler "$AUDIT_FIXTURES/open.json" dispatch "$pack" TEAM_WORK_NOW=101 TEAM_WORK_FAKE_DELIVERY=true TEAM_WORK_DISPATCH_ALLOWLIST='["owner"]' -- issue:42 dispatch 1
  [ "$status" -eq 0 ]
  lease_epoch="$(json_value "$output" leaseEpoch)"
  before="$(sha256_file "$database")"

  run_reconciler "$AUDIT_FIXTURES/open.json" dispatch-ack "$pack" TEAM_WORK_NOW=101 TEAM_WORK_FAKE_DELIVERY=true TEAM_WORK_DISPATCH_ALLOWLIST='["owner"]' -- issue:42 owner wrong-epoch received

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" acknowledged)" = "false" ]
  [ "$before" = "$(sha256_file "$database")" ]

  run_reconciler "$AUDIT_FIXTURES/open.json" dispatch-ack "$pack" TEAM_WORK_NOW=103 TEAM_WORK_FAKE_DELIVERY=true TEAM_WORK_DISPATCH_ALLOWLIST='["owner"]' -- issue:42 owner "$lease_epoch" late

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" acknowledged)" = "false" ]
  [ "$before" = "$(sha256_file "$database")" ]
  [ "$(sqlite3 "$database" "SELECT count(*) FROM team_work_current WHERE team = 'demo' AND work_item_id = 'issue:42';")" = "0" ]
}
