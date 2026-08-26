# Shared setup/teardown for agmsg BATS tests.
# Each test gets an isolated skill directory with its own DB and teams.

# Return a concrete Git executable for test fixtures. A production agmsg install
# puts its push owner-guard launcher first in PATH; fixture setup must bypass
# that launcher only while creating local bare repositories, otherwise a seed
# push is rejected before the test can exercise the launcher under test.
agmsg_test_real_git() {
  local search_path="${1:-${PATH:-}}" path_entry candidate candidate_dir name
  local -a path_entries=() names=(git git.exe)

  IFS=':' read -r -a path_entries <<< "$search_path"
  for path_entry in "${path_entries[@]}"; do
    [ -n "$path_entry" ] || path_entry='.'
    for name in "${names[@]}"; do
      candidate="$path_entry/$name"
      [ -x "$candidate" ] && [ ! -d "$candidate" ] || continue
      candidate_dir="$(cd "$(dirname "$candidate")" 2>/dev/null && pwd -P)" || continue
      candidate="$candidate_dir/$(basename "$candidate")"
      grep -Fqx '# agmsg git push owner guard launcher' "$candidate" 2>/dev/null && continue
      printf '%s\n' "$candidate"
      return 0
    done
  done
  return 1
}

setup_test_env() {
  export LC_ALL=C
  export LANG=C

  local agmsg_env manifest detect detect_var
  local -a detect_vars=()
  export TEST_SKILL_DIR="$(mktemp -d "$BATS_TEST_TMPDIR/agmsg-fixture.XXXXXX")"
  mkdir -p "$TEST_SKILL_DIR"/{home,tmp,run,db,teams,scripts}

  export HOME="$TEST_SKILL_DIR/home"
  export TMPDIR="$TEST_SKILL_DIR/tmp"
  unset CODEX_HOME
  export SKILL_DIR="$TEST_SKILL_DIR"
  export RUN_DIR="$TEST_SKILL_DIR/run"
  export SCRIPTS="$TEST_SKILL_DIR/scripts"
  export TYPES="$TEST_SKILL_DIR/scripts/drivers/types"
  export DBPATH="$TEST_SKILL_DIR/db/messages.db"

  # Do not let a parent agmsg configuration select a different store, driver,
  # plugin, or process identity. Keep only the fixture defaults below.
  while IFS= read -r agmsg_env; do
    [ -n "$agmsg_env" ] && unset "$agmsg_env"
  done < <(env | sed -n 's/^\(AGMSG_[A-Za-z0-9_]*\)=.*/\1/p')
  export AGMSG_STORAGE_PATH="$TEST_SKILL_DIR/db"
  export AGMSG_STORAGE_DRIVER=sqlite
  export AGMSG_AGENT_PID=''

  # A fixture must not inherit a real pane, workspace, or tmux server.
  while IFS= read -r agmsg_env; do
    [ -n "$agmsg_env" ] && unset "$agmsg_env"
  done < <(env | sed -n \
    -e 's/^\(HERDR_[A-Za-z0-9_]*\)=.*/\1/p' \
    -e 's/^\(TMUX_[A-Za-z0-9_]*\)=.*/\1/p' \
    -e 's/^\(TMUX\)=.*/\1/p')

  # Copy all scripts to isolated skill dir. Recursive so nested helper dirs
  # (scripts/lib/) come along without enumerating files.
  cp -R "$BATS_TEST_DIRNAME"/../scripts/. "$SCRIPTS/"
  chmod +x "$SCRIPTS/"*.sh
  chmod +x "$SCRIPTS/"*.js 2>/dev/null || true

  # Agent-type manifests + per-type runtimes now live under scripts/drivers/types/
  # (the type registry reads <skill-root>/scripts/drivers/types/<name>/type.conf),
  # so the recursive scripts/ copy above already brings them along — no separate
  # copy is needed. Just ensure codex's folded runtime scripts stay executable.
  chmod +x "$TYPES/codex/"*.sh 2>/dev/null || true

  # The manifests are data, not shell. Read each copied detect= value so a new
  # agent type cannot reintroduce host-runtime auto-detection by omission here.
  while IFS= read -r manifest; do
    detect="$(sed -n 's/^[[:space:]]*detect[[:space:]]*=[[:space:]]*//p' \
      "$manifest" | head -1)"
    [ -n "$detect" ] || continue
    read -ra detect_vars <<<"$detect"
    for detect_var in "${detect_vars[@]}"; do
      [ "$detect_var" = explicit ] && continue
      unset "$detect_var"
    done
  done < <(find "$TYPES" -type f -name type.conf -print)

  # Initialize DB only after every fixture boundary is fixed.
  bash "$SCRIPTS/internal/init-db.sh"
}

snapshot_test_env_is_msys() {
  local platform="${MSYSTEM:-}"
  if [ -z "$platform" ]; then
    platform="$(uname -s 2>/dev/null || printf '%s' unknown)"
  fi
  case "$platform" in
    MINGW*|MSYS*|CYGWIN*) return 0 ;;
    *) return 1 ;;
  esac
}

snapshot_test_env_winpid() {
  awk '
    BEGIN { winpid_column = 0 }
    {
      if (winpid_column == 0) {
        for (i = 1; i <= NF; i++) {
          header = toupper($i)
          gsub(/[^A-Z0-9]/, "", header)
          if (header == "WINPID") {
            winpid_column = i
            break
          }
        }
        next
      }
      if ($winpid_column ~ /^[0-9]+$/) {
        print $winpid_column
        exit
      }
    }
  '
}

snapshot_test_env_msys_pid() {
  local msys_pid="$1" ps_record ps_rc proc_record proc_rc
  local winpid tasklist_record tasklist_rc

  printf '    msys_pid=%s\n' "$msys_pid"
  if ps_record="$(ps -l -p "$msys_pid" 2>&1)"; then
    ps_rc=0
  else
    ps_rc="$?"
  fi
  if [ "$ps_rc" -eq 0 ]; then
    printf '    ps record for msys_pid=%s:\n%s\n' "$msys_pid" "$ps_record"
  else
    printf '    snapshot marker: ps lookup failed for msys_pid=%s (rc=%s): %s\n' \
      "$msys_pid" "$ps_rc" "$ps_record"
  fi

  if [ -r "/proc/$msys_pid/cmdline" ]; then
    if proc_record="$(tr '\0' ' ' <"/proc/$msys_pid/cmdline" 2>&1)"; then
      proc_rc=0
    else
      proc_rc="$?"
    fi
    if [ "$proc_rc" -eq 0 ]; then
      printf '    /proc/%s/cmdline: %s\n' "$msys_pid" "$proc_record"
    else
      printf '    snapshot marker: /proc/%s/cmdline unavailable (rc=%s): %s\n' \
        "$msys_pid" "$proc_rc" "$proc_record"
    fi
  else
    printf '    snapshot marker: /proc/%s/cmdline unavailable\n' "$msys_pid"
  fi

  if [ "$ps_rc" -eq 0 ]; then
    winpid="$(printf '%s\n' "$ps_record" | snapshot_test_env_winpid)"
  else
    winpid=''
  fi
  if [ -z "$winpid" ]; then
    printf '    snapshot marker: WINPID unavailable for msys_pid=%s; tasklist not queried\n' \
      "$msys_pid"
    return 0
  fi
  printf '    winpid=%s\n' "$winpid"
  if ! command -v tasklist >/dev/null 2>&1; then
    printf '    snapshot marker: tasklist unavailable for winpid=%s\n' "$winpid"
    return 0
  fi
  if tasklist_record="$(MSYS_NO_PATHCONV=1 tasklist /FI "PID eq $winpid" 2>&1)"; then
    tasklist_rc=0
  else
    tasklist_rc="$?"
  fi
  if [ "$tasklist_rc" -eq 0 ]; then
    printf '    tasklist record for winpid=%s:\n%s\n' "$winpid" "$tasklist_record"
  else
    printf '    snapshot marker: tasklist lookup failed for winpid=%s (rc=%s): %s\n' \
      "$winpid" "$tasklist_rc" "$tasklist_record"
  fi
}

snapshot_test_env_native_processes() {
  local skill_dir="$1" native_root='' process_record process_rc path_rc

  printf '  native process candidates containing %s:\n' "$skill_dir"
  if ! command -v powershell.exe >/dev/null 2>&1; then
    printf '%s\n' '    snapshot marker: powershell.exe unavailable'
    return 0
  fi

  if command -v cygpath >/dev/null 2>&1; then
    if native_root="$(MSYS_NO_PATHCONV=1 cygpath -m "$skill_dir" 2>&1)"; then
      path_rc=0
    else
      path_rc="$?"
    fi
    if [ "$path_rc" -ne 0 ]; then
      printf '    snapshot marker: cygpath failed (rc=%s): %s\n' \
        "$path_rc" "$native_root"
      native_root=''
    fi
  else
    printf '%s\n' '    snapshot marker: cygpath unavailable'
  fi

  if process_record="$(
    AGMSG_SNAPSHOT_TEST_ROOT="$skill_dir" \
    AGMSG_SNAPSHOT_TEST_ROOT_NATIVE="$native_root" \
    MSYS_NO_PATHCONV=1 powershell.exe -NoProfile -NonInteractive -Command '
      $needles = @($env:AGMSG_SNAPSHOT_TEST_ROOT, $env:AGMSG_SNAPSHOT_TEST_ROOT_NATIVE) |
        Where-Object { $_ }
      Get-CimInstance Win32_Process |
        Where-Object {
          $line = $_.CommandLine
          $line -and (($needles | Where-Object { $line.Contains($_) }).Count -gt 0)
        } |
        Select-Object ProcessId, ParentProcessId, Name, CommandLine |
        Format-List
    ' 2>&1
  )"; then
    process_rc=0
  else
    process_rc="$?"
  fi
  if [ "$process_rc" -ne 0 ]; then
    printf '    snapshot marker: Win32_Process query failed (rc=%s): %s\n' \
      "$process_rc" "$process_record"
  elif [ -n "$process_record" ]; then
    printf '%s\n' "$process_record"
  else
    printf '%s\n' '    <none>'
  fi
}

snapshot_test_env_runtime() {
  local skill_dir="${1:-}" run_dir artifact artifact_name artifact_content artifact_rc
  local artifact_pid ps_record ps_rc tasklist_record tasklist_rc
  local process_table process_rc matching_processes matching_rc
  local parse_output parse_rc

  printf '%s\n' 'runtime snapshot before rm'
  if [ -z "$skill_dir" ]; then
    printf '%s\n' '  snapshot marker: TEST_SKILL_DIR is unavailable'
    return 0
  fi

  run_dir="$skill_dir/run"
  if [ ! -d "$run_dir" ]; then
    printf '  run directory: %s (absent)\n' "$run_dir"
    return 0
  fi
  printf '  run directory: %s\n' "$run_dir"

  for artifact in "$run_dir"/codex-app-server.*.pid \
    "$run_dir"/codex-bridge.*.pid "$run_dir"/codex-bridge-lease.*; do
    [ -f "$artifact" ] || continue
    artifact_name="${artifact##*/}"
    printf '  runtime artifact: %s\n' "$artifact"

    if artifact_content="$(cat "$artifact" 2>&1)"; then
      artifact_rc=0
    else
      artifact_rc="$?"
    fi
    if [ "$artifact_rc" -ne 0 ]; then
      printf '    snapshot marker: cannot read artifact (rc=%s): %s\n' \
        "$artifact_rc" "$artifact_content"
      continue
    fi
    printf '    content: %s\n' "$artifact_content"

    artifact_pid=''
    case "$artifact_name" in
      *.pid)
        if parse_output="$(printf '%s\n' "$artifact_content" \
          | awk 'NR == 1 { gsub(/[[:space:]]/, ""); print; exit }')"; then
          parse_rc=0
        else
          parse_rc="$?"
          parse_output=''
        fi
        if [ "$parse_rc" -ne 0 ]; then
          printf '    snapshot marker: cannot parse PID (rc=%s)\n' "$parse_rc"
          continue
        fi
        artifact_pid="$parse_output"
        ;;
      *)
        if parse_output="$(printf '%s\n' "$artifact_content" \
          | awk -F= '$1 == "pid" { print $2; exit }')"; then
          parse_rc=0
        else
          parse_rc="$?"
          parse_output=''
        fi
        if [ "$parse_rc" -ne 0 ]; then
          printf '    snapshot marker: cannot parse lease PID (rc=%s)\n' "$parse_rc"
          continue
        fi
        artifact_pid="$parse_output"
        ;;
    esac

    case "$artifact_pid" in
      '' )
        ;;
      *[!0-9]*)
        printf '    snapshot marker: invalid PID value: %s\n' "$artifact_pid"
        ;;
      *)
        if snapshot_test_env_is_msys; then
          snapshot_test_env_msys_pid "$artifact_pid"
        else
          if ps_record="$(ps -p "$artifact_pid" -o pid=,ppid=,stat=,args= 2>&1)"; then
            ps_rc=0
          else
            ps_rc="$?"
          fi
          if [ "$ps_rc" -eq 0 ]; then
            printf '    ps record for pid=%s:\n%s\n' "$artifact_pid" "$ps_record"
          else
            printf '    snapshot marker: ps lookup failed for pid=%s (rc=%s): %s\n' \
              "$artifact_pid" "$ps_rc" "$ps_record"
          fi
          if command -v tasklist >/dev/null 2>&1; then
            if tasklist_record="$(MSYS_NO_PATHCONV=1 tasklist /FI "PID eq $artifact_pid" 2>&1)"; then
              tasklist_rc=0
            else
              tasklist_rc="$?"
            fi
            if [ "$tasklist_rc" -eq 0 ]; then
              printf '    tasklist record for pid=%s:\n%s\n' \
                "$artifact_pid" "$tasklist_record"
            else
              printf '    snapshot marker: tasklist lookup failed for pid=%s (rc=%s): %s\n' \
                "$artifact_pid" "$tasklist_rc" "$tasklist_record"
            fi
          fi
        fi
        ;;
    esac
  done

  if snapshot_test_env_is_msys; then
    snapshot_test_env_native_processes "$skill_dir"
  else
    printf '  process command lines containing %s:\n' "$skill_dir"
    if process_table="$(ps -Ao pid=,ppid=,stat=,args= 2>&1)"; then
      process_rc=0
    else
      process_rc="$?"
    fi
    if [ "$process_rc" -ne 0 ]; then
      printf '    snapshot marker: process table unavailable (rc=%s): %s\n' \
        "$process_rc" "$process_table"
    else
      if matching_processes="$(printf '%s\n' "$process_table" \
        | grep -F -- "$skill_dir")"; then
        matching_rc=0
      else
        matching_rc="$?"
      fi
      case "$matching_rc" in
        0)
          printf '%s\n' "$matching_processes"
          ;;
        1)
          printf '%s\n' '    <none>'
          ;;
        *)
          printf '    snapshot marker: process filter failed (rc=%s): %s\n' \
            "$matching_rc" "$matching_processes"
          ;;
      esac
    fi
  fi
  return 0
}

teardown_test_env() {
  local snapshot rm_stdout rm_stderr rm_rc residual_tree residual_rc
  local capture_root capture_dir stdout_file stderr_file capture_error capture_rc

  capture_root="${BATS_TEST_TMPDIR:-}"
  if [ -z "$capture_root" ]; then
    capture_root="$(dirname "$TEST_SKILL_DIR")"
  fi
  capture_dir=''
  capture_error=''
  if capture_dir="$(mktemp -d "$capture_root/.agmsg-teardown.XXXXXX" 2>&1)"; then
    stdout_file="$capture_dir/stdout"
    stderr_file="$capture_dir/stderr"
    if : >"$stdout_file" 2>/dev/null && : >"$stderr_file" 2>/dev/null; then
      :
    else
      capture_rc="$?"
      capture_error="snapshot marker: cannot create teardown capture files (rc=$capture_rc)"
      capture_dir=''
    fi
  else
    capture_rc="$?"
    capture_error="snapshot marker: cannot create teardown capture directory (rc=$capture_rc): $capture_dir"
    capture_dir=''
  fi

  snapshot="$(snapshot_test_env_runtime "$TEST_SKILL_DIR")"
  if [ -n "$capture_dir" ]; then
    if rm -rf "$TEST_SKILL_DIR" >"$stdout_file" 2>"$stderr_file"; then
      return 0
    else
      rm_rc="$?"
    fi
  else
    if rm -rf "$TEST_SKILL_DIR"; then
      return 0
    else
      rm_rc="$?"
    fi
  fi

  if [ -n "$capture_dir" ]; then
    if rm_stdout="$(cat "$stdout_file" 2>&1)"; then
      :
    else
      rm_stdout="snapshot marker: cannot read captured rm stdout (rc=$?)"
    fi
    if rm_stderr="$(cat "$stderr_file" 2>&1)"; then
      :
    else
      rm_stderr="snapshot marker: cannot read captured rm stderr (rc=$?)"
    fi
  else
    rm_stdout="$capture_error"
    rm_stderr="$capture_error"
  fi
  printf '%s\n' 'teardown_test_env: rm -rf failed' >&2
  printf 'rm exit status: %s\n' "$rm_rc" >&2
  printf '%s\n' 'rm stdout:' >&2
  if [ -n "$rm_stdout" ]; then
    printf '%s\n' "$rm_stdout" >&2
  else
    printf '%s\n' '  <empty>' >&2
  fi
  printf '%s\n' 'rm stderr:' >&2
  if [ -n "$rm_stderr" ]; then
    printf '%s\n' "$rm_stderr" >&2
  else
    printf '%s\n' '  <empty>' >&2
  fi
  printf '%s\n' "$snapshot" >&2
  printf '%s\n' 'residual tree after rm:' >&2
  if residual_tree="$(find "$TEST_SKILL_DIR" -print 2>&1)"; then
    residual_rc=0
  else
    residual_rc="$?"
  fi
  if [ "$residual_rc" -eq 0 ]; then
    printf '%s\n' "$residual_tree" >&2
  else
    printf '  snapshot marker: residual tree unavailable (rc=%s): %s\n' \
      "$residual_rc" "$residual_tree" >&2
  fi
  return "$rm_rc"
}

# Skip a test on native Windows / Git Bash (MSYS/MINGW/Cygwin). Use ONLY for
# behaviour that depends on POSIX process semantics agmsg does not yet support
# there — watcher discovery/kill via ps/pgrep, and session liveness via kill -0
# (#134 Bug 2, #181). These are the residual windows-latest failures left after
# the Git Bash compat (#179) and sqlite CRLF (#180) fixes; quarantining them
# lets the experimental leg report green instead of perpetually red. Each call
# site names the tracking issue so the skip is removed when the bug is fixed.
skip_on_windows() {
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) skip "${1:-not yet supported on native Windows}" ;;
  esac
}

# The inverse, for the handful of tests whose whole point is native Windows: the
# real tasklist, the real MSYS pid space, no stub in between. Everywhere else
# they would prove nothing, so they skip rather than pass vacuously.
skip_unless_windows() {
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) ;;
    *) skip "${1:-only meaningful under Git Bash}" ;;
  esac
}

# In-memory sqlite for test ASSERTIONS, stripping CR. sqlite3.exe writes stdout
# in text mode on Windows (\n -> \r\n); $(...) keeps the trailing \r, so a probe
# like [ "$(sqlite3 :memory: 'SELECT json_valid(...)')" = "1" ] compares "1\r"
# against "1" and fails even when the script under test wrote a correct file.
# This is the test-side mirror of scripts/lib/storage.sh's agmsg_sqlite_mem.
sqlite_mem() { sqlite3 :memory: "$@" | tr -d '\r'; }

# Permission bits of <path> as octal, e.g. 700.
#
# NOT `stat -f "%Lp" "$p" 2>/dev/null || stat -c "%a" "$p"`. That idiom leans on
# the BSD form FAILING under GNU. It does fail — but only after writing
# filesystem information to STDOUT, because GNU reads `-f` as `--file-system`
# and the format string as a second file operand. `2>/dev/null` hides the error
# it then prints, not the output already written, so the capture becomes that
# block with the real mode appended and no comparison can match. Green on macOS,
# red on any GNU host, and the reason is invisible at the call site.
#
# Branch on the platform instead, the way scripts/lib/compat.sh already does for
# mtime. One implementation so a fourth call site cannot reintroduce it.
file_mode() {
  case "$(uname -s)" in
    Darwin*) stat -f "%Lp" "$1" ;;
    *)       stat -c "%a" "$1" ;;
  esac
}

# Resolve a file path for use inside a sqlite3 readfile('...') call in a test.
# On native Windows, sqlite3 only reads a Windows path (C:\Users\...), not a Git
# Bash POSIX path (/c/Users/... or /tmp/...): an unconverted path reads back as
# empty, so the surrounding json_extract / json_valid sees nothing and the check
# fails even though the script under test wrote a correct file. cygpath -w
# converts it; a no-op off Windows (cygpath absent). The result is then single-
# quote-escaped for the SQL string literal. Mirrors scripts/lib/storage.sh's
# agmsg_sql_readfile_path — the production helper these tests are validating.
rf() {
  local p="$1"
  if command -v cygpath >/dev/null 2>&1; then
    p="$(cygpath -w "$p" 2>/dev/null || printf '%s' "$p")"
  fi
  printf '%s' "$p" | sed "s/'/''/g"
}

# --- Bounded condition waits -------------------------------------------------
#
# Wait for a condition to become true, polling, instead of sleeping a fixed
# interval and hoping. A fixed `sleep 1` after launching a watcher is wrong in
# both directions at once: it costs a whole second when the watcher was ready in
# 40ms, and it still flakes on a loaded runner where the watcher needs 1.2s.
# Polling is both faster and steadier, which is why the pattern already existed
# ad hoc in test_watch.bats, test_install.bats and test_codex_bridge_launcher.bats
# before it was hoisted here.
#
# Each returns non-zero on timeout, so a caller can fail with its own message or
# clean up a background process first. The 10s ceiling is far above any real
# local transition and well under the per-job CI timeout.
#
# NOTE: these replace waits for a condition that will become TRUE. A test that
# asserts something does NOT happen cannot poll for it — see the comment at the
# remaining fixed sleeps in test_delivery.bats.

_WAIT_TICKS=100    # x 0.1s = 10s ceiling
_WAIT_INTERVAL=0.1

wait_for_file() {
  local file="$1" i
  for i in $(seq 1 $_WAIT_TICKS); do
    [ -f "$file" ] && return 0
    sleep $_WAIT_INTERVAL
  done
  return 1
}

wait_for_missing() {
  local path="$1" i
  for i in $(seq 1 $_WAIT_TICKS); do
    [ ! -e "$path" ] && return 0
    sleep $_WAIT_INTERVAL
  done
  return 1
}

wait_for_file_contains() {
  local file="$1" needle="$2" i
  for i in $(seq 1 $_WAIT_TICKS); do
    [ -f "$file" ] && grep -q "$needle" "$file" && return 0
    sleep $_WAIT_INTERVAL
  done
  return 1
}

# Positive evidence that a pid is gone. NOT `kill -0 || gone`.
#
# A failed `kill -0` is ESRCH (dead) or EPERM (alive, but not signalable by us —
# sandboxes do exactly this, and a live instance of it was found in
# delivery.sh status the same day this was written). Treating every failure as
# "gone" is how a wait-for-exit helper reports success for a running process,
# which turns every test built on it into a green that proves nothing. That is
# the defect this file's own callers were just fixed for; the helper must not
# reintroduce it one level down.
#
# Mirrors _agmsg_pid_alive in scripts/lib/instance-id.sh, then cross-checks the
# process table, which does not depend on signalling permission at all. Saying
# "gone" now requires kill(2) and ps to agree.
_pid_gone() {
  local pid="$1" err stat
  # `export LC_ALL=C` rather than a bare prefix: a prefix misses the builtin on
  # bash 3.2, and the ESRCH match below is on English text.
  err="$(export LC_ALL=C; kill -0 "$pid" 2>&1)" && return 1
  case "$err" in
    *[Nn]'o such process'*) ;;
    *) return 1 ;;   # EPERM and anything unrecognised mean "assume alive"
  esac
  stat="$(ps -o stat= -p "$pid" 2>/dev/null | tr -d ' ')"
  [ -z "$stat" ] && return 0
  case "$stat" in Z*) return 0 ;; esac   # terminated, just not reaped yet
  return 1
}

# Wait for a process to actually be gone. Writing a pidfile and dying are not
# atomic, so asserting `! kill -0 $pid` the instant a pidfile disappears races
# the TERM trap (#124).
wait_for_pid_exit() {
  local pid="$1" i
  for i in $(seq 1 $_WAIT_TICKS); do
    # Reap finished children first: an unreaped zombie still answers `kill -0`,
    # so without this a process that HAS exited can keep looking alive for the
    # whole timeout. `jobs` is what makes bash collect them.
    jobs >/dev/null 2>&1 || true
    _pid_gone "$pid" && return 0
    sleep $_WAIT_INTERVAL
  done
  return 1
}

# Wait for <file> to contain exactly <expected>, for pidfile handoffs where the
# file exists throughout but its contents flip to the successor.
wait_for_file_is() {
  local file="$1" expected="$2" i
  for i in $(seq 1 $_WAIT_TICKS); do
    if [ -f "$file" ] && [ "$(cat "$file" 2>/dev/null)" = "$expected" ]; then
      return 0
    fi
    sleep $_WAIT_INTERVAL
  done
  return 1
}

# Pin a fake-owned session_id under the given run/ directory so the lock
# liveness check (which runs `kill -0` on cc-instance.<pid>) considers
# <sid> alive for the duration of the bats process.
#
# Used to be inlined in every test that needed a live peer owner. Pulled
# up here per #65 review finding 7 — the fake cc-instance pattern is part
# of the lock contract; repeating it inline invites tests that flake the
# moment we tighten what "alive" means.
#
# Usage: setup_live_owner <run_dir> <session_id>
setup_live_owner() {
  local run_dir="$1" sid="$2"
  mkdir -p "$run_dir"
  echo "$sid" > "$run_dir/cc-instance.$$"
}

# A PATH containing only `bash` and `dirname` (real binaries, via symlink)
# -- enough to exec bash itself (so `env PATH=... bash script.sh` doesn't
# fail on "bash: command not found" before the script even starts) and for
# remote.sh/team-list.sh to resolve SCRIPT_DIR/SKILL_DIR and reach their
# agmsg_require_python3 preflight check, but with no `python3` findable via
# `command -v`. Used to test the preflight check itself fails fast with a
# clear message instead of ever invoking python3 (see
# lib/require-python3.sh) -- deliberately NOT built by filtering the real
# PATH's directories, so it can't accidentally still contain a python3
# from some other directory.
# A PATH holding what doctor needs and no age. Shadowing with a stub does not
# work: `command -v` skips a non-executable entry and finds the real binary
# further along, so the lookup only fails if the PATH is built from scratch --
# the same approach path_without_python3 takes.
path_without_age() {
  local dir tool
  dir="$(mktemp -d)"
  for tool in bash dirname basename readlink python3 node uname sed grep \
              awk cat tr mktemp; do
    if command -v "$tool" >/dev/null 2>&1; then
      ln -s "$(command -v "$tool")" "$dir/$tool" 2>/dev/null || true
    fi
  done
  printf '%s' "$dir"
}

path_without_python3() {
  local dir
  dir="$(mktemp -d)"
  ln -s "$(command -v bash)" "$dir/bash"
  ln -s "$(command -v dirname)" "$dir/dirname"
  printf '%s' "$dir"
}

# Fail the test when <cmd> SUCCEEDS.
#
# `! cmd` cannot do this. POSIX errexit exempts a negated command, on every
# bash, so `! grep -q needle file` is silent when the needle IS there -- the
# one outcome it was written to catch. Measured on 3.2.57 and 5.3.15: both
# report `ok` (#670).
#
# Deliberately not `run cmd` + `[ "$status" -ne 0 ]`, which also works: `run`
# overwrites `$output` and `$status`, so converting an absence check that way
# silently breaks any assertion after it that still reads `$output`. That is a
# real bug, not a hypothetical -- it happened twice in #697 -- and 48 sites is
# too many to hand that to.
#
# Says what failed, because a bare `false` leaves the reader to work out which
# of several absence checks was the one that fired.
refute() {
  if "$@"; then
    echo "refute: '$*' unexpectedly succeeded" >&2
    return 1
  fi
}

# A live process whose command line contains <path>, and nothing else.
#
# The kill paths in session-end.sh / session-start.sh only signal a pid whose
# cmdline still looks like this install's watch.sh -- a deliberate defence
# against pid recycling. Fixtures used a bare `sleep`, whose cmdline does not
# match, so the kill never fired and the assertion checking for it was `!
# kill -0 ...`, which is silent on every bash. The tests passed for years
# without once exercising the branch they are named after (#670).
#
# It runs a script that sleeps; it does NOT exec, which would drop the argument
# from the command line, and it does NOT start the real watcher -- a live
# watcher inside a test is how a suite grows processes that outlive it.
# Sets DECOY_PID rather than printing it: `pid="$(spawn_...)"` runs the `&` in
# a command substitution's subshell, and the child dies with that subshell. The
# first version did exactly that, and the tests using it went green because the
# decoy was already gone -- not because anything had killed it. Returning
# through a variable keeps the process a child of the test.
# The same reader the product uses to decide whether a pid is one of ours, so
# a fixture's precondition is checked the way session-end.sh checks it rather
# than by a lookalike.
_decoy_cmdline() {
  # shellcheck disable=SC1090
  . "$SCRIPTS/lib/compat.sh"
  compat_get_cmdline "$1"
}

spawn_decoy_with_cmdline() {
  local path="$1" decoy
  decoy="$(mktemp -d)/decoy.sh"
  printf '#!/usr/bin/env bash\nsleep 30\n' > "$decoy"
  chmod +x "$decoy"
  bash "$decoy" "$path" 3>&- &
  DECOY_PID=$!
}
