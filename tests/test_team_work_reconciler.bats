#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
}

teardown() {
  teardown_test_env
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
