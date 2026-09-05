#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

load test_helper

setup() {
  setup_test_env
}

teardown() {
  teardown_test_env
}

json_value() {
  local object="$1" path="$2" escaped
  escaped="$(printf '%s' "$object" | sed "s/'/''/g")"
  sqlite_mem "SELECT json_extract('$escaped', '\$.$path');"
}

json_array() {
  local object="$1" path="$2" escaped
  escaped="$(printf '%s' "$object" | sed "s/'/''/g")"
  sqlite_mem "SELECT value FROM json_each('$escaped', '\$.$path');"
}

write_config() {
  local team="$1" body="$2"
  mkdir -p "$TEST_SKILL_DIR/teams/$team"
  printf '%s\n' "$body" >"$TEST_SKILL_DIR/teams/$team/config.json"
}

api_registrations() {
  bash "$SCRIPTS/api.sh" get teams "$1" registrations --schema-version 1
}

@test "registrations: one tuple is returned in a complete envelope" {
  bash "$SCRIPTS/join.sh" demo alice codex /tmp/project-a >/dev/null

  run api_registrations demo
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" resource)" = "registrations" ]
  [ "$(json_value "$output" status)" = "ok" ]
  [ "$(json_value "$output" complete)" = "1" ]
  [ "$(json_value "$output" reason)" = "" ]
  [ "$(sqlite_mem "SELECT json_array_length('$(printf '%s' "$output" | sed "s/'/''/g")', '\$.registrations');")" -eq 1 ]
  [ "$(json_value "$(json_array "$output" registrations)" canonicalProject)" = "/tmp/project-a" ]
}

@test "registrations: multiple projects and types preserve pairs without a cartesian product" {
  write_config demo '{"schemaVersion":1,"name":"demo","agents":{"alice":{"kind":"seat","role":"worker","registrations":[{"type":"codex","project":"/tmp/project-z"},{"type":"claude-code","project":"/tmp/project-a"}]}}}'

  run api_registrations demo
  [ "$status" -eq 0 ]
  local escaped count bad_pair
  escaped="$(printf '%s' "$output" | sed "s/'/''/g")"
  count="$(sqlite_mem "SELECT json_array_length('$escaped', '\$.registrations');")"
  [ "$count" -eq 2 ]
  bad_pair="$(sqlite_mem "SELECT count(*) FROM json_each('$escaped', '\$.registrations') WHERE (json_extract(value, '\$.type') = 'claude-code' AND json_extract(value, '\$.project') = '/tmp/project-z') OR (json_extract(value, '\$.type') = 'codex' AND json_extract(value, '\$.project') = '/tmp/project-a');")"
  [ "$bad_pair" -eq 0 ]
}

@test "registrations: duplicate tuples remain visible" {
  write_config demo '{"schemaVersion":1,"name":"demo","agents":{"alice":{"kind":"seat","role":"worker","registrations":[{"type":"codex","project":"/tmp/project-a"},{"type":"codex","project":"/tmp/project-a"}]}}}'

  run api_registrations demo
  [ "$status" -eq 0 ]
  [ "$(sqlite_mem "SELECT json_array_length('$(printf '%s' "$output" | sed "s/'/''/g")', '\$.registrations');")" -eq 2 ]
}

@test "registrations: empty existing team is distinct from a missing team" {
  write_config demo '{"schemaVersion":1,"name":"demo","agents":{}}'
  run api_registrations demo
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" complete)" = "1" ]
  [ "$(sqlite_mem "SELECT json_array_length('$(printf '%s' "$output" | sed "s/'/''/g")', '\$.registrations');")" -eq 0 ]

  run --separate-stderr api_registrations ghost
  [ "$status" -eq 1 ]
  [ "$(json_value "$output" status)" = "not_found" ]
  [ "$(json_value "$output" reason)" = "team_not_found" ]
  [ "$(json_value "$output" complete)" = "0" ]
  [ "$(json_value "$output" registrations)" = "" ]
}

@test "registrations: supported legacy direct fields are normalized" {
  write_config demo '{"name":"demo","agents":{"alice":{"type":"codex","project":"/tmp/project-a"}}}'

  run api_registrations demo
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" status)" = "ok" ]
  [ "$(sqlite_mem "SELECT json_array_length('$(printf '%s' "$output" | sed "s/'/''/g")', '\$.registrations');")" -eq 1 ]
}

@test "registrations: unsupported storage schema fails closed" {
  write_config demo '{"schemaVersion":2,"name":"demo","agents":{}}'

  run --separate-stderr api_registrations demo
  [ "$status" -eq 1 ]
  [ "$(json_value "$output" status)" = "unknown" ]
  [ "$(json_value "$output" reason)" = "storage_schema_unsupported" ]
  [ "$(json_value "$output" complete)" = "0" ]
  [ "$(json_value "$output" registrations)" = "" ]
}

@test "registrations: corruption does not become an empty successful array" {
  write_config demo '{"schemaVersion":1,"name":"demo","agents":{"alice":{"kind":"seat","role":"worker","registrations":[{"type":"codex","project":"/tmp/project-a"}]},"bob":{"kind":"seat","role":"worker","registrations":[{"type":"codex"}]}}}'

  run --separate-stderr api_registrations demo
  [ "$status" -eq 1 ]
  [ "$(json_value "$output" status)" = "unknown" ]
  [ "$(json_value "$output" reason)" = "data_invalid" ]
  [ "$(json_value "$output" registrations)" = "" ]
}

@test "registrations: corrupting another team does not affect the target" {
  write_config demo '{"schemaVersion":1,"name":"demo","agents":{}}'
  write_config broken '{"schemaVersion":1,"name":"broken","agents": [}'

  run api_registrations demo
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" status)" = "ok" ]
  [ "$(json_value "$output" complete)" = "1" ]
}

@test "registrations: invalid API version and options are rejected" {
  write_config demo '{"schemaVersion":1,"name":"demo","agents":{}}'

  run --separate-stderr bash "$SCRIPTS/api.sh" get teams demo registrations
  [ "$status" -eq 2 ]
  [ "$(json_value "$output" reason)" = "invalid_argument" ]

  run --separate-stderr bash "$SCRIPTS/api.sh" get teams demo registrations --schema-version 2
  [ "$status" -eq 2 ]
  [ "$(json_value "$output" reason)" = "unsupported_schema_version" ]

  run --separate-stderr bash "$SCRIPTS/api.sh" get teams demo registrations --schema-version 1 --extra
  [ "$status" -eq 2 ]
  [ "$(json_value "$output" reason)" = "invalid_argument" ]
}

@test "registrations: source exchange is reported instead of returning mixed data" {
  write_config demo '{"schemaVersion":1,"name":"demo","agents":{"alice":{"kind":"seat","role":"worker","registrations":[{"type":"codex","project":"/tmp/project-a"}]}}}'
  local barrier="$BATS_TEST_TMPDIR/registrations-barrier"
  export AGMSG_TEST_API_REGISTRATIONS_BARRIER="$barrier"
  local output_file="$BATS_TEST_TMPDIR/api-output"
  local status_file="$BATS_TEST_TMPDIR/api-status"
  (api_registrations demo >"$output_file" 2>/dev/null; echo "$?" >"$status_file") &
  local api_pid=$!
  while [ ! -e "$barrier.reached" ]; do sleep 0.01; done
  write_config demo '{"schemaVersion":1,"name":"demo","agents":{"alice":{"kind":"seat","role":"worker","registrations":[{"type":"codex","project":"/tmp/project-b"}]}}}'
  : >"$barrier.release"
  local api_status=0
  wait "$api_pid" || api_status=$?
  [ "$api_status" -eq 1 ]
  local result; result="$(cat "$output_file")"
  [ "$(json_value "$result" status)" = "unknown" ]
  [ "$(json_value "$result" reason)" = "concurrent_change" ]
  [ "$(json_value "$result" registrations)" = "" ]
}

@test "registrations: query is read-only" {
  write_config demo '{"schemaVersion":1,"name":"demo","agents":{"alice":{"kind":"seat","role":"worker","registrations":[{"type":"codex","project":"/tmp/project-a"}]}}}'
  local config="$TEST_SKILL_DIR/teams/demo/config.json"
  local before after before_db after_db before_run after_run
  before="$(shasum -a 256 "$config")"
  before_db="$(shasum -a 256 "$TEST_SKILL_DIR/db/messages.db")"
  before_run="$(find "$TEST_SKILL_DIR/run" -type f -print | sort)"
  run api_registrations demo
  [ "$status" -eq 0 ]
  after="$(shasum -a 256 "$config")"
  after_db="$(shasum -a 256 "$TEST_SKILL_DIR/db/messages.db")"
  after_run="$(find "$TEST_SKILL_DIR/run" -type f -print | sort)"
  [ "$before" = "$after" ]
  [ "$before_db" = "$after_db" ]
  [ "$before_run" = "$after_run" ]
}
