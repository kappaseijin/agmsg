#!/usr/bin/env bats

load test_helper
load helpers/g4-fixtures

setup() {
  setup_test_env
  bash "$SCRIPTS/join.sh" demo owner codex /tmp/demo-owner --role programmer --kind seat >/dev/null
  bash "$SCRIPTS/join.sh" demo manager codex /tmp/demo-manager --role manager --kind seat >/dev/null
  setup_g4_fixture
}

teardown() {
  teardown_test_env
}

run_g4_bootstrap() {
  local fixture="$1" pack="$2" manager="${3:-manager}" evidence="${4:-https://example.test/g4-bootstrap}"
  run env PATH="$G4_FAKE_GH_BIN:$PATH" G4_GH_FIXTURE="$fixture" G4_GH_LOG="$G4_GH_LOG" \
    bash "$SCRIPTS/team-work.sh" g4-bootstrap demo "$pack" "$manager" "$evidence"
}

seed_g4_row() {
  local number="${1:-42}"
  sqlite3 "$DBPATH" <<SQL
INSERT INTO team_work_g4_current(
  team, source_repository, source_number, state, owner_seat,
  work_kinds_json, revision, pack_digest, entry_digest, coverage_digest,
  audit_digest, basis_json, blocker_json, evidence, last_action, last_actor,
  created_at, updated_at
) VALUES (
  'demo', 'kappaseijin/example', $number, 'ready', 'owner',
  json('["implementation"]'), 1,
  'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
  'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
  'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
  json('{"contentDigest":"sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}'),
  NULL, 'preexisting bootstrap', 'g4-bootstrap', 'manager', 100, 100
);
SQL
}

@test "g4-bootstrap: exact manager writes every source and revision one snapshot" {
  local pack="$BATS_TEST_TMPDIR/g4-pack.json"
  write_g4_pack "$pack" two

  run_g4_bootstrap "$G4_FIXTURES/two-open.json" "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" bootstrapped)" = "true" ]
  [ "$(json_value "$output" command)" = "g4-bootstrap" ]
  [ "$(json_value "$output" team)" = "demo" ]
  [ "$(json_value "$output" managerSeat)" = "manager" ]
  [ "$(json_value "$output" evidence)" = "https://example.test/g4-bootstrap" ]
  [ "$(json_value "$output" revision)" = "1" ]
  [ "$(json_value "$output" sources[0].number)" = "42" ]
  [ "$(json_value "$output" sources[1].number)" = "43" ]
  [ "$(json_value "$output" remediation)" = "[]" ]
  printf '%s\n' "$(json_value "$output" packDigest)" | grep -Eq '^sha256:[0-9a-f]{64}$'
  printf '%s\n' "$(json_value "$output" coverageDigest)" | grep -Eq '^sha256:[0-9a-f]{64}$'
  printf '%s\n' "$(json_value "$output" auditDigest)" | grep -Eq '^sha256:[0-9a-f]{64}$'
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_current;")" = "2" ]
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_revisions;")" = "2" ]
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_current WHERE revision = 1 AND last_action = 'g4-bootstrap' AND last_actor = 'manager' AND evidence = 'https://example.test/g4-bootstrap' AND length(pack_digest) = 71 AND length(entry_digest) = 71 AND length(coverage_digest) = 71 AND length(audit_digest) = 71;")" = "2" ]
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_revisions WHERE revision = 1 AND previous_revision IS NULL AND action = 'g4-bootstrap' AND actor = 'manager';")" = "2" ]
}

@test "g4-bootstrap: identical retry rejects without changing current or revision checksums" {
  local pack="$BATS_TEST_TMPDIR/g4-pack.json" before_current before_revisions
  write_g4_pack "$pack" two
  run_g4_bootstrap "$G4_FIXTURES/two-open.json" "$pack"
  [ "$status" -eq 0 ]
  before_current="$(shasum -a 256 "$DBPATH" | awk '{print $1}')"
  before_revisions="$(sqlite3 "$DBPATH" "SELECT group_concat(source_repository || ':' || source_number || ':' || revision || ':' || action || ':' || actor, '|') FROM team_work_g4_revisions ORDER BY source_repository, source_number, revision;")"

  run_g4_bootstrap "$G4_FIXTURES/two-open.json" "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" bootstrapped)" = "false" ]
  [ "$(json_value "$output" remediation[0].code)" = "already_bootstrapped" ]
  [ "$(shasum -a 256 "$DBPATH" | awk '{print $1}')" = "$before_current" ]
  [ "$(sqlite3 "$DBPATH" "SELECT group_concat(source_repository || ':' || source_number || ':' || revision || ':' || action || ':' || actor, '|') FROM team_work_g4_revisions ORDER BY source_repository, source_number, revision;")" = "$before_revisions" ]
}

@test "g4-bootstrap: one preexisting source rejects the complete batch without a partial insert" {
  local pack="$BATS_TEST_TMPDIR/g4-pack.json" before_db
  write_g4_pack "$pack" two
  seed_g4_row 42
  before_db="$(shasum -a 256 "$DBPATH" | awk '{print $1}')"

  run_g4_bootstrap "$G4_FIXTURES/two-open.json" "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" bootstrapped)" = "false" ]
  [ "$(json_value "$output" remediation[0].code)" = "already_bootstrapped" ]
  [ "$(shasum -a 256 "$DBPATH" | awk '{print $1}')" = "$before_db" ]
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_current;")" = "1" ]
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_revisions;")" = "1" ]
}

@test "g4-bootstrap: owner, human, missing, and non-manager seats reject before live reads" {
  local pack="$BATS_TEST_TMPDIR/g4-pack.json" seat
  write_g4_pack "$pack" two
  bash "$SCRIPTS/join.sh" demo human codex /tmp/demo-human --role manager --kind human >/dev/null

  for seat in owner human missing; do
    : > "$G4_GH_LOG"
    run_g4_bootstrap "$G4_FIXTURES/two-open.json" "$pack" "$seat"
    [ "$status" -eq 0 ]
    [ "$(json_value "$output" bootstrapped)" = "false" ]
    [ "$(json_value "$output" remediation[0].code)" = "invalid_manager" ]
    [ "$(wc -l < "$G4_GH_LOG" | tr -d ' ')" -eq 0 ]
    [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_current;")" = "0" ]
  done
}

@test "g4-bootstrap: incomplete or uncertain audit never creates a row" {
  local pack="$BATS_TEST_TMPDIR/g4-pack.json" fixture

  write_g4_pack "$pack" one
  run_g4_bootstrap "$G4_FIXTURES/two-open.json" "$pack"
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" bootstrapped)" = "false" ]
  [ "$(json_value "$output" remediation[0].code)" = "audit_incomplete" ]
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_current;")" = "0" ]

  for fixture in error.json pagination-failure.json; do
    write_g4_pack "$pack" two
    run_g4_bootstrap "$G4_FIXTURES/$fixture" "$pack"
    [ "$status" -eq 0 ]
    [ "$(json_value "$output" bootstrapped)" = "false" ]
    [ "$(json_value "$output" remediation[0].code)" = "audit_incomplete" ]
    [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_current;")" = "0" ]
  done

  for fixture in predicate-negative.json predicate-gh-failure.json; do
    write_predicate_pack "$pack" issue_closed
    run_g4_bootstrap "$G4_FIXTURES/$fixture" "$pack"
    [ "$status" -eq 0 ]
    [ "$(json_value "$output" bootstrapped)" = "false" ]
    [ "$(json_value "$output" remediation[0].code)" = "audit_incomplete" ]
    [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_current;")" = "0" ]
  done
}

@test "g4-bootstrap: fake GitHub receives reads only and never a write" {
  local pack="$BATS_TEST_TMPDIR/g4-pack.json"
  write_g4_pack "$pack" two

  run_g4_bootstrap "$G4_FIXTURES/two-open.json" "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" bootstrapped)" = "true" ]
  grep -q '"kind":"read"' "$G4_GH_LOG"
  ! grep -q '"kind":"write"' "$G4_GH_LOG"
}
