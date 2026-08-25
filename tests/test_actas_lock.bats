#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
  export SKILL_DIR="$TEST_SKILL_DIR"
  # shellcheck disable=SC1090
  source "$SKILL_DIR/scripts/lib/actas-lock.sh"
  export RUN_DIR="$SKILL_DIR/run"
  mkdir -p "$RUN_DIR"
}

teardown() { teardown_test_env; }

# Pretend a CC instance with the given pid is alive and owns the given sid.
fake_cc_instance() {
  local pid="$1" sid="$2"
  echo "$sid" > "$RUN_DIR/cc-instance.$pid"
}

# Use the test process's own PID for "live owner" scenarios. It's guaranteed
# alive for the duration of the test. Avoids subshell-vs-stdout hangs that
# bite when you try to spawn a separate long-lived background pid from
# inside command substitution.
live_pid() { echo "$$"; }

# Install a local SQLite seam for gate error classification tests. The seam is
# installed from inside one test, so it cannot affect the other tests in this
# file. Successful acquire calls return this shell's PID; release calls return
# one affected row. Error text is deliberately emitted on stderr, exactly as
# sqlite3 does, so the gate must preserve and classify it rather than infer from
# an empty owner value.
install_runtime_lock_sqlite_stub() {
  export AGMSG_TEST_GATE_STUB_MODE="$1"
  export AGMSG_TEST_GATE_STUB_ATTEMPTS="$BATS_TEST_TMPDIR/gate-attempts"
  export AGMSG_TEST_GATE_STUB_TIMEOUTS="$BATS_TEST_TMPDIR/gate-timeouts"
  : > "$AGMSG_TEST_GATE_STUB_ATTEMPTS"
  : > "$AGMSG_TEST_GATE_STUB_TIMEOUTS"

  agmsg_storage_ensure_initialized() { return 0; }
  agmsg_sqlite() {
    local sql="${2:-}" count=0
    [ -s "$AGMSG_TEST_GATE_STUB_ATTEMPTS" ] && count="$(cat "$AGMSG_TEST_GATE_STUB_ATTEMPTS")"
    count=$((count + 1))
    printf '%s\n' "$count" > "$AGMSG_TEST_GATE_STUB_ATTEMPTS"
    printf '%s\n' "${AGMSG_BUSY_TIMEOUT:-unset}" >> "$AGMSG_TEST_GATE_STUB_TIMEOUTS"

    case "$AGMSG_TEST_GATE_STUB_MODE" in
      busy-once)
        if [ "$count" -eq 1 ]; then
          printf 'Error: database is locked (5)\n' >&2
          return 5
        fi
        ;;
      always-busy)
        printf 'Error: database is locked (5)\n' >&2
        return 5
        ;;
      permanent)
        printf 'Error: no such table: locks\n' >&2
        return 1
        ;;
      unknown)
        printf 'fixture: unexpected sqlite failure\n' >&2
        return 1
        ;;
    esac

    case "$sql" in
      *"SELECT changes()"*) printf '1\n' ;;
      *) printf '%s\n' "$$" ;;
    esac
  }
}

assert_output_contains() {
  local output="$1" needle="$2"
  case "$output" in
    *"$needle"*) return 0 ;;
    *) printf 'expected output to contain: %s\n%s\n' "$needle" "$output" >&2; return 1 ;;
  esac
}

# --- path encoding ---

@test "actas_lock_path: percent-encodes special bytes in team/agent" {
  local p
  p=$(actas_lock_path "team/foo" "ag ent")
  [[ "$p" == "$RUN_DIR/actas.team%2Ffoo__ag%20ent.session" ]]
}

@test "actas_lock_path: leaves safe chars alone" {
  local p
  p=$(actas_lock_path "team-A.1" "agent_B")
  [[ "$p" == "$RUN_DIR/actas.team-A.1__agent_B.session" ]]
}

# Regression for #65 review finding 2: the old underscore-replacement scheme
# made "foo bar" and "foo_bar" map to the same lock file. With percent
# encoding the two are unambiguous.
@test "actas_lock_path: names that collided under the old scheme are now distinct" {
  [ "$(actas_lock_path "foo bar" alice)" != "$(actas_lock_path "foo_bar" alice)" ]
  [ "$(actas_lock_path "a/b"   alice)" != "$(actas_lock_path "a_b"     alice)" ]
}

@test "actas_lock_path: encodes non-ASCII (UTF-8) bytes" {
  local p
  p=$(actas_lock_path "チーム" alice)
  # "チ" = E3 83 81, so the encoded prefix must contain that triple.
  [[ "$p" == *"%E3%83%81%E3%83%BC%E3%83%A0"* ]]
}

# --- claim / state ---

@test "claim: succeeds when lock file absent" {
  run actas_lock_claim "T" "alice" "sid-1"
  [ "$status" -eq 0 ]
  [ "$(actas_lock_owner "T" "alice")" = "sid-1" ]
}

@test "claim: idempotent when caller already owns it" {
  actas_lock_claim "T" "alice" "sid-1"
  run actas_lock_claim "T" "alice" "sid-1"
  [ "$status" -eq 0 ]
}

@test "claim: refuses when held by a live other session" {
  skip_on_windows "actas live-session liveness under Git Bash (#182)"
  fake_cc_instance "$(live_pid)" "sid-other"
  echo "sid-other" > "$(actas_lock_path "T" "alice")"

  run actas_lock_claim "T" "alice" "sid-mine"
  [ "$status" -eq 1 ]
  [[ "$output" == "held:sid-other" ]]
  [ "$(actas_lock_owner "T" "alice")" = "sid-other" ]
}

@test "claim: reclaims a stale lock whose owner is dead" {
  # Lock exists but no live cc-instance references that sid.
  echo "sid-dead" > "$(actas_lock_path "T" "alice")"

  run actas_lock_claim "T" "alice" "sid-mine"
  [ "$status" -eq 0 ]
  [ "$(actas_lock_owner "T" "alice")" = "sid-mine" ]
}

# Regression for #65 review finding 1, then re-review of 48339d8: a naive
# stale clear (rm or mv) reads-then-removes lock_path with no guard on the
# content, so a second caller carrying a stale decision can delete a fresh
# live lock the first caller installed. Fixed by guarding the removal with
# a per-lock mutex (mkdir on `.reclaim.d`) and re-checking ownership
# *inside* it: if a live owner snuck in between the stale observation and
# the reclaim, leave it alone.
#
# bats can't truly interleave, so we exercise the invariant via two
# complementary cases:

# Case 1: serial — once a live owner claims, peer is refused (basic
# exclusivity sanity check).
@test "claim: a live owner is never replaced by a serial peer's claim" {
  skip_on_windows "actas live-session liveness under Git Bash (#182)"
  echo "sid-dead" > "$(actas_lock_path "T" "alice")"
  setup_live_owner "$RUN_DIR" "sid-A"
  actas_lock_claim "T" "alice" "sid-A"
  run actas_lock_claim "T" "alice" "sid-B"
  [ "$status" -eq 1 ]
  [[ "$output" == "held:sid-A" ]]
  [ "$(actas_lock_owner "T" "alice")" = "sid-A" ]
}

# Case 2: simulates the exact race window flagged on re-review.
# We pre-populate lock_path with a live-owner record (modeling "winner A
# has installed its lock"), then drive a claim() call that on its first
# try_claim *would* see stale if it observed the prior state — but in our
# substitute we just verify the resulting state. Then we additionally
# stage the reclaim mutex held externally to simulate the would-be racer
# carrying a stale decision: claim must NOT touch the existing live lock
# even if it tried to enter the stale path.
@test "claim: a fresh live lock survives a concurrent claimer's stale reclaim attempt" {
  skip_on_windows "actas live-session liveness under Git Bash (#182)"
  # lock_path already records a live owner (sid-A is alive via cc-instance).
  setup_live_owner "$RUN_DIR" "sid-A"
  echo "sid-A" > "$(actas_lock_path "T" "alice")"

  # Externally hold the reclaim mutex — modeling a peer that thinks the
  # slot is stale and is about to enter the cleanup. With the fix the
  # reclaim path now re-checks ownership *inside* this mutex, so even
  # if a peer made it through, sid-A's live lock would be respected.
  local rd
  rd="$(actas_lock_path "T" "alice").reclaim.d"
  mkdir "$rd"

  run actas_lock_claim "T" "alice" "sid-B"
  rmdir "$rd"

  [ "$status" -eq 1 ]
  [[ "$output" == "held:sid-A" ]]
  [ "$(actas_lock_owner "T" "alice")" = "sid-A" ]
}

# --- liveness ---

@test "sid_alive: empty sid is not alive" {
  run actas_lock_sid_alive ""
  [ "$status" -ne 0 ]
}

@test "sid_alive: pid alive + cc-instance content matches -> alive" {
  skip_on_windows "actas live-session liveness under Git Bash (#182)"
  fake_cc_instance "$(live_pid)" "sid-A"
  run actas_lock_sid_alive "sid-A"
  [ "$status" -eq 0 ]
}

@test "sid_alive: pid dead -> not alive" {
  fake_cc_instance "99999" "sid-A"  # very unlikely live pid
  run actas_lock_sid_alive "sid-A"
  [ "$status" -ne 0 ]
}

# --- delivery gate ---

@test "delivery gate: watcher retries SQLITE_BUSY within its short budget" {
  install_runtime_lock_sqlite_stub busy-once
  local lock_path="$RUN_DIR/actas.T__alice.session"

  run actas_lock_gate_try_acquire "$lock_path"
  [ "$status" -eq 0 ]
  [ "$(cat "$AGMSG_TEST_GATE_STUB_ATTEMPTS")" -eq 2 ]
  [ "$(head -1 "$AGMSG_TEST_GATE_STUB_TIMEOUTS")" -lt 200 ]
}

@test "delivery gate: writer retries SQLITE_BUSY and succeeds" {
  install_runtime_lock_sqlite_stub busy-once
  local lock_path="$RUN_DIR/actas.T__alice.session"

  run actas_lock_gate_acquire "$lock_path"
  [ "$status" -eq 0 ]
  [ "$(cat "$AGMSG_TEST_GATE_STUB_ATTEMPTS")" -eq 2 ]
  [ "$(head -1 "$AGMSG_TEST_GATE_STUB_TIMEOUTS")" -le 100 ]
}

@test "delivery gate: permanent SQLite failure is not retried and is diagnosed" {
  install_runtime_lock_sqlite_stub permanent
  local lock_path="$RUN_DIR/actas.T__alice.session"

  run actas_lock_gate_try_acquire "$lock_path"
  [ "$status" -ne 0 ]
  [ "$(cat "$AGMSG_TEST_GATE_STUB_ATTEMPTS")" -eq 1 ]
  assert_output_contains "$output" "operation=try-acquire"
  assert_output_contains "$output" "resource=actas-delivery:$lock_path"
  assert_output_contains "$output" "observed_owner=<none>"
  assert_output_contains "$output" "pid=$$"
  assert_output_contains "$output" "classification=permanent"
  assert_output_contains "$output" "sqlite_error=Error: no such table: locks"
}

@test "delivery gate: unknown SQLite failure is fail-closed without retry" {
  install_runtime_lock_sqlite_stub unknown
  local lock_path="$RUN_DIR/actas.T__alice.session"

  run actas_lock_gate_try_acquire "$lock_path"
  [ "$status" -ne 0 ]
  [ "$(cat "$AGMSG_TEST_GATE_STUB_ATTEMPTS")" -eq 1 ]
  assert_output_contains "$output" "classification=unknown"
  assert_output_contains "$output" "sqlite_error=fixture: unexpected sqlite failure"
}

@test "runtime lock ABI preserves SQLite failure status and stderr" {
  install_runtime_lock_sqlite_stub permanent
  local resource="codex-dispatcher:diagnostic"

  run agmsg_runtime_lock_acquire "$resource" 111
  [ "$status" -ne 0 ]
  assert_output_contains "$output" "Error: no such table: locks"

  run agmsg_runtime_lock_owner "$resource"
  [ "$status" -ne 0 ]
  assert_output_contains "$output" "Error: no such table: locks"

  run agmsg_runtime_lock_verify "$resource" 111
  [ "$status" -ne 0 ]
  assert_output_contains "$output" "Error: no such table: locks"

  run agmsg_runtime_lock_release "$resource" 111
  [ "$status" -ne 0 ]
  assert_output_contains "$output" "Error: no such table: locks"
}

@test "delivery gate: transient retry exhaustion returns non-zero" {
  install_runtime_lock_sqlite_stub always-busy
  local lock_path="$RUN_DIR/actas.T__alice.session"

  run actas_lock_gate_try_acquire "$lock_path"
  [ "$status" -ne 0 ]
  [ "$(cat "$AGMSG_TEST_GATE_STUB_ATTEMPTS")" -eq 4 ]
  assert_output_contains "$output" "classification=transient"
  [ ! -f "$lock_path" ]
}

@test "delivery gate: writer waits for a live holder instead of reclaiming it" {
  local lock_path="$RUN_DIR/actas.T__alice.session"
  local resource
  resource="$(actas_lock_gate_resource "$lock_path")"
  sleep 60 &
  local holder=$!
  agmsg_runtime_lock_acquire "$resource" "$holder" >/dev/null

  (
    if actas_lock_gate_acquire "$lock_path"; then
      printf '%s\n' "acquired" > "$BATS_TEST_TMPDIR/gate-acquired"
      actas_lock_gate_release "$lock_path"
    fi
  ) &
  local waiter=$!
  local _gate_tick
  for _gate_tick in $(seq 1 20); do
    [ ! -e "$BATS_TEST_TMPDIR/gate-acquired" ] || break
    sleep 0.1
  done
  [ ! -e "$BATS_TEST_TMPDIR/gate-acquired" ]
  [ "$(agmsg_runtime_lock_owner "$resource")" = "$holder" ]

  agmsg_runtime_lock_release "$resource" "$holder"
  wait "$waiter"
  [ -f "$BATS_TEST_TMPDIR/gate-acquired" ]
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
}

@test "delivery gate: reclaims only a confirmed-dead holder with CAS" {
  local lock_path="$RUN_DIR/actas.T__alice.session"
  local resource
  resource="$(actas_lock_gate_resource "$lock_path")"
  bash -c 'exit 0' &
  local dead=$!
  wait "$dead"
  agmsg_runtime_lock_acquire "$resource" "$dead" >/dev/null

  actas_lock_gate_acquire "$lock_path"
  [ "$(agmsg_runtime_lock_owner "$resource")" = "$$" ]
  actas_lock_gate_release "$lock_path"
  [ -z "$(agmsg_runtime_lock_owner "$resource")" ]
}

@test "delivery gate: release failure preserves a successor owner" {
  local lock_path="$RUN_DIR/actas.T__alice.session"
  local resource
  resource="$(actas_lock_gate_resource "$lock_path")"
  sleep 60 &
  local successor=$!
  agmsg_runtime_lock_acquire "$resource" "$successor" >/dev/null

  run actas_lock_gate_release "$lock_path"
  [ "$status" -ne 0 ]
  assert_output_contains "$output" "operation=release"
  assert_output_contains "$output" "classification=permanent"
  [ "$(agmsg_runtime_lock_owner "$resource")" = "$successor" ]

  agmsg_runtime_lock_release "$resource" "$successor"
  kill "$successor" 2>/dev/null || true
  wait "$successor" 2>/dev/null || true
}

# --- writer mutation gate ---

@test "claim: unavailable delivery gate leaves the lock untouched" {
  install_runtime_lock_sqlite_stub permanent
  local lock_path
  lock_path="$(actas_lock_path T alice)"

  run actas_lock_claim T alice sid-mine
  [ "$status" -eq 3 ]
  [ ! -f "$lock_path" ]
  assert_output_contains "$output" "operation=acquire"
}

@test "release: unavailable delivery gate leaves the lock owner untouched" {
  local lock_path
  lock_path="$(actas_lock_path T alice)"
  printf '%s\n' sid-mine > "$lock_path"
  install_runtime_lock_sqlite_stub permanent

  run actas_lock_release T alice sid-mine
  [ "$status" -eq 3 ]
  [ "$(actas_lock_owner T alice)" = sid-mine ]
  assert_output_contains "$output" "operation=acquire"
}

@test "release_all: unavailable delivery gate leaves owned locks untouched" {
  local first second
  first="$(actas_lock_path T alice)"
  second="$(actas_lock_path T bob)"
  printf '%s\n' sid-mine > "$first"
  printf '%s\n' sid-mine > "$second"
  install_runtime_lock_sqlite_stub permanent

  run actas_lock_release_all sid-mine
  [ "$status" -ne 0 ]
  [ -f "$first" ]
  [ -f "$second" ]
}

@test "gc_stale: unavailable delivery gate leaves stale locks untouched" {
  local lock_path
  lock_path="$(actas_lock_path T alice)"
  printf '%s\n' sid-dead > "$lock_path"
  install_runtime_lock_sqlite_stub permanent

  run actas_lock_gc_stale
  [ "$status" -ne 0 ]
  [ "$(printf '%s\n' "$output" | tail -1)" = 0 ]
  [ -f "$lock_path" ]
}

# --- release / release_all ---

@test "release: removes a lock we own" {
  actas_lock_claim "T" "alice" "sid-mine"
  actas_lock_release "T" "alice" "sid-mine"
  [ ! -f "$(actas_lock_path "T" "alice")" ]
}

@test "release: leaves another session's lock alone" {
  fake_cc_instance "$(live_pid)" "sid-other"
  echo "sid-other" > "$(actas_lock_path "T" "alice")"
  actas_lock_release "T" "alice" "sid-mine"
  [ -f "$(actas_lock_path "T" "alice")" ]
}

@test "release_all: removes every lock owned by the sid, leaves others" {
  fake_cc_instance "$(live_pid)" "sid-keeper"
  actas_lock_claim "T1" "alice" "sid-going"
  actas_lock_claim "T2" "bob"   "sid-going"
  echo "sid-keeper" > "$(actas_lock_path "T3" "carol")"

  actas_lock_release_all "sid-going"

  [ ! -f "$(actas_lock_path "T1" "alice")" ]
  [ ! -f "$(actas_lock_path "T2" "bob")" ]
  [ -f   "$(actas_lock_path "T3" "carol")" ]
}

# --- gc_stale ---

@test "gc_stale: removes locks whose owner is dead, returns count" {
  skip_on_windows "actas live-session liveness under Git Bash (#182)"
  echo "sid-dead-1" > "$(actas_lock_path "T1" "alice")"
  echo "sid-dead-2" > "$(actas_lock_path "T2" "bob")"
  fake_cc_instance "$(live_pid)" "sid-live"
  echo "sid-live" > "$(actas_lock_path "T3" "carol")"

  run actas_lock_gc_stale
  [ "$status" -eq 0 ]
  [ "$output" = "2" ]
  [ ! -f "$(actas_lock_path "T1" "alice")" ]
  [ ! -f "$(actas_lock_path "T2" "bob")" ]
  [ -f   "$(actas_lock_path "T3" "carol")" ]
}

@test "gc_stale: noop when no stale locks" {
  skip_on_windows "actas live-session liveness under Git Bash (#182)"
  fake_cc_instance "$(live_pid)" "sid-live"
  echo "sid-live" > "$(actas_lock_path "T" "alice")"

  run actas_lock_gc_stale
  [ "$status" -eq 0 ]
  [ "$output" = "0" ]
  [ -f "$(actas_lock_path "T" "alice")" ]
}

# --- state classification ---

@test "state: free when no lock exists" {
  run actas_lock_state "T" "alice" "sid-me"
  [ "$status" -eq 0 ]
  [ "$output" = "free" ]
}

@test "state: mine when caller owns the lock" {
  actas_lock_claim "T" "alice" "sid-me"
  run actas_lock_state "T" "alice" "sid-me"
  [ "$output" = "mine" ]
}

@test "state: other:<sid> when held by a live different session" {
  skip_on_windows "actas live-session liveness under Git Bash (#182)"
  fake_cc_instance "$(live_pid)" "sid-other"
  echo "sid-other" > "$(actas_lock_path "T" "alice")"
  run actas_lock_state "T" "alice" "sid-me"
  [ "$output" = "other:sid-other" ]
}

@test "state: free when held by a dead session (stale)" {
  echo "sid-dead" > "$(actas_lock_path "T" "alice")"
  run actas_lock_state "T" "alice" "sid-me"
  [ "$output" = "free" ]
}
