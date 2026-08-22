#!/usr/bin/env bash
set -euo pipefail

# List (team, agent) pairs registered for a given (project_path, agent_type).
#
# Usage: identities.sh <project_path> <agent_type> [--name <name> --all-projects]
#
# Output: one "<team>\t<agent>" line per registered pair, tab-separated.
# Empty output (and exit 0) when no pair matches. Pairs are deduplicated.
#
# Used by:
#   - whoami.sh        — exact-match enumeration for identity resolution
#   - watch.sh         — subscription set for the monitor delivery mode
#   - check-inbox.sh   — turn-mode fallback enumeration

PROJECT_PATH="${1:?Usage: identities.sh <project_path> <agent_type>}"
AGENT_TYPE="${2:?Missing agent_type}"
shift 2

NAME_FILTER=""
ALL_PROJECTS=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --name)
      [ "$#" -ge 2 ] || { echo "identities.sh: --name requires a non-empty name" >&2; exit 2; }
      [ -n "$2" ] || { echo "identities.sh: --name requires a non-empty name" >&2; exit 2; }
      NAME_FILTER="$2"
      shift 2
      ;;
    --all-projects)
      ALL_PROJECTS=1
      shift
      ;;
    *)
      echo "identities.sh: unknown option '$1'" >&2
      exit 2
      ;;
  esac
done

if [ "$ALL_PROJECTS" -eq 1 ] && [ -z "$NAME_FILTER" ]; then
  echo "identities.sh: --all-projects requires --name" >&2
  exit 2
fi
if [ -n "$NAME_FILTER" ] && [ "$ALL_PROJECTS" -ne 1 ]; then
  echo "identities.sh: --name requires --all-projects" >&2
  exit 2
fi

AGENT_TYPE_SQL=$(printf '%s' "$AGENT_TYPE" | sed "s/'/''/g")
NAME_FILTER_SQL=$(printf '%s' "$NAME_FILTER" | sed "s/'/''/g")

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"  # resolve-project.sh requires SKILL_DIR
TEAMS_DIR="$SCRIPT_DIR/../teams"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/resolve-project.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/storage.sh"
PROJECT_SQL_IN=$(agmsg_project_sql_in_list "$PROJECT_PATH")

if [ "$ALL_PROJECTS" -eq 1 ]; then
  REGISTRATION_WHERE="json_extract(r.value, '\$.type') = '$AGENT_TYPE_SQL' AND name = '$NAME_FILTER_SQL'"
else
  REGISTRATION_WHERE="json_extract(r.value, '\$.project') IN ($PROJECT_SQL_IN) AND json_extract(r.value, '\$.type') = '$AGENT_TYPE_SQL'"
fi

[ -d "$TEAMS_DIR" ] || exit 0

for config_file in "$TEAMS_DIR"/*/config.json; do
  [ -f "$config_file" ] || continue
  cfg_sql=$(agmsg_sql_readfile_path "$config_file")
  TEAM_NAME=$(agmsg_sqlite_mem "
    WITH raw(json) AS (SELECT CAST(readfile('$cfg_sql') AS TEXT)),
    cfg(json) AS (SELECT CASE WHEN json_valid(json) THEN json END FROM raw)
    SELECT json_extract(json, '\$.name') FROM cfg;
  ")
  [ -z "$TEAM_NAME" ] && continue
  [ "$TEAM_NAME" = "null" ] && continue
  TEAM_SQL=$(printf '%s' "$TEAM_NAME" | sed "s/'/''/g")

  sqlite3 -separator $'\t' :memory: "
    WITH raw(json) AS (SELECT CAST(readfile('$cfg_sql') AS TEXT)),
    cfg(json) AS (SELECT CASE WHEN json_valid(json) THEN json END FROM raw),
    agents AS (
      SELECT
        key AS name,
        CASE
          WHEN json_type(json_extract(value, '\$.registrations')) = 'array' THEN json_extract(value, '\$.registrations')
          ELSE json_array(json_object('type', json_extract(value, '\$.type'), 'project', json_extract(value, '\$.project')))
        END AS registrations
      FROM cfg, json_each(json_extract(cfg.json, '\$.agents'))
    )
    SELECT DISTINCT '$TEAM_SQL' AS team, name
    FROM agents, json_each(agents.registrations) AS r
      WHERE $REGISTRATION_WHERE
      ORDER BY team, name;
  " | tr -d '\r'
done | LC_ALL=C sort -u
