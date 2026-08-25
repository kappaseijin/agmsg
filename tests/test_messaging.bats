#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
  # Create a team and two agents
  bash "$SCRIPTS/join.sh" testteam alice claude-code /tmp/project-a
  bash "$SCRIPTS/join.sh" testteam bob claude-code /tmp/project-b
}

teardown() {
  teardown_test_env
}

seed_invalid_utf8_display_events() {
  bash -c 'source "$1/lib/storage.sh"; agmsg_storage_load; storage_init testteam >/dev/null' _ "$SCRIPTS"
  sqlite3 -bail "$DBPATH" <<'SQL'
INSERT INTO events(type,id,team,from_agent,to_agent,body,at) VALUES
  ('message_sent','utf8-before','testteam','alice','bob','before-valid','2026-08-25T00:00:01Z'),
  ('message_sent','utf8-invalid','testteam','alice','bob',CAST(X'E696B02048454144208082' AS TEXT),'2026-08-25T00:00:02Z'),
  ('message_sent','utf8-after','testteam','alice','bob','after-valid','2026-08-25T00:00:03Z');
SQL
}

seed_valid_utf8_display_event() {
  bash -c 'source "$1/lib/storage.sh"; agmsg_storage_load; storage_init testteam >/dev/null' _ "$SCRIPTS"
  sqlite3 -bail "$DBPATH" <<'SQL'
INSERT INTO events(type,id,team,from_agent,to_agent,body,at)
VALUES ('message_sent','utf8-valid','testteam','alice','bob','正常な本文','2026-08-25T00:00:01Z');
SQL
}

assert_display_contains() {
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

assert_display_lacks() {
  case "$1" in
    *"$2"*) return 1 ;;
    *) return 0 ;;
  esac
}

# --- send.sh ---

@test "send: delivers a message" {
  run bash "$SCRIPTS/send.sh" testteam alice bob "hello"
  [ "$status" -eq 0 ]
  [[ "$output" =~ "Queued message" ]]
  [[ "$output" =~ Queued\ message\ #[^[:space:]]+ ]]
  [[ "$output" =~ "delivery not yet acknowledged" ]]
}

@test "send: fails without required args" {
  run bash "$SCRIPTS/send.sh"
  [ "$status" -ne 0 ]
}

@test "inbox: replaces malformed UTF-8 while preserving surrounding rows" {
  seed_invalid_utf8_display_events

  run bash "$SCRIPTS/inbox.sh" testteam bob
  [ "$status" -eq 0 ]
  assert_display_contains "$output" "before-valid"
  assert_display_contains "$output" "HEAD"
  assert_display_contains "$output" "after-valid"
  local replacement=$'\357\277\275'
  assert_display_contains "$output" "${replacement}${replacement}"
  [ "$(sqlite3 "$DBPATH" "SELECT hex(body) FROM events WHERE id='utf8-invalid';")" = "E696B02048454144208082" ]
}

@test "history: replaces malformed UTF-8 while preserving surrounding rows" {
  seed_invalid_utf8_display_events

  run bash "$SCRIPTS/history.sh" testteam
  [ "$status" -eq 0 ]
  assert_display_contains "$output" "before-valid"
  assert_display_contains "$output" "HEAD"
  assert_display_contains "$output" "after-valid"
  local replacement=$'\357\277\275'
  assert_display_contains "$output" "${replacement}${replacement}"
}

@test "display sanitizer: valid UTF-8 remains unchanged in inbox and history" {
  seed_valid_utf8_display_event
  local replacement=$'\357\277\275'

  run bash "$SCRIPTS/inbox.sh" testteam bob
  [ "$status" -eq 0 ]
  assert_display_contains "$output" "正常な本文"
  assert_display_lacks "$output" "$replacement"

  run bash "$SCRIPTS/history.sh" testteam
  [ "$status" -eq 0 ]
  assert_display_contains "$output" "正常な本文"
  assert_display_lacks "$output" "$replacement"
}

# --- send.sh: roster validation (#355) ---

@test "send: rejects an unregistered from agent and does not insert" {
  run bash "$SCRIPTS/send.sh" testteam dummy bob "hi"
  [ "$status" -ne 0 ]
  [[ "$output" =~ "from agent 'dummy' is not registered" ]]
  local n
  n=$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT COUNT(*) FROM messages;")
  [ "$n" -eq 0 ]
}

@test "send: rejects an unregistered to agent and does not insert" {
  run bash "$SCRIPTS/send.sh" testteam alice dummy "hi"
  [ "$status" -ne 0 ]
  [[ "$output" =~ "to agent 'dummy' is not registered" ]]
  local n
  n=$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT COUNT(*) FROM messages;")
  [ "$n" -eq 0 ]
}

@test "send: rejection lists the currently registered roster" {
  run bash "$SCRIPTS/send.sh" testteam alice dummy "hi"
  [ "$status" -ne 0 ]
  [[ "$output" =~ "registered: alice, bob" ]]
}

@test "send: --force bypasses the roster check even with no team config at all" {
  run bash "$SCRIPTS/send.sh" brandnewteam ghost nobody "hi" --force
  [ "$status" -eq 0 ]
  [[ "$output" =~ "Queued message" ]]
  local n
  n=$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT COUNT(*) FROM events WHERE type='message_sent' AND team='brandnewteam';")
  [ "$n" -eq 1 ]
}

@test "send: --force works in leading, middle, and trailing positions" {
  run bash "$SCRIPTS/send.sh" --force brandnewteam ghost nobody "leading"
  [ "$status" -eq 0 ]
  run bash "$SCRIPTS/send.sh" brandnewteam --force ghost nobody "middle"
  [ "$status" -eq 0 ]
  run bash "$SCRIPTS/send.sh" brandnewteam ghost nobody "trailing" --force
  [ "$status" -eq 0 ]

  local n
  n=$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT COUNT(*) FROM messages WHERE team='brandnewteam';")
  [ "$n" -eq 3 ]
}

@test "send: rejects an unknown option and extra positional argument" {
  run bash "$SCRIPTS/send.sh" testteam alice bob "hi" --forse
  [ "$status" -ne 0 ]
  [[ "$output" =~ "unknown option '--forse'" ]]
  [[ "$output" != *"Use --force to bypass"* ]]

  run bash "$SCRIPTS/send.sh" testteam alice bob "hi" extra
  [ "$status" -ne 0 ]
  [[ "$output" =~ "expected 4 positional arguments" ]]
}

# --- send.sh: team-name validation (#414) ---

@test "send: rejects a team name with path traversal (../) and never consults a config outside teams/" {
  local escape_dir
  escape_dir="$(dirname "$TEST_SKILL_DIR")/escape-send"
  mkdir -p "$escape_dir"
  echo '{"agents":{"alice":{},"bob":{}}}' >"$escape_dir/config.json"
  run bash "$SCRIPTS/send.sh" "../../escape-send" alice bob "hi"
  [ "$status" -eq 1 ]
  [[ "$output" =~ "path traversal" ]]
  local n
  n=$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT COUNT(*) FROM messages;")
  [ "$n" -eq 0 ]
  rm -rf "$escape_dir"
}

@test "send: rejects '..' and '.' as team names" {
  run bash "$SCRIPTS/send.sh" ".." alice bob "hi"
  [ "$status" -eq 1 ]
  [[ "$output" =~ "not allowed" ]]
  run bash "$SCRIPTS/send.sh" "." alice bob "hi"
  [ "$status" -eq 1 ]
}

@test "send: rejects a team name starting with '-'" {
  run bash "$SCRIPTS/send.sh" "-rf" alice bob "hi"
  [ "$status" -eq 1 ]
  [[ "$output" =~ "must not start with" ]]
}

@test "send: rejects an invalid team name even when --force is supplied" {
  run bash "$SCRIPTS/send.sh" "../../escape-force" alice bob "hi" --force
  [ "$status" -eq 1 ]
  [[ "$output" =~ "path traversal" ]]
  local n
  n=$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT COUNT(*) FROM messages;")
  [ "$n" -eq 0 ]
}

@test "send: still accepts a UTF-8 (Japanese) team name" {
  bash "$SCRIPTS/join.sh" "テストチーム" alice claude-code /tmp/project-jp
  bash "$SCRIPTS/join.sh" "テストチーム" bob claude-code /tmp/project-jp2
  run bash "$SCRIPTS/send.sh" "テストチーム" alice bob "hello"
  [ "$status" -eq 0 ]
  [[ "$output" =~ "Queued message" ]]
}

@test "message-status: reports each delivery state as JSON" {
  bash "$SCRIPTS/send.sh" testteam alice bob "queued payload" >/dev/null
  bash "$SCRIPTS/send.sh" testteam alice bob "claimed payload" >/dev/null
  bash "$SCRIPTS/send.sh" testteam alice bob "handed-off payload" >/dev/null
  bash "$SCRIPTS/send.sh" testteam alice bob "legacy payload" >/dev/null

  local claimed_id handed_off_id legacy_id
  claimed_id="$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT id FROM messages WHERE body='claimed payload';")"
  handed_off_id="$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT id FROM messages WHERE body='handed-off payload';")"
  legacy_id="$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT id FROM messages WHERE body='legacy payload';")"

  bash "$SCRIPTS/claim.sh" claim "$claimed_id" status-daemon 60
  bash "$SCRIPTS/claim.sh" claim "$handed_off_id" status-daemon 60
  bash "$SCRIPTS/claim.sh" ack "$handed_off_id" status-daemon test_handoff
  sqlite3 "$TEST_SKILL_DIR/db/messages.db" "UPDATE messages SET read_at='2026-08-14T00:00:00Z' WHERE id=$legacy_id;"

  run bash "$SCRIPTS/message-status.sh" testteam bob --format json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"schemaVersion":1'* ]]
  [[ "$output" == *'"queued":1'* ]]
  [[ "$output" == *'"claimed":1'* ]]
  [[ "$output" == *'"handedOff":1'* ]]
  [[ "$output" == *'"unknown":1'* ]]
  [[ "$output" == *'"ackSemantics":"receiver_handoff_not_task_completion"'* ]]
}

# --- message-status.sh --id: single-message lookup (herdr-agent-monitor#63 AC-2) ---
#
# "Sent to ..." is not evidence of delivery -- a sender needs a way to check
# the ONE message send.sh just printed an id for, not just aggregate counts.

@test "message-status --id: reports queued for a just-sent message" {
  bash "$SCRIPTS/send.sh" testteam alice bob "queued payload" >/dev/null
  local id
  id="$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT id FROM messages WHERE body='queued payload';")"

  run bash "$SCRIPTS/message-status.sh" testteam bob --id "$id" --format json
  [ "$status" -eq 0 ]
  grep -q "\"state\":\"queued\"" <<< "$output"
  [[ "$output" == *"\"id\":\"$id\""* ]]
}

@test "message-status --id: reports claimed and handedOff distinctly" {
  bash "$SCRIPTS/send.sh" testteam alice bob "claimed payload" >/dev/null
  bash "$SCRIPTS/send.sh" testteam alice bob "handed-off payload" >/dev/null
  local claimed_id handed_off_id
  claimed_id="$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT id FROM messages WHERE body='claimed payload';")"
  handed_off_id="$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT id FROM messages WHERE body='handed-off payload';")"
  bash "$SCRIPTS/claim.sh" claim "$claimed_id" status-daemon 60
  bash "$SCRIPTS/claim.sh" claim "$handed_off_id" status-daemon 60
  bash "$SCRIPTS/claim.sh" ack "$handed_off_id" status-daemon test_handoff

  run bash "$SCRIPTS/message-status.sh" testteam bob --id "$claimed_id" --format json
  grep -q "\"state\":\"claimed\"" <<< "$output"

  run bash "$SCRIPTS/message-status.sh" testteam bob --id "$handed_off_id" --format json
  grep -q "\"state\":\"handedOff\"" <<< "$output"
}

@test "message-status --id: an unknown id reports notFound rather than an empty aggregate" {
  run bash "$SCRIPTS/message-status.sh" testteam bob --id "no-such-id" --format json
  [ "$status" -eq 0 ]
  grep -q "\"state\":\"notFound\"" <<< "$output"
}

@test "message-status --id: human format names the state without aggregate counts" {
  bash "$SCRIPTS/send.sh" testteam alice bob "queued payload" >/dev/null
  local id
  id="$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT id FROM messages WHERE body='queued payload';")"

  run bash "$SCRIPTS/message-status.sh" testteam bob --id "$id"
  [ "$status" -eq 0 ]
  grep -q "state: queued" <<< "$output"
  [[ "$output" != *"queued: "* ]]
}

# --- inbox.sh ---

@test "inbox: shows no messages when empty" {
  run bash "$SCRIPTS/inbox.sh" testteam alice
  [ "$status" -eq 0 ]
  [[ "$output" =~ "No new messages" ]]
}

@test "inbox: shows received message" {
  bash "$SCRIPTS/send.sh" testteam alice bob "hello bob"
  run bash "$SCRIPTS/inbox.sh" testteam bob
  [ "$status" -eq 0 ]
  [[ "$output" =~ "hello bob" ]]
  [[ "$output" =~ "alice" ]]
}

@test "inbox: marks messages as read" {
  bash "$SCRIPTS/send.sh" testteam alice bob "read me"
  bash "$SCRIPTS/inbox.sh" testteam bob >/dev/null
  run bash "$SCRIPTS/inbox.sh" testteam bob
  [ "$status" -eq 0 ]
  [[ "$output" =~ "No new messages" ]]
}

@test "inbox: --quiet suppresses output when no messages" {
  run bash "$SCRIPTS/inbox.sh" testteam alice --quiet
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "inbox: --quiet shows output when messages exist" {
  bash "$SCRIPTS/send.sh" testteam bob alice "ping"
  run bash "$SCRIPTS/inbox.sh" testteam alice --quiet
  [ "$status" -eq 0 ]
  [[ "$output" =~ "ping" ]]
}

@test "inbox: handles multiline message body" {
  bash "$SCRIPTS/send.sh" testteam alice bob "line1
line2
line3"
  run bash "$SCRIPTS/inbox.sh" testteam bob
  [ "$status" -eq 0 ]
  [[ "$output" =~ "1 new message" ]]
  [[ "$output" =~ "alice" ]]
}

@test "inbox: a crafted agent arg cannot inject SQL to delete other messages (#87)" {
  bash "$SCRIPTS/send.sh" testteam alice bob "keepme"
  run bash "$SCRIPTS/inbox.sh" testteam "bob' AND read_at IS NULL; DELETE FROM messages; --"
  [ "$status" -eq 0 ]
  run bash "$SCRIPTS/inbox.sh" testteam bob
  [[ "$output" =~ "keepme" ]]
}

@test "inbox: an agent name containing a quote still receives its own messages (#87)" {
  bash "$SCRIPTS/join.sh" testteam "o'brien" claude-code /tmp/project-c
  bash "$SCRIPTS/send.sh" testteam alice "o'brien" "for quote"
  run bash "$SCRIPTS/inbox.sh" testteam "o'brien"
  [ "$status" -eq 0 ]
  [[ "$output" =~ "for quote" ]]
}

@test "check-inbox: a team name containing a quote still delivers without a SQL error (#87)" {
  local project; project="$(mktemp -d)"
  bash "$SCRIPTS/join.sh" "te'am" alice claude-code /tmp/project-a
  bash "$SCRIPTS/join.sh" "te'am" carol claude-code "$project"
  bash "$SCRIPTS/send.sh" "te'am" alice carol "quoted team delivery"
  run bash -c "echo '{}' | bash '$SCRIPTS/check-inbox.sh' claude-code '$project'"
  [ "$status" -eq 0 ]
  [[ "$output" =~ "quoted team delivery" ]]
}

@test "history: handles multiline message body" {
  bash "$SCRIPTS/send.sh" testteam alice bob "multi
line"
  run bash "$SCRIPTS/history.sh" testteam
  [ "$status" -eq 0 ]
  [[ "$output" =~ "alice" ]]
  [[ "$output" =~ "bob" ]]
}

# --- history.sh ---

@test "history: shows message history" {
  bash "$SCRIPTS/send.sh" testteam alice bob "msg1"
  bash "$SCRIPTS/send.sh" testteam bob alice "msg2"
  run bash "$SCRIPTS/history.sh" testteam
  [ "$status" -eq 0 ]
  [[ "$output" =~ "msg1" ]]
  [[ "$output" =~ "msg2" ]]
}

@test "history: marks a legacy read marker without a receipt as unknown" {
  bash "$SCRIPTS/send.sh" testteam alice bob "legacy read"
  local id
  id="$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT id FROM messages WHERE body='legacy read';")"
  sqlite3 "$TEST_SKILL_DIR/db/messages.db" "UPDATE messages SET read_at='2026-08-14T00:00:00Z' WHERE id=$id;"

  run bash "$SCRIPTS/history.sh" testteam
  [ "$status" -eq 0 ]
  [[ "$output" == *"  ? ["* ]]
  [[ "$output" == *"Legend: ● queued; ○ receiver handoff acknowledged; ? legacy/unknown receipt"* ]]
}

@test "history: filters by agent" {
  bash "$SCRIPTS/send.sh" testteam alice bob "for bob"
  bash "$SCRIPTS/send.sh" testteam bob alice "for alice"
  run bash "$SCRIPTS/history.sh" testteam alice
  [ "$status" -eq 0 ]
  [[ "$output" =~ "for" ]]
}

@test "history: respects limit" {
  bash "$SCRIPTS/send.sh" testteam alice bob "msg1"
  bash "$SCRIPTS/send.sh" testteam alice bob "msg2"
  bash "$SCRIPTS/send.sh" testteam alice bob "msg3"
  # limit=1 should return exactly 1 line with arrow
  run bash "$SCRIPTS/history.sh" testteam "" 1
  [ "$status" -eq 0 ]
  local count=$(echo "$output" | grep -c "→")
  [ "$count" -eq 1 ]
}

@test "history: shows no history message when empty" {
  run bash "$SCRIPTS/history.sh" testteam
  [ "$status" -eq 0 ]
  [[ "$output" =~ "No message history" ]]
}

@test "history: a non-numeric limit falls back to the default instead of injecting SQL (#87)" {
  bash "$SCRIPTS/send.sh" testteam alice bob "msg1"
  bash "$SCRIPTS/send.sh" testteam alice bob "msg2"
  run bash "$SCRIPTS/history.sh" testteam bob "1; DELETE FROM messages; --"
  [ "$status" -eq 0 ]
  run bash "$SCRIPTS/history.sh" testteam
  [[ "$output" =~ "msg1" ]]
  [[ "$output" =~ "msg2" ]]
}

@test "history: a team/agent name containing a quote does not break the query (#87)" {
  bash "$SCRIPTS/join.sh" testteam "o'brien" claude-code /tmp/project-c
  bash "$SCRIPTS/send.sh" testteam alice "o'brien" "for quote"
  run bash "$SCRIPTS/history.sh" testteam "o'brien"
  [ "$status" -eq 0 ]
  [[ "$output" =~ "for quote" ]]
}
