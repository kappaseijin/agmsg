#!/usr/bin/env bats

bats_require_minimum_version 1.5.0
load test_helper

setup() {
  setup_test_env
  bash "$SCRIPTS/join.sh" testteam alice claude-code /tmp/project-a
}

teardown() {
  if [ -n "${OWNER_PID:-}" ]; then
    kill "$OWNER_PID" 2>/dev/null || true
    wait "$OWNER_PID" 2>/dev/null || true
  fi
  if [ -n "${SECOND_OWNER_PID:-}" ]; then
    kill "$SECOND_OWNER_PID" 2>/dev/null || true
    wait "$SECOND_OWNER_PID" 2>/dev/null || true
  fi
  teardown_test_env
}

json_field() {
  local escaped
  escaped="$(printf %s "$1" | sed "s/'/''/g")"
  sqlite_mem "SELECT json_extract('$escaped', '\$.$2');"
}

json_type() {
  local escaped
  escaped="$(printf %s "$1" | sed "s/'/''/g")"
  sqlite_mem "SELECT json_type('$escaped', '\$.$2');"
}

claim_path() {
  # shellcheck disable=SC1091
  source "$SCRIPTS/lib/actas-lock.sh"
  actas_lock_path testteam alice
}

write_claim() {
  local value="$1"
  printf '%s\n' "$value" > "$(claim_path)"
}

write_claim_legacy() {
  local value="$1"
  printf '%s' "$value" > "$(claim_path)"
}

start_live_owner() {
  sleep 30 &
  OWNER_PID=$!
  printf 'writer-sid.%s\n' "$OWNER_PID" > "$RUN_DIR/cc-instance.$OWNER_PID"
  OWNER_TOKEN="writer-sid.$OWNER_PID"
}

@test "api actas-owner reports an alive formal writer without changing state" {
  start_live_owner
  write_claim "$OWNER_TOKEN"

  before_config="$(shasum "$TEST_SKILL_DIR/teams/testteam/config.json")"
  before_run="$(find "$RUN_DIR" -type f -print | sort)"
  before_db="$(shasum "$DBPATH")"
  run --separate-stderr bash "$SCRIPTS/api.sh" get teams testteam actas-owner alice --schema-version 1

  [ "$status" -eq 0 ]
  [ "$(printf '%s\n' "$output" | wc -l | tr -d ' ')" -eq 1 ]
  [ "$(json_field "$output" status)" = owned ]
  [ "$(json_field "$output" reason)" = '' ]
  [ "$(json_field "$output" owner)" = "$OWNER_TOKEN" ]
  [ "$(json_field "$output" ownerKind)" = composite ]
  [ "$(json_field "$output" liveness)" = alive ]
  [ "$(json_field "$output" consistency)" = observed ]
  [ "$(json_type "$output" owner)" = text ]
  [ -z "$stderr" ]
  [ "$(shasum "$TEST_SKILL_DIR/teams/testteam/config.json")" = "$before_config" ]
  [ "$(find "$RUN_DIR" -type f -print | sort)" = "$before_run" ]
  [ "$(shasum "$DBPATH")" = "$before_db" ]
}

@test "api actas-owner distinguishes absent claim from missing target" {
  run bash "$SCRIPTS/api.sh" get teams testteam actas-owner alice --schema-version 1
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" status)" = absent ]
  [ "$(json_field "$output" reason)" = claim_absent ]
  [ "$(json_type "$output" owner)" = null ]
  [ "$(json_type "$output" liveness)" = null ]

  run --separate-stderr bash "$SCRIPTS/api.sh" get teams testteam actas-owner ghost --schema-version 1
  [ "$status" -eq 1 ]
  [ "$(json_field "$output" status)" = not_found ]
  [ "$(json_field "$output" reason)" = target_not_found ]
  [ -z "$stderr" ]
}

@test "api actas-owner preserves a composite owner token and reports a dead owner as stale" {
  start_live_owner
  write_claim "$OWNER_TOKEN"
  run bash "$SCRIPTS/api.sh" get teams testteam actas-owner alice --schema-version 1
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" owner)" = "$OWNER_TOKEN" ]
  [ "$(json_field "$output" owner)" != "${OWNER_TOKEN%.*}" ]

  kill "$OWNER_PID"
  wait "$OWNER_PID" 2>/dev/null || true
  unset OWNER_PID
  run bash "$SCRIPTS/api.sh" get teams testteam actas-owner alice --schema-version 1
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" status)" = stale ]
  [ "$(json_field "$output" reason)" = owner_dead ]
  [ "$(json_field "$output" liveness)" = dead ]
  [ -f "$(claim_path)" ]
}

@test "api actas-owner accepts a legacy bare owner without a trailing newline" {
  write_claim_legacy 'legacy-sid'
  run bash "$SCRIPTS/api.sh" get teams testteam actas-owner alice --schema-version 1
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" status)" = stale ]
  [ "$(json_field "$output" reason)" = owner_dead ]
  [ "$(json_field "$output" owner)" = legacy-sid ]
  [ "$(json_field "$output" ownerKind)" = legacy ]
  [ "$(json_field "$output" liveness)" = dead ]
}

@test "api actas-owner rejects malformed or nonregular claims" {
  lock="$(claim_path)"
  for kind in empty extra extra-blank invalid-byte nul directory symlink; do
    if [ -d "$lock" ] && [ ! -L "$lock" ]; then
      rmdir "$lock"
    else
      rm -f "$lock"
    fi
    case "$kind" in
      empty) : > "$lock" ;;
      extra) printf 'sid\nsecond\n' > "$lock" ;;
      extra-blank) printf 'sid\n\n' > "$lock" ;;
      invalid-byte) printf '\377' > "$lock" ;;
      nul) printf 'sid\0' > "$lock" ;;
      directory) mkdir "$lock" ;;
      symlink) ln -s "$RUN_DIR/other-claim" "$lock" ;;
    esac
    run --separate-stderr bash "$SCRIPTS/api.sh" get teams testteam actas-owner alice --schema-version 1
    [ "$status" -eq 1 ]
    [ "$(json_field "$output" status)" = unknown ]
    [ "$(json_field "$output" reason)" = claim_invalid ]
    [ "$(json_type "$output" owner)" = null ]
    [ "$(json_type "$output" liveness)" = null ]
    [ -z "$stderr" ]
  done
}

@test "api actas-owner fails closed when liveness cannot be observed" {
  start_live_owner
  write_claim "$OWNER_TOKEN"
  AGMSG_TEST_API_ACTAS_OWNER_LIVENESS=unknown run --separate-stderr \
    bash "$SCRIPTS/api.sh" get teams testteam actas-owner alice --schema-version 1
  [ "$status" -eq 1 ]
  [ "$(json_field "$output" status)" = unknown ]
  [ "$(json_field "$output" reason)" = liveness_unavailable ]
  [ "$(json_type "$output" owner)" = null ]
  [ -z "$stderr" ]
}

@test "api actas-owner distinguishes unreadable parents from normal absence" {
  chmod 000 "$TEST_SKILL_DIR/teams/testteam"
  run --separate-stderr bash "$SCRIPTS/api.sh" get teams testteam actas-owner alice --schema-version 1
  chmod 755 "$TEST_SKILL_DIR/teams/testteam"
  [ "$status" -eq 1 ]
  [ "$(json_field "$output" status)" = unknown ]
  [ "$(json_field "$output" reason)" = read_failed ]
  [ -z "$stderr" ]

  chmod 000 "$RUN_DIR"
  run --separate-stderr bash "$SCRIPTS/api.sh" get teams testteam actas-owner alice --schema-version 1
  chmod 755 "$RUN_DIR"
  [ "$status" -eq 1 ]
  [ "$(json_field "$output" status)" = unknown ]
  [ "$(json_field "$output" reason)" = read_failed ]
  [ -z "$stderr" ]
}

@test "api actas-owner reports a claim exchange during observation as concurrent" {
  barrier="$TEST_SKILL_DIR/owner-barrier"
  output_file="$TEST_SKILL_DIR/owner-output"
  rc_file="$TEST_SKILL_DIR/owner-rc"
  export AGMSG_TEST_API_ACTAS_OWNER_BARRIER="$barrier"
  (set +e; bash "$SCRIPTS/api.sh" get teams testteam actas-owner alice --schema-version 1 > "$output_file" 2>/dev/null; rc=$?; echo "$rc" > "$rc_file") &
  query_pid=$!
  while [ ! -e "${barrier}.reached" ]; do sleep 0.01; done
  write_claim_legacy 'replacement-owner'
  : > "${barrier}.release"
  wait "$query_pid" || true

  [ "$(cat "$rc_file")" -eq 1 ]
  output="$(cat "$output_file")"
  [ "$(json_field "$output" status)" = unknown ]
  [ "$(json_field "$output" reason)" = concurrent_change ]
  [ "$(json_type "$output" owner)" = null ]
}

@test "api actas-owner reports a registration exchange during observation as concurrent" {
  barrier="$TEST_SKILL_DIR/registration-barrier"
  output_file="$TEST_SKILL_DIR/registration-output"
  rc_file="$TEST_SKILL_DIR/registration-rc"
  config="$TEST_SKILL_DIR/teams/testteam/config.json"
  changed="$TEST_SKILL_DIR/changed-config.json"
  export AGMSG_TEST_API_ACTAS_OWNER_BARRIER="$barrier"
  (set +e; bash "$SCRIPTS/api.sh" get teams testteam actas-owner alice --schema-version 1 > "$output_file" 2>/dev/null; rc=$?; echo "$rc" > "$rc_file") &
  query_pid=$!
  while [ ! -e "${barrier}.reached" ]; do sleep 0.01; done
  sed 's#project-a#project-b#' "$config" > "$changed"
  mv "$changed" "$config"
  : > "${barrier}.release"
  wait "$query_pid" || true

  [ "$(cat "$rc_file")" -eq 1 ]
  output="$(cat "$output_file")"
  [ "$(json_field "$output" status)" = unknown ]
  [ "$(json_field "$output" reason)" = concurrent_change ]
  [ "$(json_type "$output" owner)" = null ]
}

@test "api actas-owner distinguishes corrupt registration from an unregistered target" {
  config="$TEST_SKILL_DIR/teams/testteam/config.json"
  good_config="$TEST_SKILL_DIR/good-config.json"
  cp "$config" "$good_config"
  printf '%s\n' '{"schemaVersion":1,"agents":{"alice":{"registrations":"corrupt"}}}' > "$config"
  run --separate-stderr bash "$SCRIPTS/api.sh" get teams testteam actas-owner alice --schema-version 1
  [ "$status" -eq 1 ]
  [ "$(json_field "$output" status)" = unknown ]
  [ "$(json_field "$output" reason)" = read_failed ]
  [ -z "$stderr" ]

  cp "$good_config" "$config"
  run bash "$SCRIPTS/api.sh" get teams testteam actas-owner ghost --schema-version 1
  [ "$status" -eq 1 ]
  [ "$(json_field "$output" status)" = not_found ]
  [ "$(json_field "$output" reason)" = target_not_found ]
}

@test "api actas-owner rejects unsupported versions and malformed arguments" {
  run --separate-stderr bash "$SCRIPTS/api.sh" get teams testteam actas-owner alice --schema-version 2
  [ "$status" -eq 2 ]
  [ "$(json_field "$output" status)" = error ]
  [ "$(json_field "$output" reason)" = unsupported_schema_version ]
  [ -z "$stderr" ]

  run --separate-stderr bash "$SCRIPTS/api.sh" get teams testteam actas-owner alice
  [ "$status" -eq 2 ]
  [ "$(json_field "$output" status)" = error ]
  [ "$(json_field "$output" reason)" = invalid_argument ]
  [ -z "$stderr" ]
}
