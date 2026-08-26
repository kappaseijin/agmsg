#!/usr/bin/env bats

load test_helper

setup() {
  local requested_fanout="${AGMSG_TEST_P8_FANOUT_CHILDREN:-10}"
  setup_test_env
  case "$requested_fanout" in
    10|40) export AGMSG_TEST_P8_FANOUT_CHILDREN="$requested_fanout" ;;
    *)
      printf 'test setup failure: AGMSG_TEST_P8_FANOUT_CHILDREN must be 10 or 40 (got %s)\n' \
        "$requested_fanout" >&2
      return 2
      ;;
  esac
}

teardown() {
  teardown_test_env
}

utf8_locale_name() {
  locale -a 2>/dev/null | LC_ALL=C awk 'tolower($0) ~ /utf[._-]?8/ { print; exit }'
}

@test "storage: SQLite CR stripping is byte-safe under a UTF-8 locale" {
  source "$SCRIPTS/lib/storage.sh"
  local locale_name malformed_sql normal_sql import_file direct_out stdin_out normal_out error_file
  locale_name="$(utf8_locale_name)"
  [ -n "$locale_name" ] || skip "no UTF-8 locale installed"

  malformed_sql="SELECT CAST(X'FF0D41FF' AS TEXT);"
  normal_sql="SELECT 'before'||char(13)||'after';"
  import_file="$BATS_TEST_TMPDIR/malformed-import.jsonl"
  direct_out="$BATS_TEST_TMPDIR/direct.out"
  stdin_out="$BATS_TEST_TMPDIR/stdin.out"
  normal_out="$BATS_TEST_TMPDIR/normal.out"
  error_file="$BATS_TEST_TMPDIR/stderr"

  printf '{"type":"message_sent","id":"import-utf8","team":"testteam","from":"alice","to":"bob","body":"\xff\\rY","at":"2026-08-25T00:00:00Z"}\n' > "$import_file"

  run env LANG="$locale_name" LC_ALL="$locale_name" bash -c '
    set -e
    exec 2>"$8"
    source "$1/lib/storage.sh"
    agmsg_storage_load
    storage_init testteam >/dev/null
    _sqlite_data testteam "$2" > "$3"
    _sqlite_data_stdin testteam "$2" > "$4"
    _sqlite_data testteam "$5" > "$6"
    storage_import testteam "$7"
  ' _ "$SCRIPTS" "$malformed_sql" "$direct_out" "$stdin_out" "$normal_sql" "$normal_out" "$import_file" "$error_file"
  [ "$status" -eq 0 ]

  [ "$(LC_ALL=C od -An -tx1 "$direct_out" | tr -d ' \n')" = "ff41ff0a" ]
  [ "$(LC_ALL=C od -An -tx1 "$stdin_out" | tr -d ' \n')" = "ff41ff0a" ]
  [ "$(LC_ALL=C od -An -tx1 "$normal_out" | tr -d ' \n')" = "6265666f726561667465720a" ]
  [ "$(LC_ALL=C grep -c 'Illegal byte sequence' "$error_file")" -eq 0 ]
  [ "$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT COUNT(*) FROM events WHERE id='import-utf8';")" -eq 1 ]
}

# --- agmsg_db_path() resolution ---

@test "storage: default path resolves under the skill dir" {
  source "$SCRIPTS/lib/storage.sh"
  unset AGMSG_STORAGE_PATH
  [ "$(agmsg_db_path demo)" = "$TEST_SKILL_DIR/db/messages.db" ]
}

@test "storage: AGMSG_STORAGE_PATH overrides the storage dir" {
  source "$SCRIPTS/lib/storage.sh"
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/store"
  [ "$(agmsg_db_path demo)" = "$BATS_TEST_TMPDIR/store/messages.db" ]
}

@test "storage: trailing slash on the override is normalized" {
  source "$SCRIPTS/lib/storage.sh"
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/store/"
  [ "$(agmsg_db_path demo)" = "$BATS_TEST_TMPDIR/store/messages.db" ]
}

# --- agmsg_db_path() Windows path conversion (#197) ---

@test "storage: agmsg_db_path applies cygpath -m on Windows so sqlite3.exe can open it (#197)" {
  # The native sqlite3.exe cannot open a Git Bash /c/... path; cygpath -m maps it
  # to the mixed C:/... form both the shell and sqlite3.exe accept. cygpath is
  # absent off Windows, so inject a shim on PATH to exercise the branch.
  local bindir="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bindir"
  cat > "$bindir/cygpath" <<'SH'
#!/usr/bin/env bash
# Minimal stand-in: `cygpath -m /c/x` -> C:/x (BSD- and GNU-sed portable).
shift  # drop the -m flag
printf '%s\n' "$1" | sed -E 's#^/c/#C:/#'
SH
  chmod +x "$bindir/cygpath"
  run env PATH="$bindir:$PATH" AGMSG_STORAGE_PATH="/c/Users/test/db" \
    bash -c 'source "'"$SCRIPTS"'/lib/storage.sh"; agmsg_db_path demo'
  [ "$status" -eq 0 ]
  [ "$output" = "C:/Users/test/db/messages.db" ]
}

@test "storage: agmsg_db_path is a no-op without cygpath (off Windows)" {
  source "$SCRIPTS/lib/storage.sh"
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/store"
  # cygpath is absent on the test host, so the path is returned unchanged.
  [ "$(agmsg_db_path demo)" = "$BATS_TEST_TMPDIR/store/messages.db" ]
}

# --- init-db.sh honoring the override ---

@test "storage: init-db creates the db at the overridden path (and makes the dir)" {
  local custom="$BATS_TEST_TMPDIR/nested/store"
  [ ! -d "$custom" ]
  AGMSG_STORAGE_PATH="$custom" bash "$SCRIPTS/internal/init-db.sh"
  [ -f "$custom/messages.db" ]
}

# --- end-to-end roundtrip through the override ---

@test "storage: send and inbox share the overridden db" {
  bash "$SCRIPTS/join.sh" testteam alice claude-code /tmp/project-a
  bash "$SCRIPTS/join.sh" testteam bob claude-code /tmp/project-b
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/store"
  bash "$SCRIPTS/send.sh" testteam alice bob "hi via override"
  [ -f "$AGMSG_STORAGE_PATH/messages.db" ]

  run bash "$SCRIPTS/inbox.sh" testteam bob
  [ "$status" -eq 0 ]
  [[ "$output" =~ "hi via override" ]]
}

@test "storage: stop-hook delivery works when the default db dir is absent but the override is populated" {
  local store="$BATS_TEST_TMPDIR/store"
  local project="/tmp/agmsg-storage-test-proj"

  # Register an agent so check-inbox can resolve identity via whoami.
  bash "$SCRIPTS/join.sh" testteam alice claude-code "$project"
  bash "$SCRIPTS/join.sh" testteam bob claude-code /tmp/agmsg-storage-test-bob

  # A message addressed to alice lives only in the overridden store.
  AGMSG_STORAGE_PATH="$store" bash "$SCRIPTS/send.sh" testteam bob alice "via override store"

  # Simulate a clean install whose default skill db dir never existed.
  rm -rf "$TEST_SKILL_DIR/db"

  # Stop-hook delivery must still succeed (exit 0) and surface the message —
  # the cooldown marker now lives in run/, not the (absent) db dir.
  run bash -c "echo '{}' | AGMSG_STORAGE_PATH='$store' bash '$SCRIPTS/check-inbox.sh' claude-code '$project'"
  [ "$status" -eq 0 ]
  [[ "$output" =~ "via override store" ]]
}

@test "storage: default db is untouched when the override is set" {
  # The default store was initialized in setup; writing through an override
  # must not add rows to it.
  bash "$SCRIPTS/join.sh" testteam alice claude-code /tmp/project-a
  bash "$SCRIPTS/join.sh" testteam bob claude-code /tmp/project-b
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/store"
  bash "$SCRIPTS/send.sh" testteam alice bob "isolated"

  local default_count
  default_count=$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT COUNT(*) FROM messages;")
  [ "$default_count" -eq 0 ]
}

@test "storage: agmsg_sqlite sets a busy timeout without polluting output" {
  # .timeout (not PRAGMA) so the timeout value is never echoed into results.
  source "$SCRIPTS/lib/storage.sh"
  run agmsg_sqlite ":memory:" "SELECT 'only-this';"
  [ "$status" -eq 0 ]
  [ "$output" = "only-this" ]
}

@test "storage: agmsg_sqlite emits a raw char(31) separator, not caret '^_' (#102)" {
  # sqlite3 >= 3.50 renders control bytes with caret notation by default, which
  # would turn the char(31) record separator into the two chars "^_" and break
  # the IFS=$'\x1f' field splitting in inbox/history/check-inbox + the watch
  # stream. agmsg_sqlite must pass -escape off so the byte stays raw. On older
  # sqlite3 the byte is raw anyway, so this holds on every supported version.
  source "$SCRIPTS/lib/storage.sh"
  run agmsg_sqlite ":memory:" "SELECT 'a'||char(31)||'b';"
  [ "$status" -eq 0 ]
  printf '%s' "$output" | grep -q $'\x1f'
  ! printf '%s' "$output" | grep -q '\^_'
}

@test "storage: runtime lock replacement is compare-and-swap" {
  source "$SCRIPTS/lib/storage.sh"
  local resource="codex-dispatcher:test" owner

  owner="$(agmsg_runtime_lock_acquire "$resource" 111)"
  [ "$owner" = 111 ]
  owner="$(agmsg_runtime_lock_acquire "$resource" 222 111)"
  [ "$owner" = 222 ]
  # A contender that observed the old generation cannot delete its successor.
  owner="$(agmsg_runtime_lock_acquire "$resource" 333 111)"
  [ "$owner" = 222 ]
  agmsg_runtime_lock_verify "$resource" 222
  refute agmsg_runtime_lock_verify "$resource" 333
  agmsg_runtime_lock_release "$resource" 333
  agmsg_runtime_lock_verify "$resource" 222
  agmsg_runtime_lock_release "$resource" 222
  [ -z "$(agmsg_runtime_lock_owner "$resource")" ]
}

@test "storage: runtime lock initializes a fresh store before send" {
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/lock-first-store"
  source "$SCRIPTS/lib/storage.sh"

  [ "$(agmsg_runtime_lock_acquire codex-dispatcher:test 111)" = 111 ]
  bash "$SCRIPTS/send.sh" team alice bob "after lock init" --force
  [ "$(agmsg_sqlite "$(agmsg_db_path team)" "SELECT COUNT(*) FROM events WHERE type='message_sent' AND body = 'after lock init';")" = 1 ]
}

@test "storage_send: suppresses probe stderr but exposes final retry stderr" {
  source "$SCRIPTS/lib/storage.sh"
  agmsg_storage_load
  local calls_file="$BATS_TEST_TMPDIR/storage-send-calls"
  local stdout_file="$BATS_TEST_TMPDIR/storage-send.stdout"
  local stderr_file="$BATS_TEST_TMPDIR/storage-send.stderr"
  : > "$calls_file"

  # Keep the production retry path real while making both SQLite outcomes
  # deterministic. The first failure is the probe that must stay quiet; the
  # second is the final retry whose raw stderr is the diagnostic under test.
  storage_init() { return 0; }
  agmsg_sqlite() {
    local calls=0
    [ ! -s "$calls_file" ] || calls="$(cat "$calls_file")"
    calls=$((calls + 1))
    printf '%s\n' "$calls" > "$calls_file"
    if [ "$calls" -eq 1 ]; then
      printf '%s\n' 'synthetic first probe failure' >&2
    else
      printf '%s\n' 'synthetic final retry failure' >&2
    fi
    return 1
  }

  local send_status
  if storage_send team alice bob "diagnostic body" >"$stdout_file" 2>"$stderr_file"; then
    send_status=0
  else
    send_status=$?
  fi
  [ "$send_status" -ne 0 ]
  [ "$(cat "$stderr_file")" = "synthetic final retry failure" ]
  ! grep -Fq 'synthetic first probe failure' "$stderr_file"
}

storage_query() {
  local db="$1" sql="$2" result rc
  if result="$(sqlite3 "$db" "$sql")"; then
    rc=0
  else
    rc=$?
  fi
  printf '%s' "$result" | LC_ALL=C tr -d '\r'
  return "$rc"
}

p8_test_send_child() {
  local storage_dir="$1" index="$2" failure_index="${3:-}"
  if [ -n "$failure_index" ] && [ "$index" = "$failure_index" ]; then
    printf 'synthetic P8 child failure index=%s\n' "$index" >&2
    return 73
  fi
  AGMSG_STORAGE_PATH="$storage_dir" \
    bash "$SCRIPTS/send.sh" team leader "tgt$index" "job $index" --force
}

p8_run_child() {
  local status_file="$1" storage_dir="$2" index="$3" failure_index="${4:-}"
  local child_status
  if p8_test_send_child "$storage_dir" "$index" "$failure_index"; then
    child_status=0
  else
    child_status=$?
  fi
  if ! printf '%s\n' "$child_status" > "$status_file"; then
    printf 'P8 child status artifact unavailable: %s\n' "$status_file" >&2
    return 74
  fi
  return "$child_status"
}

run_storage_fanout_with_packet() {
  local storage_dir="$1" failure_index="${2:-}"
  local count="${AGMSG_TEST_P8_FANOUT_CHILDREN:-10}"
  local artifact_dir="$BATS_TEST_TMPDIR/p8-fanout"
  local db="$storage_dir/messages.db"
  local i rc child_status any_failure=0 actual_count event_rows schema_exists db_exists
  local resource_stderr="$artifact_dir/resource-query.stderr"
  local -a pids=() stdout_files=() stderr_files=() status_files=()
  local -a wait_exits=() child_statuses=()

  mkdir -p "$artifact_dir"
  : > "$resource_stderr"
  for i in $(seq 1 "$count"); do
    stdout_files[$i]="$artifact_dir/child-$i.stdout"
    stderr_files[$i]="$artifact_dir/child-$i.stderr"
    status_files[$i]="$artifact_dir/child-$i.status"
    ( p8_run_child "${status_files[$i]}" "$storage_dir" "$i" "$failure_index" ) \
      >"${stdout_files[$i]}" 2>"${stderr_files[$i]}" 3>&- &
    pids[$i]=$!
  done

  # Always reap every child. A failed wait must not prevent later children from
  # being collected or hide the resource snapshot behind set -e in a caller.
  for i in $(seq 1 "$count"); do
    if wait "${pids[$i]}"; then
      rc=0
    else
      rc=$?
      any_failure=1
    fi
    wait_exits[$i]="$rc"
    if child_status="$(cat "${status_files[$i]}" 2>/dev/null)"; then
      child_statuses[$i]="$child_status"
    else
      child_statuses[$i]=unavailable
      any_failure=1
    fi
    if [ "${child_statuses[$i]}" != "$rc" ]; then
      any_failure=1
    fi
  done

  if [ -f "$db" ]; then
    db_exists=1
  else
    db_exists=0
  fi
  schema_exists=unavailable
  actual_count=unavailable
  event_rows=unavailable
  if [ "$db_exists" -eq 1 ]; then
    if schema_exists="$(storage_query "$db" \
      "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='events';" \
      2>>"$resource_stderr")"; then
      :
    else
      schema_exists=unavailable
    fi
    if actual_count="$(storage_query "$db" \
      "SELECT COUNT(*) FROM events WHERE type='message_sent' AND from_agent='leader';" \
      2>>"$resource_stderr")"; then
      :
    else
      actual_count=unavailable
    fi
    if event_rows="$(storage_query "$db" \
      "SELECT seq || ':' || id || ':' || to_agent FROM events WHERE type='message_sent' AND from_agent='leader' ORDER BY seq;" \
      2>>"$resource_stderr")"; then
      :
    else
      event_rows=unavailable
    fi
  fi

  if [ "$any_failure" -eq 0 ] && [ "$actual_count" = "$count" ] && \
    [ "$schema_exists" = 1 ]; then
    return 0
  fi

  printf '%s\n' 'p8-failure-packet' >&2
  printf 'artifact_dir=%s\n' "$artifact_dir" >&2
  printf 'expected_event_count=%s\n' "$count" >&2
  printf 'actual_event_count=%s\n' "$actual_count" >&2
  printf 'resource db_exists=%s events_table=%s\n' "$db_exists" "$schema_exists" >&2
  printf '%s\n' 'resource event_rows:' >&2
  if [ -n "$event_rows" ] && [ "$event_rows" != unavailable ]; then
    while IFS= read -r row; do
      [ -n "$row" ] && printf '  event_row=%s\n' "$row" >&2
    done <<EOF
$event_rows
EOF
  else
    printf '%s\n' '  <unavailable>' >&2
  fi
  if [ -s "$resource_stderr" ]; then
    printf '%s\n' 'resource query stderr:' >&2
    sed 's/^/  /' "$resource_stderr" >&2
  fi

  for i in $(seq 1 "$count"); do
    printf 'child[%s]: pid=%s wait_exit=%s child_status=%s\n' \
      "$i" "${pids[$i]}" "${wait_exits[$i]}" "${child_statuses[$i]}" >&2
    printf '%s\n' '  stdout:' >&2
    if [ -s "${stdout_files[$i]}" ]; then
      sed 's/^/    /' "${stdout_files[$i]}" >&2
    else
      printf '%s\n' '    <empty>' >&2
    fi
    printf '%s\n' '  stderr:' >&2
    if [ -s "${stderr_files[$i]}" ]; then
      sed 's/^/    /' "${stderr_files[$i]}" >&2
    else
      printf '%s\n' '    <empty>' >&2
    fi
  done
  return 1
}

@test "send: fan-out failure reports child and resource state without stdout" {
  local packet_stdout="$BATS_TEST_TMPDIR/fanout.stdout"
  local packet_stderr="$BATS_TEST_TMPDIR/fanout.stderr"
  local fanout_status fanout_count="${AGMSG_TEST_P8_FANOUT_CHILDREN:-10}"

  if run_storage_fanout_with_packet "$TEST_SKILL_DIR/db" 2 \
    >"$packet_stdout" 2>"$packet_stderr"; then
    fanout_status=0
  else
    fanout_status=$?
  fi
  [ "$fanout_status" -ne 0 ]
  [ ! -s "$packet_stdout" ]
  grep -Fq 'p8-failure-packet' "$packet_stderr"
  grep -Fq "expected_event_count=$fanout_count" "$packet_stderr"
  grep -Eq 'actual_event_count=[0-9]+' "$packet_stderr"
  grep -Fq 'resource db_exists=1 events_table=1' "$packet_stderr"
  grep -Fq 'child[2]: pid=' "$packet_stderr"
  grep -Fq 'wait_exit=73' "$packet_stderr"
  grep -Fq 'synthetic P8 child failure index=2' "$packet_stderr"
  grep -Fq 'event_row=' "$packet_stderr"
  if grep -Fq 'job 1' "$packet_stderr"; then
    false
  fi
  local i
  for i in $(seq 1 "$fanout_count"); do
    [ -f "$BATS_TEST_TMPDIR/p8-fanout/child-$i.status" ]
  done
}

@test "send: concurrent fan-out to N recipients all land (no SQLITE_BUSY)" {
  # Without a busy_timeout, concurrent writers fail with SQLITE_BUSY(5) and the
  # sends silently drop. With the wrapper they wait and all land. See #114.
  local x
  for x in 1 2 3 4 5 6 7 8 9 10; do
    ( bash "$SCRIPTS/send.sh" team leader "tgt$x" "job $x" --force >/dev/null 2>&1 ) 3>&- &
  done
  wait
  local n
  n=$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" \
    "SELECT COUNT(*) FROM events WHERE type='message_sent' AND from_agent='leader';")
  [ "$n" -eq 10 ]
}

@test "send: concurrent fan-out to a FRESH (uninitialized) store all lands" {
  # No init-db first — every send races to initialize an override store that
  # doesn't exist yet. The diagnostic helper retains the original 10/10
  # acceptance predicate while preserving every child result on failure.
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/freshstore"
  run run_storage_fanout_with_packet "$AGMSG_STORAGE_PATH"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "storage: the -escape probe is memoized, not re-run on every call (#462)" {
  # `$(_agmsg_escape_flag)` ran the probe in a subshell, so the memo it set was
  # discarded on exit and every agmsg_sqlite call spawned two sqlite3 processes
  # instead of one. Count real invocations through a counting shim.
  source "$SCRIPTS/lib/storage.sh"
  local count="$BATS_TEST_TMPDIR/sqlite-calls"
  : > "$count"
  local real; real="$(command -v sqlite3)"
  sqlite3() { echo call >> "$count"; "$real" "$@"; }

  local i
  for i in 1 2 3 4 5; do
    agmsg_sqlite ":memory:" "SELECT 1;" >/dev/null 2>&1 || true
  done

  # 5 queries + exactly one probe. Before the fix this was 10.
  [ "$(wc -l < "$count" | tr -d ' ')" -eq 6 ]
}

@test "storage: a memoized probe is inherited by command substitutions (#462)" {
  # Subshells inherit shell variables, so once the probe has run in this shell
  # every later $(agmsg_sqlite ...) reuses the memo instead of re-probing.
  # (A call made before any probe still probes inside its own subshell — the
  # memo is per shell, not per machine.)
  source "$SCRIPTS/lib/storage.sh"
  local count="$BATS_TEST_TMPDIR/sqlite-calls-sub"
  local real; real="$(command -v sqlite3)"
  sqlite3() { echo call >> "$count"; "$real" "$@"; }

  agmsg_sqlite ":memory:" "SELECT 1;" >/dev/null 2>&1 || true   # primes the memo
  : > "$count"

  local i out
  for i in 1 2 3 4 5; do
    out="$(agmsg_sqlite ":memory:" "SELECT 1;" 2>/dev/null)" || true
  done

  [ "$(wc -l < "$count" | tr -d ' ')" -eq 5 ]
}

# --- storage partition axis -----------------------------------------------------
#
# Which store a team uses is a per-team driver choice. `shared` is the default
# and is what programs outside agmsg read; `per-team` is what a team moves to
# when connecting requires it. The tests that matter here are the default (every
# team in one file, unchanged from before the axis existed) and the isolation a
# moved team gets.

# Put <team> on the per-team partition the way migrate-team-store.sh does, without
# copying anything: these tests are about resolution, not migration.
_use_per_team() {
  mkdir -p "$TEST_SKILL_DIR/teams/$1"
  printf '{"name":"%s","drivers":{"partition":"per-team"}}\n' "$1" \
    > "$TEST_SKILL_DIR/teams/$1/config.json"
}

@test "storage: every team shares one store by default" {
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/db" SKILL_DIR="$TEST_SKILL_DIR"
  # shellcheck disable=SC1091
  source "$SCRIPTS/lib/storage.sh"
  # The partition external readers depend on. A team must not leave it by merely
  # existing — only by something that requires the move.
  [ "$(agmsg_db_path alpha)" = "$BATS_TEST_TMPDIR/db/messages.db" ]
  [ "$(agmsg_db_path bravo)" = "$BATS_TEST_TMPDIR/db/messages.db" ]
  [ "$(agmsg_db_path alpha)" = "$(_agmsg_runtime_db_path)" ]
}

@test "storage: a team on the per-team partition moves, and only that team" {
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/db" SKILL_DIR="$TEST_SKILL_DIR"
  # shellcheck disable=SC1091
  source "$SCRIPTS/lib/storage.sh"
  _use_per_team alpha
  [ "$(agmsg_db_path alpha)" = "$BATS_TEST_TMPDIR/db/teams/alpha/messages.db" ]
  # Its neighbour is untouched — that is the whole point of choosing per team.
  [ "$(agmsg_db_path bravo)" = "$BATS_TEST_TMPDIR/db/messages.db" ]
  # And resolution does not stick: the memoized driver must not leak across teams.
  [ "$(agmsg_db_path alpha)" = "$BATS_TEST_TMPDIR/db/teams/alpha/messages.db" ]
}

@test "storage: an unknown partition is an error, not a fallback" {
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/db" SKILL_DIR="$TEST_SKILL_DIR"
  # shellcheck disable=SC1091
  source "$SCRIPTS/lib/storage.sh"
  mkdir -p "$TEST_SKILL_DIR/teams/gamma"
  printf '{"name":"gamma","drivers":{"partition":"nope"}}\n' \
    > "$TEST_SKILL_DIR/teams/gamma/config.json"
  run agmsg_db_path gamma
  [ "$status" -ne 0 ]
  # Falling back to shared would read a real file holding other teams' rows.
  [[ ! "$output" =~ "messages.db" ]]
}

@test "storage: a selector that would escape the storage tree is refused" {
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/db" SKILL_DIR="$TEST_SKILL_DIR"
  # shellcheck disable=SC1091
  source "$SCRIPTS/lib/storage.sh"
  local bad
  # A real store was found holding a project path as a team name, so this is
  # reachable from data, not only from a hostile argument.
  for bad in ".." "." "a/b" "/Users/someone/project"; do
    run agmsg_db_path "$bad"
    [ "$status" -ne 0 ]
    [[ ! "$output" =~ "$BATS_TEST_TMPDIR/db/teams/$bad" ]]
  done
}

@test "storage: a moved team's messages are not in the shared store" {
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/db" SKILL_DIR="$TEST_SKILL_DIR"
  # shellcheck disable=SC1091
  source "$SCRIPTS/lib/storage.sh"
  agmsg_storage_load
  _use_per_team alpha
  storage_init alpha >/dev/null
  storage_init bravo >/dev/null
  storage_send alpha ann bob "alpha-only" >/dev/null
  storage_send bravo cid bob "bravo-only" >/dev/null

  [[ "$(storage_list_unread alpha bob)" =~ "alpha-only" ]]
  [[ ! "$(storage_list_unread alpha bob)" =~ "bravo-only" ]]
  [[ "$(storage_list_unread bravo bob)" =~ "bravo-only" ]]
  [[ ! "$(storage_list_unread bravo bob)" =~ "alpha-only" ]]

  # Not just filtered on the way out — the bytes are in different files.
  refute grep -q "bravo-only" "$(agmsg_db_path alpha)"
  ! grep -q "alpha-only" "$(agmsg_db_path bravo)"
}

@test "storage: resolving a store without a selector is an error, not a default" {
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/db"
  # shellcheck disable=SC1091
  source "$SCRIPTS/lib/storage.sh"
  run agmsg_db_path
  [ "$status" -ne 0 ]
  run agmsg_db_path ""
  [ "$status" -ne 0 ]
  # The runtime resolver is the one place a missing selector is correct: its
  # callers hold project-scoped state and have no team to name.
  run _agmsg_runtime_db_path
  [ "$status" -eq 0 ]
  [ -n "$output" ]
}

@test "storage: no shipped script resolves a store without a selector" {
  # The point of requiring the selector is that no half-converted caller is left
  # to find. A bare call in production is exactly that, so it is swept for
  # rather than trusted.
  #
  # storage_init is here because watching only agmsg_db_path was not enough:
  # sqlite-sync.sh called storage_init with no team, which reached the resolver
  # one frame down. It failed to stderr while the command still succeeded, so
  # nothing went red until a test captured stderr and fed it to jq.
  local offenders
  # Bare only: the name closing a substitution, ending a line, or followed by a
  # redirect or pipe. A call WITH a selector is the thing we want, so it must
  # not match.
  # server/ is swept too, because its integration tests drive the client through
  # embedded bash. One selector-less storage_init lived there through two rounds
  # of this sweep: it is not a shell file, so watching scripts/ alone never saw
  # it, and it only failed once a store per team made the empty selector reach
  # the resolver. tests/ is deliberately excluded — a bare call there is how the
  # requirement itself is asserted.
  offenders="$(cd "$BATS_TEST_DIRNAME/.." && grep -rnE '(agmsg_db_path|storage_init) *(\)|\||>|$)' \
    scripts bin server 2>/dev/null | grep -v ':[0-9]*: *#' | grep -v 'storage_init()' || true)"
  [ -z "$offenders" ] || { echo "$offenders"; false; }
}

@test "storage: events.legacy_id is indexed, on a new store and on one that predates the column (#919)" {
  source "$SCRIPTS/lib/storage.sh"
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/store"
  export AGMSG_STORAGE_DRIVER=sqlite
  agmsg_storage_load
  storage_init demo >/dev/null
  local db
  db=$(agmsg_db_path demo)
  [ "$(sqlite3 "$db" "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND tbl_name='events' AND name='events_legacy';" | tr -d '\r')" -eq 1 ]
  # The lookup every reader makes per legacy row, and the projection makes per
  # message, is a search now -- not a scan of events.
  sqlite3 "$db" "EXPLAIN QUERY PLAN SELECT 1 FROM events e2 WHERE e2.legacy_id = 5;" | grep -q 'USING COVERING INDEX events_legacy\|USING INDEX events_legacy'
  # A store created before legacy_id existed: init adds the column first (the
  # ALTER that already ran for #689), then the index on it.
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/old"
  mkdir -p "$AGMSG_STORAGE_PATH"
  sqlite3 "$AGMSG_STORAGE_PATH/messages.db" "CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, id TEXT NOT NULL, team TEXT, from_agent TEXT, to_agent TEXT, body TEXT, msg_id TEXT, agent TEXT, at TEXT NOT NULL);"
  storage_init demo >/dev/null
  [ "$(sqlite3 "$AGMSG_STORAGE_PATH/messages.db" "PRAGMA table_info(events);" | grep -c legacy_id)" -eq 1 ]
  [ "$(sqlite3 "$AGMSG_STORAGE_PATH/messages.db" "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='events_legacy';" | tr -d '\r')" -eq 1 ]
}

@test "storage: events.id is indexed, on a new store and on one that predates the index (#910)" {
  source "$SCRIPTS/lib/storage.sh"
  export AGMSG_STORAGE_PATH="$BATS_TEST_TMPDIR/store"
  export AGMSG_STORAGE_DRIVER=sqlite
  agmsg_storage_load
  storage_init demo >/dev/null
  local db
  db=$(agmsg_db_path demo)
  [ "$(sqlite3 "$db" "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND tbl_name='events' AND name='events_id';" | tr -d '\r')" -eq 1 ]
  # The lookup the sync import makes per imported message (FROM events
  # WHERE id=...) is a search now -- not a scan of every message body.
  sqlite3 "$db" "EXPLAIN QUERY PLAN SELECT seq FROM events WHERE id = 'x';" | grep -q 'USING COVERING INDEX events_id\|USING INDEX events_id'
  # A store from before this index existed picks it up on the next init.
  sqlite3 "$db" "DROP INDEX events_id;"
  storage_init demo >/dev/null
  [ "$(sqlite3 "$db" "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='events_id';" | tr -d '\r')" -eq 1 ]
}
