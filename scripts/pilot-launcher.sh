#!/usr/bin/env bash
set -euo pipefail

# pilot-launcher.sh — G4-A native launcher for the isolated PM pilot.
#
# This is intentionally separate from spawn.sh. It owns only the pilot
# process identity/generation boundary and does not implement the G4-B broker
# adapter or the G4-C collector.
#
# Usage:
#   scripts/pilot-launcher.sh --team <team> --project <path> --fresh
#   scripts/pilot-launcher.sh --team <team> --project <path> --resume
#
# G4-A invariants:
#   - agent/type/policy/provider are fixed by repository code, never callers.
#   - fresh session ids are generated here before the actas claim.
#   - resume session ids come only from pilot-binding.js inspect.
#   - actas owner is always <sessionId>.<launcher-pid>.
#   - pilot-binding.js prepare receives exactly its nine agreed CLI options;
#     sessionId is carried only through AGMSG_PM_PILOT_SESSION_ID.
#   - successful exec preserves this shell's PID, so the claim/binding PID
#     becomes the native Claude process PID.
#   - any failure after claim and before successful exec releases the claim.

# Preserve the lexical invocation path here.
#
# Do not use pwd -P: on macOS, system-standard ancestors such as
# /var -> /private/var and /tmp -> /private/tmp would otherwise rewrite
# SKILL_DIR and therefore the lexical runtime state/binding paths.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEAMS_DIR="$SKILL_DIR/teams"

PILOT_AGENT="agmsg_pm_pilot_claude"
PILOT_TYPE="claude-code"
POLICY_VERSION="pm-pilot-pretool-v1"
PROVIDER_COMMIT="0b2117c5f91f7950cc196e52edb188748adfa50a"

BINDING_HELPER="$SCRIPT_DIR/lib/pilot-binding.js"
SESSION_IDENTITY="$SCRIPT_DIR/session-identity.js"
ACTAS_LOCK_LIB="$SCRIPT_DIR/lib/actas-lock.sh"
GUARD_PATH="$SCRIPT_DIR/pm-pilot-pretool-guard"
BROKER_PATH="$SCRIPT_DIR/p2-consumer-broker.sh"

usage() {
  cat >&2 <<'USAGE'
Usage:
  pilot-launcher.sh --team <team> --project <path> --fresh
  pilot-launcher.sh --team <team> --project <path> --resume
USAGE
}

die() {
  printf 'pilot-launcher: %s\n' "$*" >&2
  exit 1
}

# Drop every inherited AGMSG_PM_* variable (#404). A pilot started from a
# live PM session would otherwise hand the live PM's decision/execution logs
# and bindings to the pilot guard and hooks. Clear by prefix, not by name, so
# a variable added later cannot reopen the same hole. Every value the pilot
# needs is exported again below immediately before exec.
while IFS= read -r inherited_pm_var; do
  unset "$inherited_pm_var"
done < <(compgen -e | grep '^AGMSG_PM_' || true)
unset inherited_pm_var

[ -f "$BINDING_HELPER" ] || die "pilot binding helper unavailable"
[ -f "$SESSION_IDENTITY" ] || die "session identity helper unavailable"
[ -f "$ACTAS_LOCK_LIB" ] || die "actas lock helper unavailable"

command -v node >/dev/null 2>&1 || die "node is required"

CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
[ -n "$CLAUDE_BIN" ] || die "native claude CLI not found in PATH"

# actas-lock.sh requires SKILL_DIR in the caller environment.
export SKILL_DIR

# shellcheck disable=SC1090
. "$ACTAS_LOCK_LIB"

MODE=""
TEAM=""
PROJECT_INPUT=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --fresh)
      [ -z "$MODE" ] ||
        die "exactly one of --fresh or --resume is required"
      MODE="fresh"
      shift
      ;;

    --resume)
      [ -z "$MODE" ] ||
        die "exactly one of --fresh or --resume is required"
      MODE="resume"
      shift
      ;;

    --team)
      [ "$#" -ge 2 ] ||
        die "--team requires a value"
      [ -n "$2" ] ||
        die "--team requires a non-empty value"
      TEAM="$2"
      shift 2
      ;;

    --project)
      [ "$#" -ge 2 ] ||
        die "--project requires a value"
      [ -n "$2" ] ||
        die "--project requires a non-empty value"
      PROJECT_INPUT="$2"
      shift 2
      ;;

    -h|--help)
      usage
      exit 0
      ;;

    *)
      die "unknown argument: $1"
      ;;
  esac
done

[ -n "$MODE" ] ||
  die "exactly one of --fresh or --resume is required"

[ -n "$TEAM" ] ||
  die "--team is required"

[ -n "$PROJECT_INPUT" ] ||
  die "--project is required"

[ -d "$TEAMS_DIR" ] ||
  die "teams directory unavailable"

[ -d "$PROJECT_INPUT" ] ||
  die "project directory unavailable: $PROJECT_INPUT"

# Project identity is deliberately canonical.
#
# Unlike runtime state/binding paths, binding.project is validated by
# pilot-binding.js/session-identity.js as a canonical filesystem path.
PROJECT="$(
  cd "$PROJECT_INPUT" 2>/dev/null &&
    pwd -P
)" || die "project directory unreadable"

[ -n "$PROJECT" ] ||
  die "project directory unreadable"

# Runtime identity state is intentionally separate from ordinary spawn/session
# state. Build each directory one component at a time and reject symlink
# redirection at every component under SKILL_DIR/run.
ensure_real_directory() {
  local directory="$1"

  if [ -L "$directory" ]; then
    die "runtime directory must not be a symlink: $directory"
  fi

  if [ -e "$directory" ]; then
    [ -d "$directory" ] ||
      die "runtime path is not a directory: $directory"
  else
    mkdir "$directory" ||
      die "cannot create runtime directory: $directory"
  fi

  [ ! -L "$directory" ] ||
    die "runtime directory became a symlink: $directory"
}

RUN_ROOT="$SKILL_DIR/run"
PILOT_ROOT="$RUN_ROOT/pilot"

SEAT_KEY="$(
  _actas_lock_encode "$TEAM"
)__$(
  _actas_lock_encode "$PILOT_AGENT"
)"

SEAT_DIR="$PILOT_ROOT/$SEAT_KEY"
BINDINGS_DIR="$SEAT_DIR/bindings"
STATE_FILE="$SEAT_DIR/state.json"

ensure_real_directory "$RUN_ROOT"
ensure_real_directory "$PILOT_ROOT"
ensure_real_directory "$SEAT_DIR"
ensure_real_directory "$BINDINGS_DIR"

# First security preflight:
# the requested team/project must select exactly one pilot registration.
#
# pilot-binding.js repeats this while the claim is held before publication.
# This initial check prevents acquiring a seat for an invalid roster.
roster_preflight() {
  node -e '
    "use strict";

    const contract = require(process.argv[1]);

    contract.validateRosterUnique({
      teamsDir: process.argv[2],
      team: process.argv[3],
      agent: contract.PILOT_AGENT,
      type: contract.PILOT_TYPE,
      project: process.argv[4],
    });
  ' \
    "$BINDING_HELPER" \
    "$TEAMS_DIR" \
    "$TEAM" \
    "$PROJECT"
}

if ! roster_preflight; then
  die "pilot roster identity is not uniquely identifiable"
fi

# Second security preflight:
# resolve the exact profile/guard/broker files and prove that their raw-byte
# digests can be obtained before any actas mutation.
#
# G4-A deliberately does not create guard/broker. Until G4-B supplies them,
# launch fails here.
digest_preflight() {
  node -e '
    "use strict";

    const fs = require("fs");
    const path = require("path");
    const contract = require(process.argv[1]);

    const files = {
      profile: process.argv[2],
      guard: process.argv[3],
      broker: process.argv[4],
    };

    const result = {};

    for (const [name, file] of Object.entries(files)) {
      let stat;

      try {
        stat = fs.lstatSync(file);
      } catch (_) {
        process.exit(1);
      }

      if (
        !stat.isFile() ||
        stat.isSymbolicLink()
      ) {
        process.exit(1);
      }

      if (
        (name === "guard" || name === "broker") &&
        process.platform !== "win32" &&
        !(stat.mode & 0o111)
      ) {
        process.exit(1);
      }

      const canonical =
        fs.realpathSync.native(file);

      /*
       * The file itself must not be a symlink (checked by lstat above).
       * Do not require canonical === path.resolve(file): that would also
       * reject harmless ancestor symlinks such as /var -> /private/var.
       */
      result[`${name}Path`] =
        file;

      result[`${name}Digest`] =
        contract.sha256File(canonical);
    }

    process.stdout.write(
      `${JSON.stringify(result)}\n`,
    );
  ' \
    "$BINDING_HELPER" \
    "$PROJECT/.claude/settings.local.json" \
    "$GUARD_PATH" \
    "$BROKER_PATH"
}

if ! PREFLIGHT_DIGESTS="$(
  digest_preflight
)"; then
  die "pilot profile/guard/broker preflight failed"
fi

[ -n "$PREFLIGHT_DIGESTS" ] ||
  die "pilot digest preflight produced no result"

# The final owner token must be known before actas_lock_claim.
#
# Fresh:
#   generate a UUID locally.
#
# Resume:
#   obtain the authoritative session id only from the latest accepted binding
#   through the read-only inspect command.
case "$MODE" in
  fresh)
    SESSION_ID="$(
      node -e '
        process.stdout.write(
          require("crypto").randomUUID(),
        );
      '
    )" ||
      die "unable to generate fresh session id"
    ;;

  resume)
    if ! SESSION_ID="$(
      node "$BINDING_HELPER" inspect \
        --skill-dir "$SKILL_DIR" \
        --teams-dir "$TEAMS_DIR" \
        --team "$TEAM" \
        --project "$PROJECT" \
        --state-file "$STATE_FILE" \
        --bindings-dir "$BINDINGS_DIR"
    )"; then
      die "resume inspection failed"
    fi
    ;;

  *)
    die "internal mode error"
    ;;
esac

[ -n "$SESSION_ID" ] ||
  die "pilot session id is empty"

# Every pilot session originates from launcher-generated UUIDs.
# A non-UUID on resume therefore indicates state outside the launcher contract.
if ! node -e '
  "use strict";

  const value = process.argv[1];

  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu
      .test(value)
  ) {
    process.exit(1);
  }
' "$SESSION_ID"; then
  die "pilot session id is not a UUID"
fi

PROCESS_PID="$$"
OWNER="${SESSION_ID}.${PROCESS_PID}"
CLAIM_FILE="$(
  actas_lock_path \
    "$TEAM" \
    "$PILOT_AGENT"
)"

CLAIMED=0

release_claim() {
  if [ "$CLAIMED" -eq 1 ]; then
    if actas_lock_release \
      "$TEAM" \
      "$PILOT_AGENT" \
      "$OWNER" \
      >/dev/null 2>&1
    then
      CLAIMED=0
      return 0
    fi

    printf \
      'pilot-launcher: failed to release actas claim owner=%s\n' \
      "$OWNER" \
      >&2

    return 1
  fi

  return 0
}

on_exit() {
  local rc="$?"

  trap - EXIT

  if ! release_claim; then
    [ "$rc" -ne 0 ] ||
      rc=1
  fi

  exit "$rc"
}

trap 'on_exit' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

CLAIM_OUTPUT=""

if CLAIM_OUTPUT="$(
  actas_lock_claim \
    "$TEAM" \
    "$PILOT_AGENT" \
    "$OWNER"
)"; then
  CLAIMED=1
else
  CLAIM_RC="$?"

  case "$CLAIM_RC" in
    1)
      die \
        "pilot seat is held by another live owner: ${CLAIM_OUTPUT:-unknown}"
      ;;

    3)
      die \
        "pilot seat claim delivery gate is unavailable"
      ;;

    *)
      die \
        "pilot seat claim failed (status=$CLAIM_RC${CLAIM_OUTPUT:+, result=$CLAIM_OUTPUT})"
      ;;
  esac
fi

CLAIM_STATE="$(
  actas_lock_state \
    "$TEAM" \
    "$PILOT_AGENT" \
    "$OWNER"
)" ||
  die "pilot seat state became unidentifiable after claim"

[ "$CLAIM_STATE" = "mine" ] ||
  die \
    "pilot seat claim was not retained after acquisition: $CLAIM_STATE"

# pidStart is deliberately obtained after the claim.
#
# The final exec preserves this shell PID, so the same pidStart identifies the
# resulting native Claude process generation.
if ! PID_START="$(
  node \
    "$SESSION_IDENTITY" \
    --process-start \
    "$PROCESS_PID"
)"; then
  die "unable to obtain launcher process-start token"
fi

[ -n "$PID_START" ] ||
  die "launcher process-start token is empty"

# prepare has exactly nine CLI options.
#
# sessionId is intentionally not a tenth argument. It is an internal,
# process-local value supplied via AGMSG_PM_PILOT_SESSION_ID.
if ! PREPARE_JSON="$(
  AGMSG_PM_PILOT_SESSION_ID="$SESSION_ID" \
    node "$BINDING_HELPER" prepare \
      --mode "$MODE" \
      --skill-dir "$SKILL_DIR" \
      --teams-dir "$TEAMS_DIR" \
      --team "$TEAM" \
      --project "$PROJECT" \
      --pid "$PROCESS_PID" \
      --pid-start "$PID_START" \
      --state-file "$STATE_FILE" \
      --bindings-dir "$BINDINGS_DIR"
)"; then
  die "pilot generation prepare failed"
fi

[ -n "$PREPARE_JSON" ] ||
  die "pilot generation prepare returned no result"

json_field() {
  local json="$1"
  local field="$2"

  node -e '
    "use strict";

    let value;

    try {
      value =
        JSON.parse(process.argv[1]);
    } catch (_) {
      process.exit(1);
    }

    const field =
      process.argv[2];

    if (
      !value ||
      typeof value !== "object" ||
      Array.isArray(value) ||
      typeof value[field] !== "string" ||
      value[field].length === 0 ||
      /[\u0000-\u001f\u007f]/u.test(
        value[field],
      )
    ) {
      process.exit(1);
    }

    process.stdout.write(
      value[field],
    );
  ' \
    "$json" \
    "$field"
}

PREP_SESSION_ID="$(
  json_field \
    "$PREPARE_JSON" \
    sessionId
)" ||
  die "prepare result sessionId invalid"

GENERATION="$(
  json_field \
    "$PREPARE_JSON" \
    generation
)" ||
  die "prepare result generation invalid"

PREP_PID="$(
  json_field \
    "$PREPARE_JSON" \
    pid
)" ||
  die "prepare result pid invalid"

PREP_PID_START="$(
  json_field \
    "$PREPARE_JSON" \
    pidStart
)" ||
  die "prepare result pidStart invalid"

BINDING_FILE="$(
  json_field \
    "$PREPARE_JSON" \
    bindingFile
)" ||
  die "prepare result bindingFile invalid"

PREP_STATE_FILE="$(
  json_field \
    "$PREPARE_JSON" \
    stateFile
)" ||
  die "prepare result stateFile invalid"

PROFILE_PATH="$(
  json_field \
    "$PREPARE_JSON" \
    profilePath
)" ||
  die "prepare result profilePath invalid"

PROFILE_DIGEST="$(
  json_field \
    "$PREPARE_JSON" \
    profileDigest
)" ||
  die "prepare result profileDigest invalid"

PREP_GUARD_PATH="$(
  json_field \
    "$PREPARE_JSON" \
    guardPath
)" ||
  die "prepare result guardPath invalid"

GUARD_DIGEST="$(
  json_field \
    "$PREPARE_JSON" \
    guardDigest
)" ||
  die "prepare result guardDigest invalid"

PREP_BROKER_PATH="$(
  json_field \
    "$PREPARE_JSON" \
    brokerPath
)" ||
  die "prepare result brokerPath invalid"

BROKER_DIGEST="$(
  json_field \
    "$PREPARE_JSON" \
    brokerDigest
)" ||
  die "prepare result brokerDigest invalid"

PREP_POLICY_VERSION="$(
  json_field \
    "$PREPARE_JSON" \
    policyVersion
)" ||
  die "prepare result policyVersion invalid"

PREP_PROVIDER_COMMIT="$(
  json_field \
    "$PREPARE_JSON" \
    providerCommit
)" ||
  die "prepare result providerCommit invalid"

PREP_PROJECT="$(
  json_field \
    "$PREPARE_JSON" \
    project
)" ||
  die "prepare result project invalid"

PREP_TEAM="$(
  json_field \
    "$PREPARE_JSON" \
    team
)" ||
  die "prepare result team invalid"

PREP_AGENT="$(
  json_field \
    "$PREPARE_JSON" \
    agent
)" ||
  die "prepare result agent invalid"

PREP_TYPE="$(
  json_field \
    "$PREPARE_JSON" \
    type
)" ||
  die "prepare result type invalid"

[ "$PREP_SESSION_ID" = "$SESSION_ID" ] ||
  die "prepare result session mismatch"

[ "$PREP_PID" = "$PROCESS_PID" ] ||
  die "prepare result pid mismatch"

[ "$PREP_PID_START" = "$PID_START" ] ||
  die "prepare result process-start mismatch"

[ "$PREP_STATE_FILE" = "$STATE_FILE" ] ||
  die "prepare result state path mismatch"

[ "$PREP_PROJECT" = "$PROJECT" ] ||
  die "prepare result project mismatch"

[ "$PREP_TEAM" = "$TEAM" ] ||
  die "prepare result team mismatch"

[ "$PREP_AGENT" = "$PILOT_AGENT" ] ||
  die "prepare result agent mismatch"

[ "$PREP_TYPE" = "$PILOT_TYPE" ] ||
  die "prepare result type mismatch"

[ "$PREP_GUARD_PATH" = "$GUARD_PATH" ] ||
  die "prepare result guard path mismatch"

[ "$PREP_BROKER_PATH" = "$BROKER_PATH" ] ||
  die "prepare result broker path mismatch"

[ "$PREP_POLICY_VERSION" = "$POLICY_VERSION" ] ||
  die "prepare result policy mismatch"

[ "$PREP_PROVIDER_COMMIT" = "$PROVIDER_COMMIT" ] ||
  die "prepare result provider commit mismatch"

case "$GENERATION" in
  ''|*[!0-9]*|0*)
    die "prepare result generation is invalid"
    ;;
esac

# The immutable binding is published at this point.
#
# Before handing the PID to Claude, verify that the seat and all mutable
# contract inputs still match the binding.
#
# On failure the EXIT trap releases the actas claim. The consumed generation
# remains immutable and is never reused.
CLAIM_STATE="$(
  actas_lock_state \
    "$TEAM" \
    "$PILOT_AGENT" \
    "$OWNER"
)" ||
  die "pilot seat state became unidentifiable before exec"

[ "$CLAIM_STATE" = "mine" ] ||
  die \
    "pilot seat ownership changed before exec: $CLAIM_STATE"

if ! roster_preflight; then
  die "pilot roster changed before exec"
fi

current_digest() {
  node -e '
    "use strict";

    const contract =
      require(process.argv[1]);

    process.stdout.write(
      contract.sha256File(
        process.argv[2],
      ),
    );
  ' \
    "$BINDING_HELPER" \
    "$1"
}

[ "$(
  current_digest "$PROFILE_PATH"
)" = "$PROFILE_DIGEST" ] ||
  die "pilot profile changed before exec"

[ "$(
  current_digest "$PREP_GUARD_PATH"
)" = "$GUARD_DIGEST" ] ||
  die "pilot guard changed before exec"

[ "$(
  current_digest "$PREP_BROKER_PATH"
)" = "$BROKER_DIGEST" ] ||
  die "pilot broker changed before exec"

[ -x "$PREP_GUARD_PATH" ] ||
  die "pilot guard is not executable before exec"

[ -x "$PREP_BROKER_PATH" ] ||
  die "pilot broker is not executable before exec"

# The pilot guard's decision log and the PostToolUse execution log live next
# to this generation's binding. Generations are never reused, so both files
# are created exclusively here (noclobber also refuses a pre-planted symlink)
# and the guard requires them to be regular files in the binding directory.
BINDING_DIR="$(dirname "$BINDING_FILE")"
DECISIONS_FILE="$BINDING_DIR/$GENERATION.decisions.jsonl"
EXECUTIONS_FILE="$BINDING_DIR/$GENERATION.executions.jsonl"

for run_log in "$DECISIONS_FILE" "$EXECUTIONS_FILE"; do
  ( set -o noclobber; : > "$run_log" ) 2>/dev/null ||
    die "cannot create pilot run log exclusively: $run_log"

  [ -f "$run_log" ] && [ ! -L "$run_log" ] ||
    die "pilot run log is not a regular file: $run_log"
done

# Environment consumed by session-identity.js, the pilot-only guard, and the
# PostToolUse recorder. Every inherited AGMSG_PM_* was cleared at startup.
# These values are process-local and become Claude's environment through exec.
export AGMSG_PM_PILOT_SESSION_ID="$SESSION_ID"
export AGMSG_PM_BINDING_FILE="$BINDING_FILE"
export AGMSG_PM_TEAM="$TEAM"
export AGMSG_PM_AGENT="$PILOT_AGENT"
export AGMSG_PM_TYPE="$PILOT_TYPE"
export AGMSG_PM_PROCESS_PID="$PROCESS_PID"
export AGMSG_PM_PROCESS_GENERATION="$GENERATION"
export AGMSG_PM_PROCESS_START="$PID_START"
export AGMSG_PM_TEAMS_DIR="$TEAMS_DIR"
export AGMSG_PM_CLAIM_FILE="$CLAIM_FILE"
export AGMSG_PM_GUARD_PATH="$PREP_GUARD_PATH"
export AGMSG_PM_BROKER_PATH="$PREP_BROKER_PATH"
export AGMSG_PM_DECISIONS_FILE="$DECISIONS_FILE"
export AGMSG_PM_EXECUTIONS_FILE="$EXECUTIONS_FILE"

cd "$PROJECT" ||
  die "cannot enter project before exec"

# Do not clear the EXIT trap before exec.
#
# Successful exec replaces this shell and Bash does not run the EXIT trap.
# If exec itself fails, set -e terminates this shell and the EXIT trap releases
# the actas claim.
case "$MODE" in
  fresh)
    exec "$CLAUDE_BIN" \
      --session-id "$SESSION_ID" \
      --settings "$PROFILE_PATH"
    ;;

  resume)
    exec "$CLAUDE_BIN" \
      --resume "$SESSION_ID" \
      --settings "$PROFILE_PATH"
    ;;

  *)
    die "internal mode error before exec"
    ;;
esac