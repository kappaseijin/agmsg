#!/usr/bin/env bash
set -euo pipefail

# Usage: send.sh <team> <from> <to> <message> [--force]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/cli-options.sh"

agmsg_parse_cli_options "send.sh" 1 0 "$@" || {
  parse_status=$?
  exit "$parse_status"
}
if [ "${#AGMSG_POSITIONAL_ARGS[@]}" -ne 4 ]; then
  echo "Usage: send.sh <team> <from> <to> <message> [--force]" >&2
  echo "send.sh: expected 4 positional arguments, got ${#AGMSG_POSITIONAL_ARGS[@]}." >&2
  exit 2
fi

TEAM="${AGMSG_POSITIONAL_ARGS[0]}"
FROM="${AGMSG_POSITIONAL_ARGS[1]}"
TO="${AGMSG_POSITIONAL_ARGS[2]}"
BODY="${AGMSG_POSITIONAL_ARGS[3]}"
FORCE="$AGMSG_OPTION_FORCE"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/validate.sh"

# Validate every positional input before loading storage, resolving a DB path,
# initializing a DB, or consulting the roster. --force bypasses roster
# membership only; malformed UTF-8 is never accepted (#146).
agmsg_validate_utf8 "team" "$TEAM" || exit 1
agmsg_validate_utf8 "from agent" "$FROM" || exit 1
agmsg_validate_utf8 "to agent" "$TO" || exit 1
agmsg_validate_utf8 "message body" "$BODY" || exit 1

# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/storage.sh"

# #414: TEAM becomes a path segment (teams/$TEAM/config.json) below whether or
# not --force is given, so validate it unconditionally, before any config-path
# resolution or DB init. --force bypasses roster *membership* only — it must
# never bypass team-name path safety.
agmsg_validate_team_name "$TEAM" || exit 1

agmsg_storage_load
DB="$(agmsg_db_path "$TEAM")"

# Keep the full-schema bootstrap (registry + storage tables) for a first-ever
# command; the message write itself goes through the storage facade below.
[ -f "$DB" ] || bash "$SCRIPT_DIR/internal/init-db.sh" >/dev/null

# #355: reject a from/to that isn't registered in <team> — an unnoticed typo
# (e.g. a stray send to "dummy") used to insert successfully with exit 0,
# landing an undeliverable message and polluting history. Validation lives
# here (the front door), not in storage.sh, so other entry points (api.sh)
# can keep their own policy. --force bypasses this for intentional
# pre-registration sends (e.g. notifying a role before its own join.sh runs).
if [ "$FORCE" -ne 1 ]; then
  TEAM_CONFIG="$SCRIPT_DIR/../teams/$TEAM/config.json"

  _agmsg_roster_check() {
    local role="$1" name="$2"
    if [ ! -f "$TEAM_CONFIG" ]; then
      echo "Error: team '$TEAM' has no registered agents — cannot send as $role '$name' (use --force to bypass)." >&2
      return 1
    fi
    local cfg_sql name_sql found roster q="'"
    cfg_sql=$(agmsg_sql_readfile_path "$TEAM_CONFIG")
    name_sql=${name//$q/$q$q}
    found=$(agmsg_sqlite_mem "
      WITH raw(json) AS (SELECT CAST(readfile('$cfg_sql') AS TEXT)),
      cfg(json) AS (SELECT CASE WHEN json_valid(json) THEN json END FROM raw)
      SELECT value
      FROM cfg, json_each(json_extract(cfg.json, '\$.agents'))
      WHERE key = '$name_sql';
    ")
    if [ -z "$found" ]; then
      roster=$(agmsg_sqlite_mem "
        WITH raw(json) AS (SELECT CAST(readfile('$cfg_sql') AS TEXT)),
        cfg(json) AS (SELECT CASE WHEN json_valid(json) THEN json END FROM raw)
        SELECT group_concat(key, ', ')
        FROM cfg, json_each(json_extract(cfg.json, '\$.agents'));
      ")
      echo "Error: $role agent '$name' is not registered in team '$TEAM' (registered: ${roster:-none}). Use --force to bypass." >&2
      return 1
    fi
    return 0
  }

  _agmsg_roster_check "from" "$FROM" || exit 1
  _agmsg_roster_check "to" "$TO" || exit 1
fi

# Write through the storage axis (§2.1 storage_send) — the active driver now owns
# the message log (an append-only message_sent event), not a direct INSERT.
# storage_send re-inits its schema idempotently before writing, which subsumes the
# #114 concurrent first-write race the old path retried around (a process seeing
# the DB file before the table exists just creates it).
MESSAGE_ID="$(storage_send "$TEAM" "$FROM" "$TO" "$BODY")"

case "$MESSAGE_ID" in
  '')
    echo "Error: queue insert did not return a message id." >&2
    exit 1
    ;;
esac

printf 'Queued message #%s to %s in team %s; delivery not yet acknowledged.\n' "$MESSAGE_ID" "$TO" "$TEAM"
printf 'Check delivery with: message-status.sh %s %s --id %s\n' "$TEAM" "$TO" "$MESSAGE_ID"
