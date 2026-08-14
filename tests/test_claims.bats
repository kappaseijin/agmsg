#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
  bash "$SCRIPTS/join.sh" team alice claude-code /tmp/project-a >/dev/null
  bash "$SCRIPTS/join.sh" team bob claude-code /tmp/project-a >/dev/null
}

teardown() {
  teardown_test_env
}

@test "claim: one owner exclusively claims the next unread message" {
  bash "$SCRIPTS/send.sh" team bob alice "claim payload" >/dev/null

  run bash "$SCRIPTS/claim.sh" next team alice daemon-a 60
  [ "$status" -eq 0 ]
  [ -n "$output" ]
  [ "${output%%$'\x1f'*}" = "1" ]

  run bash "$SCRIPTS/claim.sh" next team alice daemon-b 60
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "claim: a released message is reclaimed by another owner" {
  bash "$SCRIPTS/send.sh" team bob alice "reclaim payload" >/dev/null

  run bash "$SCRIPTS/claim.sh" next team alice daemon-a 60
  [ "$status" -eq 0 ]
  local id="${output%%$'\x1f'*}"

  run bash "$SCRIPTS/claim.sh" release "$id" daemon-a
  [ "$status" -eq 0 ]

  run bash "$SCRIPTS/claim.sh" next team alice daemon-b 60
  [ "$status" -eq 0 ]
  [ "${output%%$'\x1f'*}" = "$id" ]
}

@test "claim: only the owner acknowledges a host handoff" {
  bash "$SCRIPTS/send.sh" team bob alice "ack payload" >/dev/null

  run bash "$SCRIPTS/claim.sh" next team alice daemon-a 60
  [ "$status" -eq 0 ]
  local id="${output%%$'\x1f'*}"

  run bash "$SCRIPTS/claim.sh" ack "$id" daemon-b wrong_owner
  [ "$status" -ne 0 ]
  [ "$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT read_at IS NULL FROM messages WHERE id=$id;")" -eq 1 ]
  [ "$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT COUNT(*) FROM message_receipts WHERE message_id=$id;")" -eq 0 ]

  run bash "$SCRIPTS/claim.sh" ack "$id" daemon-a inline_inbox
  [ "$status" -eq 0 ]
  [ "$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT read_at IS NOT NULL FROM messages WHERE id=$id;")" -eq 1 ]
  [ "$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT owner || ':' || evidence FROM message_receipts WHERE message_id=$id;")" = "daemon-a:inline_inbox" ]
}

@test "claim: an expired lease is reclaimed by another owner" {
  bash "$SCRIPTS/send.sh" team bob alice "expiry payload" >/dev/null

  run bash "$SCRIPTS/claim.sh" next team alice daemon-a 0
  [ "$status" -eq 0 ]
  local id="${output%%$'\x1f'*}"

  run bash "$SCRIPTS/claim.sh" next team alice daemon-b 60
  [ "$status" -eq 0 ]
  [ "${output%%$'\x1f'*}" = "$id" ]
}

@test "claim: next preserves escaped newlines and tabs" {
  bash "$SCRIPTS/send.sh" team bob alice $'line one\nline two\tend' >/dev/null

  run bash "$SCRIPTS/claim.sh" next team alice daemon-a 60
  [ "$status" -eq 0 ]
  [[ "$output" == *$'line one\\nline two\\tend'* ]]
}

@test "claim: a receiver reserves a selected unread message id" {
  bash "$SCRIPTS/send.sh" team bob alice "first payload" >/dev/null
  bash "$SCRIPTS/send.sh" team bob alice "selected payload" >/dev/null
  local selected_id
  selected_id="$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT id FROM messages WHERE body='selected payload';")"

  run bash "$SCRIPTS/claim.sh" claim "$selected_id" daemon-a 60
  [ "$status" -eq 0 ]

  run bash "$SCRIPTS/claim.sh" next team alice daemon-b 60
  [ "$status" -eq 0 ]
  [[ "$output" == *"first payload"* ]]
}

@test "claim: next scopes a reused owner to the requested team and receiver" {
  bash "$SCRIPTS/join.sh" otherteam alice claude-code /tmp/project-b >/dev/null
  bash "$SCRIPTS/join.sh" otherteam bob claude-code /tmp/project-b >/dev/null
  bash "$SCRIPTS/send.sh" team bob alice "first team payload" >/dev/null
  bash "$SCRIPTS/send.sh" otherteam bob alice "other team payload" >/dev/null

  run bash "$SCRIPTS/claim.sh" next team alice shared-daemon 60
  [ "$status" -eq 0 ]
  [[ "$output" == *"first team payload"* ]]

  run bash "$SCRIPTS/claim.sh" next otherteam alice shared-daemon 60
  [ "$status" -eq 0 ]
  [[ "$output" == *"other team payload"* ]]
}
