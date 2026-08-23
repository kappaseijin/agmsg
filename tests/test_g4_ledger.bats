#!/usr/bin/env bats

load test_helper
load helpers/g4-fixtures

setup() {
  setup_test_env
}

seed_g4_row() {
  sqlite3 "$DBPATH" <<'SQL'
INSERT INTO team_work_g4_current(
  team, source_repository, source_number, state, owner_seat,
  work_kinds_json, revision, pack_digest, entry_digest, coverage_digest,
  audit_digest, basis_json, blocker_json, evidence, last_action, last_actor,
  created_at, updated_at
) VALUES (
  'demo', 'kappaseijin/example', 42, 'ready', 'owner',
  json('["implementation"]'), 1,
  'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
  'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
  'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
  json('{"contentDigest":"sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}'),
  NULL, 'bootstrap evidence', 'g4-bootstrap', 'manager', 100, 100
);
SQL
}

@test "g4 ledger schema creates current and append-only revision tables" {
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name IN ('team_work_g4_current', 'team_work_g4_revisions');")" = "2" ]
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name IN ('team_work_g4_current_history_insert', 'team_work_g4_current_history_update', 'team_work_g4_revisions_immutable_update', 'team_work_g4_revisions_immutable_delete');")" = "4" ]

  bash "$SCRIPTS/internal/init-db.sh"
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name IN ('team_work_g4_current', 'team_work_g4_revisions');")" = "2" ]
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name IN ('team_work_g4_current_history_insert', 'team_work_g4_current_history_update', 'team_work_g4_revisions_immutable_update', 'team_work_g4_revisions_immutable_delete');")" = "4" ]
}

@test "g4 ledger insert appends a revision one canonical snapshot" {
  seed_g4_row

  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_current;")" = "1" ]
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_revisions;")" = "1" ]
  [ "$(sqlite3 "$DBPATH" "SELECT revision || ':' || COALESCE(previous_revision, 'null') || ':' || action || ':' || actor FROM team_work_g4_revisions;")" = "1:null:g4-bootstrap:manager" ]
  [ "$(sqlite3 "$DBPATH" "SELECT json_extract(snapshot_json, '$.source.repository') || ':' || json_extract(snapshot_json, '$.source.number') || ':' || json_extract(snapshot_json, '$.state') || ':' || json_extract(snapshot_json, '$.workKinds[0]') FROM team_work_g4_revisions;")" = "kappaseijin/example:42:ready:implementation" ]
  [ "$(sqlite3 "$DBPATH" "SELECT json_valid(snapshot_json) FROM team_work_g4_revisions;")" = "1" ]
}

@test "g4 ledger current update appends the next revision and preserves the previous one" {
  seed_g4_row
  sqlite3 "$DBPATH" <<'SQL'
UPDATE team_work_g4_current
SET state = 'blocked',
    blocker_json = json('{"reasonCode":"review","releasePredicate":{"kind":"not_before","at":"2099-01-01T00:00:00+09:00"}}'),
    revision = revision + 1,
    last_action = 'g4-transition',
    last_actor = 'manager',
    updated_at = 200
WHERE team = 'demo' AND source_repository = 'kappaseijin/example' AND source_number = 42;
SQL

  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_revisions;")" = "2" ]
  [ "$(sqlite3 "$DBPATH" "SELECT previous_revision FROM team_work_g4_revisions WHERE revision = 2;")" = "1" ]
  [ "$(sqlite3 "$DBPATH" "SELECT json_extract(snapshot_json, '$.state') || ':' || json_extract(snapshot_json, '$.blocker.reasonCode') FROM team_work_g4_revisions WHERE revision = 2;")" = "blocked:review" ]
}

@test "g4 ledger revision history rejects direct update and delete" {
  seed_g4_row
  run sqlite3 "$DBPATH" "UPDATE team_work_g4_revisions SET actor = 'attacker';"
  [ "$status" -ne 0 ]
  grep -Fq 'team_work_g4_revisions is append-only' <<<"$output"
  run sqlite3 "$DBPATH" "DELETE FROM team_work_g4_revisions;"
  [ "$status" -ne 0 ]
  grep -Fq 'team_work_g4_revisions is append-only' <<<"$output"
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_revisions;")" = "1" ]
}

@test "g4 ledger invalid JSON and state combinations do not create rows" {
  run sqlite3 "$DBPATH" "INSERT INTO team_work_g4_current(team, source_repository, source_number, state, owner_seat, work_kinds_json, revision, pack_digest, entry_digest, coverage_digest, audit_digest, basis_json, blocker_json, evidence, last_action, last_actor, created_at, updated_at) VALUES ('demo', 'kappaseijin/example', 42, 'ready', 'owner', 'not-json', 1, 'pack', 'entry', 'coverage', 'audit', '{}', NULL, 'bad', 'bad', 'manager', 100, 100);"
  [ "$status" -ne 0 ]
  run sqlite3 "$DBPATH" "INSERT INTO team_work_g4_current(team, source_repository, source_number, state, owner_seat, work_kinds_json, revision, pack_digest, entry_digest, coverage_digest, audit_digest, basis_json, blocker_json, evidence, last_action, last_actor, created_at, updated_at) VALUES ('demo', 'kappaseijin/example', 42, 'ready', 'owner', '[]', 1, 'pack', 'entry', 'coverage', 'audit', '{}', '{}', 'bad', 'bad', 'manager', 100, 100);"
  [ "$status" -ne 0 ]
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_current;")" = "0" ]
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_revisions;")" = "0" ]
}

@test "g4 ledger transaction failure leaves current and revisions unchanged" {
  seed_g4_row
  local before_current before_revisions
  before_current="$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_current;")"
  before_revisions="$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_revisions;")"

  run node - "$DBPATH" <<'NODE'
const ledger = require("./scripts/lib/g4-ledger");
try {
  const statements = "INSERT INTO team_work_g4_current(" +
    "team, source_repository, source_number, state, owner_seat, " +
    "work_kinds_json, revision, pack_digest, entry_digest, coverage_digest, " +
    "audit_digest, basis_json, blocker_json, evidence, last_action, last_actor, " +
    "created_at, updated_at) VALUES (" +
    "'demo', 'kappaseijin/example', 43, 'invalid', 'owner', '[]', 1, " +
    "'pack', 'entry', 'coverage', 'audit', '{}', NULL, 'bad', 'bad', 'manager', 100, 100);";
  ledger.runG4Transaction(process.argv[2], statements);
  process.exit(1);
} catch (_) {
  process.exit(0);
}
NODE
  [ "$status" -eq 0 ]
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_current;")" = "$before_current" ]
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_revisions;")" = "$before_revisions" ]
}

@test "g4 ledger helpers read parsed current rows and canonical snapshots" {
  seed_g4_row
  run node - "$DBPATH" <<'NODE'
const ledger = require("./scripts/lib/g4-ledger");
const row = ledger.readG4Current(process.argv[2], "demo", {
  repository: "kappaseijin/example",
  number: 42,
});
if (!row) process.exit(1);
process.stdout.write(ledger.canonicalJson({
  row,
  snapshot: ledger.snapshotG4Row(row),
}) + "\n");
NODE

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" row.team)" = "demo" ]
  [ "$(json_value "$output" row.source.number)" = "42" ]
  [ "$(json_value "$output" row.workKinds[0])" = "implementation" ]
  [ "$(json_value "$output" row.blocker)" = "null" ]
  [ "$(json_value "$output" snapshot.action)" = "g4-bootstrap" ]
  [ "$(json_value "$output" snapshot.source.repository)" = "kappaseijin/example" ]
  assert_canonical_json "$output"
}

@test "g4 ledger read returns null for an absent source without hiding database errors" {
  run node - "$DBPATH" <<'NODE'
const ledger = require("./scripts/lib/g4-ledger");
const row = ledger.readG4Current(process.argv[2], "demo", {
  repository: "kappaseijin/example",
  number: 99,
});
if (row !== null) process.exit(1);
NODE
  [ "$status" -eq 0 ]
}
