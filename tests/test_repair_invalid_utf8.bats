#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
  bash "$SCRIPTS/join.sh" testteam alice claude-code /tmp/project-a
  bash "$SCRIPTS/join.sh" testteam bob claude-code /tmp/project-b
  bash -c 'source "$1/lib/storage.sh"; agmsg_storage_load; storage_init testteam >/dev/null' _ "$SCRIPTS"
}

teardown() {
  teardown_test_env
}

assert_output_contains() {
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

assert_output_lacks() {
  case "$1" in
    *"$2"*) return 1 ;;
    *) return 0 ;;
  esac
}

seed_repairable_pair_fixture() {
  sqlite3 -bail "$DBPATH" <<'SQL'
INSERT INTO messages(team,from_agent,to_agent,body,created_at) VALUES
  ('testteam','alice','bob','before-valid','2026-08-25T00:00:01Z'),
  ('testteam','alice','bob',CAST(X'E696B02048454144208082' AS TEXT),'2026-08-25T00:00:02Z'),
  ('testteam','alice','bob','after-valid','2026-08-25T00:00:03Z');
INSERT INTO events(type,id,team,from_agent,to_agent,body,at,legacy_id)
SELECT 'message_sent','evt-before',team,from_agent,to_agent,body,created_at,id
  FROM messages WHERE body='before-valid';
INSERT INTO events(type,id,team,from_agent,to_agent,body,at,legacy_id)
SELECT 'message_sent','evt-invalid',team,from_agent,to_agent,body,created_at,id
  FROM messages WHERE hex(body)='E696B02048454144208082';
INSERT INTO events(type,id,team,from_agent,to_agent,body,at,legacy_id)
SELECT 'message_sent','evt-after',team,from_agent,to_agent,body,created_at,id
  FROM messages WHERE body='after-valid';
SQL
}

@test "repair check detects invalid body in events and messages without writing" {
  seed_repairable_pair_fixture
  local before_events before_messages cursor_before
  before_events="$(sqlite3 "$DBPATH" "SELECT group_concat(hex(body), ',') FROM events WHERE type='message_sent' ORDER BY seq;")"
  before_messages="$(sqlite3 "$DBPATH" "SELECT group_concat(hex(body), ',') FROM messages ORDER BY id;")"
  cursor_before="$(sqlite3 "$DBPATH" "SELECT group_concat(team || ':' || agent || ':' || local_position, ',') FROM read_cursors ORDER BY team, agent;")"

  run bash "$SCRIPTS/repair-invalid-utf8.sh" --check testteam
  [ "$status" -eq 0 ]
  assert_output_contains "$output" "table=events"
  assert_output_contains "$output" "table=messages"
  assert_output_contains "$output" "field=body"
  assert_output_contains "$output" "status=repairable"
  assert_output_contains "$output" "repairable_count=2"
  assert_output_contains "$output" "unsupported_count=0"
  assert_output_lacks "$output" "E696B02048454144208082"

  [ "$(sqlite3 "$DBPATH" "SELECT group_concat(hex(body), ',') FROM events WHERE type='message_sent' ORDER BY seq;")" = "$before_events" ]
  [ "$(sqlite3 "$DBPATH" "SELECT group_concat(hex(body), ',') FROM messages ORDER BY id;")" = "$before_messages" ]
  [ "$(sqlite3 "$DBPATH" "SELECT group_concat(team || ':' || agent || ':' || local_position, ',') FROM read_cursors ORDER BY team, agent;")" = "$cursor_before" ]
}

@test "repair scopes a shared store to the selected team" {
  seed_repairable_pair_fixture
  sqlite3 -bail "$DBPATH" <<'SQL'
INSERT INTO messages(team,from_agent,to_agent,body,created_at)
VALUES ('otherteam','alice','bob',CAST(X'E696B02048454144208082' AS TEXT),'2026-08-25T00:01:00Z');
SQL
  local other_before
  other_before="$(sqlite3 "$DBPATH" "SELECT hex(body) FROM messages WHERE team='otherteam';")"

  run bash "$SCRIPTS/repair-invalid-utf8.sh" --check testteam
  [ "$status" -eq 0 ]
  assert_output_contains "$output" "repairable_count=2"
  assert_output_contains "$output" "unsupported_count=0"

  run bash "$SCRIPTS/repair-invalid-utf8.sh" --apply testteam --backup "$BATS_TEST_TMPDIR/scoped.backup"
  [ "$status" -eq 0 ]
  assert_output_contains "$output" "status=applied"
  [ "$(sqlite3 "$DBPATH" "SELECT hex(body) FROM messages WHERE team='otherteam';")" = "$other_before" ]
}

@test "repair safely quotes a team selector" {
  run bash "$SCRIPTS/repair-invalid-utf8.sh" --check "team'quote"
  [ "$status" -eq 0 ]
  assert_output_contains "$output" "repairable_count=0"
  assert_output_contains "$output" "unsupported_count=0"
}

@test "repair apply backs up and repairs both tables before history and inbox reads" {
  seed_repairable_pair_fixture
  local backup="$BATS_TEST_TMPDIR/messages.db.backup"
  local expected="E696B0204845414420EFBFBDEFBFBD"
  [ ! -e "$backup" ]

  run bash "$SCRIPTS/repair-invalid-utf8.sh" --apply testteam --backup "$backup"
  [ "$status" -eq 0 ]
  assert_output_contains "$output" "status=applied"
  assert_output_contains "$output" "repairable_count=2"
  [ -f "$backup" ]
  [ "$(sqlite3 "$DBPATH" "SELECT hex(body) FROM messages WHERE id=2;")" = "$expected" ]
  [ "$(sqlite3 "$DBPATH" "SELECT hex(body) FROM events WHERE id='evt-invalid';")" = "$expected" ]
  [ "$(sqlite3 "$backup" "SELECT hex(body) FROM messages WHERE id=2;")" = "E696B02048454144208082" ]
  [ "$(sqlite3 "$backup" "SELECT hex(body) FROM events WHERE id='evt-invalid';")" = "E696B02048454144208082" ]
  [ "$(sqlite3 "$DBPATH" 'PRAGMA integrity_check;')" = "ok" ]
  [ "$(sqlite3 "$backup" 'PRAGMA integrity_check;')" = "ok" ]

  run bash "$SCRIPTS/history.sh" testteam bob 20
  [ "$status" -eq 0 ]
  assert_output_contains "$output" "before-valid"
  assert_output_contains "$output" "after-valid"
  assert_output_contains "$output" "HEAD"
  assert_output_contains "$output" "�"

  run bash "$SCRIPTS/inbox.sh" testteam bob
  [ "$status" -eq 0 ]
  assert_output_contains "$output" "before-valid"
  assert_output_contains "$output" "after-valid"
  assert_output_contains "$output" "�"
}

@test "repair apply is a no-op for a valid-only database" {
  sqlite3 -bail "$DBPATH" <<'SQL'
INSERT INTO messages(team,from_agent,to_agent,body,created_at)
VALUES ('testteam','alice','bob','正常な本文','2026-08-25T00:00:01Z');
SQL
  local backup="$BATS_TEST_TMPDIR/valid-only.backup"
  local before
  before="$(sqlite3 "$DBPATH" "SELECT id || ':' || hex(body) FROM messages;")"

  run bash "$SCRIPTS/repair-invalid-utf8.sh" --check testteam
  [ "$status" -eq 0 ]
  assert_output_contains "$output" "repairable_count=0"
  assert_output_contains "$output" "unsupported_count=0"

  run bash "$SCRIPTS/repair-invalid-utf8.sh" --apply testteam --backup "$backup"
  [ "$status" -eq 0 ]
  assert_output_contains "$output" "status=no_changes"
  [ ! -e "$backup" ]
  [ "$(sqlite3 "$DBPATH" "SELECT id || ':' || hex(body) FROM messages;")" = "$before" ]
}

@test "repair apply refuses invalid non-body fields without backup or writes" {
  sqlite3 -bail "$DBPATH" <<'SQL'
INSERT INTO events(type,id,team,from_agent,to_agent,body,at)
VALUES ('message_sent','evt-unsupported','testteam',CAST(X'E696B02048454144208082' AS TEXT),
        'bob','valid-body','2026-08-25T00:00:01Z');
INSERT INTO messages(team,from_agent,to_agent,body,created_at)
VALUES ('testteam',CAST(X'E696B02048454144208082' AS TEXT),'bob','valid-body','2026-08-25T00:00:02Z');
SQL
  local backup="$BATS_TEST_TMPDIR/unsupported.backup"
  local before_event before_message
  before_event="$(sqlite3 "$DBPATH" "SELECT hex(from_agent) || ':' || hex(body) FROM events WHERE id='evt-unsupported';")"
  before_message="$(sqlite3 "$DBPATH" "SELECT hex(from_agent) || ':' || hex(body) FROM messages WHERE body='valid-body';")"

  run bash "$SCRIPTS/repair-invalid-utf8.sh" --apply testteam --backup "$backup"
  [ "$status" -ne 0 ]
  assert_output_contains "$output" "unsupported_corruption"
  assert_output_contains "$output" "table=events"
  assert_output_contains "$output" "table=messages"
  assert_output_contains "$output" "unsupported_count=2"
  [ ! -e "$backup" ]
  [ "$(sqlite3 "$DBPATH" "SELECT hex(from_agent) || ':' || hex(body) FROM events WHERE id='evt-unsupported';")" = "$before_event" ]
  [ "$(sqlite3 "$DBPATH" "SELECT hex(from_agent) || ':' || hex(body) FROM messages WHERE body='valid-body';")" = "$before_message" ]
}

@test "repair rejects invalid CLI combinations before opening a store" {
  local isolated="$BATS_TEST_TMPDIR/repair-cli-store"
  mkdir -p "$isolated"

  run env AGMSG_STORAGE_PATH="$isolated" bash "$SCRIPTS/repair-invalid-utf8.sh" --apply testteam
  [ "$status" -ne 0 ]
  assert_output_contains "$output" "--backup"
  [ ! -e "$isolated/messages.db" ]
}

@test "repair UTF-8 hex scanner preserves valid boundaries and replaces malformed bytes" {
  run bash -c 'source "$1/lib/utf8.sh"; for value in \
    4142 C2A2 E0A080 ED9FBF F0908080 F48FBFBF EFBFBD \
    C0AF EDA080 F4908080 E696 FF; do
    agmsg_sanitize_utf8_hex "$value"; printf "\n"
  done' _ "$SCRIPTS"
  [ "$status" -eq 0 ]
  local expected
  expected=$'4142\nC2A2\nE0A080\nED9FBF\nF0908080\nF48FBFBF\nEFBFBD\nEFBFBDEFBFBD\nEFBFBDEFBFBDEFBFBD\nEFBFBDEFBFBDEFBFBDEFBFBD\nEFBFBDEFBFBD\nEFBFBD'
  [ "$output" = "$expected" ]
}

@test "repair guarded transaction rolls back when the original body no longer matches" {
  seed_repairable_pair_fixture
  sqlite3 -bail "$DBPATH" <<'SQL'
CREATE TRIGGER mutate_legacy_after_event_update
AFTER UPDATE OF body ON events
WHEN NEW.id='evt-invalid'
BEGIN
  UPDATE messages SET body='trigger-change' WHERE id=2;
END;
SQL
  local backup="$BATS_TEST_TMPDIR/guard.backup"

  run bash "$SCRIPTS/repair-invalid-utf8.sh" --apply testteam --backup "$backup"
  [ "$status" -ne 0 ]
  assert_output_contains "$output" "status=apply_failed"
  [ -f "$backup" ]
  [ "$(sqlite3 "$DBPATH" "SELECT hex(body) FROM events WHERE id='evt-invalid';")" = "E696B02048454144208082" ]
  [ "$(sqlite3 "$DBPATH" "SELECT hex(body) FROM messages WHERE id=2;")" = "E696B02048454144208082" ]
}
