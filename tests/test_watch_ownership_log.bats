#!/usr/bin/env bats

# R2-A regression: ownership transition diagnostics must survive the watcher's
# production stderr sink and land in the durable per-session log.

load test_helper

setup() {
  setup_test_env
  skip_on_windows "watcher ownership/liveness semantics under Git Bash (#182)"
  # Keep the durable-log filename deterministic even when the suite itself is
  # running below a Claude/Codex process that could otherwise resolve a
  # composite instance id.
  export AGMSG_AGENT_PID=""
  export SKILL_DIR="$TEST_SKILL_DIR"
  export RUN_DIR="$SKILL_DIR/run"
  export PROJ="/tmp/agmsg-watch-ownership-log"
  mkdir -p "$RUN_DIR"
  bash "$SCRIPTS/join.sh" team alice claude-code "$PROJ" >/dev/null
  bash "$SCRIPTS/join.sh" team carol claude-code "$PROJ" >/dev/null
  # shellcheck disable=SC1090
  source "$SCRIPTS/lib/actas-lock.sh"
}

teardown() {
  teardown_test_env
}

@test "watch: persists broad ownership claim and release transitions" {
  local out="$BATS_TEST_TMPDIR/watch.out"
  local log="$RUN_DIR/watch.sid-broad.log"
  local lock_path watcher
  : > "$out"

  # A seed message proves the watcher completed subscription resolution before
  # the test changes the ownership state. Without this positive control, a
  # watcher that never reached its poll loop could pass the absence side of the
  # assertions below.
  bash "$SCRIPTS/send.sh" team alice carol startup-marker >/dev/null
  AGMSG_WATCH_INTERVAL=1 bash "$SCRIPTS/watch.sh" sid-broad "$PROJ" claude-code \
    >"$out" 2>/dev/null 3>&- 4>&- &
  watcher=$!
  if ! wait_for_file_contains "$out" startup-marker; then
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    cat "$out" >&2
    false
  fi

  # Make the claim transition observable as a live peer takeover.
  setup_live_owner "$RUN_DIR" sid-new
  lock_path="$(actas_lock_path team alice)"
  printf '%s\n' sid-new > "$lock_path"
  if ! wait_for_file_contains "$log" "team/alice was claimed by session sid-new"; then
    rm "$lock_path"
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    cat "$out" "$log" >&2 2>/dev/null || true
    false
  fi

  # Remove the peer lock and wait for the return transition. The watcher keeps
  # the pair in its subscription, so the unread message state is not altered.
  rm "$lock_path"
  if ! wait_for_file_contains "$log" "team/alice is unheld again"; then
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    cat "$out" "$log" >&2 2>/dev/null || true
    false
  fi

  kill "$watcher" 2>/dev/null || true
  wait "$watcher" 2>/dev/null || true

  # fd 2 was /dev/null above. These assertions therefore prove the durable log
  # path, not the stderr mirror, is what carries both transitions.
  run grep -F "team/alice was claimed by session sid-new" "$log"
  [ "$status" -eq 0 ]
  run grep -F "team/alice is unheld again" "$log"
  [ "$status" -eq 0 ]
}

@test "watch: persists subscription resolution failure and exits non-zero" {
  local out="$BATS_TEST_TMPDIR/watch-subscription.out"
  local log="$RUN_DIR/watch.sid-subscription.log"
  local watcher watcher_status

  # Mutate only the isolated copy. A fixed non-zero identities.sh result is the
  # negative control for the downstream resolver failure; the production
  # watcher still owns the durable-log assertion below.
  cp "$SCRIPTS/identities.sh" "$BATS_TEST_TMPDIR/identities.sh.copy"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 37' > "$SCRIPTS/identities.sh"
  chmod +x "$SCRIPTS/identities.sh"

  AGMSG_WATCH_INTERVAL=1 bash "$SCRIPTS/watch.sh" sid-subscription "$PROJ" claude-code \
    >"$out" 2>/dev/null 3>&- 4>&- &
  watcher=$!
  if ! wait_for_file_contains "$log" "agmsg_subscription_pairs"; then
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    cat "$out" "$log" >&2 2>/dev/null || true
    false
  fi

  if wait "$watcher"; then
    watcher_status=0
  else
    watcher_status="$?"
  fi
  [ "$watcher_status" -ne 0 ]

  # fd 2 was /dev/null. The operation and both target dimensions must be in the
  # durable record, while this subscription failure must not borrow DB wording.
  run grep -F "agmsg_subscription_pairs" "$log"
  [ "$status" -eq 0 ]
  run grep -F "project '$PROJ'" "$log"
  [ "$status" -eq 0 ]
  run grep -F "agent type 'claude-code'" "$log"
  [ "$status" -eq 0 ]
  run grep -F "cannot open message DB" "$log"
  [ "$status" -ne 0 ]
}

@test "watch: persists DB selector failure without DB-open misdiagnosis" {
  local out="$BATS_TEST_TMPDIR/watch-db-selector.out"
  local log="$RUN_DIR/watch.sid-db-selector.log"
  local cfg="$TEST_SKILL_DIR/teams/team/config.json"
  local updated watcher watcher_status

  # Keep the roster and subscription valid, then make only the team's
  # partition selector unresolvable. This reaches agmsg_db_path after
  # subscription resolution and distinguishes selector failure from an
  # existing unreadable database.
  updated="$(sqlite_mem "SELECT json_set(CAST(readfile('$(rf "$cfg")') AS TEXT), '\$.drivers.partition', 'missing-driver');")"
  printf '%s\n' "$updated" > "$cfg"

  AGMSG_WATCH_INTERVAL=1 bash "$SCRIPTS/watch.sh" sid-db-selector "$PROJ" claude-code \
    >"$out" 2>/dev/null 3>&- 4>&- &
  watcher=$!
  if ! wait_for_file_contains "$log" "agmsg_db_path"; then
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    cat "$out" "$log" >&2 2>/dev/null || true
    false
  fi

  if wait "$watcher"; then
    watcher_status=0
  else
    watcher_status="$?"
  fi
  [ "$watcher_status" -ne 0 ]

  # A selector failure names its operation and team. It must not be reported as
  # an unreadable DB, which is a later watch_check_existing_db condition.
  run grep -F "agmsg_db_path" "$log"
  [ "$status" -eq 0 ]
  run grep -F "team 'team'" "$log"
  [ "$status" -eq 0 ]
  run grep -F "cannot open message DB" "$log"
  [ "$status" -ne 0 ]
}

@test "watch: persists DB-open failure without subscription misdiagnosis" {
  local out="$BATS_TEST_TMPDIR/watch-db-open.out"
  local log="$RUN_DIR/watch.sid-db-open.log"
  local cfg="$TEST_SKILL_DIR/teams/team/config.json"
  local updated db watcher watcher_status

  # Keep selector resolution valid, then corrupt only the existing per-team
  # store. This reaches watch_check_existing_db after subscription resolution,
  # providing the positive DB-open control for the two selector-failure tests.
  updated="$(sqlite_mem "SELECT json_set(CAST(readfile('$(rf "$cfg")') AS TEXT), '\$.drivers.partition', 'per-team');")"
  printf '%s\n' "$updated" > "$cfg"
  source "$SCRIPTS/lib/storage.sh"
  agmsg_storage_load
  db="$(agmsg_db_path team)"
  mkdir -p "$(dirname "$db")"
  printf '%s\n' 'not a sqlite database' > "$db"

  AGMSG_WATCH_INTERVAL=1 bash "$SCRIPTS/watch.sh" sid-db-open "$PROJ" claude-code \
    >"$out" 2>/dev/null 3>&- 4>&- &
  watcher=$!
  if ! wait_for_file_contains "$log" "cannot open message DB"; then
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    cat "$out" "$log" >&2 2>/dev/null || true
    false
  fi

  if wait "$watcher"; then
    watcher_status=0
  else
    watcher_status="$?"
  fi
  [ "$watcher_status" -ne 0 ]

  # The real DB-open diagnostic is present, while neither startup resolver
  # failure label is allowed to stand in for it.
  run grep -F "cannot open message DB" "$log"
  [ "$status" -eq 0 ]
  run grep -F "agmsg_subscription_pairs" "$log"
  [ "$status" -ne 0 ]
  run grep -F "agmsg_db_path failed" "$log"
  [ "$status" -ne 0 ]
}

@test "watch: cursor read failure is logged and does not become cursor zero" {
  local out="$BATS_TEST_TMPDIR/watch-cursor-failure.out"
  local log="$RUN_DIR/watch.sid-cursor-failure.log"
  local watcher

  # Seed an unread message before installing the isolated driver seam. The
  # seam makes the real cursor read fail with the driver's fixed non-zero code.
  bash "$SCRIPTS/send.sh" team alice carol cursor-failure-marker >/dev/null
  export AGMSG_STORAGE_DRIVER=sqlite
  printf '%s\n' '' 'storage_read_cursor_get() {' '  return 13' '}' \
    >> "$SCRIPTS/drivers/storage/sqlite.sh"

  AGMSG_WATCH_INTERVAL=1 bash "$SCRIPTS/watch.sh" sid-cursor-failure "$PROJ" claude-code \
    >"$out" 2>/dev/null 3>&- 4>&- &
  watcher=$!
  if ! wait_for_file_contains "$log" "storage_read_cursor_get failed"; then
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    cat "$out" "$log" >&2 2>/dev/null || true
    false
  fi
  kill "$watcher" 2>/dev/null || true
  wait "$watcher" 2>/dev/null || true

  # fd 2 was /dev/null. The failure is durable, and the unread marker was not
  # delivered through a fabricated cursor 0 scan.
  run grep -F "storage_read_cursor_get failed" "$log"
  [ "$status" -eq 0 ]
  run grep -F "skipping this poll" "$log"
  [ "$status" -eq 0 ]
  run grep -F "cursor-failure-marker" "$out"
  [ "$status" -ne 0 ]
}

@test "watch: storage scan failure is logged and does not become no unread" {
  local out="$BATS_TEST_TMPDIR/watch-scan-failure.out"
  local log="$RUN_DIR/watch.sid-scan-failure.log"
  local watcher

  # Seed an unread message, then make only the real storage scan return the
  # driver's fixed non-zero failure. A hidden failure would look like an empty
  # OUT value and silently skip the unread message without a durable reason.
  bash "$SCRIPTS/send.sh" team alice carol scan-failure-marker >/dev/null
  export AGMSG_STORAGE_DRIVER=sqlite
  printf '%s\n' '' 'storage_watch_after() {' '  return 13' '}' \
    >> "$SCRIPTS/drivers/storage/sqlite.sh"

  AGMSG_WATCH_INTERVAL=1 bash "$SCRIPTS/watch.sh" sid-scan-failure "$PROJ" claude-code \
    >"$out" 2>/dev/null 3>&- 4>&- &
  watcher=$!
  if ! wait_for_file_contains "$log" "storage_watch_after failed"; then
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    cat "$out" "$log" >&2 2>/dev/null || true
    false
  fi
  kill "$watcher" 2>/dev/null || true
  wait "$watcher" 2>/dev/null || true

  # The poll is skipped with an explicit durable reason; an empty result must
  # not be mistaken for a successful no-unread scan.
  run grep -F "storage_watch_after failed" "$log"
  [ "$status" -eq 0 ]
  run grep -F "skipping this poll" "$log"
  [ "$status" -eq 0 ]
  run grep -F "scan-failure-marker" "$out"
  [ "$status" -ne 0 ]
}

@test "watch: consume failure is logged after stdout delivery" {
  local out="$BATS_TEST_TMPDIR/watch-consume-failure.out"
  local log="$RUN_DIR/watch.sid-consume-failure.log"
  local watcher

  # The message must reach stdout before the isolated consume seam fails. The
  # failure is post-delivery state only; it must not change delivery semantics.
  bash "$SCRIPTS/send.sh" team alice carol consume-failure-marker >/dev/null
  export AGMSG_STORAGE_DRIVER=sqlite
  printf '%s\n' '' 'storage_read_cursor_consume() {' '  return 13' '}' \
    >> "$SCRIPTS/drivers/storage/sqlite.sh"

  AGMSG_WATCH_INTERVAL=1 bash "$SCRIPTS/watch.sh" sid-consume-failure "$PROJ" claude-code \
    >"$out" 2>/dev/null 3>&- 4>&- &
  watcher=$!
  if ! wait_for_file_contains "$out" "consume-failure-marker"; then
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    cat "$out" "$log" >&2 2>/dev/null || true
    false
  fi
  if ! wait_for_file_contains "$log" "storage_read_cursor_consume failed"; then
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    cat "$out" "$log" >&2 2>/dev/null || true
    false
  fi
  kill "$watcher" 2>/dev/null || true
  wait "$watcher" 2>/dev/null || true

  # fd 2 was /dev/null. The durable diagnostic identifies the post-delivery
  # operation, recipient pair, and fixed seam status.
  run grep -F "consume-failure-marker" "$out"
  [ "$status" -eq 0 ]
  run grep -F "team/carol: storage_read_cursor_consume failed" "$log"
  [ "$status" -eq 0 ]
  run grep -F "status 13" "$log"
  [ "$status" -eq 0 ]
}

@test "watch: receipt failure is logged after stdout delivery" {
  local out="$BATS_TEST_TMPDIR/watch-receipt-failure.out"
  local log="$RUN_DIR/watch.sid-receipt-failure.log"
  local watcher

  # Receipt persistence is also post-delivery state. A fixed non-zero seam
  # must leave the already emitted message visible to the recipient.
  bash "$SCRIPTS/send.sh" team alice carol receipt-failure-marker >/dev/null
  export AGMSG_STORAGE_DRIVER=sqlite
  printf '%s\n' '' 'storage_record_receipts() {' '  return 13' '}' \
    >> "$SCRIPTS/drivers/storage/sqlite.sh"

  AGMSG_WATCH_INTERVAL=1 bash "$SCRIPTS/watch.sh" sid-receipt-failure "$PROJ" claude-code \
    >"$out" 2>/dev/null 3>&- 4>&- &
  watcher=$!
  if ! wait_for_file_contains "$out" "receipt-failure-marker"; then
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    cat "$out" "$log" >&2 2>/dev/null || true
    false
  fi
  if ! wait_for_file_contains "$log" "storage_record_receipts failed"; then
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    cat "$out" "$log" >&2 2>/dev/null || true
    false
  fi
  kill "$watcher" 2>/dev/null || true
  wait "$watcher" 2>/dev/null || true

  # fd 2 was /dev/null. The durable diagnostic identifies the post-delivery
  # receipt operation, recipient pair, and fixed seam status.
  run grep -F "receipt-failure-marker" "$out"
  [ "$status" -eq 0 ]
  run grep -F "team/carol: storage_record_receipts failed" "$log"
  [ "$status" -eq 0 ]
  run grep -F "status 13" "$log"
  [ "$status" -eq 0 ]
}
