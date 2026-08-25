#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
  export FIXTURES="$BATS_TEST_DIRNAME/fixtures/pane-liveness"
  export FAKE_BIN="$TEST_SKILL_DIR/fake-bin"
  export HERDR_ARGS_LOG="$TEST_SKILL_DIR/herdr-args.log"
  mkdir -p "$FAKE_BIN"
  write_fake_herdr
}

teardown() {
  teardown_test_env
}

write_fake_herdr() {
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'printf "%s\\n" "$*" >> "$AGMSG_TEST_HERDR_ARGS_LOG"' \
    'if [ "${1:-}" != pane ] || [ "${2:-}" != read ]; then exit 64; fi' \
    'if [ "$#" -ne 3 ]; then exit 65; fi' \
    'if [ "${AGMSG_TEST_HERDR_FAIL:-0}" = 1 ]; then exit 66; fi' \
    'if [ "${AGMSG_TEST_EMPTY:-0}" = 1 ]; then exit 0; fi' \
    'cat "$AGMSG_TEST_PANE_FIXTURE"' > "$FAKE_BIN/herdr"
  chmod +x "$FAKE_BIN/herdr"
}

run_liveness() {
  local fixture="$1"
  export AGMSG_TEST_PANE_FIXTURE="$FIXTURES/$fixture"
  unset AGMSG_TEST_HERDR_FAIL AGMSG_TEST_EMPTY
  : > "$HERDR_ARGS_LOG"
  run env PATH="$FAKE_BIN:$PATH" AGMSG_TEST_HERDR_ARGS_LOG="$HERDR_ARGS_LOG" \
    bash "$SCRIPTS/pane-liveness.sh" workspace-1 pane-1
}

assert_default_read() {
  [ "$(cat "$HERDR_ARGS_LOG")" = "pane read pane-1" ]
}

pane_output_contains() {
  printf '%s\n' "$output" | grep -qF -- "$1"
}

pane_output_absent() {
  ! printf '%s\n' "$output" | grep -qF -- "$1"
}

@test "pane-liveness: captured resume failure is crashed" {
  run_liveness crashed-pane.txt
  [ "$status" -eq 0 ]
  pane_output_contains "pane_liveness=crashed"
  assert_default_read
}

@test "pane-liveness: queued follow-up input is live" {
  run_liveness queued-live-pane.txt
  [ "$status" -eq 0 ]
  pane_output_contains "pane_liveness=live"
  pane_output_absent "pane_liveness=crashed"
  assert_default_read
}

@test "pane-liveness: quoted crash terms in history do not crash a healthy pane" {
  run_liveness quoted-crash-terms-pane.txt
  [ "$status" -eq 0 ]
  pane_output_contains "pane_liveness=live"
  pane_output_absent "pane_liveness=crashed"
  assert_default_read
}

@test "pane-liveness: quiet healthy pane is unknown" {
  run_liveness quiet-pane.txt
  [ "$status" -eq 0 ]
  pane_output_contains "pane_liveness=unknown"
  pane_output_absent "pane_liveness=crashed"
  assert_default_read
}

@test "pane-liveness: unreadable pane is unknown" {
  export AGMSG_TEST_PANE_FIXTURE="$FIXTURES/quiet-pane.txt"
  export AGMSG_TEST_HERDR_FAIL=1
  : > "$HERDR_ARGS_LOG"
  run env PATH="$FAKE_BIN:$PATH" AGMSG_TEST_HERDR_ARGS_LOG="$HERDR_ARGS_LOG" \
    bash "$SCRIPTS/pane-liveness.sh" workspace-1 pane-1
  [ "$status" -eq 0 ]
  pane_output_contains "pane_liveness=unknown"
  assert_default_read
}

@test "pane-liveness: zero-byte read is unknown" {
  export AGMSG_TEST_PANE_FIXTURE="$FIXTURES/quiet-pane.txt"
  export AGMSG_TEST_EMPTY=1
  : > "$HERDR_ARGS_LOG"
  run env PATH="$FAKE_BIN:$PATH" AGMSG_TEST_HERDR_ARGS_LOG="$HERDR_ARGS_LOG" \
    bash "$SCRIPTS/pane-liveness.sh" workspace-1 pane-1
  [ "$status" -eq 0 ]
  pane_output_contains "pane_liveness=unknown"
  pane_output_absent "pane_liveness=crashed"
  assert_default_read
}
