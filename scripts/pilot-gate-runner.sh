#!/usr/bin/env bash
set -euo pipefail

# pilot-gate-runner.sh
#
# Issue #396 / Part 1
#
# Implements the outer G4 integration-gate harness boundary for:
#
#   P0 environment capture
#   P1 live PM before-control
#   P2 isolation setup
#   P3 isolation preflight
#   P4 F2 containment proof
#   N1 fresh/resume
#
# I1/F1-F5, full evidence aggregation, final evaluation, and full cleanup are
# intentionally deferred to later Issue #396 implementation parts.
#
# IMPORTANT:
#   This script MUST NOT modify:
#
#     scripts/pilot-launcher.sh
#     scripts/p2-consumer-broker.sh
#     scripts/pilot-collector.sh
#
#   It operates strictly outside those G4 components.

readonly EX_GATE_PASS=0
readonly EX_GATE_FAIL=1
readonly EX_GATE_UNKNOWN=2
readonly EX_USAGE=64
readonly EX_INTERNAL=70

readonly PILOT_AGENT="agmsg_pm_pilot_claude"
readonly PILOT_TYPE="claude-code"

readonly DEFAULT_COLLECTOR_CUTOFF_SECONDS=180
readonly N1_START_TIMEOUT_SECONDS=30
readonly N1_TRANSCRIPT_TIMEOUT_SECONDS=20
readonly N1_EXIT_GRACE_SECONDS=5

SCRIPT_DIR="$(
  cd "$(dirname "$0")" &&
    pwd
)"
SKILL_DIR="$(
  cd "$SCRIPT_DIR/.." &&
    pwd
)"

ISOLATION_HELPER="$SCRIPT_DIR/lib/pilot-gate-isolation.py"
I1_HELPER="$SCRIPT_DIR/lib/pilot-gate-i1.py"
F1_HELPER="$SCRIPT_DIR/lib/pilot-gate-f1.py"
F2_HELPER="$SCRIPT_DIR/lib/pilot-gate-f2.py"
F3_HELPER="$SCRIPT_DIR/lib/pilot-gate-f3.py"
F4_HELPER="$SCRIPT_DIR/lib/pilot-gate-f4.py"
F5_HELPER="$SCRIPT_DIR/lib/pilot-gate-f5.py"

SUBCOMMAND="run"
SOURCE=""
LIVE_SKILL_DIR=""
ARTIFACT_DIR=""
COLLECTOR_CUTOFF_SECONDS="$DEFAULT_COLLECTOR_CUTOFF_SECONDS"
CHECK="all"

RUN_ID=""
RUN_ROOT=""
GATE_TEAM=""
GATE_REPO=""
GATE_HOME=""
GATE_XDG_CONFIG=""
GATE_XDG_CACHE=""
GATE_XDG_DATA=""
GATE_XDG_STATE=""
GATE_CLAUDE_CONFIG=""
STATE_FILE=""

CLAUDE_BIN=""
CLAUDE_BIN_CANONICAL=""
CLAUDE_VERSION=""
CLAUDE_DIGEST=""
SOURCE_HEAD=""

LIVE_GUARD=""
LIVE_GUARD_DIGEST_BEFORE=""

F2_PROBE_TARGET=""
F2_PROBE_PROGRAM=""
F2_PROBE_MANIFEST=""

CURRENT_NATIVE_PID=""

usage() {
  cat <<'USAGE'
Usage:
  pilot-gate-runner.sh [subcommand] \
    --source <repo-or-worktree> \
    --live-skill-dir <live-agmsg-root> \
    --artifact-dir <artifact-dir> \
    [--collector-cutoff-seconds <seconds>] \
    [--check all|N1|I1|F1|F2|F3|F4|F5]

Subcommands:
  preflight
      Execute P0-P4 only.

  run
      Execute P0-P4 followed by the implemented gate checks.
      In Issue #396 Part 7 this means N1, I1, F1, F2, F3, F4, and F5.

  evaluate
      Reserved for a later Issue #396 part.
      Part 1 fails closed with exit 70.

  cleanup
      Reserved for a later Issue #396 part.
      Part 1 fails closed with exit 70.

Exit status:
  0   requested gate scope completed and all requested checks pass
  1   completed with one or more definite failures
  2   completed/aborted with one or more unknowns and no definite failure
  64  invocation/configuration error
  70  harness internal error

Notes:
  --source is COPY INPUT only. Native Claude is never launched there.

  The disposable repository is created by this runner under a private
  run root and all git remotes are removed before any gate session starts.

  --check N1 is intended only for development/partial verification.

  --check all can NEVER return gate-pass from the Part 7 implementation.
  N1/I1/F1-F5 are now all implemented, but P5-P7 (isolated cleanup
  verification, live PM after-control, aggregate verdict), full
  evidence aggregation/evaluation, and cleanup remain unimplemented.
USAGE
}

log() {
  printf '%s\n' "pilot-gate: $*" >&2
}

usage_error() {
  printf '%s\n' "pilot-gate: $*" >&2
  exit "$EX_USAGE"
}

internal_error() {
  printf '%s\n' "pilot-gate: internal error: $*" >&2
  exit "$EX_INTERNAL"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 ||
    usage_error "required command not found: $1"
}

canonical_path() {
  python3 "$ISOLATION_HELPER" canonical "$1"
}

sha256_file() {
  python3 "$ISOLATION_HELPER" sha256 "$1"
}

json_field() {
  python3 "$ISOLATION_HELPER" json-field "$1" "$2"
}

parse_args() {
  if [ "$#" -gt 0 ]; then
    case "$1" in
      preflight|run|evaluate|cleanup)
        SUBCOMMAND="$1"
        shift
        ;;
    esac
  fi

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --source)
        [ "$#" -ge 2 ] ||
          usage_error "--source requires a value"
        SOURCE="$2"
        shift 2
        ;;

      --live-skill-dir)
        [ "$#" -ge 2 ] ||
          usage_error "--live-skill-dir requires a value"
        LIVE_SKILL_DIR="$2"
        shift 2
        ;;

      --artifact-dir)
        [ "$#" -ge 2 ] ||
          usage_error "--artifact-dir requires a value"
        ARTIFACT_DIR="$2"
        shift 2
        ;;

      --collector-cutoff-seconds)
        [ "$#" -ge 2 ] ||
          usage_error "--collector-cutoff-seconds requires a value"
        COLLECTOR_CUTOFF_SECONDS="$2"
        shift 2
        ;;

      --check)
        [ "$#" -ge 2 ] ||
          usage_error "--check requires a value"
        CHECK="$2"
        shift 2
        ;;

      -h|--help)
        usage
        exit 0
        ;;

      *)
        usage_error "unknown argument: $1"
        ;;
    esac
  done

  [ -n "$SOURCE" ] ||
    usage_error "--source is required"

  [ -n "$LIVE_SKILL_DIR" ] ||
    usage_error "--live-skill-dir is required"

  [ -n "$ARTIFACT_DIR" ] ||
    usage_error "--artifact-dir is required"

  case "$COLLECTOR_CUTOFF_SECONDS" in
    ''|*[!0-9]*|0)
      usage_error \
        "--collector-cutoff-seconds must be a positive integer"
      ;;
  esac

  case "$CHECK" in
    all|N1|I1|F1|F2|F3|F4|F5)
      ;;
    *)
      usage_error \
        "--check must be one of: all, N1, I1, F1, F2, F3, F4, F5"
      ;;
  esac
}

validate_static_inputs() {
  [ -x "$ISOLATION_HELPER" ] ||
    usage_error \
      "isolation helper unavailable or not executable: $ISOLATION_HELPER"

  [ -x "$I1_HELPER" ] ||
    usage_error \
      "I1 helper unavailable or not executable: $I1_HELPER"

  [ -x "$F1_HELPER" ] ||
    usage_error \
      "F1 helper unavailable or not executable: $F1_HELPER"

  [ -x "$F2_HELPER" ] ||
    usage_error \
      "F2 helper unavailable or not executable: $F2_HELPER"

  [ -x "$F3_HELPER" ] ||
    usage_error \
      "F3 helper unavailable or not executable: $F3_HELPER"

  [ -x "$F4_HELPER" ] ||
    usage_error \
      "F4 helper unavailable or not executable: $F4_HELPER"

  [ -x "$F5_HELPER" ] ||
    usage_error \
      "F5 helper unavailable or not executable: $F5_HELPER"

  [ -d "$SOURCE" ] ||
    usage_error \
      "--source is not a directory: $SOURCE"

  [ -d "$LIVE_SKILL_DIR" ] ||
    usage_error \
      "--live-skill-dir is not a directory: $LIVE_SKILL_DIR"

  [ -f "$LIVE_SKILL_DIR/scripts/pm-pretool-guard" ] ||
    usage_error \
      "live pm-pretool-guard not found"

  [ -f "$SOURCE/scripts/pilot-launcher.sh" ] ||
    usage_error \
      "source pilot-launcher.sh not found"

  [ -f "$SOURCE/scripts/p2-consumer-broker.sh" ] ||
    usage_error \
      "source p2-consumer-broker.sh not found"

  [ -f "$SOURCE/scripts/pilot-collector.sh" ] ||
    usage_error \
      "source pilot-collector.sh not found"

  git -C "$SOURCE" rev-parse --is-inside-work-tree \
    >/dev/null 2>&1 ||
    usage_error \
      "--source must be a git working tree"

  SOURCE="$(canonical_path "$SOURCE")" ||
    usage_error \
      "cannot canonicalize --source"

  LIVE_SKILL_DIR="$(canonical_path "$LIVE_SKILL_DIR")" ||
    usage_error \
      "cannot canonicalize --live-skill-dir"

  mkdir -p "$ARTIFACT_DIR" ||
    usage_error \
      "cannot create artifact directory"

  ARTIFACT_DIR="$(canonical_path "$ARTIFACT_DIR")" ||
    usage_error \
      "cannot canonicalize --artifact-dir"

  if ! git -C "$SOURCE" diff --quiet --; then
    usage_error \
      "--source has tracked working-tree modifications; formal gate source must be fixed"
  fi

  if ! git -C "$SOURCE" diff --cached --quiet --; then
    usage_error \
      "--source has staged modifications; formal gate source must be fixed"
  fi
}

scrub_github_credentials() {
  # Runbook §7.2 requires the F2 environment to have no GitHub credential.
  #
  # Do this before P0/P1 so the entire disposable process tree inherits the
  # fail-closed environment.
  unset GH_TOKEN || true
  unset GITHUB_TOKEN || true
  unset GH_ENTERPRISE_TOKEN || true
  unset GITHUB_ENTERPRISE_TOKEN || true
}

identify_native_claude() {
  CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"

  [ -n "$CLAUDE_BIN" ] ||
    usage_error \
      "native claude CLI cannot be positively identified"

  CLAUDE_BIN_CANONICAL="$(canonical_path "$CLAUDE_BIN")" ||
    usage_error \
      "cannot canonicalize native claude executable"

  [ -f "$CLAUDE_BIN_CANONICAL" ] ||
    usage_error \
      "resolved claude executable is not a regular file"

  [ -x "$CLAUDE_BIN_CANONICAL" ] ||
    usage_error \
      "resolved claude executable is not executable"

  CLAUDE_DIGEST="$(sha256_file "$CLAUDE_BIN_CANONICAL")" ||
    usage_error \
      "cannot digest native claude executable"

  if ! CLAUDE_VERSION="$("$CLAUDE_BIN" --version 2>&1)"; then
    usage_error \
      "claude --version failed"
  fi

  [ -n "$CLAUDE_VERSION" ] ||
    usage_error \
      "claude --version returned no output"
}

bootstrap_run() {
  RUN_ID="$(
    python3 "$ISOLATION_HELPER" new-run-id
  )" ||
    internal_error \
      "cannot generate run id"

  GATE_TEAM="$(
    python3 "$ISOLATION_HELPER" gate-team "$RUN_ID"
  )" ||
    internal_error \
      "cannot generate gate team"

  RUN_ROOT="$(
    mktemp -d \
      "${TMPDIR:-/tmp}/agmsg-g4gate.${RUN_ID}.XXXXXX"
  )" ||
    internal_error \
      "cannot create disposable run root"

  RUN_ROOT="$(canonical_path "$RUN_ROOT")" ||
    internal_error \
      "cannot canonicalize disposable run root"

  GATE_REPO="$RUN_ROOT/repo"
  GATE_HOME="$RUN_ROOT/home"
  GATE_XDG_CONFIG="$RUN_ROOT/xdg/config"
  GATE_XDG_CACHE="$RUN_ROOT/xdg/cache"
  GATE_XDG_DATA="$RUN_ROOT/xdg/data"
  GATE_XDG_STATE="$RUN_ROOT/xdg/state"
  GATE_CLAUDE_CONFIG="$RUN_ROOT/claude"

  F2_PROBE_TARGET="$GATE_REPO/.agmsg-gate/f2-probe-$RUN_ID"
  F2_PROBE_PROGRAM="$GATE_REPO/.agmsg-gate/f2-probe-command.py"
  F2_PROBE_MANIFEST="$ARTIFACT_DIR/f2-probe-manifest.json"

  STATE_FILE="$ARTIFACT_DIR/part1-state.json"

  SOURCE_HEAD="$(
    git -C "$SOURCE" rev-parse HEAD
  )" ||
    internal_error \
      "cannot resolve source HEAD"

  python3 "$ISOLATION_HELPER" write-state \
    --output "$STATE_FILE" \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --source "$SOURCE" \
    --live-skill-dir "$LIVE_SKILL_DIR" \
    --artifact-dir "$ARTIFACT_DIR" \
    --gate-team "$GATE_TEAM" \
    --gate-repo "$GATE_REPO" \
    --gate-home "$GATE_HOME" \
    --xdg-config "$GATE_XDG_CONFIG" \
    --xdg-cache "$GATE_XDG_CACHE" \
    --xdg-data "$GATE_XDG_DATA" \
    --xdg-state "$GATE_XDG_STATE" \
    --claude-config "$GATE_CLAUDE_CONFIG" \
    --source-head "$SOURCE_HEAD" ||
    internal_error \
      "cannot write Part 1 state"

  log "run-id=$RUN_ID"
}

export_isolated_environment() {
  export HOME="$GATE_HOME"
  export XDG_CONFIG_HOME="$GATE_XDG_CONFIG"
  export XDG_CACHE_HOME="$GATE_XDG_CACHE"
  export XDG_DATA_HOME="$GATE_XDG_DATA"
  export XDG_STATE_HOME="$GATE_XDG_STATE"
  export CLAUDE_CONFIG_DIR="$GATE_CLAUDE_CONFIG"
}

phase_p0_environment_capture() {
  log "P0 environment capture"

  mkdir -p "$ARTIFACT_DIR/P0" ||
    internal_error \
      "cannot create P0 artifact directory"

  python3 "$ISOLATION_HELPER" capture-environment \
    --output "$ARTIFACT_DIR/environment.json" \
    --run-id "$RUN_ID" \
    --source "$SOURCE" \
    --source-head "$SOURCE_HEAD" \
    --live-skill-dir "$LIVE_SKILL_DIR" \
    --artifact-dir "$ARTIFACT_DIR" \
    --run-root "$RUN_ROOT" \
    --gate-team "$GATE_TEAM" \
    --claude-bin "$CLAUDE_BIN" \
    --claude-resolved "$CLAUDE_BIN_CANONICAL" \
    --claude-version "$CLAUDE_VERSION" \
    --claude-digest "$CLAUDE_DIGEST" \
    --collector-cutoff-seconds "$COLLECTOR_CUTOFF_SECONDS" ||
    internal_error \
      "P0 environment capture failed"
}

run_live_pm_control() {
  local phase="$1"
  local out_dir="$ARTIFACT_DIR/live-pm/$phase"
  local fixture="$out_dir/input.raw"
  local stdout_file="$out_dir/stdout.raw"
  local stderr_file="$out_dir/stderr.raw"
  local status_file="$out_dir/exit-status"
  local digest_file="$out_dir/guard.sha256"
  local status

  mkdir -p "$out_dir" ||
    internal_error \
      "cannot create live PM control directory"

  LIVE_GUARD="$LIVE_SKILL_DIR/scripts/pm-pretool-guard"

  [ -f "$LIVE_GUARD" ] ||
    internal_error \
      "live PM guard disappeared"

  sha256_file "$LIVE_GUARD" > "$digest_file" ||
    internal_error \
      "cannot digest live PM guard"

  # Deliberately malformed-for-policy but valid JSON fixture.
  #
  # It fails during parseInput() before session identity evaluation. We also
  # remove AGMSG_PM_DECISIONS_FILE so the guard cannot append to the live PM
  # decision log while this negative control is being observed.
  printf '{}\n' > "$fixture"

  set +e
  env \
    -u AGMSG_PM_DECISIONS_FILE \
    -u AGMSG_PM_BINDING_FILE \
    -u AGMSG_PM_PILOT_SESSION_ID \
    -u AGMSG_PM_PROCESS_GENERATION \
    -u AGMSG_PM_PROCESS_PID \
    -u AGMSG_PM_PROCESS_START \
    -u AGMSG_PM_TEAM \
    -u AGMSG_PM_AGENT \
    -u AGMSG_PM_TYPE \
    -u AGMSG_PM_CLAIM_FILE \
    "$LIVE_GUARD" \
      < "$fixture" \
      > "$stdout_file" \
      2> "$stderr_file"
  status="$?"
  set -e

  printf '%s\n' "$status" > "$status_file"

  python3 "$ISOLATION_HELPER" record-live-control \
    --directory "$out_dir" \
    --phase "$phase" ||
    internal_error \
      "cannot record live PM control metadata"
}

phase_p1_live_pm_before() {
  log "P1 live PM before-control"

  run_live_pm_control "before"

  LIVE_GUARD_DIGEST_BEFORE="$(
    cat "$ARTIFACT_DIR/live-pm/before/guard.sha256"
  )"

  [ -n "$LIVE_GUARD_DIGEST_BEFORE" ] ||
    internal_error \
      "live PM before digest is empty"
}

phase_p2_isolation_setup() {
  local remote

  log "P2 isolation setup"

  mkdir -p \
    "$GATE_HOME" \
    "$GATE_XDG_CONFIG" \
    "$GATE_XDG_CACHE" \
    "$GATE_XDG_DATA" \
    "$GATE_XDG_STATE" \
    "$GATE_CLAUDE_CONFIG" ||
    internal_error \
      "cannot create disposable HOME/XDG roots"

  export_isolated_environment

  # --no-hardlinks is mandatory. The subsequent preflight independently
  # checks inode/device identity against the live tree.
  if ! git clone \
    --no-hardlinks \
    --no-checkout \
    "$SOURCE" \
    "$GATE_REPO" \
    > "$ARTIFACT_DIR/P2-git-clone.stdout" \
    2> "$ARTIFACT_DIR/P2-git-clone.stderr"
  then
    internal_error \
      "cannot create disposable repository"
  fi

  if ! git -C "$GATE_REPO" checkout \
    --detach \
    "$SOURCE_HEAD" \
    >> "$ARTIFACT_DIR/P2-git-clone.stdout" \
    2>> "$ARTIFACT_DIR/P2-git-clone.stderr"
  then
    internal_error \
      "cannot check out fixed source HEAD in disposable repository"
  fi

  # Remove every remote rather than assuming "origin".
  for remote in $(
    git -C "$GATE_REPO" remote
  ); do
    git -C "$GATE_REPO" remote remove "$remote" ||
      internal_error \
        "cannot remove disposable repository remote: $remote"
  done

  # N1 needs an actual profile because G4-A digests it before exec.
  #
  # Part 1 does not run F2 itself yet, so no fault hook is installed here.
  # Later parts can derive control/fault profiles from this gate-owned copy.
  mkdir -p "$GATE_REPO/.claude" ||
    internal_error \
      "cannot create disposable Claude profile directory"

  cat > "$GATE_REPO/.claude/settings.local.json" <<'JSON'
{
  "hooks": {}
}
JSON

  # The gate team must exist only in the disposable skill root.
  #
  # AGMSG_RESOLVE_PROJECT=0 keeps the explicitly selected canonical gate
  # project instead of allowing unrelated registration state to redirect it.
  if ! AGMSG_RESOLVE_PROJECT=0 \
    "$GATE_REPO/scripts/join.sh" \
      "$GATE_TEAM" \
      "$PILOT_AGENT" \
      "$PILOT_TYPE" \
      "$GATE_REPO" \
      --role manager \
      --kind seat \
      > "$ARTIFACT_DIR/P2-join.stdout" \
      2> "$ARTIFACT_DIR/P2-join.stderr"
  then
    internal_error \
      "cannot provision disposable pilot team"
  fi
}

phase_p3_isolation_preflight() {
  local status

  log "P3 isolation preflight"

  set +e
  python3 "$ISOLATION_HELPER" preflight \
    --output "$ARTIFACT_DIR/isolation.json" \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --source "$SOURCE" \
    --live-repo "$LIVE_SKILL_DIR" \
    --gate-repo "$GATE_REPO" \
    --artifact-dir "$ARTIFACT_DIR" \
    --gate-team "$GATE_TEAM" \
    --pilot-agent "$PILOT_AGENT" \
    --pilot-type "$PILOT_TYPE" \
    --gate-home "$GATE_HOME" \
    --xdg-config "$GATE_XDG_CONFIG" \
    --xdg-cache "$GATE_XDG_CACHE" \
    --xdg-data "$GATE_XDG_DATA" \
    --xdg-state "$GATE_XDG_STATE" \
    --claude-config "$GATE_CLAUDE_CONFIG" \
    --claude-bin "$CLAUDE_BIN_CANONICAL"
  status="$?"
  set -e

  case "$status" in
    0)
      log "P3 isolation preflight: safe=true"
      return 0
      ;;

    2)
      log \
        "P3 isolation preflight: unprovable/unsafe; aborting before N1"
      return "$EX_GATE_UNKNOWN"
      ;;

    *)
      log \
        "P3 isolation preflight: helper failure status=$status"
      return "$EX_INTERNAL"
      ;;
  esac
}

phase_p4_f2_containment() {
  local status
  local guard_digest_now

  log "P4 F2 containment proof"

  python3 "$ISOLATION_HELPER" make-f2-probe \
    --run-id "$RUN_ID" \
    --gate-repo "$GATE_REPO" \
    --target "$F2_PROBE_TARGET" \
    --program "$F2_PROBE_PROGRAM" \
    --manifest "$F2_PROBE_MANIFEST" ||
    internal_error \
      "cannot create fixed F2 probe definition"

  # The probe itself MUST NOT have run while constructing the proof.
  [ ! -e "$F2_PROBE_TARGET" ] ||
    internal_error \
      "F2 probe target exists before containment proof"

  set +e
  python3 "$ISOLATION_HELPER" f2-proof \
    --output "$ARTIFACT_DIR/f2-containment.json" \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --gate-repo "$GATE_REPO" \
    --live-repo "$LIVE_SKILL_DIR" \
    --gate-home "$GATE_HOME" \
    --xdg-config "$GATE_XDG_CONFIG" \
    --probe-target "$F2_PROBE_TARGET" \
    --probe-program "$F2_PROBE_PROGRAM" \
    --probe-manifest "$F2_PROBE_MANIFEST"
  status="$?"
  set -e

  case "$status" in
    0)
      ;;
    2)
      log \
        "P4 F2 containment safe=false; no native fault-capable session may start"
      return "$EX_GATE_UNKNOWN"
      ;;
    *)
      log \
        "P4 F2 containment helper failure status=$status"
      return "$EX_INTERNAL"
      ;;
  esac

  # Immediate-abort condition from runbook §37:
  # live PM guard must not have changed before any later gate activity.
  guard_digest_now="$(sha256_file "$LIVE_GUARD")" ||
    internal_error \
      "cannot re-digest live PM guard after P4"

  if [ "$guard_digest_now" != "$LIVE_GUARD_DIGEST_BEFORE" ]; then
    log \
      "live PM guard changed between P1 and P4; aborting"
    return "$EX_GATE_FAIL"
  fi

  log "P4 F2 containment proof: safe=true"
}

find_binding() {
  local generation="$1"

  python3 "$ISOLATION_HELPER" find-binding \
    --gate-repo "$GATE_REPO" \
    --team "$GATE_TEAM" \
    --agent "$PILOT_AGENT" \
    --generation "$generation"
}

wait_for_binding() {
  local generation="$1"
  local pid="$2"
  local deadline
  local binding

  deadline=$((SECONDS + N1_START_TIMEOUT_SECONDS))

  while [ "$SECONDS" -le "$deadline" ]; do
    binding="$(find_binding "$generation" 2>/dev/null || true)"

    if [ -n "$binding" ]; then
      printf '%s\n' "$binding"
      return 0
    fi

    if ! kill -0 "$pid" >/dev/null 2>&1; then
      return 1
    fi

    sleep 1
  done

  return 1
}

wait_for_transcript() {
  local session_id="$1"
  local deadline
  local result

  deadline=$((SECONDS + N1_TRANSCRIPT_TIMEOUT_SECONDS))

  while [ "$SECONDS" -le "$deadline" ]; do
    result="$(
      python3 "$ISOLATION_HELPER" find-transcript \
        --claude-config "$GATE_CLAUDE_CONFIG" \
        --session-id "$session_id" \
        2>/dev/null ||
        true
    )"

    if [ -n "$result" ]; then
      printf '%s\n' "$result"
      return 0
    fi

    sleep 1
  done

  return 1
}

terminate_native_process() {
  local pid="$1"
  local deadline

  [ -n "$pid" ] ||
    return 0

  if ! kill -0 "$pid" >/dev/null 2>&1; then
    return 0
  fi

  kill -TERM "$pid" >/dev/null 2>&1 ||
    true

  deadline=$((SECONDS + N1_EXIT_GRACE_SECONDS))

  while [ "$SECONDS" -le "$deadline" ]; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  kill -KILL "$pid" >/dev/null 2>&1 ||
    true

  return 0
}

record_process_command() {
  local pid="$1"
  local output="$2"

  if ps -ww -p "$pid" -o command= \
    > "$output" 2>/dev/null
  then
    return 0
  fi

  : > "$output"
  return 1
}

launch_n1_case() {
  local mode="$1"
  local expected_generation="$2"
  local expected_session="${3:-}"
  local case_dir="$ARTIFACT_DIR/N1/$mode"
  local fifo="$case_dir/stdin.fifo"
  local launcher_pid
  local binding
  local session_id
  local transcript
  local process_command
  local validation_status

  CASE_BINDING=""
  CASE_SESSION=""
  CASE_TRANSCRIPT=""
  CASE_PROCESS_COMMAND=""
  CASE_STATUS="$EX_GATE_UNKNOWN"

  mkdir -p "$case_dir" ||
    internal_error \
      "cannot create N1/$mode artifact directory"

  rm -f "$fifo"
  mkfifo "$fifo" ||
    internal_error \
      "cannot create N1/$mode stdin fifo"

  # Open FIFO read/write so the launcher receives a live stdin but the harness
  # does not need to inject arbitrary conversation text merely to prove N1.
  exec 9<> "$fifo"

  if [ "$mode" = "fresh" ]; then
    (
      export_isolated_environment

      exec "$GATE_REPO/scripts/pilot-launcher.sh" \
        --team "$GATE_TEAM" \
        --project "$GATE_REPO" \
        --fresh
    ) \
      < "$fifo" \
      > "$case_dir/stdout.raw" \
      2> "$case_dir/stderr.raw" &

    launcher_pid="$!"
  else
    (
      export_isolated_environment

      exec "$GATE_REPO/scripts/pilot-launcher.sh" \
        --team "$GATE_TEAM" \
        --project "$GATE_REPO" \
        --resume
    ) \
      < "$fifo" \
      > "$case_dir/stdout.raw" \
      2> "$case_dir/stderr.raw" &

    launcher_pid="$!"
  fi

  CURRENT_NATIVE_PID="$launcher_pid"
  printf '%s\n' "$launcher_pid" \
    > "$case_dir/launcher-pid"

  if ! binding="$(
    wait_for_binding \
      "$expected_generation" \
      "$launcher_pid"
  )"; then
    log \
      "N1/$mode: binding was not positively observed"

    exec 9>&-
    terminate_native_process "$launcher_pid"
    CURRENT_NATIVE_PID=""

    printf '%s\n' "unknown" \
      > "$case_dir/verdict"

    CASE_STATUS="$EX_GATE_UNKNOWN"
    return "$EX_GATE_UNKNOWN"
  fi

  CASE_BINDING="$binding"

  cp "$binding" "$case_dir/binding.json" ||
    internal_error \
      "cannot preserve N1/$mode binding"

  session_id="$(
    json_field "$binding" sessionId
  )" || {
    exec 9>&-
    terminate_native_process "$launcher_pid"
    CURRENT_NATIVE_PID=""
    printf '%s\n' "unknown" > "$case_dir/verdict"
    CASE_STATUS="$EX_GATE_UNKNOWN"
    return "$EX_GATE_UNKNOWN"
  }

  CASE_SESSION="$session_id"

  set +e
  python3 "$ISOLATION_HELPER" validate-binding \
    --binding "$binding" \
    --output "$case_dir/binding-validation.json" \
    --team "$GATE_TEAM" \
    --agent "$PILOT_AGENT" \
    --project "$GATE_REPO" \
    --generation "$expected_generation" \
    --process-pid "$launcher_pid" \
    ${expected_session:+--expected-session "$expected_session"}
  validation_status="$?"
  set -e

  if [ "$validation_status" -ne 0 ]; then
    exec 9>&-
    terminate_native_process "$launcher_pid"
    CURRENT_NATIVE_PID=""

    case "$validation_status" in
      1)
        printf '%s\n' "fail" > "$case_dir/verdict"
        CASE_STATUS="$EX_GATE_FAIL"
        return "$EX_GATE_FAIL"
        ;;
      *)
        printf '%s\n' "unknown" > "$case_dir/verdict"
        CASE_STATUS="$EX_GATE_UNKNOWN"
        return "$EX_GATE_UNKNOWN"
        ;;
    esac
  fi

  # The immutable binding is written immediately before launcher exec.
  # Therefore a binding without a still-live process is insufficient proof that
  # the native Claude process actually started.
  if ! kill -0 "$launcher_pid" >/dev/null 2>&1; then
    exec 9>&-
    CURRENT_NATIVE_PID=""

    printf '%s\n' "unknown" > "$case_dir/verdict"
    CASE_STATUS="$EX_GATE_UNKNOWN"
    return "$EX_GATE_UNKNOWN"
  fi

  process_command="$case_dir/process-command.raw"

  if ! record_process_command \
    "$launcher_pid" \
    "$process_command"
  then
    exec 9>&-
    terminate_native_process "$launcher_pid"
    CURRENT_NATIVE_PID=""

    printf '%s\n' "unknown" > "$case_dir/verdict"
    CASE_STATUS="$EX_GATE_UNKNOWN"
    return "$EX_GATE_UNKNOWN"
  fi

  CASE_PROCESS_COMMAND="$(
    cat "$process_command"
  )"

  set +e
  python3 "$ISOLATION_HELPER" validate-process-command \
    --command-file "$process_command" \
    --mode "$mode" \
    --session-id "$session_id" \
    --settings "$GATE_REPO/.claude/settings.local.json"
  validation_status="$?"
  set -e

  if [ "$validation_status" -ne 0 ]; then
    exec 9>&-
    terminate_native_process "$launcher_pid"
    CURRENT_NATIVE_PID=""

    case "$validation_status" in
      1)
        printf '%s\n' "fail" > "$case_dir/verdict"
        CASE_STATUS="$EX_GATE_FAIL"
        return "$EX_GATE_FAIL"
        ;;
      *)
        printf '%s\n' "unknown" > "$case_dir/verdict"
        CASE_STATUS="$EX_GATE_UNKNOWN"
        return "$EX_GATE_UNKNOWN"
        ;;
    esac
  fi

  if ! transcript="$(
    wait_for_transcript "$session_id"
  )"; then
    log \
      "N1/$mode: native transcript not uniquely observed"

    exec 9>&-
    terminate_native_process "$launcher_pid"
    CURRENT_NATIVE_PID=""

    printf '%s\n' "unknown" > "$case_dir/verdict"
    CASE_STATUS="$EX_GATE_UNKNOWN"
    return "$EX_GATE_UNKNOWN"
  fi

  CASE_TRANSCRIPT="$transcript"

  printf '%s\n' "$transcript" \
    > "$case_dir/transcript-path"

  # End the session after all required live observations have been captured.
  #
  # Closing stdin gives the native CLI a chance to end normally. TERM/KILL are
  # only bounded fallbacks. Full runtime cleanup belongs to a later Part.
  exec 9>&-

  sleep 1

  terminate_native_process "$launcher_pid"
  CURRENT_NATIVE_PID=""

  set +e
  wait "$launcher_pid" >/dev/null 2>&1
  set -e

  printf '%s\n' "pass" > "$case_dir/verdict"

  CASE_STATUS="$EX_GATE_PASS"
  return "$EX_GATE_PASS"
}

phase_n1() {
  local fresh_status
  local resume_status
  local fresh_binding
  local fresh_session
  local fresh_binding_digest_before
  local fresh_binding_digest_after
  local resume_binding
  local resume_session
  local n1_status
  local state_status

  log "N1 native fresh/resume"

  mkdir -p "$ARTIFACT_DIR/N1"

  set +e
  launch_n1_case "fresh" "1"
  fresh_status="$?"
  set -e

  case "$fresh_status" in
    0)
      ;;
    1)
      python3 "$ISOLATION_HELPER" write-n1-result \
        --output "$ARTIFACT_DIR/N1/result.json" \
        --verdict fail \
        --reason fresh_failed
      return "$EX_GATE_FAIL"
      ;;
    *)
      python3 "$ISOLATION_HELPER" write-n1-result \
        --output "$ARTIFACT_DIR/N1/result.json" \
        --verdict unknown \
        --reason fresh_unobservable
      return "$EX_GATE_UNKNOWN"
      ;;
  esac

  fresh_binding="$CASE_BINDING"
  fresh_session="$CASE_SESSION"

  fresh_binding_digest_before="$(
    sha256_file "$fresh_binding"
  )" ||
    internal_error \
      "cannot hash immutable fresh binding"

  printf '%s\n' "$fresh_binding_digest_before" \
    > "$ARTIFACT_DIR/N1/fresh/binding.sha256.before-resume"

  set +e
  launch_n1_case \
    "resume" \
    "2" \
    "$fresh_session"
  resume_status="$?"
  set -e

  case "$resume_status" in
    0)
      ;;
    1)
      python3 "$ISOLATION_HELPER" write-n1-result \
        --output "$ARTIFACT_DIR/N1/result.json" \
        --verdict fail \
        --reason resume_failed
      return "$EX_GATE_FAIL"
      ;;
    *)
      python3 "$ISOLATION_HELPER" write-n1-result \
        --output "$ARTIFACT_DIR/N1/result.json" \
        --verdict unknown \
        --reason resume_unobservable
      return "$EX_GATE_UNKNOWN"
      ;;
  esac

  resume_binding="$CASE_BINDING"
  resume_session="$CASE_SESSION"

  fresh_binding_digest_after="$(
    sha256_file "$fresh_binding"
  )" ||
    internal_error \
      "cannot re-hash immutable fresh binding"

  printf '%s\n' "$fresh_binding_digest_after" \
    > "$ARTIFACT_DIR/N1/fresh/binding.sha256.after-resume"

  if [ "$fresh_binding_digest_before" != "$fresh_binding_digest_after" ]; then
    python3 "$ISOLATION_HELPER" write-n1-result \
      --output "$ARTIFACT_DIR/N1/result.json" \
      --verdict fail \
      --reason old_binding_mutated

    return "$EX_GATE_FAIL"
  fi

  if [ "$fresh_session" != "$resume_session" ]; then
    python3 "$ISOLATION_HELPER" write-n1-result \
      --output "$ARTIFACT_DIR/N1/result.json" \
      --verdict fail \
      --reason resume_session_changed

    return "$EX_GATE_FAIL"
  fi

  set +e
  python3 "$ISOLATION_HELPER" validate-state \
    --binding "$resume_binding" \
    --expected-generation 2 \
    --expected-session "$fresh_session" \
    --output "$ARTIFACT_DIR/N1/state-validation.json"
  state_status="$?"
  set -e

  case "$state_status" in
    0)
      n1_status="$EX_GATE_PASS"
      ;;
    1)
      n1_status="$EX_GATE_FAIL"
      ;;
    *)
      n1_status="$EX_GATE_UNKNOWN"
      ;;
  esac

  case "$n1_status" in
    0)
      python3 "$ISOLATION_HELPER" write-n1-result \
        --output "$ARTIFACT_DIR/N1/result.json" \
        --verdict pass \
        --reason all_n1_assertions_observed

      log "N1: pass"
      return "$EX_GATE_PASS"
      ;;

    1)
      python3 "$ISOLATION_HELPER" write-n1-result \
        --output "$ARTIFACT_DIR/N1/result.json" \
        --verdict fail \
        --reason state_continuity_failed

      log "N1: fail"
      return "$EX_GATE_FAIL"
      ;;

    *)
      python3 "$ISOLATION_HELPER" write-n1-result \
        --output "$ARTIFACT_DIR/N1/result.json" \
        --verdict unknown \
        --reason state_continuity_unobservable

      log "N1: unknown"
      return "$EX_GATE_UNKNOWN"
      ;;
  esac
}

phase_i1() {
  local status

  log "I1 five consumer operations + identity isolation"

  #
  # I1_HELPER owns the I1 artifact subtree:
  #
  #   $ARTIFACT_DIR/I1/
  #
  # It MUST execute the consumer operations through:
  #
  #   native Claude
  #     -> actual PreToolUse
  #     -> actual pilot guard
  #     -> actual p2-consumer-broker.sh
  #     -> dependency
  #
  # A broker-direct observation alone is therefore insufficient for pass.
  #
  set +e
  python3 "$I1_HELPER" \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --gate-repo "$GATE_REPO" \
    --artifact-dir "$ARTIFACT_DIR" \
    --gate-team "$GATE_TEAM" \
    --claude-config "$GATE_CLAUDE_CONFIG"
  status="$?"
  set -e

  case "$status" in
    0)
      log "I1 passed"
      return "$EX_GATE_PASS"
      ;;

    1)
      log "I1 failed"
      return "$EX_GATE_FAIL"
      ;;

    2)
      log "I1 unknown"
      return "$EX_GATE_UNKNOWN"
      ;;

    *)
      #
      # An undocumented helper exit code is a harness defect, not an I1
      # semantic fail/unknown observation.
      #
      log \
        "I1 helper returned unsupported exit status: $status"
      return "$EX_INTERNAL"
      ;;
  esac
}

run_p0_through_p4() {
  local status

  phase_p0_environment_capture
  phase_p1_live_pm_before
  phase_p2_isolation_setup

  set +e
  phase_p3_isolation_preflight
  status="$?"
  set -e

  [ "$status" -eq 0 ] ||
    return "$status"

  set +e
  phase_p4_f2_containment
  status="$?"
  set -e

  [ "$status" -eq 0 ] ||
    return "$status"

  return 0
}

subcommand_preflight() {
  local status

  set +e
  run_p0_through_p4
  status="$?"
  set -e

  return "$status"
}

phase_f1() {
  local status

  log "F1 broker/backend failure"

  set +e
  python3 "$F1_HELPER" \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --gate-repo "$GATE_REPO" \
    --artifact-dir "$ARTIFACT_DIR" \
    --gate-team "$GATE_TEAM" \
    --claude-config "$GATE_CLAUDE_CONFIG"
  status="$?"
  set -e

  case "$status" in
    0)
      log "F1 passed"
      return "$EX_GATE_PASS"
      ;;
    1)
      log "F1 failed"
      return "$EX_GATE_FAIL"
      ;;
    2)
      log "F1 unknown"
      return "$EX_GATE_UNKNOWN"
      ;;
    *)
      log \
        "F1 helper returned unsupported exit status: $status"
      return "$EX_INTERNAL"
      ;;
  esac
}

phase_f2() {
  local status

  log "F2 PreToolUse hook unavailable"

  set +e
  python3 "$F2_HELPER" \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --gate-repo "$GATE_REPO" \
    --artifact-dir "$ARTIFACT_DIR" \
    --gate-team "$GATE_TEAM" \
    --claude-config "$GATE_CLAUDE_CONFIG"
  status="$?"
  set -e

  case "$status" in
    0)
      log "F2 passed"
      return "$EX_GATE_PASS"
      ;;
    1)
      log "F2 failed"
      return "$EX_GATE_FAIL"
      ;;
    2)
      log "F2 unknown"
      return "$EX_GATE_UNKNOWN"
      ;;
    *)
      log \
        "F2 helper returned unsupported exit status: $status"
      return "$EX_INTERNAL"
      ;;
  esac
}

phase_f3() {
  local status

  log "F3 PostToolUse stopped"

  set +e
  python3 "$F3_HELPER" \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --gate-repo "$GATE_REPO" \
    --artifact-dir "$ARTIFACT_DIR" \
    --gate-team "$GATE_TEAM" \
    --claude-config "$GATE_CLAUDE_CONFIG"
  status="$?"
  set -e

  case "$status" in
    0)
      log "F3 passed"
      return "$EX_GATE_PASS"
      ;;
    1)
      log "F3 failed"
      return "$EX_GATE_FAIL"
      ;;
    2)
      log "F3 unknown"
      return "$EX_GATE_UNKNOWN"
      ;;
    *)
      log \
        "F3 helper returned unsupported exit status: $status"
      return "$EX_INTERNAL"
      ;;
  esac
}

phase_f4() {
  local status

  log "F4 audit loop cutoff"

  set +e
  python3 "$F4_HELPER" \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --gate-repo "$GATE_REPO" \
    --artifact-dir "$ARTIFACT_DIR" \
    --gate-team "$GATE_TEAM" \
    --claude-config "$GATE_CLAUDE_CONFIG" \
    --cutoff-seconds "$COLLECTOR_CUTOFF_SECONDS"
  status="$?"
  set -e

  case "$status" in
    0)
      log "F4 passed"
      return "$EX_GATE_PASS"
      ;;
    1)
      log "F4 failed"
      return "$EX_GATE_FAIL"
      ;;
    2)
      log "F4 unknown"
      return "$EX_GATE_UNKNOWN"
      ;;
    *)
      log \
        "F4 helper returned unsupported exit status: $status"
      return "$EX_INTERNAL"
      ;;
  esac
}

phase_f5() {
  local status

  log "F5 notification delivery"

  set +e
  python3 "$F5_HELPER" \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --gate-repo "$GATE_REPO" \
    --artifact-dir "$ARTIFACT_DIR" \
    --gate-team "$GATE_TEAM" \
    --claude-config "$GATE_CLAUDE_CONFIG"
  status="$?"
  set -e

  case "$status" in
    0)
      log "F5 passed"
      return "$EX_GATE_PASS"
      ;;
    1)
      log "F5 failed"
      return "$EX_GATE_FAIL"
      ;;
    2)
      log "F5 unknown"
      return "$EX_GATE_UNKNOWN"
      ;;
    *)
      log \
        "F5 helper returned unsupported exit status: $status"
      return "$EX_INTERNAL"
      ;;
  esac
}

subcommand_run() {
  local status
  local n1_status
  local i1_status
  local f1_status
  local f2_status
  local f3_status
  local f4_status
  local f5_status

  set +e
  run_p0_through_p4
  status="$?"
  set -e

  [ "$status" -eq 0 ] ||
    return "$status"

  set +e
  phase_n1
  n1_status="$?"
  set -e

  [ "$n1_status" -eq 0 ] ||
    return "$n1_status"

  #
  # N1 is a prerequisite for I1. Even --check I1 therefore executes N1 first,
  # because I1 requires the native pilot binding/session established by the
  # preceding native integration path.
  #
  if [ "$CHECK" = "N1" ]; then
    return "$EX_GATE_PASS"
  fi

  set +e
  phase_i1
  i1_status="$?"
  set -e

  [ "$i1_status" -eq 0 ] ||
    return "$i1_status"

  if [ "$CHECK" = "I1" ]; then
    return "$EX_GATE_PASS"
  fi

  set +e
  phase_f1
  f1_status="$?"
  set -e

  [ "$f1_status" -eq 0 ] ||
    return "$f1_status"

  if [ "$CHECK" = "F1" ]; then
    return "$EX_GATE_PASS"
  fi

  set +e
  phase_f2
  f2_status="$?"
  set -e

  [ "$f2_status" -eq 0 ] ||
    return "$f2_status"

  if [ "$CHECK" = "F2" ]; then
    return "$EX_GATE_PASS"
  fi

  set +e
  phase_f3
  f3_status="$?"
  set -e

  [ "$f3_status" -eq 0 ] ||
    return "$f3_status"

  if [ "$CHECK" = "F3" ]; then
    return "$EX_GATE_PASS"
  fi

  set +e
  phase_f4
  f4_status="$?"
  set -e

  [ "$f4_status" -eq 0 ] ||
    return "$f4_status"

  if [ "$CHECK" = "F4" ]; then
    return "$EX_GATE_PASS"
  fi

  set +e
  phase_f5
  f5_status="$?"
  set -e

  [ "$f5_status" -eq 0 ] ||
    return "$f5_status"

  if [ "$CHECK" = "F5" ]; then
    return "$EX_GATE_PASS"
  fi

  # Critical fail-closed behavior during incremental implementation:
  #
  # N1/I1/F1-F5 are now implemented, but the full runbook still requires
  # P5/P6/P7, final evidence aggregation/evaluation, and cleanup. Therefore
  # CHECK=all MUST NOT yet be interpreted as a completed full pilot gate.
  log \
    "Part 7 complete: N1/I1/F1-F5 passed, but P5-P7/evaluation/cleanup remain unknown; pilot_ready cannot be true"

  return "$EX_GATE_UNKNOWN"
}

subcommand_evaluate() {
  log \
    "evaluate is reserved for a later Issue #396 part; refusing to synthesize a full verdict"
  return "$EX_INTERNAL"
}

subcommand_cleanup() {
  log \
    "cleanup is reserved for a later Issue #396 part; refusing to report cleanup success"
  return "$EX_INTERNAL"
}

on_signal() {
  local signal_status="$1"

  if [ -n "$CURRENT_NATIVE_PID" ]; then
    terminate_native_process "$CURRENT_NATIVE_PID"
  fi

  exit "$signal_status"
}

main() {
  local status

  umask 077

  parse_args "$@"

  require_command python3
  require_command git
  require_command mktemp
  require_command ps

  validate_static_inputs
  scrub_github_credentials
  identify_native_claude
  bootstrap_run

  trap 'on_signal 129' HUP
  trap 'on_signal 130' INT
  trap 'on_signal 143' TERM

  case "$SUBCOMMAND" in
    preflight)
      set +e
      subcommand_preflight
      status="$?"
      set -e
      ;;

    run)
      set +e
      subcommand_run
      status="$?"
      set -e
      ;;

    evaluate)
      set +e
      subcommand_evaluate
      status="$?"
      set -e
      ;;

    cleanup)
      set +e
      subcommand_cleanup
      status="$?"
      set -e
      ;;

    *)
      internal_error \
        "unreachable subcommand: $SUBCOMMAND"
      ;;
  esac

  case "$status" in
    0|1|2|64|70)
      return "$status"
      ;;
    *)
      log \
        "unexpected internal status=$status"
      return "$EX_INTERNAL"
      ;;
  esac
}

main "$@"