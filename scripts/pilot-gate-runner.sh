#!/usr/bin/env bash
set -euo pipefail

# pilot-gate-runner.sh
#
# Issue #396
#
# Implements the outer G4 integration-gate harness boundary for:
#
#   P0 environment capture
#   P1 live PM before-control
#   P2 isolation setup
#   P3 isolation preflight
#   P4 F2 containment proof
#   N1 fresh/resume
#   I1 consumer-operations + identity-isolation
#   F1-F5 fault injection checks
#   P5 disposable-resource cleanup + cleanup verification
#   P6 live PM final negative control
#   P7 aggregate verdict / results.json
#
# tests/test_pilot_gate_runner.bats and companion Python test files remain to
# be implemented separately (Issue #396's own test-suite scope).
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
# #448: after the binding is observed, how long the launcher PID is watched
# for its exec into claude, and how often. A separate budget from
# N1_START_TIMEOUT_SECONDS so an unobserved exec means only that.
readonly N1_EXEC_TIMEOUT_SECONDS=10
readonly N1_EXEC_POLL_SECONDS=0.2
# #444 section 5: how long to wait for the native prompt input, and the CLI
# versions whose ready/blocking screen texts the verifier has observed. Add a
# version only after the verifier has measured it.
readonly N1_READY_TIMEOUT_SECONDS=30
readonly N1_READY_VERIFIED_CLI_VERSIONS="2.1.268"

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
CLEANUP_HELPER="$SCRIPT_DIR/lib/pilot-gate-cleanup.py"
# The one start path for the native pilot (#426); the Python helpers use
# the same module through pilot-gate-i1.NativePilot.
PTY_HELPER="$SCRIPT_DIR/lib/pilot-pty.py"

# Liveness of the launcher pids this runner spawns goes through
# _agmsg_pid_alive_local (EPERM-aware, ps cross-check), never a bare
# signal-0 probe (tests/test_instance_id.bats, #500).
# Resolved from BASH_SOURCE, not SCRIPT_DIR: SCRIPT_DIR comes from $0, which
# names the caller when this file is sourced (the bats unit tests do).
# shellcheck source=lib/instance-id.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/instance-id.sh"

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
# Private directory holding the N1 PTY control socket (#434); removed when the
# case stops.
CURRENT_N1_CONTROL_DIR=""

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
      This executes P0-P7: N1, I1, F1-F5, cleanup verification,
      live PM final negative control, and aggregate evaluation.

  evaluate
      Re-evaluate existing artifacts and regenerate observations.jsonl/results.json.
      No disposable or live resource is mutated.

  cleanup
      Run idempotent disposable-resource cleanup and cleanup verification.

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

  --check all returns gate-pass only when N1/I1/F1-F5, cleanup,
  and the live PM final negative control all pass with zero unknowns.
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

  [ -x "$CLEANUP_HELPER" ] ||
    usage_error \
      "cleanup helper unavailable or not executable: $CLEANUP_HELPER"

  [ -x "$PTY_HELPER" ] ||
    usage_error \
      "pty helper unavailable or not executable: $PTY_HELPER"

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

  # The pilot profile routes every tool through the pilot guard (#404).
  [ -f "$SOURCE/scripts/lib/pilot-profile.js" ] ||
    usage_error \
      "source scripts/lib/pilot-profile.js not found"

  [ -f "$SOURCE/scripts/pm-pilot-pretool-guard" ] ||
    usage_error \
      "source scripts/pm-pilot-pretool-guard not found"

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

# cleanup and evaluate act on the run that already exists for ARTIFACT_DIR.
# They must not mint a new run id or overwrite part1-state.json (Issue #405):
# a new run root would be cleaned instead of the one the run left behind.
load_existing_run() {
  local state="$ARTIFACT_DIR/part1-state.json"
  local fields=()
  local field

  [ -f "$state" ] ||
    usage_error \
      "no existing run for this artifact directory: $state not found"

  while IFS= read -r -d '' field; do
    fields+=("$field")
  done < <(
    python3 - "$state" "$ARTIFACT_DIR" <<'PY'
import json
import os
import sys

state_path, artifact_dir = sys.argv[1], sys.argv[2]

try:
    with open(state_path, "r", encoding="utf-8") as fh:
        state = json.load(fh)
    xdg = state["xdg"]
    values = [
        state["runId"],
        state["runRoot"],
        state["gateTeam"],
        state["gateRepo"],
        state["gateHome"],
        xdg["config"],
        xdg["cache"],
        xdg["data"],
        xdg["state"],
        state["claudeConfigDir"],
        state["artifactDir"],
    ]
except Exception:
    raise SystemExit(0)

if not all(isinstance(v, str) and v and "\0" not in v for v in values):
    raise SystemExit(0)

if os.path.realpath(values[-1]) != os.path.realpath(artifact_dir):
    raise SystemExit(0)

sys.stdout.write("\0".join(values) + "\0")
PY
  )

  [ "${#fields[@]}" -eq 11 ] ||
    usage_error \
      "part1-state.json is unreadable or belongs to another artifact directory: $state"

  RUN_ID="${fields[0]}"
  RUN_ROOT="${fields[1]}"
  GATE_TEAM="${fields[2]}"
  GATE_REPO="${fields[3]}"
  GATE_HOME="${fields[4]}"
  GATE_XDG_CONFIG="${fields[5]}"
  GATE_XDG_CACHE="${fields[6]}"
  GATE_XDG_DATA="${fields[7]}"
  GATE_XDG_STATE="${fields[8]}"
  GATE_CLAUDE_CONFIG="${fields[9]}"
  STATE_FILE="$state"

  log "existing run-id=$RUN_ID"
}

# Write <gate repo>/.claude/settings.local.json with scripts/lib/pilot-profile.js
# from the disposable copy, pointing at the copy's pm-pilot-pretool-guard. An
# empty profile would let N1/F2 run without the guard (#397, #404), so any
# failure here stops the run instead of falling back to one.
write_pilot_profile() {
  local profile_js="$GATE_REPO/scripts/lib/pilot-profile.js"
  local guard="$GATE_REPO/scripts/pm-pilot-pretool-guard"
  local posttool="$GATE_REPO/scripts/pm-posttool-record"
  local log="$ARTIFACT_DIR/P2-pilot-profile.log"

  node "$profile_js" \
    --project "$GATE_REPO" \
    --guard "$guard" \
    --posttool "$posttool" \
    > "$log" 2>&1 ||
    internal_error \
      "cannot write pilot profile (see $log)"

  [ -f "$GATE_REPO/.claude/settings.local.json" ] ||
    internal_error \
      "pilot profile was not written"
}

# Every AGMSG_PM_* variable names live PM state (binding, decision log,
# executions log, claim file, ...). A pilot inherits whatever the gate
# process carries, so a gate started from a live PM session would make the
# pilot write the live PM's logs (#415). The launcher clears and re-sets them;
# the runner clears them as well, before anything is started.
scrub_agmsg_pm_environment() {
  local name

  for name in $(compgen -v); do
    case "$name" in
      AGMSG_PM_*)
        unset "$name"
        ;;
    esac
  done
}

# P3 records that the gate environment carries no AGMSG_PM_* at all, and
# refuses to continue if one is present.
check_agmsg_pm_environment() {
  local name
  local present=()

  for name in $(compgen -v); do
    case "$name" in
      AGMSG_PM_*)
        present+=("$name")
        ;;
    esac
  done

  python3 - "$ARTIFACT_DIR/P3-agmsg-pm-environment.json" \
    "${present[@]+"${present[@]}"}" <<'PY' ||
import json
import sys

output, names = sys.argv[1], sorted(sys.argv[2:])
with open(output, "w", encoding="utf-8") as fh:
    json.dump(
        {
            "schemaVersion": 1,
            "check": "gate-environment-has-no-AGMSG_PM",
            "present": names,
            "verdict": "pass" if not names else "fail",
        },
        fh,
        indent=2,
        sort_keys=True,
    )
    fh.write("\n")
PY
    internal_error \
      "cannot record the AGMSG_PM environment check"

  [ "${#present[@]}" -eq 0 ]
}

export_isolated_environment() {
  scrub_agmsg_pm_environment
  export HOME="$GATE_HOME"
  export XDG_CONFIG_HOME="$GATE_XDG_CONFIG"
  export XDG_CACHE_HOME="$GATE_XDG_CACHE"
  export XDG_DATA_HOME="$GATE_XDG_DATA"
  export XDG_STATE_HOME="$GATE_XDG_STATE"
  export CLAUDE_CONFIG_DIR="$GATE_CLAUDE_CONFIG"
  # #444: the pilot authenticates only with CLAUDE_CODE_OAUTH_TOKEN, which is
  # inherited untouched. Competing credentials would make it unclear which
  # one the native CLI used.
  unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN
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

  status=0
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
      2> "$stderr_file" || status="$?"

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

  # N1 needs an actual profile because G4-A digests it before exec, and
  # every tool the pilot uses must go through the pilot guard (#404).
  # F2/F3 derive their control/fault profiles from this gate-owned copy.
  write_pilot_profile

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

  if ! check_agmsg_pm_environment; then
    log \
      "P3 isolation preflight: AGMSG_PM_* present in the gate environment; aborting before N1"
    return "$EX_GATE_UNKNOWN"
  fi

  status=0
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
    --claude-bin "$CLAUDE_BIN_CANONICAL" || status="$?"

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

  status=0
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
    --probe-manifest "$F2_PROBE_MANIFEST" || status="$?"

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

    if ! _agmsg_pid_alive_local "$pid"; then
      return 1
    fi

    sleep 1
  done

  return 1
}

wait_for_transcript() {
  local session_id="$1"
  local marker="$2"
  local deadline
  local result

  deadline=$((SECONDS + N1_TRANSCRIPT_TIMEOUT_SECONDS))

  while [ "$SECONDS" -le "$deadline" ]; do
    result="$(
      python3 "$ISOLATION_HELPER" find-transcript \
        --claude-config "$GATE_CLAUDE_CONFIG" \
        --session-id "$session_id" \
        --marker "$marker" \
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

  if ! _agmsg_pid_alive_local "$pid"; then
    return 0
  fi

  kill -TERM "$pid" >/dev/null 2>&1 ||
    true

  deadline=$((SECONDS + N1_EXIT_GRACE_SECONDS))

  while [ "$SECONDS" -le "$deadline" ]; do
    if ! _agmsg_pid_alive_local "$pid"; then
      return 0
    fi
    sleep 1
  done

  kill -KILL "$pid" >/dev/null 2>&1 ||
    true

  return 0
}

# Wait for the pty helper to report the launcher's PID. Fails if the helper
# exits first or nothing valid appears in time.
wait_for_launcher_pid() {
  local pid_file="$1"
  local helper_pid="$2"
  local deadline
  local pid

  deadline=$((SECONDS + N1_START_TIMEOUT_SECONDS))

  while [ "$SECONDS" -le "$deadline" ]; do
    if [ -f "$pid_file" ]; then
      pid="$(cat "$pid_file" 2>/dev/null || true)"
      if _agmsg_pid_valid "$pid" 2147483647; then
        printf '%s\n' "$pid"
        return 0
      fi
    fi

    _agmsg_pid_alive_local "$helper_pid" ||
      return 1

    sleep 0.2
  done

  return 1
}

# Stop an N1 case: the launcher (if known), then the pty helper holding its
# terminal, and reap the helper so no background job is left behind.
stop_native_case() {
  local launcher_pid="$1"
  local helper_pid="$2"

  if [ -n "$launcher_pid" ]; then
    terminate_native_process "$launcher_pid"
  fi

  if [ -n "$helper_pid" ]; then
    terminate_native_process "$helper_pid"
    wait "$helper_pid" >/dev/null 2>&1 || true
  fi

  if [ -n "$CURRENT_N1_CONTROL_DIR" ]; then
    rm -rf -- "$CURRENT_N1_CONTROL_DIR"
    CURRENT_N1_CONTROL_DIR=""
  fi
}

# --- #444: N1 input preconditions -------------------------------------------

# Merge the onboarding/trust keys into the gate's .claude.json and read them
# back. Runs before every N1 launch: the CLI rewrites the file during fresh.
prewrite_claude_config() {
  local case_dir="$1"

  python3 "$ISOLATION_HELPER" prewrite-claude-config \
    --config-dir "$GATE_CLAUDE_CONFIG" \
    --run-root "$RUN_ROOT" \
    --gate-repo "$GATE_REPO" \
    --output "$case_dir/claude-config-prewrite.json"
}

# Wait until the native screen shows the prompt input and no blocking screen.
# Sends nothing to the terminal.
wait_for_n1_ready() {
  local case_dir="$1"
  local pid="$2"
  local version
  local -a verified=()

  for version in $N1_READY_VERIFIED_CLI_VERSIONS; do
    verified+=(--verified-cli-version "$version")
  done

  python3 "$ISOLATION_HELPER" wait-n1-ready \
    --log "$case_dir/pty.raw" \
    --pid "$pid" \
    --cli-version "$CLAUDE_VERSION" \
    "${verified[@]}" \
    --timeout "$N1_READY_TIMEOUT_SECONDS" \
    --output "$case_dir/ready.json"
}

# The reason recorded by a precondition helper, or the given fallback.
n1_record_reason() {
  n1_record_reason_field "$1" reason "$2"
}

# A string field of a helper's JSON record, or the given fallback.
n1_record_reason_field() {
  local record="$1"
  local field="$2"
  local fallback="$3"
  local reason

  reason="$(
    python3 -c 'import json,sys; r=json.load(open(sys.argv[1])).get(sys.argv[2]); print(r if isinstance(r, str) and r else "")' \
      "$record" "$field" 2>/dev/null
  )" || reason=""

  printf '%s\n' "${reason:-$fallback}"
}

launch_n1_case() {
  local mode="$1"
  local expected_generation="$2"
  local expected_session="${3:-}"
  local case_dir="$ARTIFACT_DIR/N1/$mode"
  local pty_helper_pid
  local launcher_pid
  local binding
  local session_id
  local transcript
  local marker
  local process_command
  local validation_status
  local control_socket
  local prompt
  local launch_started_seconds
  local binding_observed_elapsed

  CASE_BINDING=""
  CASE_SESSION=""
  CASE_TRANSCRIPT=""
  CASE_PROCESS_COMMAND=""
  CASE_STATUS="$EX_GATE_UNKNOWN"
  CASE_REASON=""

  mkdir -p "$case_dir" ||
    internal_error \
      "cannot create N1/$mode artifact directory"

  # #444 section 3: onboarding and folder trust are prewritten, never passed
  # by pressing keys. Without a verified prewrite the launcher is not started.
  if ! prewrite_claude_config "$case_dir"; then
    CASE_REASON="$(n1_record_reason "$case_dir/claude-config-prewrite.json" claude_config_prewrite_unverified)"
    log "N1/$mode: .claude.json prewrite not verified: $CASE_REASON"
    printf '%s\n' "unknown" > "$case_dir/verdict"
    printf '%s\n' "$CASE_REASON" > "$case_dir/reason"
    return "$EX_GATE_UNKNOWN"
  fi

  # Start the launcher inside a pseudo terminal (#426). With a FIFO or a
  # file as stdin, Claude Code runs in --print mode and exits at once
  # ("Input must be provided either through stdin or as a prompt argument
  # when using --print"); the live PM is an interactive session, and so is
  # the pilot. The helper writes the launcher's PID (the launcher execs
  # claude, so it is also claude's PID) to launcher-pid.
  rm -f "$case_dir/launcher-pid"
  # AF_UNIX paths are short (104 bytes on macOS), so the control socket
  # cannot live in the nested artifact tree. Put it in a private 0700
  # directory under /tmp that only this case owns: nothing else can sit at
  # the path the helper unlinks and binds. The durable input record stays in
  # the case directory below.
  if ! CURRENT_N1_CONTROL_DIR="$(mktemp -d /tmp/agmsg-n1.XXXXXX)"; then
    CURRENT_N1_CONTROL_DIR=""
    log "N1/$mode: cannot create the PTY control directory"
    printf '%s\n' "unknown" > "$case_dir/verdict"
    CASE_STATUS="$EX_GATE_UNKNOWN"
    return "$EX_GATE_UNKNOWN"
  fi
  control_socket="$CURRENT_N1_CONTROL_DIR/control.sock"

  (
    export_isolated_environment

    exec python3 "$PTY_HELPER" run \
      --log "$case_dir/pty.raw" \
      --pid-file "$case_dir/launcher-pid" \
      --control-socket "$control_socket" \
      --cwd "$GATE_REPO" \
      -- \
      "$GATE_REPO/scripts/pilot-launcher.sh" \
        --team "$GATE_TEAM" \
        --project "$GATE_REPO" \
        "--$mode"
  ) \
    > "$case_dir/stdout.raw" \
    2> "$case_dir/stderr.raw" 3>&- 4>&- &

  pty_helper_pid="$!"
  launch_started_seconds="$SECONDS"

  if ! launcher_pid="$(
    wait_for_launcher_pid \
      "$case_dir/launcher-pid" \
      "$pty_helper_pid"
  )"; then
    log \
      "N1/$mode: the pty helper did not report a launcher PID"

    stop_native_case "" "$pty_helper_pid"

    printf '%s\n' "unknown" > "$case_dir/verdict"
    CASE_STATUS="$EX_GATE_UNKNOWN"
    return "$EX_GATE_UNKNOWN"
  fi

  CURRENT_NATIVE_PID="$launcher_pid"

  if ! binding="$(
    wait_for_binding \
      "$expected_generation" \
      "$launcher_pid"
  )"; then
    log \
      "N1/$mode: binding was not positively observed"

    stop_native_case "$launcher_pid" "$pty_helper_pid"
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
    stop_native_case "$launcher_pid" "$pty_helper_pid"
    CURRENT_NATIVE_PID=""
    printf '%s\n' "unknown" > "$case_dir/verdict"
    CASE_STATUS="$EX_GATE_UNKNOWN"
    return "$EX_GATE_UNKNOWN"
  }

  CASE_SESSION="$session_id"

  validation_status=0
  python3 "$ISOLATION_HELPER" validate-binding \
    --binding "$binding" \
    --output "$case_dir/binding-validation.json" \
    --team "$GATE_TEAM" \
    --agent "$PILOT_AGENT" \
    --project "$GATE_REPO" \
    --generation "$expected_generation" \
    --process-pid "$launcher_pid" \
    ${expected_session:+--expected-session "$expected_session"} || validation_status="$?"

  if [ "$validation_status" -ne 0 ]; then
    stop_native_case "$launcher_pid" "$pty_helper_pid"
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

  # The launcher publishes the binding and then still checks its claim, the
  # roster and the profile/guard/broker digests (several node starts) before
  # it execs claude (#448). The launcher PID may therefore still be the
  # launcher here. Watch it until it has become claude, and only then judge
  # the argv; the launcher's own command line is never an args mismatch.
  binding_observed_elapsed="$((SECONDS - launch_started_seconds))"
  validation_status=0
  python3 "$ISOLATION_HELPER" observe-n1-exec \
    --pid "$launcher_pid" \
    --launcher "$GATE_REPO/scripts/pilot-launcher.sh" \
    --claude-bin "$CLAUDE_BIN" \
    --claude-bin-canonical "$CLAUDE_BIN_CANONICAL" \
    --mode "$mode" \
    --session-id "$session_id" \
    --settings "$GATE_REPO/.claude/settings.local.json" \
    --binding "$binding" \
    --binding-observed-elapsed "$binding_observed_elapsed" \
    --pty-raw "$case_dir/pty.raw" \
    --case-dir "$case_dir" \
    --timeout "$N1_EXEC_TIMEOUT_SECONDS" \
    --poll "$N1_EXEC_POLL_SECONDS" \
    --late-window "$N1_EXIT_GRACE_SECONDS" || validation_status="$?"

  process_command="$case_dir/process-command.raw"
  CASE_PROCESS_COMMAND="$(cat "$process_command" 2>/dev/null || true)"

  if [ "$validation_status" -ne 0 ]; then
    CASE_REASON="$(n1_record_reason_field "$case_dir/exec-observation.json" result process_identity_unrecognized)"
    log "N1/$mode: exec into claude not confirmed: $CASE_REASON"
    stop_native_case "$launcher_pid" "$pty_helper_pid"
    CURRENT_NATIVE_PID=""
    printf '%s\n' "$CASE_REASON" > "$case_dir/reason"

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

  # #444 section 5: prompt only a screen that is positively ready and never
  # showed a blocking screen. No key is ever sent to get past one.
  if ! wait_for_n1_ready "$case_dir" "$launcher_pid"; then
    CASE_REASON="$(n1_record_reason "$case_dir/ready.json" ready_observation_failed)"
    log "N1/$mode: native prompt input not ready: $CASE_REASON"
    printf '%s\n' "unknown" > "$case_dir/verdict"
    printf '%s\n' "$CASE_REASON" > "$case_dir/reason"
    CASE_STATUS="$EX_GATE_UNKNOWN"
    stop_native_case "$launcher_pid" "$pty_helper_pid"
    CURRENT_NATIVE_PID=""
    return "$EX_GATE_UNKNOWN"
  fi

  marker="AGMSG_N1_TRANSCRIPT_MARKER_${RUN_ID}_${mode}_$(openssl rand -hex 16)" || {
    printf '%s\n' "unknown" > "$case_dir/verdict"
    CASE_STATUS="$EX_GATE_UNKNOWN"
    stop_native_case "$launcher_pid" "$pty_helper_pid"
    CURRENT_NATIVE_PID=""
    return "$EX_GATE_UNKNOWN"
  }
  printf '%s\n' "$marker" > "$case_dir/marker.txt"
  prompt="Reply with exactly this marker once. Do not use tools, commands, files, or network: $marker"
  printf '%s\n' "$prompt" > "$case_dir/prompt.txt"
  # Exactly one attempt per case, recorded before it is made: a failed or
  # partial write is still one input, and is never retried (design 3.2,
  # N1M-07). input-result records whether the whole prompt reached the PTY.
  printf '%s\n' "1" > "$case_dir/input-count"
  if ! python3 "$PTY_HELPER" send --socket "$control_socket" --input "$case_dir/prompt.txt"; then
    printf '%s\n' "error" > "$case_dir/input-result"
    log "N1/$mode: the prompt did not reach the native PTY in full"
    printf '%s\n' "unknown" > "$case_dir/verdict"
    CASE_STATUS="$EX_GATE_UNKNOWN"
    stop_native_case "$launcher_pid" "$pty_helper_pid"
    CURRENT_NATIVE_PID=""
    return "$EX_GATE_UNKNOWN"
  fi
  printf '%s\n' "ok" > "$case_dir/input-result"

  if ! transcript="$(
    wait_for_transcript "$session_id" "$marker"
  )"; then
    log \
      "N1/$mode: native transcript not uniquely observed"

    stop_native_case "$launcher_pid" "$pty_helper_pid"
    CURRENT_NATIVE_PID=""

    printf '%s\n' "unknown" > "$case_dir/verdict"
    CASE_STATUS="$EX_GATE_UNKNOWN"
    return "$EX_GATE_UNKNOWN"
  fi

  CASE_TRANSCRIPT="$transcript"

  printf '%s\n' "$transcript" \
    > "$case_dir/transcript-path"

  # End the session after all required live observations have been captured.
  # TERM, then KILL after the grace period; the pty helper then exits with the
  # launcher. Full runtime cleanup belongs to a later Part.
  stop_native_case "$launcher_pid" "$pty_helper_pid"
  CURRENT_NATIVE_PID=""

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

  # #444 section 4: without a usable CLAUDE_CODE_OAUTH_TOKEN in the
  # environment neither case starts a launcher. The token is never read from
  # a file and no other credential is tried.
  if ! python3 "$ISOLATION_HELPER" n1-auth --output "$ARTIFACT_DIR/N1/auth.json"; then
    log "N1: CLAUDE_CODE_OAUTH_TOKEN is absent; no native pilot is started"
    local absent_mode
    for absent_mode in fresh resume; do
      mkdir -p "$ARTIFACT_DIR/N1/$absent_mode"
      printf '%s\n' "unknown" > "$ARTIFACT_DIR/N1/$absent_mode/verdict"
      printf '%s\n' "auth_token_absent" > "$ARTIFACT_DIR/N1/$absent_mode/reason"
    done
    python3 "$ISOLATION_HELPER" write-n1-result \
      --output "$ARTIFACT_DIR/N1/result.json" \
      --verdict unknown \
      --reason auth_token_absent
    return "$EX_GATE_UNKNOWN"
  fi

  fresh_status=0
  launch_n1_case "fresh" "1" || fresh_status="$?"

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
        --reason "${CASE_REASON:-fresh_unobservable}"
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

  resume_status=0
  launch_n1_case \
    "resume" \
    "2" \
    "$fresh_session" || resume_status="$?"

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
        --reason "${CASE_REASON:-resume_unobservable}"
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

  state_status=0
  python3 "$ISOLATION_HELPER" validate-state \
    --binding "$resume_binding" \
    --expected-generation 2 \
    --expected-session "$fresh_session" \
    --output "$ARTIFACT_DIR/N1/state-validation.json" || state_status="$?"

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
  status=0
  python3 "$I1_HELPER" \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --gate-repo "$GATE_REPO" \
    --artifact-dir "$ARTIFACT_DIR" \
    --gate-team "$GATE_TEAM" \
    --claude-config "$GATE_CLAUDE_CONFIG" || status="$?"

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

  status=0
  phase_p3_isolation_preflight || status="$?"

  [ "$status" -eq 0 ] ||
    return "$status"

  status=0
  phase_p4_f2_containment || status="$?"

  [ "$status" -eq 0 ] ||
    return "$status"

  return 0
}

# Worst of two gate statuses: internal > fail > unknown > pass.
worst_status() {
  local a="$1"
  local b="$2"

  case "$a:$b" in
    *"$EX_INTERNAL"*) printf '%s\n' "$EX_INTERNAL" ;;
    *1*) printf '%s\n' "$EX_GATE_FAIL" ;;
    *2*) printf '%s\n' "$EX_GATE_UNKNOWN" ;;
    0:0) printf '%s\n' "$EX_GATE_PASS" ;;
    *) printf '%s\n' "$EX_INTERNAL" ;;
  esac
}

subcommand_preflight() {
  local status
  local cleanup_status

  status=0
  run_p0_through_p4 || status="$?"

  # preflight creates a disposable run root; it must not outlive the
  # command (Issue #405). No live PM mutation happens in P0-P4, so only
  # P5 is needed here.
  FINALIZE_PENDING=0
  cleanup_status=0
  phase_p5_cleanup || cleanup_status="$?"

  return "$(worst_status "$status" "$cleanup_status")"
}

phase_f1() {
  local status

  log "F1 broker/backend failure"

  status=0
  python3 "$F1_HELPER" \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --gate-repo "$GATE_REPO" \
    --artifact-dir "$ARTIFACT_DIR" \
    --gate-team "$GATE_TEAM" \
    --claude-config "$GATE_CLAUDE_CONFIG" || status="$?"

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

  status=0
  python3 "$F2_HELPER" \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --gate-repo "$GATE_REPO" \
    --artifact-dir "$ARTIFACT_DIR" \
    --gate-team "$GATE_TEAM" \
    --claude-config "$GATE_CLAUDE_CONFIG" || status="$?"

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

  status=0
  python3 "$F3_HELPER" \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --gate-repo "$GATE_REPO" \
    --artifact-dir "$ARTIFACT_DIR" \
    --gate-team "$GATE_TEAM" \
    --claude-config "$GATE_CLAUDE_CONFIG" || status="$?"

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

  status=0
  python3 "$F4_HELPER" \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --gate-repo "$GATE_REPO" \
    --artifact-dir "$ARTIFACT_DIR" \
    --gate-team "$GATE_TEAM" \
    --claude-config "$GATE_CLAUDE_CONFIG" \
    --cutoff-seconds "$COLLECTOR_CUTOFF_SECONDS" || status="$?"

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

  status=0
  python3 "$F5_HELPER" \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --gate-repo "$GATE_REPO" \
    --artifact-dir "$ARTIFACT_DIR" \
    --gate-team "$GATE_TEAM" \
    --claude-config "$GATE_CLAUDE_CONFIG" || status="$?"

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

phase_p5_cleanup() {
  local status

  log "P5 cleanup + cleanup verification"

  status=0
  python3 "$CLEANUP_HELPER" cleanup \
    --run-id "$RUN_ID" \
    --run-root "$RUN_ROOT" \
    --gate-repo "$GATE_REPO" \
    --gate-home "$GATE_HOME" \
    --xdg-config "$GATE_XDG_CONFIG" \
    --xdg-cache "$GATE_XDG_CACHE" \
    --xdg-data "$GATE_XDG_DATA" \
    --xdg-state "$GATE_XDG_STATE" \
    --claude-config "$GATE_CLAUDE_CONFIG" \
    --artifact-dir "$ARTIFACT_DIR" \
    --gate-team "$GATE_TEAM" || status="$?"

  case "$status" in
    0)
      log "P5 cleanup passed"
      return "$EX_GATE_PASS"
      ;;
    1)
      log "P5 cleanup failed"
      return "$EX_GATE_FAIL"
      ;;
    2)
      log "P5 cleanup unknown"
      return "$EX_GATE_UNKNOWN"
      ;;
    *)
      log \
        "cleanup helper returned unsupported exit status: $status"
      return "$EX_INTERNAL"
      ;;
  esac
}

phase_p6_live_pm_after() {
  local control_status
  local compare_status

  log "P6 live PM final negative control"

  control_status=0
  run_live_pm_control "after" || control_status="$?"

  compare_status=0
  python3 "$CLEANUP_HELPER" compare-live \
    --artifact-dir "$ARTIFACT_DIR" \
    --after-status "$control_status" || compare_status="$?"

  case "$control_status" in
    0|1|2)
      ;;
    *)
      log \
        "live PM after-control returned unsupported exit status: $control_status"
      return "$EX_INTERNAL"
      ;;
  esac

  case "$compare_status" in
    0)
      log "P6 live PM negative control passed"
      return "$EX_GATE_PASS"
      ;;
    1)
      log "P6 live PM negative control failed"
      return "$EX_GATE_FAIL"
      ;;
    2)
      log "P6 live PM negative control unknown"
      return "$EX_GATE_UNKNOWN"
      ;;
    *)
      log \
        "live PM comparison returned unsupported exit status: $compare_status"
      return "$EX_INTERNAL"
      ;;
  esac
}

phase_p7_aggregate() {
  local execution_status="${1:-2}"
  local status

  log "P7 aggregate verdict + results.json"

  case "$execution_status" in
    0|1|2)
      ;;
    *)
      execution_status=2
      ;;
  esac

  status=0
  python3 "$CLEANUP_HELPER" evaluate \
    --run-id "$RUN_ID" \
    --artifact-dir "$ARTIFACT_DIR" \
    --requested-check "$CHECK" \
    --execution-status "$execution_status" || status="$?"

  case "$status" in
    0)
      log "P7 aggregate passed"
      return "$EX_GATE_PASS"
      ;;
    1)
      log "P7 aggregate failed"
      return "$EX_GATE_FAIL"
      ;;
    2)
      log "P7 aggregate unknown"
      return "$EX_GATE_UNKNOWN"
      ;;
    *)
      log \
        "aggregate helper returned unsupported exit status: $status"
      return "$EX_INTERNAL"
      ;;
  esac
}

subcommand_run() {
  local status
  local n1_status="$EX_GATE_UNKNOWN"
  local i1_status="$EX_GATE_UNKNOWN"
  local f1_status="$EX_GATE_UNKNOWN"
  local f2_status="$EX_GATE_UNKNOWN"
  local f3_status="$EX_GATE_UNKNOWN"
  local f4_status="$EX_GATE_UNKNOWN"
  local f5_status="$EX_GATE_UNKNOWN"
  local execution_status="$EX_GATE_PASS"
  local cleanup_status="$EX_GATE_UNKNOWN"
  local live_status="$EX_GATE_UNKNOWN"
  local aggregate_status="$EX_GATE_UNKNOWN"
  local internal_status=0
  local continue_checks=1

  #
  # P0-P4 are mandatory prerequisites for every N1/I1/F1-F5 check.
  #
  status=0
  run_p0_through_p4 || status="$?"

  case "$status" in
    0)
      ;;
    1)
      execution_status="$EX_GATE_FAIL"
      continue_checks=0
      ;;
    2)
      execution_status="$EX_GATE_UNKNOWN"
      continue_checks=0
      ;;
    *)
      execution_status="$EX_GATE_UNKNOWN"
      internal_status="$EX_INTERNAL"
      continue_checks=0
      ;;
  esac

  #
  # N1
  #
  if [ "$continue_checks" -eq 1 ]; then
    n1_status=0
    phase_n1 || n1_status="$?"

    case "$n1_status" in
      0)
        if [ "$CHECK" = "N1" ]; then
          continue_checks=0
        fi
        ;;
      1)
        execution_status="$EX_GATE_FAIL"
        continue_checks=0
        ;;
      2)
        execution_status="$EX_GATE_UNKNOWN"
        continue_checks=0
        ;;
      *)
        execution_status="$EX_GATE_UNKNOWN"
        internal_status="$EX_INTERNAL"
        continue_checks=0
        ;;
    esac
  fi

  #
  # I1
  #
  if [ "$continue_checks" -eq 1 ]; then
    i1_status=0
    phase_i1 || i1_status="$?"

    case "$i1_status" in
      0)
        if [ "$CHECK" = "I1" ]; then
          continue_checks=0
        fi
        ;;
      1)
        execution_status="$EX_GATE_FAIL"
        continue_checks=0
        ;;
      2)
        execution_status="$EX_GATE_UNKNOWN"
        continue_checks=0
        ;;
      *)
        execution_status="$EX_GATE_UNKNOWN"
        internal_status="$EX_INTERNAL"
        continue_checks=0
        ;;
    esac
  fi

  #
  # F1
  #
  if [ "$continue_checks" -eq 1 ]; then
    f1_status=0
    phase_f1 || f1_status="$?"

    case "$f1_status" in
      0)
        if [ "$CHECK" = "F1" ]; then
          continue_checks=0
        fi
        ;;
      1)
        execution_status="$EX_GATE_FAIL"
        continue_checks=0
        ;;
      2)
        execution_status="$EX_GATE_UNKNOWN"
        continue_checks=0
        ;;
      *)
        execution_status="$EX_GATE_UNKNOWN"
        internal_status="$EX_INTERNAL"
        continue_checks=0
        ;;
    esac
  fi

  #
  # F2
  #
  if [ "$continue_checks" -eq 1 ]; then
    f2_status=0
    phase_f2 || f2_status="$?"

    case "$f2_status" in
      0)
        if [ "$CHECK" = "F2" ]; then
          continue_checks=0
        fi
        ;;
      1)
        execution_status="$EX_GATE_FAIL"
        continue_checks=0
        ;;
      2)
        execution_status="$EX_GATE_UNKNOWN"
        continue_checks=0
        ;;
      *)
        execution_status="$EX_GATE_UNKNOWN"
        internal_status="$EX_INTERNAL"
        continue_checks=0
        ;;
    esac
  fi

  #
  # F3
  #
  if [ "$continue_checks" -eq 1 ]; then
    f3_status=0
    phase_f3 || f3_status="$?"

    case "$f3_status" in
      0)
        if [ "$CHECK" = "F3" ]; then
          continue_checks=0
        fi
        ;;
      1)
        execution_status="$EX_GATE_FAIL"
        continue_checks=0
        ;;
      2)
        execution_status="$EX_GATE_UNKNOWN"
        continue_checks=0
        ;;
      *)
        execution_status="$EX_GATE_UNKNOWN"
        internal_status="$EX_INTERNAL"
        continue_checks=0
        ;;
    esac
  fi

  #
  # F4
  #
  if [ "$continue_checks" -eq 1 ]; then
    f4_status=0
    phase_f4 || f4_status="$?"

    case "$f4_status" in
      0)
        if [ "$CHECK" = "F4" ]; then
          continue_checks=0
        fi
        ;;
      1)
        execution_status="$EX_GATE_FAIL"
        continue_checks=0
        ;;
      2)
        execution_status="$EX_GATE_UNKNOWN"
        continue_checks=0
        ;;
      *)
        execution_status="$EX_GATE_UNKNOWN"
        internal_status="$EX_INTERNAL"
        continue_checks=0
        ;;
    esac
  fi

  #
  # F5
  #
  if [ "$continue_checks" -eq 1 ]; then
    f5_status=0
    phase_f5 || f5_status="$?"

    case "$f5_status" in
      0)
        if [ "$CHECK" = "F5" ]; then
          continue_checks=0
        fi
        ;;
      1)
        execution_status="$EX_GATE_FAIL"
        continue_checks=0
        ;;
      2)
        execution_status="$EX_GATE_UNKNOWN"
        continue_checks=0
        ;;
      *)
        execution_status="$EX_GATE_UNKNOWN"
        internal_status="$EX_INTERNAL"
        continue_checks=0
        ;;
    esac
  fi

  #
  # Every CHECK value reaches this point: a partial run stops running
  # checks after the requested one, but cleanup, the live PM after-control
  # and the aggregate evaluation are never skipped (runbook §33, §37).
  # P7 is given CHECK, so its exit status covers the requested scope while
  # results.json keeps the full-pilot verdict.
  #
  # Do not continue fault injection after a prerequisite/check has produced
  # fail/unknown. The remaining unexecuted result artifacts stay absent and
  # P7 will preserve them as unknown; an already observed fail still dominates.
  #
  # P5 MUST nevertheless be attempted regardless of the N1/I1/F1-F5 result.
  #
  FINALIZE_PENDING=0
  cleanup_status=0
  phase_p5_cleanup || cleanup_status="$?"

  case "$cleanup_status" in
    0|1|2)
      ;;
    *)
      internal_status="$EX_INTERNAL"
      ;;
  esac

  #
  # P6 also runs regardless of cleanup verdict. Its before-control evidence
  # lives outside RUN_ROOT in ARTIFACT_DIR, so it remains available after P5.
  #
  live_status=0
  phase_p6_live_pm_after || live_status="$?"

  case "$live_status" in
    0|1|2)
      ;;
    *)
      internal_status="$EX_INTERNAL"
      ;;
  esac

  #
  # P7 is authoritative for the semantic gate verdict. execution_status is
  # only the N1/I1/F1-F5 execution aggregate; cleanup and live PM negative
  # control are read independently from their artifacts by the evaluator.
  #
  aggregate_status=0
  phase_p7_aggregate "$execution_status" || aggregate_status="$?"

  case "$aggregate_status" in
    0|1|2)
      ;;
    *)
      internal_status="$EX_INTERNAL"
      ;;
  esac

  #
  # A harness-internal failure remains process exit 70 even if P7 managed to
  # emit a conservative results.json. For ordinary gate fail/unknown/pass,
  # P7/results.json is authoritative.
  #
  if [ "$internal_status" -ne 0 ]; then
    return "$EX_INTERNAL"
  fi

  return "$aggregate_status"
}
subcommand_evaluate() {
  local execution_status="$EX_GATE_UNKNOWN"
  local recorded_execution_status=""
  local status

  if [ -f "$ARTIFACT_DIR/results.json" ]; then
    status=0
    recorded_execution_status="$(
      python3 - "$ARTIFACT_DIR/results.json" <<'PY'
import json
import sys

path = sys.argv[1]

try:
    with open(path, "r", encoding="utf-8") as fh:
        value = json.load(fh)
except Exception:
    raise SystemExit(2)

status = value.get("executionStatus")

mapping = {
    "pass": "0",
    "fail": "1",
    "unknown": "2",
}

result = mapping.get(status)

if result is None:
    raise SystemExit(2)

print(result)
PY
    )" || status="$?"

    if [ "$status" -eq 0 ]; then
      case "$recorded_execution_status" in
        0|1|2)
          execution_status="$recorded_execution_status"
          ;;
        *)
          execution_status="$EX_GATE_UNKNOWN"
          ;;
      esac
    fi
  fi

  status=0
  phase_p7_aggregate "$execution_status" || status="$?"

  return "$status"
}
subcommand_cleanup() {
  local status

  status=0
  phase_p5_cleanup || status="$?"

  return "$status"
}

# Set once run/preflight has created a disposable run root and cleared
# when the normal path takes over P5. If the process leaves before that
# (internal_error's exit 70, an unexpected errexit, a signal), the EXIT trap
# still attempts cleanup, the live PM after-control and the aggregate
# (runbook §37: "abort後もcleanupとlive PM after-controlは行う").
FINALIZE_PENDING=0

finalize_after_abort() {
  local exit_status="$?"

  if [ "$FINALIZE_PENDING" -eq 1 ]; then
    FINALIZE_PENDING=0

    log "aborted with status=$exit_status; attempting P5/P6/P7 before exit"

    phase_p5_cleanup || true

    if [ "$SUBCOMMAND" = "run" ]; then
      phase_p6_live_pm_after || true
      phase_p7_aggregate "$EX_GATE_UNKNOWN" || true
    fi
  fi

  exit "$exit_status"
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
  require_command node
  require_command git
  require_command mktemp
  require_command ps

  validate_static_inputs
  scrub_github_credentials
  scrub_agmsg_pm_environment
  identify_native_claude

  case "$SUBCOMMAND" in
    cleanup|evaluate)
      load_existing_run
      ;;
    *)
      trap finalize_after_abort EXIT
      bootstrap_run
      FINALIZE_PENDING=1
      ;;
  esac

  trap 'on_signal 129' HUP
  trap 'on_signal 130' INT
  trap 'on_signal 143' TERM

  case "$SUBCOMMAND" in
    preflight)
      status=0
      subcommand_preflight || status="$?"
      ;;

    run)
      status=0
      subcommand_run || status="$?"
      ;;

    evaluate)
      status=0
      subcommand_evaluate || status="$?"
      ;;

    cleanup)
      status=0
      subcommand_cleanup || status="$?"
      ;;

    *)
      internal_error \
        "unreachable subcommand: $SUBCOMMAND"
      ;;
  esac

  # The subcommand returned: it owned P5-P7 on this path, so there is no
  # abort left for the EXIT trap to finish.
  FINALIZE_PENDING=0

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

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
