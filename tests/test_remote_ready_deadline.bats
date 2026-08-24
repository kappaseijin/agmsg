#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
  bash "$SCRIPTS/join.sh" testteam alice claude-code /tmp/project-a

  local cfg="$TEST_SKILL_DIR/teams/testteam/config.json" escaped updated
  escaped="$(sed "s/'/''/g" "$cfg")"
  updated="$(sqlite_mem "
    SELECT json_set('$escaped', '\$.remote_binding', json_object(
      'endpoint', 'https://remote.example',
      'server_instance_id', '018f0000-0000-7000-8000-000000000001',
      'remote_team_id', '018f0000-0000-7000-8000-000000000002',
      'protocol_version', 1,
      'capabilities', json_object('write_allowed_ciphers', json_array('none')),
      'connected_at', '2026-07-30T00:00:00Z',
      'disconnected_at', null
    ));")"
  printf '%s\n' "$updated" > "$cfg"
  mkdir -p "$TEST_SKILL_DIR/run"
}

teardown() {
  local pidfile="$TEST_SKILL_DIR/run/remote-sync.testteam.pid" pid=""
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    case "$pid" in
      ''|*[!0-9]*|0|1) ;;
      *) kill "$pid" 2>/dev/null || true; wait_for_pid_exit "$pid" || true ;;
    esac
  fi
  teardown_test_env
}

write_fake_node() {
  local ready="${1:-yes}" fake_node="$TEST_SKILL_DIR/fake-node"
  printf '%s\n' '#!/usr/bin/env bash' \
    'if [ "${1:-}" = "--version" ]; then' \
    '  echo v23.0.0' \
    '  exit 0' \
    'fi' \
    "if [ '$ready' = yes ]; then" \
    '  echo "{\"event\":\"capabilities\",\"startup_nonce\":\"${AGMSG_SYNC_START_NONCE:-}\"}"' \
    'fi' \
    'trap '\''[ -z "${AGMSG_TEST_KILLED_FILE:-}" ] || : > "$AGMSG_TEST_KILLED_FILE"; exit 0'\'' TERM INT' \
    'while :; do sleep 1; done' > "$fake_node"
  chmod +x "$fake_node"
  printf '%s\n' "$fake_node"
}

write_matching_ps_fixture() {
  local fake_bin="$TEST_SKILL_DIR/fake-bin"
  local pidfile="$TEST_SKILL_DIR/run/remote-sync.testteam.pid"
  mkdir -p "$fake_bin"
  printf '%s\n' '#!/usr/bin/env bash' \
    'pid=""; args=0' \
    'for a in "$@"; do' \
    '  case "$a" in -o) ;; args=) args=1 ;; esac' \
    'done' \
    'case " $* " in *" -o args= "*) args=1 ;; esac' \
    '[ "$args" = 1 ] || exec /bin/ps "$@"' \
    'while [ $# -gt 0 ]; do' \
    '  if [ "$1" = "-p" ]; then pid="$2"; shift 2; else shift; fi' \
    'done' \
    "engine_pid=\"\$(cat '$pidfile' 2>/dev/null || true)\"" \
    "if [ \"\$pid\" = \"\$engine_pid\" ]; then" \
    "  printf '%s\\n' 'bash $SCRIPTS/internal/remote-sync.mjs run --team testteam'" \
    '  exit 0' \
    'fi' \
    'exec /bin/ps -o args= -p "$pid"' > "$fake_bin/ps"
  chmod +x "$fake_bin/ps"
  printf '%s\n' "$fake_bin"
}

assert_output_contains() {
  local output="$1" needle="$2"
  case "$output" in
    *"$needle"*) ;;
    *) printf 'expected output to contain: %s\nactual output: %s\n' "$needle" "$output" >&2; return 1 ;;
  esac
}

@test "sync start: unready engine exits at the real-time deadline (#88)" {
  # Positive control for the suspected failure mode. The engine never emits a
  # ready marker, so the production cleanup must kill it after the one-second
  # override. The old 1600-turn loop ignores that override and is killed by the
  # eight-second Python watchdog instead. The watchdog runs in its own process
  # group so a RED run cannot kill the Bats test shell.
  local fake_node fake_bin killed_file pidfile engine_pid="" began elapsed watchdog
  fake_node="$(write_fake_node no)"
  fake_bin="$(write_matching_ps_fixture)"
  killed_file="$TEST_SKILL_DIR/no-ready-engine-killed"
  pidfile="$TEST_SKILL_DIR/run/remote-sync.testteam.pid"
  watchdog='import os, signal, subprocess, sys
proc = subprocess.Popen(sys.argv[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
try:
    output, _ = proc.communicate(timeout=8)
except subprocess.TimeoutExpired as exc:
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        tail, _ = proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        tail, _ = proc.communicate()
    sys.stdout.write((exc.output or "") + (tail or ""))
    raise SystemExit(124)
sys.stdout.write(output or "")
raise SystemExit(proc.returncode)'

  began=$SECONDS
  run env PATH="$fake_bin:$PATH" AGMSG_NODE="$fake_node" \
    AGMSG_TEST_KILLED_FILE="$killed_file" \
    AGMSG_REMOTE_SYNC_READY_TIMEOUT_S=1 \
    python3 -c "$watchdog" bash "$SCRIPTS/remote.sh" sync start testteam
  elapsed=$((SECONDS - began))

  # A watchdog kill is test cleanup, not production cleanup. Remove any fake
  # engine left by a RED run before asserting so it cannot leak a process.
  if [ -f "$pidfile" ]; then
    engine_pid="$(cat "$pidfile" 2>/dev/null || true)"
    case "$engine_pid" in
      ''|*[!0-9]*|0|1) ;;
      *) kill "$engine_pid" 2>/dev/null || true; wait_for_pid_exit "$engine_pid" || true ;;
    esac
  fi

  [ "$status" -ne 124 ]
  [ "$status" -ne 0 ]
  [ "$elapsed" -ge 1 ]
  [ "$elapsed" -le 8 ]
  assert_output_contains "$output" "did not become ready"
  [ -f "$killed_file" ]
  refute test -e "$pidfile"
}

@test "sync start: ready engine still succeeds before the deadline (#88 negative control)" {
  # Negative control: a normal engine emits the marker and must retain the
  # existing success contract under the same deadline override.
  local fake_node fake_bin output engine_pid pidfile
  fake_node="$(write_fake_node yes)"
  fake_bin="$(write_matching_ps_fixture)"
  pidfile="$TEST_SKILL_DIR/run/remote-sync.testteam.pid"

  run env PATH="$fake_bin:$PATH" AGMSG_NODE="$fake_node" \
    AGMSG_REMOTE_SYNC_READY_TIMEOUT_S=8 \
    bash "$SCRIPTS/remote.sh" sync start testteam
  [ "$status" -eq 0 ]
  assert_output_contains "$output" "Sync engine started for 'testteam' (pid "
  engine_pid="$(cat "$pidfile")"
  kill "$engine_pid" 2>/dev/null || true
  wait_for_pid_exit "$engine_pid"
}
