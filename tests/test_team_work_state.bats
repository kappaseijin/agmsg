#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
  bash "$SCRIPTS/join.sh" demo owner codex /tmp/demo-owner --role programmer --kind seat >/dev/null
  bash "$SCRIPTS/join.sh" demo other codex /tmp/demo-other --role programmer --kind seat >/dev/null
  bash "$SCRIPTS/join.sh" demo dispatch codex /tmp/demo-dispatch --role manager --kind seat >/dev/null
  bash "$SCRIPTS/join.sh" demo human-manager codex /tmp/demo-human-manager --role manager --kind human >/dev/null
}

teardown() {
  teardown_test_env
}

write_pack() {
  printf '%s\n' '{"schemaVersion":1,"team":"demo","workItems":[{"schemaVersion":1,"workItem":{"id":"issue:41","source":{"kind":"issue","repository":"kappaseijin/agmsg","number":41}},"ownerSeat":"owner","workKinds":["implementation"],"relations":[],"revision":1,"classificationBasis":{"contentDigest":"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","refs":[{"kind":"issue","repository":"kappaseijin/agmsg","number":41}]},"writebackRequired":false}]}' > "$1"
}

json_field() {
  JSON_INPUT="$1" JSON_FIELD="$2" node -e '
const value = JSON.parse(process.env.JSON_INPUT);
const field = process.env.JSON_FIELD;
process.stdout.write(typeof value[field] === "string" ? value[field] : JSON.stringify(value[field]));
'
}

set_pack_field() {
  PACK_PATH="$1" PACK_VALUE="$2" node -e '
const fs = require("fs");
const pack = JSON.parse(fs.readFileSync(process.env.PACK_PATH, "utf8"));
pack.workItems[0].writebackRequired = JSON.parse(process.env.PACK_VALUE);
fs.writeFileSync(process.env.PACK_PATH, JSON.stringify(pack));
'
}

state_sql() {
  sqlite3 "$TEST_SKILL_DIR/db/messages.db" "$1" | tr -d '\r'
}

state_snapshot() {
  state_sql "SELECT revision || ':' || state || ':' || coalesce(lease_owner, '') || ':' || coalesce(lease_expires_at, '') || ':' || last_action FROM team_work_current WHERE team = 'demo' AND work_item_id = 'issue:41';"
}

history_revisions() {
  state_sql "SELECT group_concat(revision, ',') FROM (SELECT revision FROM team_work_revisions WHERE team = 'demo' AND work_item_id = 'issue:41' ORDER BY revision);"
}

sha256_file() {
  FILE_PATH="$1" node -e 'const crypto = require("crypto"); const fs = require("fs"); process.stdout.write(crypto.createHash("sha256").update(fs.readFileSync(process.env.FILE_PATH)).digest("hex"));'
}

seed_transport_rows() {
  state_sql "
INSERT INTO messages(id, team, from_agent, to_agent, body, created_at, read_at)
VALUES (701, 'demo', 'sender', 'receiver', 'keep-message', '2026-08-15T00:00:00Z', NULL);
INSERT INTO message_claims(message_id, owner, claimed_at, expires_at)
VALUES (701, 'receiver', '2026-08-15T00:00:00Z', '2026-08-15T00:05:00Z');
INSERT INTO message_receipts(message_id, owner, handed_off_at, evidence)
VALUES (701, 'receiver', '2026-08-15T00:00:01Z', 'keep-receipt');
"
}

transport_snapshot() {
  state_sql "
SELECT group_concat(entry, '|') FROM (
  SELECT 'message:' || id || ':' || team || ':' || from_agent || ':' || to_agent || ':' || body || ':' || created_at || ':' || coalesce(read_at, '') AS entry FROM messages
  UNION ALL
  SELECT 'claim:' || message_id || ':' || owner || ':' || claimed_at || ':' || expires_at FROM message_claims
  UNION ALL
  SELECT 'receipt:' || message_id || ':' || owner || ':' || handed_off_at || ':' || evidence FROM message_receipts
  ORDER BY entry
);
"
}

@test "team-work state: owner claim and ack append revisions" {
  local pack="$BATS_TEST_TMPDIR/pack.json"
  write_pack "$pack"

  run bash "$SCRIPTS/team-work.sh" claim demo "$pack" issue:41 owner 60
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" revision)" = "1" ]
  [ "$(json_field "$output" state)" = "claimed" ]
  [ "$(json_field "$output" leaseOwner)" = "owner" ]

  run bash "$SCRIPTS/team-work.sh" ack demo "$pack" issue:41 owner received
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" revision)" = "2" ]
  [ "$(json_field "$output" state)" = "acknowledged" ]
  [ "$(state_sql "SELECT count(*) FROM team_work_revisions WHERE team = 'demo' AND work_item_id = 'issue:41';")" = "2" ]
  [ "$(history_revisions)" = "1,2" ]
}

@test "team-work state: competing owner and manager claims choose one lease holder" {
  local pack="$BATS_TEST_TMPDIR/pack.json"
  local owner_result="$BATS_TEST_TMPDIR/owner-result"
  local manager_result="$BATS_TEST_TMPDIR/manager-result"
  local lease_owner
  write_pack "$pack"

  run env SCRIPTS="$SCRIPTS" PACK="$pack" OWNER_RESULT="$owner_result" MANAGER_RESULT="$manager_result" bash -c '
    bash "$SCRIPTS/team-work.sh" claim demo "$PACK" issue:41 owner 60 >"$OWNER_RESULT" 2>&1 &
    owner_pid=$!
    bash "$SCRIPTS/team-work.sh" claim demo "$PACK" issue:41 dispatch 60 >"$MANAGER_RESULT" 2>&1 &
    manager_pid=$!
    wait "$owner_pid"; owner_status=$?
    wait "$manager_pid"; manager_status=$?
    printf "statuses:%s,%s\\n" "$owner_status" "$manager_status"
    exit 0
  '

  [ "$status" -eq 0 ]
  [[ "$output" == *"statuses:0,2"* || "$output" == *"statuses:2,0"* ]]
  lease_owner="$(state_sql "SELECT lease_owner FROM team_work_current WHERE team = 'demo' AND work_item_id = 'issue:41';")"
  [[ "$lease_owner" = "owner" || "$lease_owner" = "dispatch" ]]
  [ "$(history_revisions)" = "1" ]
}

@test "team-work state: non-holder cannot ack renew or release without a revision" {
  local pack="$BATS_TEST_TMPDIR/pack.json"
  local before
  write_pack "$pack"

  run bash "$SCRIPTS/team-work.sh" claim demo "$pack" issue:41 owner 60
  [ "$status" -eq 0 ]
  before="$(state_snapshot)"

  run bash "$SCRIPTS/team-work.sh" ack demo "$pack" issue:41 other wrong-owner
  [ "$status" -eq 2 ]
  [[ "$output" == *"schema error:"* ]]
  [ "$before" = "$(state_snapshot)" ]
  [ "$(history_revisions)" = "1" ]

  run bash "$SCRIPTS/team-work.sh" renew demo "$pack" issue:41 other 60
  [ "$status" -eq 2 ]
  [[ "$output" == *"schema error:"* ]]
  [ "$before" = "$(state_snapshot)" ]
  [ "$(history_revisions)" = "1" ]

  run bash "$SCRIPTS/team-work.sh" release demo "$pack" issue:41 other
  [ "$status" -eq 2 ]
  [[ "$output" == *"schema error:"* ]]
  [ "$before" = "$(state_snapshot)" ]
  [ "$(history_revisions)" = "1" ]

  run bash "$SCRIPTS/team-work.sh" ack demo "$pack" issue:41 dispatch manager-cannot-ack
  [ "$status" -eq 2 ]
  [[ "$output" == *"schema error:"* ]]
  [ "$before" = "$(state_snapshot)" ]
  [ "$(history_revisions)" = "1" ]

  run bash "$SCRIPTS/team-work.sh" renew demo "$pack" issue:41 dispatch 60
  [ "$status" -eq 2 ]
  [[ "$output" == *"schema error:"* ]]
  [ "$before" = "$(state_snapshot)" ]
  [ "$(history_revisions)" = "1" ]

  run bash "$SCRIPTS/team-work.sh" release demo "$pack" issue:41 dispatch
  [ "$status" -eq 2 ]
  [[ "$output" == *"schema error:"* ]]
  [ "$before" = "$(state_snapshot)" ]
  [ "$(history_revisions)" = "1" ]
}

@test "team-work state: active owner renews and releases its lease in order" {
  local pack="$BATS_TEST_TMPDIR/pack.json"
  write_pack "$pack"

  run bash "$SCRIPTS/team-work.sh" claim demo "$pack" issue:41 owner 60
  [ "$status" -eq 0 ]

  run bash "$SCRIPTS/team-work.sh" renew demo "$pack" issue:41 owner 120
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" revision)" = "2" ]
  [ "$(json_field "$output" leaseOwner)" = "owner" ]

  run bash "$SCRIPTS/team-work.sh" release demo "$pack" issue:41 owner
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" revision)" = "3" ]
  [ "$(json_field "$output" leaseOwner)" = "null" ]
  [ "$(history_revisions)" = "1,2,3" ]
}

@test "team-work state: expired lease is reclaimed with a contiguous revision chain" {
  local pack="$BATS_TEST_TMPDIR/pack.json"
  write_pack "$pack"

  run bash "$SCRIPTS/team-work.sh" claim demo "$pack" issue:41 owner 0
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" revision)" = "1" ]

  run bash "$SCRIPTS/team-work.sh" claim demo "$pack" issue:41 dispatch 60
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" revision)" = "2" ]
  [ "$(json_field "$output" leaseOwner)" = "dispatch" ]
  [ "$(history_revisions)" = "1,2" ]
  [ "$(state_sql "SELECT previous_revision FROM team_work_revisions WHERE team = 'demo' AND work_item_id = 'issue:41' AND revision = 2;")" = "1" ]
  [ "$(state_sql "SELECT group_concat(json_extract(snapshot_json, '$.revision'), ',') FROM (SELECT snapshot_json FROM team_work_revisions WHERE team = 'demo' AND work_item_id = 'issue:41' ORDER BY revision);")" = "1,2" ]
}

@test "team-work state: active owner and exact manager seat may update state and work references" {
  local pack="$BATS_TEST_TMPDIR/pack.json"
  local before
  write_pack "$pack"

  run bash "$SCRIPTS/team-work.sh" claim demo "$pack" issue:41 owner 60
  [ "$status" -eq 0 ]

  run bash "$SCRIPTS/team-work.sh" set-state demo "$pack" issue:41 owner in_progress
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" revision)" = "2" ]
  [ "$(json_field "$output" state)" = "in_progress" ]
  [ "$(json_field "$output" leaseOwner)" = "owner" ]

  run bash "$SCRIPTS/team-work.sh" set-state demo "$pack" issue:41 dispatch blocked
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" revision)" = "3" ]
  [ "$(json_field "$output" state)" = "blocked" ]
  [ "$(json_field "$output" leaseOwner)" = "owner" ]

  run bash "$SCRIPTS/team-work.sh" link-pr demo "$pack" issue:41 dispatch kappaseijin/agmsg 47 contributes
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" revision)" = "4" ]
  [ "$(state_sql "SELECT json_extract(pr_links_json, '\$[0].number') FROM team_work_current WHERE team = 'demo' AND work_item_id = 'issue:41';")" = "47" ]

  run bash "$SCRIPTS/team-work.sh" writeback demo "$pack" issue:41 dispatch local-evidence
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" revision)" = "5" ]
  [ "$(state_sql "SELECT json_extract(writebacks_json, '\$[0].evidence') FROM team_work_current WHERE team = 'demo' AND work_item_id = 'issue:41';")" = "local-evidence" ]

  before="$(state_snapshot)"
  run bash "$SCRIPTS/team-work.sh" link-pr demo "$pack" issue:41 dispatch kappaseijin/agmsg 47 contributes
  [ "$status" -eq 2 ]
  [[ "$output" == *"schema error:"* ]]
  [ "$before" = "$(state_snapshot)" ]
  [ "$(history_revisions)" = "1,2,3,4,5" ]

  run bash "$SCRIPTS/team-work.sh" set-state demo "$pack" issue:41 human-manager blocked
  [ "$status" -eq 2 ]
  [[ "$output" == *"schema error:"* ]]
  [ "$before" = "$(state_snapshot)" ]
  [ "$(history_revisions)" = "1,2,3,4,5" ]
}

@test "team-work state: contract drift is rejected without a revision" {
  local pack="$BATS_TEST_TMPDIR/pack.json"
  local before
  write_pack "$pack"

  run bash "$SCRIPTS/team-work.sh" claim demo "$pack" issue:41 owner 60
  [ "$status" -eq 0 ]
  before="$(state_snapshot)"
  set_pack_field "$pack" true

  run bash "$SCRIPTS/team-work.sh" ack demo "$pack" issue:41 owner stale-contract
  [ "$status" -eq 2 ]
  [[ "$output" == *"schema error:"* ]]
  [ "$before" = "$(state_snapshot)" ]
  [ "$(history_revisions)" = "1" ]
}

@test "team-work state: mutation preserves the pack roster and message transport rows" {
  local pack="$BATS_TEST_TMPDIR/pack.json"
  local roster="$TEST_SKILL_DIR/teams/demo/config.json"
  local before_pack before_roster before_transport
  write_pack "$pack"
  seed_transport_rows
  before_pack="$(sha256_file "$pack")"
  before_roster="$(sha256_file "$roster")"
  before_transport="$(transport_snapshot)"

  run bash "$SCRIPTS/team-work.sh" claim demo "$pack" issue:41 owner 60
  [ "$status" -eq 0 ]
  [ "$before_pack" = "$(sha256_file "$pack")" ]
  [ "$before_roster" = "$(sha256_file "$roster")" ]
  [ "$before_transport" = "$(transport_snapshot)" ]
}
