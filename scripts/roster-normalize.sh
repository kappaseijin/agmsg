#!/usr/bin/env bash
set -euo pipefail

# Normalize one legacy team roster to the versioned roster contract.
#
# This command deliberately changes only the root schemaVersion field. It does
# not infer roles or registration kinds, and it does not write the live config
# until the candidate has passed the shared roster contract validator.
#
# Usage: roster-normalize.sh <team> (--check|--apply)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEAMS_DIR="$SKILL_DIR/teams"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/validate.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/storage.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/registry-lock.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/roster-contract.sh"

if [ "$#" -ne 2 ]; then
  printf 'Usage: roster-normalize.sh <team> (--check|--apply)\n' >&2
  exit 2
fi

TEAM="$1"
MODE="$2"

if ! agmsg_validate_team_name "$TEAM"; then
  exit 2
fi

case "$MODE" in
  --check|--apply) ;;
  *)
    printf 'Usage: roster-normalize.sh <team> (--check|--apply)\n' >&2
    exit 2
    ;;
esac

TEAM_DIR="$TEAMS_DIR/$TEAM"
CONFIG="$TEAM_DIR/config.json"

if [ ! -d "$TEAM_DIR" ] || [ ! -f "$CONFIG" ]; then
  printf 'agmsg: roster config not found for team %s\n' "$TEAM" >&2
  exit 1
fi

# The candidate lives in a private sibling directory. The validator reads a
# path, so keeping it beside config.json lets the normal path/SQL checks run
# without changing any shared state.
AGMSG_ROSTER_NORMALIZE_TMP_DIR=""
AGMSG_ROSTER_NORMALIZE_TMP_FILE=""

agmsg_roster_normalize_cleanup() {
  if [ -n "${AGMSG_ROSTER_NORMALIZE_TMP_FILE:-}" ]; then
    rm -f "$AGMSG_ROSTER_NORMALIZE_TMP_FILE" 2>/dev/null || true
  fi
  if [ -n "${AGMSG_ROSTER_NORMALIZE_TMP_DIR:-}" ]; then
    rmdir "$AGMSG_ROSTER_NORMALIZE_TMP_DIR" 2>/dev/null || true
  fi
  AGMSG_ROSTER_NORMALIZE_TMP_FILE=""
  AGMSG_ROSTER_NORMALIZE_TMP_DIR=""
}

agmsg_roster_normalize_make_tmp() {
  local attempts=0 tmp_dir

  while :; do
    tmp_dir="$TEAM_DIR/.roster-normalize.$$.$RANDOM.d"
    if ( umask 077; mkdir "$tmp_dir" ) 2>/dev/null; then
      AGMSG_ROSTER_NORMALIZE_TMP_DIR="$tmp_dir"
      AGMSG_ROSTER_NORMALIZE_TMP_FILE="$tmp_dir/config.json"
      return 0
    fi
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 32 ]; then
      printf 'agmsg: could not create a private roster candidate beside %s\n' "$CONFIG" >&2
      return 1
    fi
  done
}

agmsg_roster_normalize_build_candidate() {
  local config="$1" team="$2"
  local config_sql json_valid schema_type candidate changed
  local status

  AGMSG_ROSTER_NORMALIZE_CANDIDATE=""
  AGMSG_ROSTER_NORMALIZE_CHANGED=""

  if ! agmsg_sql_readfile_ok "$config"; then
    printf 'agmsg: could not read roster config for team %s\n' "$team" >&2
    return 1
  fi

  config_sql="$(agmsg_sql_readfile_path "$config")"
  if ! json_valid="$(agmsg_sqlite_mem "SELECT json_valid(CAST(readfile('$config_sql') AS TEXT));")"; then
    printf 'agmsg: could not inspect roster config for team %s\n' "$team" >&2
    return 1
  fi

  if [ "$json_valid" != "1" ]; then
    # Let the shared validator own the schema diagnostic and exit status.
    if agmsg_roster_contract_team_json "$config" "$team" >/dev/null; then
      printf 'schema error: config is not valid JSON\n' >&2
      return 2
    else
      status=$?
      return "$status"
    fi
  fi

  if ! schema_type="$(agmsg_sqlite_mem "SELECT COALESCE(json_type(CAST(readfile('$config_sql') AS TEXT), '\$.schemaVersion'), 'missing');")"; then
    printf 'agmsg: could not inspect schemaVersion for team %s\n' "$team" >&2
    return 1
  fi

  changed=false
  if [ "$schema_type" = "missing" ]; then
    if ! candidate="$(agmsg_sqlite_mem "SELECT json_set(CAST(readfile('$config_sql') AS TEXT), '\$.schemaVersion', 1);")"; then
      printf 'agmsg: could not build roster candidate for team %s\n' "$team" >&2
      return 1
    fi
    changed=true
  else
    if ! candidate="$(cat "$config")"; then
      printf 'agmsg: could not read roster config for team %s\n' "$team" >&2
      return 1
    fi
  fi

  if ! agmsg_roster_normalize_make_tmp; then
    return 1
  fi
  if ! ( umask 077; printf '%s\n' "$candidate" > "$AGMSG_ROSTER_NORMALIZE_TMP_FILE" ) 2>/dev/null; then
    printf 'agmsg: could not write the roster candidate for team %s\n' "$team" >&2
    agmsg_roster_normalize_cleanup
    return 1
  fi

  if agmsg_roster_contract_team_json "$AGMSG_ROSTER_NORMALIZE_TMP_FILE" "$team" >/dev/null; then
    AGMSG_ROSTER_NORMALIZE_CANDIDATE="$candidate"
    AGMSG_ROSTER_NORMALIZE_CHANGED="$changed"
    return 0
  else
    status=$?
    agmsg_roster_normalize_cleanup
    return "$status"
  fi
}

agmsg_roster_normalize_emit_result() {
  local status="$1" changed="$2" team_sql changed_json
  team_sql="$(agmsg_sqlesc "$TEAM")"
  changed_json=false
  [ "$changed" = true ] && changed_json=true
  agmsg_sqlite_mem "SELECT json_object('schemaVersion', 1, 'team', '$team_sql', 'status', '$status', 'changed', json('$changed_json'));"
}

if [ "$MODE" = "--apply" ]; then
  if ! agmsg_lock_acquire "$TEAM_DIR"; then
    exit 1
  fi
  trap 'agmsg_roster_normalize_cleanup; agmsg_lock_release' EXIT
  trap 'agmsg_roster_normalize_cleanup; agmsg_lock_release; exit 130' INT
  trap 'agmsg_roster_normalize_cleanup; agmsg_lock_release; exit 143' TERM
else
  trap 'agmsg_roster_normalize_cleanup' EXIT
  trap 'agmsg_roster_normalize_cleanup; exit 130' INT
  trap 'agmsg_roster_normalize_cleanup; exit 143' TERM
fi

if agmsg_roster_normalize_build_candidate "$CONFIG" "$TEAM"; then
  :
else
  exit $?
fi

if [ "$MODE" = "--apply" ] && [ "$AGMSG_ROSTER_NORMALIZE_CHANGED" = true ]; then
  if ! agmsg_write_atomic "$CONFIG" "$AGMSG_ROSTER_NORMALIZE_CANDIDATE"; then
    exit 1
  fi
  RESULT_STATUS=applied
else
  RESULT_STATUS=ready
  [ "$AGMSG_ROSTER_NORMALIZE_CHANGED" = false ] && RESULT_STATUS=already_current
fi

RESULT_CHANGED="$AGMSG_ROSTER_NORMALIZE_CHANGED"
agmsg_roster_normalize_cleanup
agmsg_roster_normalize_emit_result "$RESULT_STATUS" "$RESULT_CHANGED"
