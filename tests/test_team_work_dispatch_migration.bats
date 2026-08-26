#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
}

teardown() {
  teardown_test_env
}

write_legacy_dispatch_db() {
  local db="$1"
  sqlite3 "$db" <<'SQL'
CREATE TABLE team_work_dispatch_current (
  team TEXT NOT NULL,
  work_item_id TEXT NOT NULL,
  contract_digest TEXT NOT NULL,
  envelope_digest TEXT NOT NULL,
  owner_seat TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('dispatching', 'claimed')),
  lease_epoch TEXT NOT NULL,
  lease_expires_at INTEGER NOT NULL,
  queue_digest TEXT NOT NULL,
  delivery_evidence_json TEXT NOT NULL CHECK (json_valid(delivery_evidence_json)),
  ack_evidence TEXT,
  last_action TEXT NOT NULL,
  last_actor TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (team, work_item_id)
);
CREATE TABLE team_work_dispatch_revisions (
  team TEXT NOT NULL,
  work_item_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK (revision > 0),
  previous_revision INTEGER,
  action TEXT NOT NULL,
  actor TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('dispatching', 'claimed')),
  lease_epoch TEXT NOT NULL,
  snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
  created_at INTEGER NOT NULL,
  PRIMARY KEY (team, work_item_id, revision)
);
INSERT INTO team_work_dispatch_current VALUES
  ('demo','issue:42','sha256:contract','sha256:envelope','owner','claimed','epoch-2',100,'sha256:queue','{}','{"evidence":"ack"}','dispatch-ack','owner',90,100);
INSERT INTO team_work_dispatch_revisions VALUES
  ('demo','issue:42',1,NULL,'dispatch','manager','dispatching','epoch-1','{"schemaVersion":1,"state":"dispatching","leaseEpoch":"epoch-1"}',80),
  ('demo','issue:42',2,1,'dispatch-ack','owner','claimed','epoch-2','{"schemaVersion":1,"state":"claimed","leaseEpoch":"epoch-2"}',100);
SQL
}

write_invalid_legacy_dispatch_db() {
  local db="$1"
  sqlite3 "$db" <<'SQL'
CREATE TABLE team_work_dispatch_current (
  team TEXT NOT NULL, work_item_id TEXT NOT NULL, contract_digest TEXT NOT NULL,
  envelope_digest TEXT NOT NULL, owner_seat TEXT NOT NULL, state TEXT NOT NULL,
  lease_epoch TEXT NOT NULL, lease_expires_at INTEGER NOT NULL, queue_digest TEXT NOT NULL,
  delivery_evidence_json TEXT NOT NULL, ack_evidence TEXT, last_action TEXT NOT NULL,
  last_actor TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  PRIMARY KEY (team, work_item_id)
);
CREATE TABLE team_work_dispatch_revisions (
  team TEXT NOT NULL, work_item_id TEXT NOT NULL, revision INTEGER NOT NULL,
  previous_revision INTEGER, action TEXT NOT NULL, actor TEXT NOT NULL,
  state TEXT NOT NULL, lease_epoch TEXT NOT NULL, snapshot_json TEXT NOT NULL,
  created_at INTEGER NOT NULL, PRIMARY KEY (team, work_item_id, revision)
);
INSERT INTO team_work_dispatch_current VALUES
  ('demo','issue:42','sha256:contract','sha256:envelope','owner','dispatching','epoch-1',100,'sha256:queue','{not-json}',NULL,'dispatch','manager',90,90);
INSERT INTO team_work_dispatch_revisions VALUES
  ('demo','issue:42',1,NULL,'dispatch','manager','dispatching','epoch-1','{not-json}',90);
SQL
}

write_partial_legacy_dispatch_db() {
  local db="$1"
  write_legacy_dispatch_db "$db"
  sqlite3 "$db" "DROP TABLE team_work_dispatch_revisions;"
}

ensure_store() {
  local store="$1" fail_copy="${2:-0}"
  run env AGMSG_STORAGE_PATH="$store" AGMSG_TEAM_WORK_MIGRATION_FAIL_COPY="$fail_copy" bash -c '
    set -euo pipefail
    source "$1/lib/storage.sh"
    agmsg_storage_ensure_initialized
  ' bash "$SCRIPTS"
}

@test "team-work dispatch migration: copies legacy rows, preserves history, and is idempotent" {
  local store="$BATS_TEST_TMPDIR/legacy-store"
  local db="$store/messages.db"
  local before after
  mkdir -p "$store"
  write_legacy_dispatch_db "$db"

  ensure_store "$store"
  [ "$status" -eq 0 ]
  sqlite3 "$db" "PRAGMA table_info(team_work_dispatch_current);" | cut -d'|' -f2 | tr '\n' '|' | grep -Fq 'recovery_evidence|'
  sqlite3 "$db" "PRAGMA table_info(team_work_dispatch_revisions);" | cut -d'|' -f2 | tr '\n' '|' | grep -Fq 'recovery_evidence|'
  [ "$(sqlite3 "$db" "SELECT state || ':' || lease_epoch FROM team_work_dispatch_current;")" = "claimed:epoch-2" ]
  [ "$(sqlite3 "$db" "SELECT group_concat(revision || ':' || state || ':' || coalesce(previous_revision, ''), '|') FROM team_work_dispatch_revisions ORDER BY revision;")" = "1:dispatching:|2:claimed:1" ]

  before="$(shasum -a 256 "$db" | awk '{print $1}')"
  ensure_store "$store"
  [ "$status" -eq 0 ]
  after="$(shasum -a 256 "$db" | awk '{print $1}')"
  [ "$before" = "$after" ]

  run sqlite3 "$db" "UPDATE team_work_dispatch_revisions SET state='dispatching' WHERE revision=1;"
  [ "$status" -ne 0 ]
  [[ "$output" == *"append-only"* ]]
}

@test "team-work dispatch migration: copy failure rolls back the legacy schema" {
  local store="$BATS_TEST_TMPDIR/legacy-copy-failure"
  local db="$store/messages.db"
  mkdir -p "$store"
  write_legacy_dispatch_db "$db"

  ensure_store "$store" 1
  [ "$status" -ne 0 ]
  [ -z "$(sqlite3 "$db" "PRAGMA table_info(team_work_dispatch_current);" | awk -F'|' '$2 == "recovery_evidence" {print $2}')" ]
  [ "$(sqlite3 "$db" "SELECT state || ':' || lease_epoch FROM team_work_dispatch_current;")" = "claimed:epoch-2" ]
  [ "$(sqlite3 "$db" "SELECT count(*) FROM team_work_dispatch_revisions;")" = "2" ]
}

@test "team-work dispatch migration: invalid JSON fails closed before destructive copy" {
  local store="$BATS_TEST_TMPDIR/legacy-invalid-json"
  local db="$store/messages.db"
  mkdir -p "$store"
  write_invalid_legacy_dispatch_db "$db"

  ensure_store "$store"
  [ "$status" -ne 0 ]
  [ -z "$(sqlite3 "$db" "PRAGMA table_info(team_work_dispatch_current);" | awk -F'|' '$2 == "recovery_evidence" {print $2}')" ]
  [ "$(sqlite3 "$db" "SELECT delivery_evidence_json FROM team_work_dispatch_current;")" = "{not-json}" ]
  [ "$(sqlite3 "$db" "SELECT snapshot_json FROM team_work_dispatch_revisions;")" = "{not-json}" ]
}

@test "team-work dispatch migration: one legacy table fails closed" {
  local store="$BATS_TEST_TMPDIR/legacy-partial-schema"
  local db="$store/messages.db"
  mkdir -p "$store"
  write_partial_legacy_dispatch_db "$db"

  run env AGMSG_STORAGE_PATH="$store" bash "$SCRIPTS/internal/migrate-team-work-dispatch.sh"
  [ "$status" -ne 0 ]
  printf '%s\n' "$output" | grep -Fq "team-work dispatch migration requires both legacy tables"
  [ "$(sqlite3 "$db" "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='team_work_dispatch_current';")" = 1 ]
  [ "$(sqlite3 "$db" "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='team_work_dispatch_revisions';")" = 0 ]
}
