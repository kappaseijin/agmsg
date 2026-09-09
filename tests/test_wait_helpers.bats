#!/usr/bin/env bats
#
# The bounded condition waits in test_helper.bash decide whether many other
# tests' assertions mean anything. `wait_for_pid_exit` in particular is the
# evidence that a process was killed, so if it can report "gone" for a live
# process, every test that uses it becomes a green that proves nothing — which
# is exactly the failure that was found in the session-end test it replaced.

setup() { load 'test_helper'; }

@test "_pid_gone: reports a live process as alive" {
  sleep 5 &
  local p=$!
  run _pid_gone "$p"
  kill "$p" 2>/dev/null || true
  wait "$p" 2>/dev/null || true
  [ "$status" -ne 0 ]
}

@test "_pid_gone: reports a pid that never existed as gone" {
  # Far above any live pid on the platforms this suite runs on.
  run _pid_gone 4194303
  [ "$status" -eq 0 ]
}

@test "_pid_gone: reports an exited child as gone, zombie or not" {
  sleep 0.1 &
  local p=$!
  wait "$p" 2>/dev/null || true
  run _pid_gone "$p"
  [ "$status" -eq 0 ]
}

@test "_pid_gone: a failed kill -0 that is not ESRCH counts as ALIVE" {
  # The EPERM case cannot be produced portably in-suite, so pin the decision
  # rule itself: anything other than "no such process" must not be read as
  # death. This is the branch that keeps a sandboxed, unsignalable-but-running
  # process from being reported as exited.
  local decided
  decided=$(
    kill() { echo "bash: kill: (1234) - Operation not permitted" >&2; return 1; }
    ps() { return 1; }   # even with no process-table evidence
    _pid_gone 1234 && echo GONE || echo ALIVE
  )
  [ "$decided" = "ALIVE" ]
}

@test "wait_for_pid_exit: returns promptly once the process is gone" {
  sleep 0.2 &
  local p=$!
  run wait_for_pid_exit "$p"
  [ "$status" -eq 0 ]
}

@test "wait_for_pid_exit: times out rather than claiming a live process exited" {
  sleep 30 &
  local p=$!
  local wait_status=0
  if AGMSG_TEST_WAIT_TIMEOUT_S=1 AGMSG_TEST_WAIT_POLL_S=0.1 \
    wait_for_pid_exit "$p"; then
    wait_status=0
  else
    wait_status=$?
  fi
  kill "$p" 2>/dev/null || true
  wait "$p" 2>/dev/null || true
  [ "$wait_status" -eq 1 ]
}

@test "wait_for_file: timeout is a named real-time deadline" {
  local missing="$BATS_TEST_TMPDIR/never"
  local error="$BATS_TEST_TMPDIR/wait.err" target watchdog started elapsed target_status=0

  AGMSG_TEST_WAIT_TIMEOUT_S=1 AGMSG_TEST_WAIT_POLL_S=0.25 \
    bash -c 'source "$1"; wait_for_file "$2"' _ \
    "$BATS_TEST_DIRNAME/test_helper.bash" "$missing" >/dev/null 2>"$error" &
  target=$!
  (
    sleep 4
    kill "$target" 2>/dev/null || true
  ) &
  watchdog=$!
  started=$SECONDS

  wait "$target" || target_status=$?
  elapsed=$((SECONDS - started))
  kill "$watchdog" 2>/dev/null || true
  wait "$watchdog" 2>/dev/null || true

  [ "$target_status" -eq 1 ]
  [ "$elapsed" -lt 4 ]
  [ "$(grep -Fc 'helper=wait_for_file' "$error")" -eq 1 ]
  [ "$(grep -Fc 'predicate=file-present' "$error")" -eq 1 ]
  [ "$(grep -Fc 'timeout_s=1' "$error")" -eq 1 ]
  [ "$(grep -Fc 'poll_s=0.25' "$error")" -eq 1 ]
  [ "$(grep -Fc 'state=absent' "$error")" -eq 1 ]
}

@test "wait_for_file: observes a delayed regular file before deadline" {
  local file="$BATS_TEST_TMPDIR/delayed-file"
  : > "$file.tmp"
  rm -f "$file"
  ( sleep 0.2; mv "$file.tmp" "$file" ) &
  local writer=$!

  AGMSG_TEST_WAIT_TIMEOUT_S=2 wait_for_file "$file"
  wait "$writer" 2>/dev/null || true
  [ -f "$file" ]
}

@test "wait_for_missing: observes a delayed file removal before deadline" {
  local file="$BATS_TEST_TMPDIR/delayed-removal"
  : > "$file"
  ( sleep 0.2; rm -f "$file" ) &
  local remover=$!

  AGMSG_TEST_WAIT_TIMEOUT_S=2 wait_for_missing "$file"
  wait "$remover" 2>/dev/null || true
  [ ! -e "$file" ]
}

@test "wait_for_file_contains: observes delayed content before deadline" {
  local file="$BATS_TEST_TMPDIR/delayed-content"
  : > "$file"
  ( sleep 0.2; printf '%s\n' ready >> "$file" ) &
  local writer=$!

  AGMSG_TEST_WAIT_TIMEOUT_S=2 wait_for_file_contains "$file" ready
  wait "$writer" 2>/dev/null || true
  [ "$(grep -Fc ready "$file")" -eq 1 ]
}

@test "wait_for_pid_exit: observes delayed process exit before deadline" {
  sleep 0.2 &
  local p=$!

  AGMSG_TEST_WAIT_TIMEOUT_S=2 wait_for_pid_exit "$p"
  wait "$p" 2>/dev/null || true
  [ "$(_pid_gone "$p"; echo "$?")" -eq 0 ]
}

@test "wait_for_file_is: observes delayed content replacement before deadline" {
  local file="$BATS_TEST_TMPDIR/content-replacement"
  printf '%s\n' old > "$file"
  ( sleep 0.2; printf '%s\n' new > "$file" ) &
  local writer=$!

  AGMSG_TEST_WAIT_TIMEOUT_S=2 wait_for_file_is "$file" new
  wait "$writer" 2>/dev/null || true
  [ "$(cat "$file")" = new ]
}

@test "wait_for_file: reports safe metadata on timeout" {
  local error="$BATS_TEST_TMPDIR/file-timeout.err"
  local missing="$BATS_TEST_TMPDIR/missing-file" wait_status=0
  if AGMSG_TEST_WAIT_TIMEOUT_S=1 AGMSG_TEST_WAIT_POLL_S=0.1 \
    wait_for_file "$missing" 2>"$error"; then
    wait_status=0
  else
    wait_status=$?
  fi

  [ "$wait_status" -eq 1 ]
  [ "$(grep -Fc 'helper=wait_for_file' "$error")" -eq 1 ]
  [ "$(grep -Fc 'predicate=file-present' "$error")" -eq 1 ]
  [ "$(grep -Fc 'timeout_s=1' "$error")" -eq 1 ]
  [ "$(grep -Fc 'poll_s=0.1' "$error")" -eq 1 ]
  [ "$(grep -Fc 'state=absent' "$error")" -eq 1 ]
  [ "$(grep -Ec 'elapsed_s=[0-9]+' "$error")" -eq 1 ]
  [ "$(grep -Ec 'attempts=[0-9]+' "$error")" -eq 1 ]
}

@test "wait_for_missing: reports still-present on timeout" {
  local error="$BATS_TEST_TMPDIR/missing-timeout.err"
  local file="$BATS_TEST_TMPDIR/present-file" wait_status=0
  : > "$file"
  if AGMSG_TEST_WAIT_TIMEOUT_S=1 AGMSG_TEST_WAIT_POLL_S=0.1 \
    wait_for_missing "$file" 2>"$error"; then
    wait_status=0
  else
    wait_status=$?
  fi

  [ "$wait_status" -eq 1 ]
  [ "$(grep -Fc 'helper=wait_for_missing' "$error")" -eq 1 ]
  [ "$(grep -Fc 'predicate=path-absent' "$error")" -eq 1 ]
  [ "$(grep -Fc 'state=still-present' "$error")" -eq 1 ]
}

@test "wait_for_file_contains: omits the needle from timeout diagnostics" {
  local error="$BATS_TEST_TMPDIR/contains-timeout.err"
  local file="$BATS_TEST_TMPDIR/contains-file" needle=never-this-value wait_status=0
  printf '%s\n' other > "$file"
  if AGMSG_TEST_WAIT_TIMEOUT_S=1 AGMSG_TEST_WAIT_POLL_S=0.1 \
    wait_for_file_contains "$file" "$needle" 2>"$error"; then
    wait_status=0
  else
    wait_status=$?
  fi

  [ "$wait_status" -eq 1 ]
  [ "$(grep -Fc 'helper=wait_for_file_contains' "$error")" -eq 1 ]
  [ "$(grep -Fc 'predicate=file-contains' "$error")" -eq 1 ]
  [ "$(grep -Fc 'state=needle-not-observed' "$error")" -eq 1 ]
  [ "$(grep -Fc "$needle" "$error")" -eq 0 ]
}

@test "wait_for_pid_exit: reports safe process state on timeout" {
  local error="$BATS_TEST_TMPDIR/pid-timeout.err"
  local p wait_status=0
  sleep 30 &
  p=$!
  if AGMSG_TEST_WAIT_TIMEOUT_S=1 AGMSG_TEST_WAIT_POLL_S=0.1 \
    wait_for_pid_exit "$p" 2>"$error"; then
    wait_status=0
  else
    wait_status=$?
  fi
  kill "$p" 2>/dev/null || true
  wait "$p" 2>/dev/null || true

  [ "$wait_status" -eq 1 ]
  [ "$(grep -Fc 'helper=wait_for_pid_exit' "$error")" -eq 1 ]
  [ "$(grep -Fc 'predicate=pid-gone' "$error")" -eq 1 ]
  [ "$(grep -Fc 'state=not-gone' "$error")" -eq 1 ]
  [ "$(grep -Fc 'process_stat=' "$error")" -eq 1 ]
  [ "$(grep -Fc 'sleep 30' "$error")" -eq 0 ]
}

@test "wait_for_file_is: omits expected content from timeout diagnostics" {
  local error="$BATS_TEST_TMPDIR/file-is-timeout.err"
  local file="$BATS_TEST_TMPDIR/file-is" expected=expected-only wait_status=0
  printf '%s\n' actual > "$file"
  if AGMSG_TEST_WAIT_TIMEOUT_S=1 AGMSG_TEST_WAIT_POLL_S=0.1 \
    wait_for_file_is "$file" "$expected" 2>"$error"; then
    wait_status=0
  else
    wait_status=$?
  fi

  [ "$wait_status" -eq 1 ]
  [ "$(grep -Fc 'helper=wait_for_file_is' "$error")" -eq 1 ]
  [ "$(grep -Fc 'predicate=file-content' "$error")" -eq 1 ]
  [ "$(grep -Fc 'state=content-mismatch' "$error")" -eq 1 ]
  [ "$(grep -Fc "$expected" "$error")" -eq 0 ]
}

@test "wait_for_file / wait_for_missing / wait_for_file_is agree with the filesystem" {
  local f="$BATS_TEST_TMPDIR/probe"
  run wait_for_file "$f"
  [ "$status" -ne 0 ] || false   # absent file must not be reported present

  echo "42" > "$f"
  run wait_for_file "$f"
  [ "$status" -eq 0 ]
  run wait_for_file_is "$f" "42"
  [ "$status" -eq 0 ]
  run wait_for_file_is "$f" "43"
  [ "$status" -ne 0 ]

  rm -f "$f"
  run wait_for_missing "$f"
  [ "$status" -eq 0 ]
}
