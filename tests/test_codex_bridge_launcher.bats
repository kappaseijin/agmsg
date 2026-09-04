#!/usr/bin/env bats

# Unit tests for codex-bridge-launcher.sh thread resolution (#350).
# The launcher must bind the bridge to the role's RECORDED codex thread instead
# of the app-server's ambiguous "loaded" thread (which a co-resident codex thread
# in the same cwd could otherwise capture). A mock bridge records the --thread
# the launcher passes.
#
# The mock replaces codex-bridge.js itself (the file the launcher's DEFAULT
# bridge_run resolves to) rather than being swapped in via AGMSG_CODEX_BRIDGE_CMD
# (#595). AGMSG_CODEX_BRIDGE_CMD is a real, documented user-facing override (a
# custom bridge wrapper), and codex-bridge-launcher.sh takes a materially
# different code path for it (a synchronous wait on the launched process) than
# for its default codex-bridge.js path -- exercising that override path here
# tested a branch these tests have no interest in and does not run for anyone
# using agmsg without a custom wrapper, and its wait, sized for a real bridge
# process's lifetime, raced these tests' shorter deregistration-response
# assertions. Only tests/ files change here: setup_test_env already copies the
# whole scripts/ tree into an isolated $TEST_SKILL_DIR per test, so overwriting
# codex-bridge.js below mutates only that disposable copy.

load test_helper

setup() {
  setup_test_env
  export PROJ="$TEST_SKILL_DIR/proj"; mkdir -p "$PROJ"
  bash "$SCRIPTS/join.sh" team alice codex "$PROJ" >/dev/null

  export CAPTURE="$TEST_SKILL_DIR/thread-capture.txt"
  # A leaked AGMSG_CODEX_BRIDGE_CMD from the ambient environment (not this
  # file, which no longer sets it) would silently put the launcher back on the
  # override code path this suite is no longer testing -- unset it explicitly
  # rather than relying on it merely being absent here (#595).
  unset AGMSG_CODEX_BRIDGE_CMD
  [ -z "${AGMSG_CODEX_BRIDGE_CMD:-}" ]
  # Overwrite the (already-isolated, per-test) copy of codex-bridge.js with a
  # mock that records its argv. AGMSG_NODE is the documented override for the
  # Node binary codex-bridge-launcher.sh resolves this file through; pointing
  # it at bash makes bash the interpreter for this file regardless of its .js
  # name, so the launcher's default (no-custom-wrapper) path runs unmodified.
  # Mock bridge: records argv AND publishes the same per-PID identity lease the
  # real bridge does (so the reaper, which reads leases, can find/spare it). All
  # of $CAPTURE / $SCRIPTS / $RUN_DIR come from the environment it inherits.
  cat > "$SCRIPTS/drivers/types/codex/codex-bridge.js" <<'EOF'
#!/usr/bin/env bash
if [ -n "${MOCK_BRIDGE_EVENTS:-}" ]; then
  printf 'exec pid=%s ppid=%s\n' "$$" "$PPID" >> "$MOCK_BRIDGE_EVENTS"
fi
[ -z "${MOCK_BRIDGE_CAPTURE_DELAY:-}" ] || sleep "$MOCK_BRIDGE_CAPTURE_DELAY"
printf '%s\n' "$*" >> "$CAPTURE"
source "$SCRIPTS/lib/hash.sh" 2>/dev/null || true
_proj=""; _parr=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --project) _proj="$2"; shift 2 ;;
    --pair) _parr+=("$2"); shift 2 ;;
    *) shift ;;
  esac
done
_lease="$RUN_DIR/codex-bridge-lease.$$"
# Start token exactly as codex-bridge-launcher.sh _start_token computes it: /proc
# field 22 where available, else a trimmed `ps -o lstart=`.
if [ -r "/proc/$$/stat" ]; then
  _s="$(cat "/proc/$$/stat")"; _r="${_s##*)}"; read -ra _a <<< "$_r"
  _start="${_a[19]}"; _ssrc=proc
else
  _start="$(ps -o lstart= -p $$)"
  _start="${_start#"${_start%%[![:space:]]*}"}"; _start="${_start%"${_start##*[![:space:]]}"}"
  _ssrc=ps
fi
_ph=""
for _pv in "${_parr[@]}"; do _ph="$_ph$(printf '%s' "$_pv" | agmsg_sha1)
"; done
_pairs_hash="$(printf '%s' "$(printf '%s' "$_ph" | LC_ALL=C sort | sed '/^$/d')" | agmsg_sha1)"
{ printf 'v=1\nproject=%s\npairs=%s\nhost=%s\npid=%s\nstart=%s\nstartsrc=%s\n' \
    "$(printf '%s' "$_proj" | agmsg_sha1)" \
    "$_pairs_hash" \
    "$(hostname)" "$$" "$_start" "$_ssrc" ; } > "$_lease.tmp" && mv "$_lease.tmp" "$_lease"
_mock_event() {
  [ -n "${MOCK_BRIDGE_EVENTS:-}" ] || return 0
  printf '%s pid=%s startsrc=%s start=%s lease=%s\n' \
    "$1" "$$" "$_ssrc" "$_start" "$_lease" >> "$MOCK_BRIDGE_EVENTS"
}
_mock_event start
trap 'rm -f "$_lease"; _mock_event exit' EXIT
[ -z "${MOCK_BRIDGE_SLEEP:-}" ] || sleep "$MOCK_BRIDGE_SLEEP"
exit 0
EOF
  chmod +x "$SCRIPTS/drivers/types/codex/codex-bridge.js"
  export AGMSG_NODE="$(command -v bash)"
  export LAUNCHER="$SCRIPTS/drivers/types/codex/codex-bridge-launcher.sh"
}

# PIDs of live per-role child launchers for this test's project: any process
# whose argv contains both LAUNCHER and PROJ, one line per pid. Not scoped to a
# single role name -- teardown must reap every role's child a test spawned
# (e.g. "the identity cache still sees a role added mid-loop" joins a second
# role, "bob", mid-test), unlike count_child_launchers below, which measures
# one specific role on purpose. This also does not dedupe transient
# command-substitution subshells by parent pid -- for killing that distinction
# does not matter, signaling and waiting on a subshell that has already
# exited on its own is a harmless no-op.
_launcher_child_pids() {
  local launcher="${CHILD_LAUNCHER_PATTERN:-$LAUNCHER}"
  ps -Ao pid=,args= 2>/dev/null \
    | grep -F "$launcher" \
    | grep -F "$PROJ" \
    | awk '{print $1}'
}

# Return success only for the POSIX process namespace where this suite's argv
# and pidfile ownership checks are valid. Git Bash's MSYS/MINGW PID mapping is
# a separate diagnostic boundary (#169B), so an unset or unknown mapping is
# never turned into a kill target here.
_launcher_posix_processes() {
  case "${MSYSTEM:-}" in
    '') return 0 ;;
    *) return 1 ;;
  esac
}

# Take one coherent process-table snapshot and add only this test's bridge
# pidfiles. The argv predicates require this test's isolated project path as
# well as the process identity, so a foreign listener in the same project and
# a launcher for another project cannot enter the set.
_launcher_snapshot_owned_pids() {
  _launcher_posix_processes || return 0
  [ -n "${RUN_DIR:-}" ] || return 0
  [ -n "${PROJ:-}" ] || return 0

  local table pidfile pid launcher="${CHILD_LAUNCHER_PATTERN:-$LAUNCHER}"
  if ! table="$(ps -Ao pid=,args= 2>/dev/null)"; then
    printf '%s\n' 'launcher test reaper: process snapshot unavailable' >&2
    return 1
  fi

  {
    printf '%s\n' "$table" \
      | awk -v launcher="$launcher" -v project="$PROJ" \
        'index($0, launcher) && index($0, project) && $1 ~ /^[1-9][0-9]*$/ { print $1 }'

    for pidfile in "$RUN_DIR"/codex-bridge.*.pid; do
      [ -f "$pidfile" ] || continue
      pid=''
      IFS= read -r pid < "$pidfile" 2>/dev/null || true
      case "$pid" in
        ''|*[!0-9]*|0) ;;
        *) printf '%s\n' "$pid" ;;
      esac
    done

    printf '%s\n' "$table" \
      | awk -v project="$PROJ" \
        'index($0, project) && index($0, "codex-bridge.js") && $1 ~ /^[1-9][0-9]*$/ { print $1 }'
  } | awk '!seen[$1]++ { print $1 }'
}

# A test's own kill/wait sequence reaches the dispatcher and the short-lived
# parent it was handed, but a per-role child (nohup'd, independent of both) and
# the bridge process it launched are not direct children of anything a test
# holds a pid for. Snapshot the pid set once, signal all of it, then wait for
# all of it, rather than interleaving kill/wait against a changing process
# table or pidfile view. Waiting for the reap keeps teardown_test_env's rm -rf
# from racing a process still touching this test's $TEST_SKILL_DIR (#595/#615).
_reap_test_owned_codex_processes() {
  _launcher_posix_processes || return 0

  local snapshot pid reap_status=0
  if ! snapshot="$(_launcher_snapshot_owned_pids)"; then
    return 1
  fi

  for pid in $snapshot; do
    kill "$pid" 2>/dev/null || true
  done
  for pid in $snapshot; do
    if ! wait_for_pid_exit "$pid"; then
      printf 'launcher test reaper: pid %s did not exit\n' "$pid" >&2
      reap_status=1
    fi
  done
  return "$reap_status"
}

teardown() {
  local cleanup_rc=0 pid
  if ! _reap_test_owned_codex_processes; then
    cleanup_rc=1
  fi

  # These are test-only controls that deliberately exercise the MSYS no-op
  # path or do not carry an owned launcher/bridge identity.
  for pid in ${TEST_LAUNCHER_EXTRA_PIDS:-}; do
    case "$pid" in
      ''|*[!0-9]*|0) ;;
      *) kill "$pid" 2>/dev/null || true ;;
    esac
  done
  for pid in ${TEST_LAUNCHER_EXTRA_PIDS:-}; do
    case "$pid" in
      ''|*[!0-9]*|0) ;;
      *)
        if ! wait_for_pid_exit "$pid"; then
          cleanup_rc=1
        fi
        ;;
    esac
  done

  if ! teardown_test_env; then
    cleanup_rc=1
  fi
  return "$cleanup_rc"
}

@test "launcher: test-owned reaper is a no-op in MSYS pid space" {
  skip_on_windows "models the unverified Git Bash namespace from POSIX"

  local owned_script="$TEST_SKILL_DIR/owned-launcher"
  cat > "$owned_script" <<'EOF'
#!/usr/bin/env bash
while :; do sleep 0.1; done
EOF
  chmod +x "$owned_script"
  bash "$owned_script" "$LAUNCHER" "$PROJ" 3>&- & local owned_pid=$!
  printf '%s\n' "$owned_pid" > "$RUN_DIR/codex-bridge.synthetic.pid"
  TEST_LAUNCHER_EXTRA_PIDS="$owned_pid"

  export MSYSTEM=MINGW64
  run _reap_test_owned_codex_processes
  [ "$status" -eq 0 ]
  kill -0 "$owned_pid"

  kill "$owned_pid" 2>/dev/null || true
  wait "$owned_pid" 2>/dev/null || true
  TEST_LAUNCHER_EXTRA_PIDS=''
}

@test "launcher: reaps owned launcher and bridge but leaves foreign controls" {
  skip_on_windows "requires POSIX process argv and kill semantics"

  (
    set -e
    parent=''; dispatcher=''; foreign=''; foreign_launcher=''
    cleanup() {
      local pid
      for pid in "$dispatcher" "$parent" "$foreign" "$foreign_launcher"; do
        case "$pid" in
          ''|*[!0-9]*|0) ;;
          *) kill "$pid" 2>/dev/null || true ;;
        esac
      done
      for pid in "$dispatcher" "$parent" "$foreign" "$foreign_launcher"; do
        case "$pid" in
          ''|*[!0-9]*|0) ;;
          *) wait "$pid" 2>/dev/null || true ;;
        esac
      done
    }
    trap cleanup EXIT

    export MOCK_BRIDGE_SLEEP=25
    put_record team alice test-thread "$PROJ" codex
    sleep 30 3>&- & parent=$!
    bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent" \
      >/dev/null 2>&1 3>&- & dispatcher=$!

    foreign_ready="$TEST_SKILL_DIR/foreign.ready"
    python3 -c '
import socket, sys
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", 0)); s.listen(8)
open(sys.argv[1], "w").write(str(s.getsockname()[1]))
sys.stdout.flush()
while True:
    c, _ = s.accept(); c.close()
' "$foreign_ready" "$PROJ" 3>&- & foreign=$!
    wait_for_file "$foreign_ready"
    kill -0 "$foreign"

    other_project="$TEST_SKILL_DIR/other-project"
    foreign_launcher_script="$TEST_SKILL_DIR/foreign-launcher"
    mkdir -p "$other_project"
    cat > "$foreign_launcher_script" <<'EOF'
#!/usr/bin/env bash
while :; do sleep 0.1; done
EOF
    chmod +x "$foreign_launcher_script"
    bash "$foreign_launcher_script" "$LAUNCHER" "$other_project" 3>&- &
    foreign_launcher=$!

    snapshot=''; bridge_pid=''; i=0
    for i in {1..100}; do
      snapshot="$(_launcher_snapshot_owned_pids)"
      if [ -n "$snapshot" ] \
        && [ -s "$RUN_DIR/codex-bridge.team.alice.pid" ] \
        && kill -0 "$foreign" 2>/dev/null \
        && kill -0 "$foreign_launcher" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done
    [ -n "$snapshot" ]
    bridge_pid="$(cat "$RUN_DIR/codex-bridge.team.alice.pid")"
    [ -n "$bridge_pid" ]
    kill -0 "$dispatcher"
    kill -0 "$bridge_pid"
    printf '%s\n' "$snapshot" | grep -Fxq "$dispatcher"
    printf '%s\n' "$snapshot" | grep -Fxq "$bridge_pid"
    ! printf '%s\n' "$snapshot" | grep -Fxq "$foreign"
    ! printf '%s\n' "$snapshot" | grep -Fxq "$foreign_launcher"

    _reap_test_owned_codex_processes
    for i in $snapshot; do
      wait_for_pid_exit "$i"
    done
    kill -0 "$foreign"
    kill -0 "$foreign_launcher"
  )
}

@test "launcher: test-owned reaper waits for every signaled pid before returning" {
  skip_on_windows "requires POSIX process and signal semantics"

  (
    set -e
    release="$TEST_SKILL_DIR/reaper.release"
    term_marker="$TEST_SKILL_DIR/reaper.term"
    holder="$TEST_SKILL_DIR/reaper-holder"
    owned_pid=''; reaper_pid=''
    cleanup() {
      local pid
      for pid in "$reaper_pid" "$owned_pid"; do
        case "$pid" in
          ''|*[!0-9]*|0) ;;
          *) kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true ;;
        esac
      done
    }
    trap cleanup EXIT

    cat > "$holder" <<'EOF'
#!/usr/bin/env bash
trap ': > "$TERM_MARKER"; while [ ! -e "$RELEASE_MARKER" ]; do sleep 0.1; done; exit 0' TERM INT
while :; do sleep 0.1; done
EOF
    chmod +x "$holder"
    TERM_MARKER="$term_marker" RELEASE_MARKER="$release" \
      bash "$holder" 3>&- & owned_pid=$!
    printf '%s\n' "$owned_pid" > "$RUN_DIR/codex-bridge.wait.pid"

    _reap_test_owned_codex_processes & reaper_pid=$!
    wait_for_file "$term_marker"
    kill -0 "$reaper_pid"

    : > "$release"
    wait "$reaper_pid"
    wait_for_pid_exit "$owned_pid"
  )
}

# Write a role-session record (team, agent) -> thread for a project.
put_record() {
  SKILL_DIR="$TEST_SKILL_DIR" bash -c \
    'source "$1/lib/role-session.sh"; agmsg_role_session_record "$2" "$3" "$4" "$5" "$6"' \
    _ "$SCRIPTS" "$@"
}

write_request() {
  local thread="$1" hash
  hash=$(SKILL_DIR="$TEST_SKILL_DIR" bash -c \
    'source "$1/lib/hash.sh"; printf "%s" "$2" | agmsg_sha1' _ "$SCRIPTS" "$PROJ")
  printf 'codex\t%s\tws://127.0.0.1:1\n' "$thread" > "$RUN_DIR/codex-bridge-request.$hash"
}

# Drive the launcher against a short-lived parent, blocking until it exits. fd 3
# is closed on the backgrounded parent and the launcher so a stray descriptor
# can't keep bats from exiting on macOS (#bats-fd3). Pass `no-capture` for a
# negative test that proves no bridge was started.
run_launcher() {
  local expectation="${1:-capture}"
  sleep 6 3>&- & local p=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$p" >/dev/null 2>&1 3>&- || true
  wait "$p" 2>/dev/null || true
  [ "$expectation" = "no-capture" ] && return 0
  if ! wait_for_file "$CAPTURE"; then
    echo "bridge start did not complete: capture file was not created" >&2
    if [ -f "$RUN_DIR/codex-bridge.team.alice.pid" ]; then
      echo "bridge pidfile: $(cat "$RUN_DIR/codex-bridge.team.alice.pid")" >&2
    else
      echo "bridge pidfile: absent" >&2
    fi
    return 1
  fi
}

# Record the launch points that matter to the native-Windows lifecycle test.
# The dispatcher and role child are both detached from the Bats process, so a
# failure needs their first observed PIDs rather than a final process listing.
record_windows_native_events() {
  local event_file="$1" dispatcher_pid="$2" parent_pid="$3" child
  if ! grep -q '^dispatcher-start ' "$event_file" 2>/dev/null; then
    printf 'dispatcher-start pid=%s parent=%s\n' "$dispatcher_pid" "$parent_pid" >> "$event_file"
  fi
  grep -q '^role-child-start ' "$event_file" 2>/dev/null && return 0
  for child in $(_launcher_child_pids); do
    grep -q "^role-child-start pid=$child " "$event_file" 2>/dev/null && continue
    printf 'role-child-start pid=%s dispatcher=%s parent=%s\n' \
      "$child" "$dispatcher_pid" "$parent_pid" >> "$event_file"
  done
}

windows_native_diagnostics() {
  local parent_pid="$1" dispatcher_pid="$2" event_file="$3" snapshot
  echo "windows-native diagnostics:"
  echo "capture path: $CAPTURE"
  echo "parent pid: $parent_pid"
  echo "dispatcher pid: $dispatcher_pid"
  echo "MSYSTEM: ${MSYSTEM:-unset}"
  echo "tasklist result:"
  if command -v tasklist >/dev/null 2>&1; then
    MSYS_NO_PATHCONV=1 tasklist /FI "PID eq $parent_pid" 2>&1 || true
  else
    echo "tasklist: unavailable"
  fi
  echo "local pid probe:"
  if (source "$SCRIPTS/lib/instance-id.sh"; _agmsg_pid_alive_local "$parent_pid"); then
    echo "parent is alive by local probe"
  else
    echo "parent is not alive by local probe"
  fi
  echo "dispatcher/role-child start events:"
  if [ -f "$event_file" ]; then
    sed -n -e '/^dispatcher-start /p' -e '/^role-child-start /p' "$event_file"
  else
    echo "no dispatcher or role-child event observed"
  fi
  echo "bridge exec events:"
  if [ -f "$MOCK_BRIDGE_EVENTS" ]; then
    sed -n '/^exec /p' "$MOCK_BRIDGE_EVENTS"
  else
    echo "no bridge exec event observed"
  fi
  echo "process snapshot:"
  snapshot="$(ps -Ao pid=,ppid=,stat=,args= 2>&1 || true)"
  printf '%s\n' "$snapshot" | grep -F -e "$LAUNCHER" -e 'codex-bridge.js' -e "$PROJ" || true
}

cleanup_windows_native_processes() {
  local dispatcher_pid="$1" parent_pid="$2"
  # Signal both direct children before waiting for either. Waiting on the
  # dispatcher first can hold the parent alive until its full test timer when a
  # native signal is delivered between two launcher polls.
  kill "$dispatcher_pid" 2>/dev/null || true
  kill "$parent_pid" 2>/dev/null || true
  wait "$dispatcher_pid" 2>/dev/null || true
  wait "$parent_pid" 2>/dev/null || true
}

# Win32_Process is the only process table here that exposes the command line
# together with the native PID. tasklist is then the liveness authority for
# that native PID; never pass an MSYS PID to taskkill (#169).
_windows_native_require_bridge_tools() {
  local tool
  for tool in powershell.exe cygpath tasklist taskkill; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      printf 'windows-native bridge cleanup failure: command unavailable: %s\n' \
        "$tool" >&2
      return 1
    fi
  done
}

_windows_native_query_bridge_pids() {
  local root="$1" native_root='' process_record process_rc path_rc pid valid_pids=''

  if native_root="$(MSYS_NO_PATHCONV=1 cygpath -m "$root" 2>&1)"; then
    path_rc=0
  else
    path_rc="$?"
  fi
  native_root="$(printf '%s\n' "$native_root" | tr -d '\r')"
  if [ "$path_rc" -ne 0 ] || [ -z "$native_root" ]; then
    printf 'windows-native bridge cleanup failure: cygpath root=%s rc=%s output=%s\n' \
      "$root" "$path_rc" "$native_root" >&2
    return 1
  fi

  if process_record="$(
    AGMSG_WINDOWS_ROOT_POSIX="$root" \
    AGMSG_WINDOWS_ROOT_NATIVE="$native_root" \
    MSYS_NO_PATHCONV=1 powershell.exe -NoProfile -NonInteractive -Command '
      $ErrorActionPreference = "Stop"
      try {
        $needles = @($env:AGMSG_WINDOWS_ROOT_POSIX, $env:AGMSG_WINDOWS_ROOT_NATIVE) |
          Where-Object { $_ } |
          ForEach-Object { $_.Replace("\", "/").ToLowerInvariant() }
        Get-CimInstance Win32_Process |
          Where-Object {
            $line = $_.CommandLine
            if (-not $line) { return $false }
            $normalized = $line.Replace("\", "/").ToLowerInvariant()
            $normalized.Contains("codex-bridge.js") -and
              (($needles | Where-Object { $normalized.Contains($_) }).Count -gt 0)
          } |
          ForEach-Object { $_.ProcessId }
      } catch {
        Write-Error $_
        exit 1
      }
    ' 2>&1
  )"; then
    process_rc=0
  else
    process_rc="$?"
  fi
  process_record="$(printf '%s\n' "$process_record" | tr -d '\r')"
  if [ "$process_rc" -ne 0 ]; then
    printf 'windows-native bridge cleanup failure: Win32_Process query root=%s rc=%s output=%s\n' \
      "$root" "$process_rc" "$process_record" >&2
    return 1
  fi

  for pid in $process_record; do
    case "$pid" in
      ''|*[!0-9]*|0)
        printf 'windows-native bridge cleanup failure: invalid native PID root=%s value=%s\n' \
          "$root" "$pid" >&2
        return 1
        ;;
    esac
    valid_pids="${valid_pids}${valid_pids:+$'\n'}$pid"
  done
  printf '%s\n' "$valid_pids" | awk 'NF && !seen[$1]++ { print $1 }'
}

# Return 0 when tasklist has the native PID, 1 when it is definitely gone,
# and 2 when the state cannot be classified. Unknown is a test failure, not a
# reason to continue toward rm.
_windows_native_tasklist_has_pid() {
  local pid="$1" tasklist_record
  case "$pid" in
    ''|*[!0-9]*|0)
      printf 'windows-native bridge cleanup failure: invalid tasklist PID=%s\n' "$pid" >&2
      return 2
      ;;
  esac

  if tasklist_record="$(MSYS_NO_PATHCONV=1 tasklist /FI "PID eq $pid" /FO CSV /NH 2>&1)"; then
    :
  else
    printf 'windows-native bridge cleanup failure: tasklist PID=%s output=%s\n' \
      "$pid" "$tasklist_record" >&2
    return 2
  fi
  tasklist_record="$(printf '%s\n' "$tasklist_record" | tr -d '\r')"

  if printf '%s\n' "$tasklist_record" | awk -F',' -v pid="$pid" \
    '$2 == "\"" pid "\"" { found=1 } END { exit !found }'; then
    return 0
  fi
  case "$tasklist_record" in
    ''|INFO:*|*No\ tasks*|*no\ tasks*) return 1 ;;
  esac
  printf 'windows-native bridge cleanup failure: tasklist PID=%s unclassified output=%s\n' \
    "$pid" "$tasklist_record" >&2
  return 2
}

_windows_native_bridge_pids() {
  local root="$1" queried_pids live_pids='' pid tasklist_rc
  _windows_native_require_bridge_tools || return 1
  if ! queried_pids="$(_windows_native_query_bridge_pids "$root")"; then
    return 1
  fi

  for pid in $queried_pids; do
    if _windows_native_tasklist_has_pid "$pid"; then
      live_pids="${live_pids}${live_pids:+$'\n'}$pid"
    else
      tasklist_rc="$?"
      case "$tasklist_rc" in
        1) ;; # The Win32 query raced a naturally exiting process.
        *) return 1 ;;
      esac
    fi
  done
  printf '%s\n' "$live_pids" | awk 'NF && !seen[$1]++ { print $1 }'
}

_windows_native_assert_no_bridge_processes() {
  local root="$1" pids
  if ! pids="$(_windows_native_bridge_pids "$root")"; then
    return 1
  fi
  if [ -n "$pids" ]; then
    printf 'windows-native bridge cleanup failure: live codex-bridge.js root=%s native_pids=%s\n' \
      "$root" "$(printf '%s' "$pids" | tr '\n' ',')" >&2
    return 1
  fi
}

_windows_native_assert_bridge_alive() {
  local root="$1" pids
  if ! pids="$(_windows_native_bridge_pids "$root")"; then
    return 1
  fi
  if [ -z "$pids" ]; then
    printf 'windows-native bridge negative control failure: foreign root is not alive: %s\n' \
      "$root" >&2
    return 1
  fi
}

_windows_native_wait_tasklist_gone() {
  local pid="$1" timeout_s poll_s start deadline attempts=0 tasklist_rc state=present
  timeout_s="$(_agmsg_test_wait_timeout_s)" || return 1
  poll_s="$(_agmsg_test_wait_poll_s)" || return 1
  start=$SECONDS
  deadline=$((start + timeout_s))
  while [ "$SECONDS" -lt "$deadline" ]; do
    attempts=$((attempts + 1))
    if _windows_native_tasklist_has_pid "$pid"; then
      state=present
      sleep "$poll_s"
      continue
    else
      tasklist_rc="$?"
    fi
    case "$tasklist_rc" in
      1) return 0 ;;
      *)
        printf 'windows-native bridge cleanup failure: tasklist state unknown pid=%s\n' \
          "$pid" >&2
        return 1
        ;;
    esac
  done
  _agmsg_test_wait_timeout_diag \
    _windows_native_wait_tasklist_gone tasklist-pid-gone "$pid" \
    "$timeout_s" "$start" "$poll_s" "$attempts" "$state"
  printf 'windows-native bridge cleanup failure: native PID remained after taskkill pid=%s\n' \
    "$pid" >&2
  return 1
}

_windows_native_reap_bridge_root() {
  local root="$1" pids pid taskkill_output taskkill_rc wait_rc reap_status=0
  if ! pids="$(_windows_native_bridge_pids "$root")"; then
    return 1
  fi
  # A failed wait must not prevent later candidates from receiving taskkill.
  # Aggregate failures and make the final residual check after every attempt.
  for pid in $pids; do
    if taskkill_output="$(MSYS_NO_PATHCONV=1 taskkill /PID "$pid" /T /F 2>&1)"; then
      taskkill_rc=0
    else
      taskkill_rc="$?"
      reap_status=1
      printf 'windows-native bridge cleanup failure: taskkill PID=%s rc=%s output=%s\n' \
        "$pid" "$taskkill_rc" "$taskkill_output" >&2
    fi
    if _windows_native_wait_tasklist_gone "$pid"; then
      wait_rc=0
    else
      wait_rc="$?"
      reap_status=1
      printf 'windows-native bridge cleanup failure: wait tasklist gone PID=%s rc=%s taskkill_rc=%s output=%s\n' \
        "$pid" "$wait_rc" "$taskkill_rc" "$taskkill_output" >&2
    fi
  done
  if ! _windows_native_assert_no_bridge_processes "$root"; then
    reap_status=1
  fi
  return "$reap_status"
}

_windows_native_wait_bridge_pid() {
  local root="$1" timeout_s poll_s start deadline attempts=0 pids
  timeout_s="$(_agmsg_test_wait_timeout_s)" || return 1
  poll_s="$(_agmsg_test_wait_poll_s)" || return 1
  start=$SECONDS
  deadline=$((start + timeout_s))
  while [ "$SECONDS" -lt "$deadline" ]; do
    attempts=$((attempts + 1))
    if ! pids="$(_windows_native_bridge_pids "$root")"; then
      return 1
    fi
    if [ -n "$pids" ]; then
      printf '%s\n' "$pids"
      return 0
    fi
    sleep "$poll_s"
  done
  _agmsg_test_wait_timeout_diag \
    _windows_native_wait_bridge_pid command-line-pid-visible "$root" \
    "$timeout_s" "$start" "$poll_s" "$attempts" absent
  printf 'windows-native bridge setup failure: no live codex-bridge.js for root=%s\n' \
    "$root" >&2
  return 1
}

@test "launcher: binds the recorded thread when the record's project matches (#350)" {
  put_record team alice rec-thread-1 "$PROJ" codex
  run_launcher
  [ -f "$CAPTURE" ]
  grep -q -- "--thread rec-thread-1" "$CAPTURE"
  ! grep -q -- "--thread loaded" "$CAPTURE"
}

@test "launcher: dispatcher initializes a fresh runtime store once before its lock" {
  local fresh_store="$BATS_TEST_TMPDIR/fresh-runtime-store"
  local init_count="$BATS_TEST_TMPDIR/init-db-count"
  local real_init="$SKILL_DIR/scripts/internal/init-db.real.sh"
  printf '0\n' > "$init_count"
  mv "$SKILL_DIR/scripts/internal/init-db.sh" "$real_init"
  cat > "$SKILL_DIR/scripts/internal/init-db.sh" <<SH
#!/usr/bin/env bash
count=0
[ -s "$init_count" ] && count="\$(cat "$init_count")"
printf '%s\n' \$((count + 1)) > "$init_count"
exec bash "$real_init" "\$@"
SH
  chmod +x "$SKILL_DIR/scripts/internal/init-db.sh"
  export AGMSG_STORAGE_PATH="$fresh_store"
  put_record team alice fresh-thread "$PROJ" codex

  run_launcher

  [ "$(cat "$init_count")" -eq 1 ]
  [ "$(sqlite3 "$fresh_store/messages.db" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='locks';")" -eq 1 ]
}

@test "launcher: waits for a delayed bridge start beyond the legacy poll window" {
  export MOCK_BRIDGE_CAPTURE_DELAY=10
  put_record team alice delayed-thread "$PROJ" codex
  run_launcher

  [ -f "$CAPTURE" ]
  grep -q -- "--thread delayed-thread" "$CAPTURE"
}

@test "launcher: passes the active storage override as a workspace root" {
  export AGMSG_STORAGE_PATH="$TEST_SKILL_DIR/custom-store"
  put_record team alice rec-thread-1 "$PROJ" codex
  run_launcher

  grep -q -- "--workspace-root $AGMSG_STORAGE_PATH" "$CAPTURE"
  ! grep -q -- "--workspace-root $TEST_SKILL_DIR/db" "$CAPTURE"
}

@test "launcher: leaves a role without a recorded live thread unsubscribed (#150)" {
  run_launcher no-capture
  [ ! -f "$CAPTURE" ]
}

@test "launcher: leaves a role with a foreign-project record unsubscribed (#150)" {
  put_record team alice other-thread "/some/other/project" codex
  run_launcher no-capture
  [ ! -f "$CAPTURE" ]
}

@test "launcher: writes the bound-thread file so a later launcher can rebind (#350)" {
  put_record team alice rec-thread-1 "$PROJ" codex
  run_launcher
  [ "$(cat "$RUN_DIR/codex-bridge.team.alice.thread" 2>/dev/null)" = "rec-thread-1" ]
}

@test "launcher: replaces a stale role pidfile with the spawned bridge pid" {
  put_record team alice rec-thread-1 "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=3
  printf '%s\n' 99999999 > "$RUN_DIR/codex-bridge.team.alice.pid"
  run_launcher 3>&- & local driver_pid=$!

  local i recorded=""
  for i in {1..50}; do
    recorded="$(cat "$RUN_DIR/codex-bridge.team.alice.pid" 2>/dev/null || true)"
    [ -n "$recorded" ] && [ "$recorded" != 99999999 ] && break
    sleep 0.1
  done
  [ -n "$recorded" ]
  [ "$recorded" != 99999999 ]
  kill -0 "$recorded"

  wait "$driver_pid" 2>/dev/null || true
}

@test "launcher: starts one bridge per recorded role and thread (#150 phase 2)" {
  bash "$SCRIPTS/join.sh" team bob codex "$PROJ" >/dev/null
  put_record team alice thread-alice "$PROJ" codex
  put_record team bob thread-bob "$PROJ" codex
  run_launcher

  local i lines=0
  for i in {1..30}; do
    if [ -f "$CAPTURE" ]; then
      lines=$(wc -l < "$CAPTURE" | tr -d ' ')
    fi
    [ "$lines" -ge 2 ] && break
    sleep 0.1
  done
  [ "$lines" -ge 2 ]
  grep -q -- $'--pair team\talice --thread thread-alice' "$CAPTURE"
  grep -q -- $'--pair team\tbob --thread thread-bob' "$CAPTURE"
}

@test "launcher: only one dispatcher runs per project" {
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=8
  sleep 10 3>&- & local parent_a=$!
  sleep 10 3>&- & local parent_b=$!

  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent_a" >/dev/null 2>&1 3>&- &
  local launcher_a=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent_b" >/dev/null 2>&1 3>&- &
  local launcher_b=$!

  local i
  for i in {1..50}; do
    [ -f "$CAPTURE" ] && break
    sleep 0.1
  done
  [ -f "$CAPTURE" ]
  [ "$(wc -l < "$CAPTURE" | tr -d ' ')" -eq 1 ]

  wait "$launcher_a" 2>/dev/null || true
  wait "$launcher_b" 2>/dev/null || true
  wait "$parent_a" 2>/dev/null || true
  wait "$parent_b" 2>/dev/null || true
}

@test "launcher: stale dispatcher reclamation remains singleton under contention" {
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=8
  local hash lock_db
  hash=$(printf '%s' "$PROJ" | bash -c 'source "$1"; agmsg_sha1' _ "$SCRIPTS/lib/hash.sh")
  lock_db="$TEST_SKILL_DIR/db/messages.db"
  sqlite3 "$lock_db" "INSERT OR REPLACE INTO locks VALUES('codex-dispatcher:$hash', 99999999, datetime('now'));"
  # A crash from the former two-directory implementation can leave this behind.
  # The transactional lock protocol must not depend on that legacy reaper.
  mkdir "$RUN_DIR/codex-bridge-dispatcher.$hash.reap"
  export AGMSG_TEST_DISPATCHER_STALE_BARRIER="$TEST_SKILL_DIR/stale-observed"
  sleep 10 3>&- & local parent_a=$!
  sleep 10 3>&- & local parent_b=$!

  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent_a" >/dev/null 2>&1 3>&- &
  local launcher_a=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent_b" >/dev/null 2>&1 3>&- &
  local launcher_b=$!

  local i
  for i in {1..50}; do
    [ -f "$CAPTURE" ] && break
    sleep 0.1
  done
  [ -f "$CAPTURE" ]
  [ "$(wc -l < "$CAPTURE" | tr -d ' ')" -eq 1 ]

  wait "$launcher_a" 2>/dev/null || true
  wait "$launcher_b" 2>/dev/null || true
  wait "$parent_a" 2>/dev/null || true
  wait "$parent_b" 2>/dev/null || true
}

@test "launcher: project request thread never overrides per-role recorded threads (#150 phase 2)" {
  bash "$SCRIPTS/join.sh" team bob codex "$PROJ" >/dev/null
  put_record team alice thread-alice "$PROJ" codex
  put_record team bob thread-bob "$PROJ" codex
  write_request thread-bob
  run_launcher

  grep -q -- $'--pair team\talice --thread thread-alice' "$CAPTURE"
  grep -q -- $'--pair team\tbob --thread thread-bob' "$CAPTURE"
  ! grep -q -- $'--pair team\talice --thread thread-bob' "$CAPTURE"
}

@test "launcher: role record update keeps child scoped to the same pair" {
  put_record team alice thread-before "$PROJ" codex
  sleep 6 3>&- & local p=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$p" $'team\talice' >/dev/null 2>&1 3>&- &
  local launcher_pid=$!
  local i
  for i in {1..50}; do
    grep -q -- $'--pair team\talice --thread thread-before' "$CAPTURE" 2>/dev/null && break
    sleep 0.1
  done
  grep -q -- $'--pair team\talice --thread thread-before' "$CAPTURE"
  put_record team alice thread-after "$PROJ" codex
  wait "$launcher_pid" 2>/dev/null || true
  wait "$p" 2>/dev/null || true

  grep -q -- $'--pair team\talice --thread thread-before' "$CAPTURE"
  grep -q -- $'--pair team\talice --thread thread-after' "$CAPTURE"
  ! grep -q -- '--pair team bob' "$CAPTURE"
}

# Return the independent role-child roots for this test's project. A child is
# distinguished from a dispatcher by carrying the role pair as its 5th argument;
# match on the agent name rather than the whole pair, because macOS ps renders
# the tab inside that argument as the escape sequence \011, not a literal tab.
#
# Only processes whose parent is not itself a match are counted. Every command
# substitution the launcher runs forks a subshell that inherits the launcher's
# argv, so those subshells are indistinguishable from a real child by command
# line alone -- a naive count reads 3 where there is one child, depending purely
# on when the sample lands. Filtering on ppid counts independent children, which
# is the property these tests are actually about.
_child_launcher_roots() {
  local launcher="${CHILD_LAUNCHER_PATTERN:-$LAUNCHER}"
  local role="${CHILD_ROLE:-alice}"
  ps -Ao pid=,ppid=,args= 2>/dev/null \
    | awk -v launcher="$launcher" -v project="$PROJ" -v role="$role" '
        $3 != "awk" && index($0, launcher) && index($0, project) && index($0, role) {
          pid[$1] = 1
          parent[$1] = $2
        }
        END {
          for (p in pid) if (!(parent[p] in pid)) print p, parent[p]
        }' \
    | sort -n
}

_count_root_lines() {
  if [ -n "$1" ]; then
    printf '%s\n' "$1" | awk 'NF { n++ } END { print n + 0 }'
  else
    printf '0\n'
  fi
}

count_child_launchers() {
  ps -Ao pid=,ppid=,args= 2>/dev/null \
    | grep -F "$LAUNCHER" \
    | grep -F "$PROJ" \
    | grep alice \
    | awk '{ pid[$1] = 1; parent[$1] = $2 }
           END { n = 0; for (p in pid) if (!(parent[p] in pid)) n++; print n }'
}

# Block until the child count settles on <n>, then return it. Spawn and exit are
# both asynchronous, so sampling on the first sighting races the transition.
wait_for_child_count() {
  local want="$1" i
  for i in {1..100}; do
    [ "$(count_child_launchers)" -eq "$want" ] && break
    sleep 0.1
  done
  count_child_launchers
}

# Observe a count and its root identities over consecutive samples. The
# identity comparison is what distinguishes a settled singleton from the
# transient second child that exists while the per-role lock rejects it.
_wait_for_stable_child_roots() { # <count> <samples> <seconds>
  local want="$1" samples="$2" seconds="$3"
  local i j roots next stable
  CHILD_ROOT_SNAPSHOT=""
  for ((i = 0; i < seconds * 10; i++)); do
    roots="$(_child_launcher_roots)"
    if [ "$(_count_root_lines "$roots")" -eq "$want" ]; then
      stable=1
      for ((j = 1; j < samples; j++)); do
        sleep 0.1
        next="$(_child_launcher_roots)"
        if [ "$(_count_root_lines "$next")" -ne "$want" ] || [ "$next" != "$roots" ]; then
          stable=0
          break
        fi
      done
      if [ "$stable" -eq 1 ]; then
        CHILD_ROOT_SNAPSHOT="$roots"
        return 0
      fi
    fi
    sleep 0.1
  done
  return 1
}

_wait_for_pid_alive() { # <pid> <seconds>
  local pid="$1" seconds="$2" i
  for ((i = 0; i < seconds * 10; i++)); do
    kill -0 "$pid" 2>/dev/null && return 0
    sleep 0.1
  done
  return 1
}

_runtime_lock_owner() { # <resource>
  sqlite3 "$TEST_SKILL_DIR/db/messages.db" \
    "SELECT owner_pid FROM locks WHERE resource = '$1';" 2>/dev/null \
    | tr -d '\r'
}

_wait_for_runtime_lock_owner() { # <resource> <pid> <seconds>
  local resource="$1" expected="$2" seconds="$3" i
  for ((i = 0; i < seconds * 10; i++)); do
    [ "$(_runtime_lock_owner "$resource")" = "$expected" ] && return 0
    sleep 0.1
  done
  return 1
}

_make_no_role_lock_launcher() {
  NO_ROLE_LOCK_LAUNCHER="$SCRIPTS/drivers/types/codex/codex-bridge-launcher-no-role-lock.sh"
  grep -Fqx 'acquire_runtime_lock "$CHILD_LOCK_RESOURCE" || exit 0' "$LAUNCHER" || return 1
  sed 's@^acquire_runtime_lock "\$CHILD_LOCK_RESOURCE" || exit 0$@# role lock disabled for #485 negative control@' \
    "$LAUNCHER" > "$NO_ROLE_LOCK_LAUNCHER" || return 1
  chmod +x "$NO_ROLE_LOCK_LAUNCHER"
  grep -Fqx '# role lock disabled for #485 negative control' "$NO_ROLE_LOCK_LAUNCHER"
}

_diagnose_485_failure() {
  local pid state label
  printf '485 diagnostics: child_launcher=%s expected_roots=%s root_snapshot=%s\n' \
    "${CHILD_LAUNCHER_PATTERN:-$LAUNCHER}" \
    "${EXPECTED_CHILD_ROOTS:-unknown}" "${CHILD_ROOT_SNAPSHOT:-empty}" >&2
  printf '485 dispatchers:\n' >&2
  for label in A B; do
    if [ "$label" = A ]; then pid="${dispatcher_a:-}"; else pid="${dispatcher_b:-}"; fi
    printf '  dispatcher_%s pid=%s\n' "$label" "${pid:-unknown}" >&2
    [ -n "$pid" ] || continue
    ps -o pid=,ppid=,stat=,args= -p "$pid" 2>/dev/null \
      | sed 's/^/    /' >&2 || true
  done
  printf '485 parent liveness:\n' >&2
  for pid in "${parent_a:-}" "${parent_b:-}"; do
    [ -n "$pid" ] || continue
    if kill -0 "$pid" 2>/dev/null; then state=alive; else state=dead; fi
    printf '  pid=%s state=%s\n' "$pid" "$state" >&2
  done
  printf '485 role-child roots:\n' >&2
  _child_launcher_roots | sed 's/^/  /' >&2 || true
  printf '485 role-child descendants:\n' >&2
  ps -Ao pid=,ppid=,stat=,args= 2>/dev/null \
    | awk -v project="$PROJ" \
      '$4 != "awk" && index($0, project) && (index($0, "codex-bridge-launcher") || index($0, "codex-bridge.js")) { print "  " $0 }' >&2 || true
  printf '485 runtime locks:\n' >&2
  sqlite3 "$TEST_SKILL_DIR/db/messages.db" \
    'SELECT resource, owner_pid, acquired_at FROM locks ORDER BY resource;' 2>/dev/null \
    | sed 's/^/  /' >&2 || true
  printf '485 lock owners: dispatcher=%s role=%s\n' \
    "${DISPATCHER_LOCK_RESOURCE:+$(_runtime_lock_owner "$DISPATCHER_LOCK_RESOURCE")}" \
    "${ROLE_LOCK_RESOURCE:+$(_runtime_lock_owner "$ROLE_LOCK_RESOURCE")}" >&2
  return 0
}

@test "launcher: a replacement dispatcher does not double the role children (#485)" {
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=12
  local settle_seconds=10 stable_samples=4 parent_lifetime=25
  local tab project_hash role_hash role_pair
  tab=$(printf '\t')
  role_pair="team${tab}alice"
  project_hash=$(printf '%s' "$PROJ" | bash -c 'source "$1"; agmsg_sha1' _ "$SCRIPTS/lib/hash.sh")
  role_hash=$(printf '%s' "$role_pair" | bash -c 'source "$1"; agmsg_sha1' _ "$SCRIPTS/lib/hash.sh")
  DISPATCHER_LOCK_RESOURCE="codex-dispatcher:$project_hash"
  ROLE_LOCK_RESOURCE="codex-child:$project_hash:$role_hash"
  CHILD_LAUNCHER_PATTERN="$LAUNCHER"
  EXPECTED_CHILD_ROOTS=1
  sleep "$parent_lifetime" 3>&- & local parent_a=$!
  sleep "$parent_lifetime" 3>&- & local parent_b=$!

  # Dispatcher A spawns the role child, which is nohup'd and outlives A.
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent_a" >/dev/null 2>&1 3>&- &
  local dispatcher_a=$!
  if ! _wait_for_stable_child_roots 1 "$stable_samples" "$settle_seconds"; then
    _diagnose_485_failure
    return 1
  fi

  # SIGKILL is what a pane teardown effectively does to a dispatcher that never
  # trapped the signal: the EXIT trap does not run, so the lock row is left
  # behind owned by a dead pid, exactly the state a replacement dispatcher hits.
  kill -9 "$dispatcher_a" 2>/dev/null || true
  wait "$dispatcher_a" 2>/dev/null || true
  if ! _wait_for_stable_child_roots 1 "$stable_samples" "$settle_seconds"; then
    _diagnose_485_failure
    return 1
  fi

  # Dispatcher B must first be observed as the new project-lock owner. Only
  # after that transition is a singleton assertion meaningful: otherwise the
  # old child can make a fixed-delay count pass before B has run at all.
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent_b" >/dev/null 2>&1 3>&- &
  local dispatcher_b=$!
  if ! _wait_for_pid_alive "$dispatcher_b" "$settle_seconds" \
    || ! _wait_for_runtime_lock_owner "$DISPATCHER_LOCK_RESOURCE" "$dispatcher_b" "$settle_seconds"; then
    _diagnose_485_failure
    return 1
  fi
  if ! _wait_for_stable_child_roots 1 "$stable_samples" "$settle_seconds"; then
    _diagnose_485_failure
    return 1
  fi

  kill "$dispatcher_b" 2>/dev/null || true
  wait "$dispatcher_b" 2>/dev/null || true
  kill "$parent_a" "$parent_b" 2>/dev/null || true
  wait "$parent_a" 2>/dev/null || true
  wait "$parent_b" 2>/dev/null || true
}

@test "launcher: per-role lock disabled exposes two independent roots (#485)" {
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=12
  local settle_seconds=10 stable_samples=4 parent_lifetime=25
  local tab project_hash role_hash role_pair
  tab=$(printf '\t')
  role_pair="team${tab}alice"
  project_hash=$(printf '%s' "$PROJ" | bash -c 'source "$1"; agmsg_sha1' _ "$SCRIPTS/lib/hash.sh")
  role_hash=$(printf '%s' "$role_pair" | bash -c 'source "$1"; agmsg_sha1' _ "$SCRIPTS/lib/hash.sh")
  DISPATCHER_LOCK_RESOURCE="codex-dispatcher:$project_hash"
  ROLE_LOCK_RESOURCE="codex-child:$project_hash:$role_hash"
  EXPECTED_CHILD_ROOTS=2
  if ! _make_no_role_lock_launcher; then
    _diagnose_485_failure
    return 1
  fi
  CHILD_LAUNCHER_PATTERN="$NO_ROLE_LOCK_LAUNCHER"
  sleep "$parent_lifetime" 3>&- & local parent_a=$!
  sleep "$parent_lifetime" 3>&- & local parent_b=$!

  bash "$NO_ROLE_LOCK_LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent_a" >/dev/null 2>&1 3>&- &
  local dispatcher_a=$!
  if ! _wait_for_stable_child_roots 1 "$stable_samples" "$settle_seconds"; then
    _diagnose_485_failure
    return 1
  fi
  kill -9 "$dispatcher_a" 2>/dev/null || true
  wait "$dispatcher_a" 2>/dev/null || true
  if ! _wait_for_stable_child_roots 1 "$stable_samples" "$settle_seconds"; then
    _diagnose_485_failure
    return 1
  fi

  bash "$NO_ROLE_LOCK_LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent_b" >/dev/null 2>&1 3>&- &
  local dispatcher_b=$!
  if ! _wait_for_pid_alive "$dispatcher_b" "$settle_seconds" \
    || ! _wait_for_runtime_lock_owner "$DISPATCHER_LOCK_RESOURCE" "$dispatcher_b" "$settle_seconds"; then
    _diagnose_485_failure
    return 1
  fi
  if ! _wait_for_stable_child_roots 2 "$stable_samples" "$settle_seconds"; then
    _diagnose_485_failure
    return 1
  fi

  kill "$dispatcher_b" "$parent_a" "$parent_b" 2>/dev/null || true
  wait "$dispatcher_b" 2>/dev/null || true
  wait "$parent_a" 2>/dev/null || true
  wait "$parent_b" 2>/dev/null || true
}

@test "launcher: a re-registered role gets a fresh child after deregistration (#485)" {
  put_record team alice thread-alice "$PROJ" codex
  # A custom bridge command is waited synchronously by its role launcher. Keep
  # the mock lifetime below wait_for_child_count's 10-second ceiling so this
  # test measures deregistration, not the intentionally blocking test adapter.
  export MOCK_BRIDGE_SLEEP=2
  sleep 20 3>&- & local parent=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent" >/dev/null 2>&1 3>&- &
  local dispatcher=$!
  [ "$(wait_for_child_count 1)" -eq 1 ]

  # Deregistering the role retires its child through the existing re-exec path.
  run bash "$SCRIPTS/leave.sh" team alice
  [ "$status" -eq 0 ]
  [ "$(wait_for_child_count 0)" -eq 0 ]

  # The dispatcher must have forgotten the pair. Otherwise known_pairs still
  # lists it, the re-spawn is suppressed, and the role silently never gets a
  # bridge again for the rest of the app-server's life.
  bash "$SCRIPTS/join.sh" team alice codex "$PROJ" >/dev/null
  put_record team alice thread-alice "$PROJ" codex
  [ "$(wait_for_child_count 1)" -eq 1 ]

  kill "$dispatcher" 2>/dev/null || true
  wait "$dispatcher" 2>/dev/null || true
  kill "$parent" 2>/dev/null || true
  wait "$parent" 2>/dev/null || true
}

@test "launcher: the identity cache still sees a role added mid-loop (#466)" {
  # The poll no longer re-runs identities.sh every tick; it serves a cache
  # guarded on the team configs' mtimes. This is the test that fails if that
  # guard never invalidates: a role joined while the dispatcher is already
  # looping has to be picked up anyway.
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=20
  sleep 25 3>&- & local parent=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent" >/dev/null 2>&1 3>&- &
  local dispatcher=$!
  local i
  for i in {1..80}; do
    grep -q -- $'--pair team\talice' "$CAPTURE" 2>/dev/null && break
    sleep 0.1
  done
  grep -q -- $'--pair team\talice' "$CAPTURE"

  # Let the loop settle into its backed-off steady state before changing
  # anything, so this exercises a cache hit being invalidated rather than a
  # loop that happened to still be resolving every tick.
  sleep 3
  bash "$SCRIPTS/join.sh" team bob codex "$PROJ" >/dev/null
  put_record team bob thread-bob "$PROJ" codex
  for i in {1..100}; do
    grep -q -- $'--pair team\tbob' "$CAPTURE" 2>/dev/null && break
    sleep 0.1
  done
  grep -q -- $'--pair team\tbob --thread thread-bob' "$CAPTURE"

  kill "$dispatcher" 2>/dev/null || true
  wait "$dispatcher" 2>/dev/null || true
  kill "$parent" 2>/dev/null || true
  wait "$parent" 2>/dev/null || true
}

# --- which pid space (#567) ---

@test "launcher: starts the bridge when tasklist cannot see the parent (#567)" {
  skip_on_windows "stubs tasklist to model Git Bash; the real one is authoritative there"
  # Every pid the launcher waits on -- PARENT_PID, LIFETIME_PID, the dispatcher
  # lock owner -- is minted by $! or $$ in one of these shells, so under Git Bash
  # it is numbered in the MSYS space and `tasklist` has no record of it. A probe
  # that asks tasklist calls the live parent dead: the startup loop is never
  # entered, the supervision loop never runs, and no bridge is ever launched.
  # Measured on our own Windows runner -- $$, $! and a pid read back from a
  # pidfile all report tasklist_hits=0 while kill -0 answers yes.
  local stubdir="$TEST_SKILL_DIR/stub-bin"
  mkdir -p "$stubdir"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$stubdir/tasklist"
  chmod +x "$stubdir/tasklist"

  put_record team alice thread-msys "$PROJ" codex
  export MSYSTEM=MINGW64 PATH="$stubdir:$PATH"
  export MOCK_BRIDGE_EVENTS="$TEST_SKILL_DIR/native-bridge-events.log"
  local lifecycle_events="$TEST_SKILL_DIR/native-launcher-events.log"

  # Keep the simulated MSYS parent alive while the launcher observes the
  # tasklist blind spot. This control must exercise the same bounded lifecycle
  # as the real native leg, or its six-second parent becomes another race.
  sleep 30 3>&- & local p=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$p" >/dev/null 2>&1 3>&- &
  local dispatcher=$!
  record_windows_native_events "$lifecycle_events" "$dispatcher" "$p"
  local i
  for i in {1..150}; do
    record_windows_native_events "$lifecycle_events" "$dispatcher" "$p"
    [ -f "$CAPTURE" ] && break
    sleep 0.1
  done
  record_windows_native_events "$lifecycle_events" "$dispatcher" "$p"

  # A bridge was launched at all -- this is what the whole class costs on Windows.
  if [ ! -f "$CAPTURE" ]; then
    windows_native_diagnostics "$p" "$dispatcher" "$lifecycle_events"
    cleanup_windows_native_processes "$dispatcher" "$p"
    _windows_native_reap_bridge_root "$TEST_SKILL_DIR" || true
    false
  fi
  cleanup_windows_native_processes "$dispatcher" "$p"
  grep -q -- '--thread thread-msys' "$CAPTURE"
}

@test "launcher: native reaper attempts every PID after a wait failure" {
  local attempted="$TEST_SKILL_DIR/native-reap-attempted"
  local waited="$TEST_SKILL_DIR/native-reap-waited"
  local final_assert="$TEST_SKILL_DIR/native-reap-final-assert"

  _windows_native_bridge_pids() {
    printf '%s\n' 101 202
  }
  taskkill() {
    printf '%s\n' "$2" >> "$attempted"
  }
  _windows_native_wait_tasklist_gone() {
    printf '%s\n' "$1" >> "$waited"
    [ "$1" -ne 101 ]
  }
  _windows_native_assert_no_bridge_processes() {
    : > "$final_assert"
  }

  run _windows_native_reap_bridge_root "$TEST_SKILL_DIR"
  [ "$status" -ne 0 ]
  [ "$(wc -l < "$attempted" | tr -d ' ')" -eq 2 ]
  grep -Fqx 101 "$attempted"
  grep -Fqx 202 "$attempted"
  [ "$(wc -l < "$waited" | tr -d ' ')" -eq 2 ]
  grep -Fqx 101 "$waited"
  grep -Fqx 202 "$waited"
  [ -f "$final_assert" ]
}

@test "launcher: native reaper aggregates taskkill failure after all attempts" {
  local attempted="$TEST_SKILL_DIR/native-reap-taskkill-attempted"
  local final_assert="$TEST_SKILL_DIR/native-reap-taskkill-final-assert"

  _windows_native_bridge_pids() {
    printf '%s\n' 101 202
  }
  taskkill() {
    printf '%s\n' "$2" >> "$attempted"
    [ "$2" -ne 101 ]
  }
  _windows_native_wait_tasklist_gone() {
    return 0
  }
  _windows_native_assert_no_bridge_processes() {
    : > "$final_assert"
  }

  run _windows_native_reap_bridge_root "$TEST_SKILL_DIR"
  [ "$status" -ne 0 ]
  [ "$(wc -l < "$attempted" | tr -d ' ')" -eq 2 ]
  grep -Fqx 101 "$attempted"
  grep -Fqx 202 "$attempted"
  [ -f "$final_assert" ]
  printf '%s\n' "$output" | grep -Fq 'taskkill PID=101 rc=1'
}

@test "launcher: windows-native starts the bridge (#567)" {
  skip_unless_windows "the point is the real tasklist and the real MSYS pid space"
  # The counterpart to codex-monitor's windows-native test, and the half #582
  # does NOT fix: reaching the bridged handoff is not the same as delivering a
  # message. PARENT_PID is codex-monitor.sh's own $$, so on Git Bash the loops
  # at :291 and :381 are asking tasklist about an MSYS pid -- false on the first
  # evaluation, which means neither loop turns over and no bridge is ever
  # started. Real tasklist, no stub.
  put_record team alice thread-win "$PROJ" codex
  export MOCK_BRIDGE_CAPTURE_DELAY=7
  export MOCK_BRIDGE_EVENTS="$TEST_SKILL_DIR/native-bridge-events.log"
  local lifecycle_events="$TEST_SKILL_DIR/native-launcher-events.log"

  # Keep the MSYS parent alive beyond the capture deadline. The old fixture
  # waited for a six-second parent before looking at capture for three seconds,
  # so a slow bridge could be killed before the assertion ever observed it.
  sleep 30 3>&- & local p=$!
  # Keep the bridge alive after it has published the capture. This makes the
  # native descendant cleanup an observable invariant rather than a race with
  # the mock's natural exit.
  export MOCK_BRIDGE_SLEEP=30
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$p" >/dev/null 2>&1 3>&- &
  local dispatcher=$!
  record_windows_native_events "$lifecycle_events" "$dispatcher" "$p"
  local i
  for i in {1..150}; do
    record_windows_native_events "$lifecycle_events" "$dispatcher" "$p"
    [ -f "$CAPTURE" ] && break
    sleep 0.1
  done
  record_windows_native_events "$lifecycle_events" "$dispatcher" "$p"

  if [ ! -f "$CAPTURE" ]; then
    windows_native_diagnostics "$p" "$dispatcher" "$lifecycle_events"
    cleanup_windows_native_processes "$dispatcher" "$p"
    _windows_native_reap_bridge_root "$TEST_SKILL_DIR" || true
    false
  fi

  # A bridge under a different root is the negative control: target-root
  # cleanup must not become a global "kill every codex-bridge.js" sweep.
  local foreign_root="$BATS_TEST_TMPDIR/foreign-bridge-root"
  local foreign_bridge="$foreign_root/codex-bridge.js"
  mkdir -p "$foreign_root"
  cat > "$foreign_bridge" <<'EOF'
#!/usr/bin/env bash
while :; do sleep 1; done
EOF
  chmod +x "$foreign_bridge"
  bash "$foreign_bridge" >/dev/null 2>&1 3>&- & local foreign_pid=$!
  TEST_LAUNCHER_EXTRA_PIDS="$foreign_pid"
  if ! _windows_native_wait_bridge_pid "$foreign_root" >/dev/null; then
    windows_native_diagnostics "$p" "$dispatcher" "$lifecycle_events"
    cleanup_windows_native_processes "$dispatcher" "$p"
    _windows_native_reap_bridge_root "$TEST_SKILL_DIR" || true
    _windows_native_reap_bridge_root "$foreign_root" || true
    TEST_LAUNCHER_EXTRA_PIDS=''
    false
  fi

  cleanup_windows_native_processes "$dispatcher" "$p"
  local target_cleanup_rc=0 foreign_control_rc=0 foreign_cleanup_rc=0
  if ! _windows_native_reap_bridge_root "$TEST_SKILL_DIR"; then
    target_cleanup_rc=1
  fi
  # Assert the invariant immediately before the rest of the test teardown.
  if ! _windows_native_assert_no_bridge_processes "$TEST_SKILL_DIR"; then
    target_cleanup_rc=1
  fi
  if ! _windows_native_assert_bridge_alive "$foreign_root"; then
    foreign_control_rc=1
  fi
  if ! _windows_native_reap_bridge_root "$foreign_root"; then
    foreign_cleanup_rc=1
  fi
  TEST_LAUNCHER_EXTRA_PIDS=''
  if [ "$target_cleanup_rc" -ne 0 ] || [ "$foreign_control_rc" -ne 0 ] || \
    [ "$foreign_cleanup_rc" -ne 0 ]; then
    false
  fi
  grep -q -- '--thread thread-win' "$CAPTURE"
}

# --- #937: reap a same-(project,role) orphan via its per-PID identity lease ---

_count_role_bridges() { # <project> <name>
  # Match the role name at a word boundary, not as a substring: "alice" must not
  # also count "alice2" (the survive tests turn on exactly that distinction), so a
  # trailing digit/letter excludes it. Names here are plain [a-z0-9] test tokens.
  ps -Ao pid=,args= 2>/dev/null | grep -F "codex-bridge.js" | grep -F -- "--project $1 " | grep -E "$2([^0-9A-Za-z]|\$)" | grep -c . | tr -d ' '
}
# Run the mock bridge directly for a given (project, pairs) so it publishes a
# lease of that identity and stays alive. Sets FAKE_PID (NOT via $(...) -- a
# background job in command substitution is killed when that subshell exits).
_spawn_fake() { # <project> <pair...>
  local proj="$1"; shift
  local args=(--project "$proj") pv
  for pv in "$@"; do args+=(--pair "$pv"); done
  MOCK_BRIDGE_SLEEP=25 bash "$SCRIPTS/drivers/types/codex/codex-bridge.js" "${args[@]}" 3>&- &
  FAKE_PID=$!
}

_lease_value() { # <key> <lease>
  awk -F= -v key="$1" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$2"
}

@test "launcher: count one without an old exit event is not success (#151 negative control)" {
  export MOCK_BRIDGE_SLEEP=25
  local tab; tab=$(printf '\t')
  _spawn_fake "$PROJ" "team${tab}alice"; local old_pid=$FAKE_PID
  local lease="$RUN_DIR/codex-bridge-lease.$old_pid"
  local lease_deadline=$(( $(date +%s) + 5 ))
  while [ ! -f "$lease" ] && [ "$(date +%s)" -lt "$lease_deadline" ]; do
    sleep 0.1
  done
  [ -f "$lease" ]
  local old_startsrc old_start
  old_startsrc="$(_lease_value startsrc "$lease")"
  old_start="$(_lease_value start "$lease")"
  [ -n "$old_startsrc" ]
  [ -n "$old_start" ]
  [ "$(_count_role_bridges "$PROJ" alice)" -eq 1 ]
  local wait_deadline=$(( $(date +%s) + 1 ))
  run _wait_for_old_identity_exit "$old_pid" "$lease" "$old_startsrc" "$old_start" "$wait_deadline"
  [ "$status" -eq 1 ]
  kill -0 "$old_pid"
}

_snapshot_role_identity() {
  local pidfile="$RUN_DIR/codex-bridge.team.alice.pid" pid lease lease_pid
  local startsrc start live_start
  SNAPSHOT_PID=""
  SNAPSHOT_LEASE=""
  SNAPSHOT_STARTSRC=""
  SNAPSHOT_START=""
  [ -s "$pidfile" ] || return 1
  IFS= read -r pid < "$pidfile" || return 1
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  lease="$RUN_DIR/codex-bridge-lease.$pid"
  [ -s "$lease" ] || return 1
  lease_pid="$(_lease_value pid "$lease")"
  [ "$lease_pid" = "$pid" ] || return 1
  startsrc="$(_lease_value startsrc "$lease")"
  start="$(_lease_value start "$lease")"
  [ -n "$startsrc" ] && [ -n "$start" ] || return 1
  live_start="$(_937_start_token "$pid")" || return 1
  [ "$live_start" = "$(printf '%s\t%s' "$startsrc" "$start")" ] || return 1
  SNAPSHOT_PID="$pid"
  SNAPSHOT_LEASE="$lease"
  SNAPSHOT_STARTSRC="$startsrc"
  SNAPSHOT_START="$start"
}

_937_start_token() {
  local pid="$1" s r tok
  local -a fields
  if [ -r "/proc/$pid/stat" ]; then
    s="$(cat "/proc/$pid/stat" 2>/dev/null)" || return 1
    r="${s##*)}"
    read -ra fields <<< "$r"
    tok="${fields[19]:-}"
    case "$tok" in ''|*[!0-9]*) return 1 ;; esac
    printf 'proc\t%s' "$tok"
    return 0
  fi
  tok="$(ps -o lstart= -p "$pid" 2>/dev/null)"
  tok="${tok#"${tok%%[![:space:]]*}"}"
  tok="${tok%"${tok##*[![:space:]]}"}"
  [ -n "$tok" ] || return 1
  printf 'ps\t%s' "$tok"
}

_wait_for_role_identity() { # <pid> <lease> <startsrc> <start> <samples> <deadline> [count] [survivor-pid]
  local expected_pid="$1" expected_lease="$2" expected_startsrc="$3" expected_start="$4"
  local samples="$5" deadline="$6" expected_count="${7:-}"
  local survivor_pid="${8:-}"
  local i count
  for ((i = 0; i < samples; i++)); do
    [ "$(date +%s)" -lt "$deadline" ] || return 1
    count="$(_count_role_bridges "$PROJ" alice)"
    LAST_937_COUNT="$count"
    _snapshot_role_identity || return 1
    [ -z "$expected_count" ] || [ "$count" -eq "$expected_count" ] || return 1
    [ "$SNAPSHOT_PID" = "$expected_pid" ] || return 1
    [ "$SNAPSHOT_LEASE" = "$expected_lease" ] || return 1
    [ "$SNAPSHOT_STARTSRC" = "$expected_startsrc" ] || return 1
    [ "$SNAPSHOT_START" = "$expected_start" ] || return 1
    if [ -n "$survivor_pid" ]; then
      kill -0 "$survivor_pid" 2>/dev/null || return 1
    fi
    if [ "$i" -lt $((samples - 1)) ]; then
      [ "$(date +%s)" -lt "$deadline" ] || return 1
      sleep 0.1
    fi
  done
}

_wait_for_stable_role_identity() { # <deadline> [count], sets ROLE_STABLE_*
  local deadline="$1" expected_count="${2:-}"
  local candidate_pid candidate_lease candidate_startsrc candidate_start
  ROLE_STABLE_PID=""
  ROLE_STABLE_LEASE=""
  ROLE_STABLE_STARTSRC=""
  ROLE_STABLE_START=""
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if _snapshot_role_identity; then
      candidate_pid="$SNAPSHOT_PID"
      candidate_lease="$SNAPSHOT_LEASE"
      candidate_startsrc="$SNAPSHOT_STARTSRC"
      candidate_start="$SNAPSHOT_START"
      if _wait_for_role_identity \
        "$candidate_pid" "$candidate_lease" "$candidate_startsrc" "$candidate_start" \
        3 "$deadline" "$expected_count"; then
        ROLE_STABLE_PID="$candidate_pid"
        ROLE_STABLE_LEASE="$candidate_lease"
        ROLE_STABLE_STARTSRC="$candidate_startsrc"
        ROLE_STABLE_START="$candidate_start"
        return 0
      fi
    fi
    [ "$(date +%s)" -lt "$deadline" ] || break
    sleep 0.1
  done
  return 1
}

_wait_for_old_identity_exit() { # <pid> <lease> <startsrc> <start> <deadline>
  local pid="$1" lease="$2" startsrc="$3" start="$4" deadline="$5"
  local event="exit pid=$pid startsrc=$startsrc start=$start lease=$lease"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if [ ! -f "$lease" ] && [ -f "$MOCK_BRIDGE_EVENTS" ] \
      && grep -F -q -- "$event" "$MOCK_BRIDGE_EVENTS"; then
      return 0
    fi
    [ "$(date +%s)" -lt "$deadline" ] || break
    sleep 0.1
  done
  return 1
}

_wait_for_stable_replacement() { # <old-pid> <old-start> <deadline> [survivor-pid], sets ROLE_STABLE_*
  local old_pid="$1" old_start="$2" deadline="$3"
  local survivor_pid="${4:-}"
  local candidate_pid candidate_lease candidate_startsrc candidate_start
  ROLE_STABLE_PID=""
  ROLE_STABLE_LEASE=""
  ROLE_STABLE_STARTSRC=""
  ROLE_STABLE_START=""
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if _snapshot_role_identity; then
      candidate_pid="$SNAPSHOT_PID"
      candidate_lease="$SNAPSHOT_LEASE"
      candidate_startsrc="$SNAPSHOT_STARTSRC"
      candidate_start="$SNAPSHOT_START"
      if { [ "$candidate_pid" != "$old_pid" ] || [ "$candidate_start" != "$old_start" ]; } \
        && _wait_for_role_identity \
          "$candidate_pid" "$candidate_lease" "$candidate_startsrc" "$candidate_start" \
          3 "$deadline" 1 "$survivor_pid"; then
        ROLE_STABLE_PID="$candidate_pid"
        ROLE_STABLE_LEASE="$candidate_lease"
        ROLE_STABLE_STARTSRC="$candidate_startsrc"
        ROLE_STABLE_START="$candidate_start"
        return 0
      fi
    fi
    [ "$(date +%s)" -lt "$deadline" ] || break
    sleep 0.1
  done
  return 1
}

_wait_for_stable_survivor_only() { # <survivor-pid> <deadline>
  local survivor_pid="$1" deadline="$2"
  local stable_samples=0 count
  while [ "$(date +%s)" -lt "$deadline" ]; do
    count="$(_count_role_bridges "$PROJ" alice)"
    if [ "$count" -eq 1 ] && kill -0 "$survivor_pid" 2>/dev/null; then
      stable_samples=$((stable_samples + 1))
      [ "$stable_samples" -ge 3 ] && return 0
    else
      stable_samples=0
    fi
    LAST_937_COUNT="$count"
    [ "$(date +%s)" -lt "$deadline" ] || break
    sleep 0.1
  done
  return 1
}

_diagnose_937_failure() {
  local pidfile="$RUN_DIR/codex-bridge.team.alice.pid" f
  printf '937 diagnostics: elapsed=%ss deadline=%s last_count=%s survivor_pid=%s old_pid=%s old_startsrc=%s old_start=%s old_lease=%s new_pid=%s new_startsrc=%s new_start=%s new_lease=%s\n' \
    "${ELAPSED_937_SECONDS:-unknown}" "${DEADLINE_937_EPOCH:-unknown}" \
    "${LAST_937_COUNT:-unknown}" "${SURVIVOR_937_PID:-unknown}" \
    "${old_pid:-unknown}" "${old_startsrc:-unknown}" "${old_start:-unknown}" \
    "${old_lease:-unknown}" "${new_pid:-unknown}" "${new_startsrc:-unknown}" \
    "${new_start:-unknown}" "${new_lease:-unknown}" >&2
  printf '937 pidfile: %s\n' "$pidfile" >&2
  if [ -f "$pidfile" ]; then
    sed 's/^/  /' "$pidfile" >&2 || true
  else
    printf '  absent\n' >&2
  fi
  printf '937 leases:\n' >&2
  for f in "$RUN_DIR"/codex-bridge-lease.*; do
    [ -f "$f" ] || continue
    printf '  %s\n' "$f" >&2
    sed 's/^/    /' "$f" >&2 || true
  done
  printf '937 rate markers:\n' >&2
  for f in "$RUN_DIR"/codex-bridge-rate.*; do
    [ -e "$f" ] || continue
    printf '  %s\n' "$f" >&2
  done
  printf '937 runtime locks:\n' >&2
  sqlite3 "$TEST_SKILL_DIR/db/messages.db" \
    'SELECT resource, owner_pid, acquired_at FROM locks ORDER BY resource;' 2>/dev/null \
    | sed 's/^/  /' >&2 || true
  printf '937 mock events:\n' >&2
  [ -f "$MOCK_BRIDGE_EVENTS" ] && sed 's/^/  /' "$MOCK_BRIDGE_EVENTS" >&2 || true
  printf '937 process tree:\n' >&2
  ps -Ao pid=,ppid=,stat=,args= 2>/dev/null \
    | awk -v launcher="$LAUNCHER" -v project="$PROJ" \
      'index($0, launcher) || index($0, "codex-bridge.js") || index($0, project) { print "  " $0 }' >&2 || true
  return 1
}

@test "launcher: reaps a same-(project,role) orphan the pidfile lost, converging to one (#937)" {
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=25
  export MOCK_BRIDGE_EVENTS="$TEST_SKILL_DIR/bridge-events"
  : > "$MOCK_BRIDGE_EVENTS"
  local started_at deadline_epoch parent_seconds
  started_at="$(date +%s)"
  deadline_epoch=$((started_at + 15))
  DEADLINE_937_EPOCH="$deadline_epoch"
  parent_seconds=$((deadline_epoch - started_at + 15))
  # Keep the parent alive well beyond the deadline, or its deregistration
  # cleanup can masquerade as the reaper's exit event under runner load.
  sleep "$parent_seconds" 3>&- & local parent=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent" >/dev/null 2>&1 3>&- & local disp=$!
  local old_pid old_lease old_startsrc old_start
  if ! _wait_for_stable_role_identity "$deadline_epoch"; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  old_pid="$ROLE_STABLE_PID"
  old_lease="$ROLE_STABLE_LEASE"
  old_startsrc="$ROLE_STABLE_STARTSRC"
  old_start="$ROLE_STABLE_START"
  local pidfile="$RUN_DIR/codex-bridge.team.alice.pid"
  rm -f "$pidfile"
  if ! _wait_for_old_identity_exit "$old_pid" "$old_lease" "$old_startsrc" "$old_start" "$deadline_epoch"; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  local new_pid new_lease new_startsrc new_start
  if ! _wait_for_stable_replacement "$old_pid" "$old_start" "$deadline_epoch"; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  new_pid="$ROLE_STABLE_PID"
  new_lease="$ROLE_STABLE_LEASE"
  new_startsrc="$ROLE_STABLE_STARTSRC"
  new_start="$ROLE_STABLE_START"
  if ! { [ "$new_pid" != "$old_pid" ] || [ "$new_start" != "$old_start" ]; }; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  if ! { [ "$new_lease" != "$old_lease" ] || [ "$new_start" != "$old_start" ]; }; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  kill "$disp" "$parent" 2>/dev/null || true; wait "$disp" 2>/dev/null || true
}

@test "launcher: a reap for one role leaves a same-project OTHER role alive (#937)" {
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=25
  export MOCK_BRIDGE_EVENTS="$TEST_SKILL_DIR/bridge-events"
  : > "$MOCK_BRIDGE_EVENTS"
  local tab; tab=$(printf '\t')
  _spawn_fake "$PROJ" "team${tab}bob"; local bob=$FAKE_PID
  local started_at deadline_epoch parent_seconds
  started_at="$(date +%s)"
  deadline_epoch=$((started_at + 15))
  DEADLINE_937_EPOCH="$deadline_epoch"
  SURVIVOR_937_PID="$bob"
  parent_seconds=$((deadline_epoch - started_at + 15))
  sleep "$parent_seconds" 3>&- & local parent=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent" >/dev/null 2>&1 3>&- & local disp=$!
  local old_pid old_lease old_startsrc old_start
  if ! _wait_for_stable_role_identity "$deadline_epoch"; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  old_pid="$ROLE_STABLE_PID"
  old_lease="$ROLE_STABLE_LEASE"
  old_startsrc="$ROLE_STABLE_STARTSRC"
  old_start="$ROLE_STABLE_START"
  rm -f "$RUN_DIR"/codex-bridge.*.pid
  if ! _wait_for_old_identity_exit "$old_pid" "$old_lease" "$old_startsrc" "$old_start" "$deadline_epoch"; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  if ! _wait_for_stable_replacement "$old_pid" "$old_start" "$deadline_epoch" "$bob"; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  kill "$bob" "$disp" "$parent" 2>/dev/null || true; wait "$disp" 2>/dev/null || true; wait "$bob" 2>/dev/null || true
}

@test "launcher: a reap for one project leaves the SAME role in another project alive (#937)" {
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=25
  export MOCK_BRIDGE_EVENTS="$TEST_SKILL_DIR/bridge-events"
  : > "$MOCK_BRIDGE_EVENTS"
  local tab; tab=$(printf '\t')
  _spawn_fake "$TEST_SKILL_DIR/other-proj" "team${tab}alice"; local other=$FAKE_PID
  local started_at deadline_epoch parent_seconds
  started_at="$(date +%s)"
  deadline_epoch=$((started_at + 15))
  DEADLINE_937_EPOCH="$deadline_epoch"
  SURVIVOR_937_PID="$other"
  parent_seconds=$((deadline_epoch - started_at + 15))
  sleep "$parent_seconds" 3>&- & local parent=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent" >/dev/null 2>&1 3>&- & local disp=$!
  local old_pid old_lease old_startsrc old_start
  if ! _wait_for_stable_role_identity "$deadline_epoch"; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  old_pid="$ROLE_STABLE_PID"
  old_lease="$ROLE_STABLE_LEASE"
  old_startsrc="$ROLE_STABLE_STARTSRC"
  old_start="$ROLE_STABLE_START"
  rm -f "$RUN_DIR"/codex-bridge.*.pid
  if ! _wait_for_old_identity_exit "$old_pid" "$old_lease" "$old_startsrc" "$old_start" "$deadline_epoch"; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  if ! _wait_for_stable_replacement "$old_pid" "$old_start" "$deadline_epoch" "$other"; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  kill "$other" "$disp" "$parent" 2>/dev/null || true; wait "$disp" 2>/dev/null || true; wait "$other" 2>/dev/null || true
}

@test "launcher: a reap for role 'alice' does not sweep the prefix-colliding 'alice2' (#937)" {
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=25
  export MOCK_BRIDGE_EVENTS="$TEST_SKILL_DIR/bridge-events"
  : > "$MOCK_BRIDGE_EVENTS"
  local tab; tab=$(printf '\t')
  _spawn_fake "$PROJ" "team${tab}alice2"; local alice2=$FAKE_PID
  local started_at deadline_epoch parent_seconds
  started_at="$(date +%s)"
  deadline_epoch=$((started_at + 15))
  DEADLINE_937_EPOCH="$deadline_epoch"
  SURVIVOR_937_PID="$alice2"
  parent_seconds=$((deadline_epoch - started_at + 15))
  sleep "$parent_seconds" 3>&- & local parent=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent" >/dev/null 2>&1 3>&- & local disp=$!
  local old_pid old_lease old_startsrc old_start
  if ! _wait_for_stable_role_identity "$deadline_epoch"; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  old_pid="$ROLE_STABLE_PID"
  old_lease="$ROLE_STABLE_LEASE"
  old_startsrc="$ROLE_STABLE_STARTSRC"
  old_start="$ROLE_STABLE_START"
  rm -f "$RUN_DIR"/codex-bridge.*.pid
  if ! _wait_for_old_identity_exit "$old_pid" "$old_lease" "$old_startsrc" "$old_start" "$deadline_epoch"; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  if ! _wait_for_stable_replacement "$old_pid" "$old_start" "$deadline_epoch" "$alice2"; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  kill "$alice2" "$disp" "$parent" 2>/dev/null || true; wait "$disp" 2>/dev/null || true; wait "$alice2" 2>/dev/null || true
}

@test "launcher: an alice reaper does not kill a bridge that also serves bob (pair superset) (#937)" {
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=25
  export MOCK_BRIDGE_EVENTS="$TEST_SKILL_DIR/bridge-events"
  : > "$MOCK_BRIDGE_EVENTS"
  local tab; tab=$(printf '\t')
  _spawn_fake "$PROJ" "team${tab}alice" "team${tab}bob"; local both=$FAKE_PID
  local started_at deadline_epoch parent_seconds
  started_at="$(date +%s)"
  deadline_epoch=$((started_at + 15))
  DEADLINE_937_EPOCH="$deadline_epoch"
  SURVIVOR_937_PID="$both"
  parent_seconds=$((deadline_epoch - started_at + 15))
  sleep "$parent_seconds" 3>&- & local parent=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent" >/dev/null 2>&1 3>&- & local disp=$!
  local old_pid old_lease old_startsrc old_start
  if ! _wait_for_stable_role_identity "$deadline_epoch"; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  old_pid="$ROLE_STABLE_PID"
  old_lease="$ROLE_STABLE_LEASE"
  old_startsrc="$ROLE_STABLE_STARTSRC"
  old_start="$ROLE_STABLE_START"
  rm -f "$RUN_DIR"/codex-bridge.*.pid
  if ! _wait_for_old_identity_exit "$old_pid" "$old_lease" "$old_startsrc" "$old_start" "$deadline_epoch"; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  if ! _wait_for_stable_survivor_only "$both" "$deadline_epoch"; then
    ELAPSED_937_SECONDS=$(( $(date +%s) - started_at )); _diagnose_937_failure
    false
  fi
  # Its pair set is {alice,bob}, not {alice}: set inequality spares it.
  kill "$both" "$disp" "$parent" 2>/dev/null || true; wait "$disp" 2>/dev/null || true; wait "$both" 2>/dev/null || true
}

@test "launcher: a live bridge with no lease (legacy) is left alone, not killed (#937)" {
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=25
  local tab; tab=$(printf '\t')
  # A same-(project,role) process that never published a lease -- an older bridge
  # build. Fail-open: no lease, no kill. Spawn it, then remove its lease.
  _spawn_fake "$PROJ" "team${tab}alice"; local legacy=$FAKE_PID
  local j; for j in {1..50}; do [ -f "$RUN_DIR/codex-bridge-lease.$legacy" ] && break; sleep 0.1; done
  rm -f "$RUN_DIR/codex-bridge-lease.$legacy"
  sleep 22 3>&- & local parent=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent" >/dev/null 2>&1 3>&- & local disp=$!
  sleep 3
  kill -0 "$legacy"
  kill "$legacy" "$disp" "$parent" 2>/dev/null || true; wait "$disp" 2>/dev/null || true; wait "$legacy" 2>/dev/null || true
}

# --- #937 finding (4): the lease is the reaper's authority, so a broken one is a
# new failure surface. Every malformed/partial/foreign lease must fail CLOSED
# (no kill), asserted by a same-(project,alice) fake surviving the reaper. Each
# starts from the valid lease the mock published, then corrupts that one file. ---
_fake_alice_lease() { # sets FAKE_PID once its lease file exists
  local tab; tab=$(printf '\t')
  _spawn_fake "$PROJ" "team${tab}alice"
  local j; for j in {1..50}; do [ -f "$RUN_DIR/codex-bridge-lease.$FAKE_PID" ] && break; sleep 0.1; done
}

@test "launcher: a truncated lease (missing fields) is not killed, fail-closed (#937)" {
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=25
  _fake_alice_lease; local victim=$FAKE_PID lease="$RUN_DIR/codex-bridge-lease.$FAKE_PID"
  head -3 "$lease" > "$lease.x"; mv "$lease.x" "$lease"
  sleep 22 3>&- & local parent=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent" >/dev/null 2>&1 3>&- & local disp=$!
  sleep 3
  kill -0 "$victim"
  kill "$victim" "$disp" "$parent" 2>/dev/null || true; wait "$disp" 2>/dev/null || true; wait "$victim" 2>/dev/null || true
}

@test "launcher: a lease with a foreign host is not killed, fail-closed (#937)" {
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=25
  _fake_alice_lease; local victim=$FAKE_PID lease="$RUN_DIR/codex-bridge-lease.$FAKE_PID"
  awk '{ if ($0 ~ /^host=/) print "host=some-other-host.invalid"; else print }' "$lease" > "$lease.x"; mv "$lease.x" "$lease"
  sleep 22 3>&- & local parent=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent" >/dev/null 2>&1 3>&- & local disp=$!
  sleep 3
  kill -0 "$victim"
  kill "$victim" "$disp" "$parent" 2>/dev/null || true; wait "$disp" 2>/dev/null || true; wait "$victim" 2>/dev/null || true
}

@test "launcher: a lease with an unknown extra key is not killed, fail-closed (#937)" {
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=25
  _fake_alice_lease; local victim=$FAKE_PID lease="$RUN_DIR/codex-bridge-lease.$FAKE_PID"
  { cat "$lease"; printf 'rogue=1\n'; } > "$lease.x"; mv "$lease.x" "$lease"
  sleep 22 3>&- & local parent=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent" >/dev/null 2>&1 3>&- & local disp=$!
  sleep 3
  kill -0 "$victim"
  kill "$victim" "$disp" "$parent" 2>/dev/null || true; wait "$disp" 2>/dev/null || true; wait "$victim" 2>/dev/null || true
}

@test "launcher: a lease with a duplicated key is not killed, fail-closed (#937)" {
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=25
  _fake_alice_lease; local victim=$FAKE_PID lease="$RUN_DIR/codex-bridge-lease.$FAKE_PID"
  { cat "$lease"; printf 'pid=99999\n'; } > "$lease.x"; mv "$lease.x" "$lease"
  sleep 22 3>&- & local parent=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent" >/dev/null 2>&1 3>&- & local disp=$!
  sleep 3
  kill -0 "$victim"
  kill "$victim" "$disp" "$parent" 2>/dev/null || true; wait "$disp" 2>/dev/null || true; wait "$victim" 2>/dev/null || true
}

@test "launcher: a lease whose start token no longer matches the live pid is not killed (#937)" {
  # The reuse guard: if the process now at that pid started at a different time
  # than the lease records (a recycled pid, an unrelated bridge), killing it would
  # be a wrong-kill. The token must PROVE the live process is the leased one, or
  # nothing happens. Same identity as the launcher, so only the token spares it.
  put_record team alice thread-alice "$PROJ" codex
  export MOCK_BRIDGE_SLEEP=25
  _fake_alice_lease; local victim=$FAKE_PID lease="$RUN_DIR/codex-bridge-lease.$FAKE_PID"
  # Rewrite start= to a value that cannot equal the live process's actual token
  # (its src stays valid so the lease still parses; only the token is wrong).
  awk '{ if ($0 ~ /^start=/) print "start=1"; else print }' "$lease" > "$lease.x"; mv "$lease.x" "$lease"
  sleep 22 3>&- & local parent=$!
  bash "$LAUNCHER" codex "$PROJ" "ws://127.0.0.1:1" "$parent" >/dev/null 2>&1 3>&- & local disp=$!
  sleep 3
  kill -0 "$victim"
  kill "$victim" "$disp" "$parent" 2>/dev/null || true; wait "$disp" 2>/dev/null || true; wait "$victim" 2>/dev/null || true
}
