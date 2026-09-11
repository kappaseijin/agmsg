#!/usr/bin/env bats
load test_helper

setup() {
  export STOP_ROOT="$BATS_TEST_TMPDIR/owned-stop"
  mkdir -p "$STOP_ROOT"
  export STOP_HELPERS="$BATS_TEST_DIRNAME"
  cat > "$STOP_ROOT/watcher.sh" <<'SH'
#!/usr/bin/env bash
case "$MODE" in
  ignore) trap '' TERM ;;
  code) trap 'exit 42' TERM ;;
  *) trap 'exit 0' TERM ;;
esac
: > "$STOP_ROOT/ready"
while :; do sleep 1; done
SH
  cat > "$STOP_ROOT/driver.sh" <<'SH'
#!/usr/bin/env bash
source "$STOP_HELPERS/test_helper.bash"
source "$STOP_HELPERS/watch_stop_helper.bash"
export AGMSG_TEST_WAIT_TIMEOUT_S=2
export AGMSG_TEST_WAIT_POLL_S=0.05
bash "$STOP_ROOT/watcher.sh" >"$STOP_ROOT/out" 2>"$STOP_ROOT/err" 3>&- &
w=$!
trap 'kill -KILL "$w" 2>/dev/null || :; wait "$w" 2>/dev/null || :' EXIT
wait_for_file "$STOP_ROOT/ready" || exit 91
_watch_stop_register "$w" "$STOP_ROOT/watcher.sh" "$STOP_ROOT/packet" || exit 92
case "$CASE" in
  exited) kill "$w"; wait "$w" || exit 93 ;;
  foreign) _WATCH_STOP_SCRIPT="$STOP_ROOT/different-root.sh" ;;
  generation) _WATCH_STOP_GENERATION=wrong-generation ;;
  denied) _watch_stop_signal() { return 77; } ;;
esac
start=$SECONDS
rc=0
_watch_stop_owned || rc=$?
printf 'stop_rc=%s elapsed=%s\n' "$rc" "$((SECONDS-start))"
[ "$((SECONDS-start))" -le 8 ] || exit 96
if [ "$CASE" = foreign ] || [ "$CASE" = generation ] || [ "$CASE" = denied ]; then
  kill -0 "$w" || exit 94
  printf 'foreign-or-unknown-survived\n'
else
  _pid_gone "$w" || exit 95
fi
exit "$rc"
SH
}

@test "bounded watch stop: normal and already-exited children are reaped" {
  local mode
  for mode in normal exited; do
    rm -f "$STOP_ROOT/ready"
    run env MODE=normal CASE="$mode" bash "$STOP_ROOT/driver.sh"
    [ "$status" -eq 0 ]
    grep -q 'phase=test-stop-end.*result=0' "$STOP_ROOT/packet"
  done
}

@test "bounded watch stop: an owned child exiting other than 0/143 fails at child-status" {
  # #268: ownership, signal, and wait all succeed; only child_rc=42 is wrong.
  run env MODE=code CASE=normal bash "$STOP_ROOT/driver.sh"
  [ "$status" -eq 1 ]
  printf '%s\n' "$output" | grep -Fq -- "stop_rc=1"
  grep -q 'phase=signal-result.*result=0' "$STOP_ROOT/packet"
  grep -q 'phase=wait-exit.*result=0' "$STOP_ROOT/packet"
  grep -q 'phase=child-status.*result=42' "$STOP_ROOT/packet"
  grep -q 'phase=test-stop-end.*result=1' "$STOP_ROOT/packet"
  grep -q 'phase=ownership-unknown' "$STOP_ROOT/packet" && return 1
  grep -q 'phase=recovery' "$STOP_ROOT/packet" && return 1
  # The failure is reported, not hidden: the packet reaches stderr.
  printf '%s\n' "$output" | grep -q 'phase=child-status.*result=42'
}

@test "bounded watch stop: TERM-ignoring owned tree fails and is recovered" {
  run env MODE=ignore CASE=timeout bash "$STOP_ROOT/driver.sh"
  [ "$status" -eq 1 ]
  printf '%s\n' "$output" | grep -Fq -- "stop_rc=1"
  grep -q 'phase=wait-exit.*result=1' "$STOP_ROOT/packet"
  grep -q 'phase=recovery-end.*result=0' "$STOP_ROOT/packet"
}

@test "bounded watch stop: foreign or changed generation is not signaled" {
  local mode
  for mode in foreign generation; do
    rm -f "$STOP_ROOT/ready"
    run env MODE=normal CASE="$mode" bash "$STOP_ROOT/driver.sh"
    [ "$status" -eq 1 ]
    printf '%s\n' "$output" | grep -Fq -- "foreign-or-unknown-survived"
    grep -q 'phase=ownership-unknown' "$STOP_ROOT/packet"
  done
}

@test "bounded watch stop: signal error remains failure and preserves unknown target" {
  run env MODE=normal CASE=denied bash "$STOP_ROOT/driver.sh"
  [ "$status" -eq 1 ]
  printf '%s\n' "$output" | grep -Fq -- "foreign-or-unknown-survived"
  grep -q 'phase=signal-result.*result=77' "$STOP_ROOT/packet"
}

@test "bounded watch stop: caller failure and live sentinel survive cleanup; suite EOF is separate" {
  run python3 "$BATS_TEST_DIRNAME/watch_stop_caller.py"
  [ "$status" -eq 0 ]
  printf '%s\n' "$output" | grep -Fq -- "caller-control=stop rc=1"
  printf '%s\n' "$output" | grep -Fq -- "caller-control=sentinel rc=1"
  printf '%s\n' "$output" | grep -Fq -- "caller-control=childrc rc=1 child-status=42"
  printf '%s\n' "$output" | grep -Fq -- "Bats/xargs/driver rc=0"
}
