#!/usr/bin/env bash
set -euo pipefail

# Usage: team.sh <team> [--format json]
# Shows team members.

if [ "$#" -lt 1 ]; then
  echo "Usage: team.sh <team> [--format json]" >&2
  exit 1
fi

TEAM="$1"
shift
FORMAT="human"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --format)
      [ "$#" -ge 2 ] || { echo "Error: --format requires a value" >&2; exit 1; }
      FORMAT="$2"
      shift
      ;;
    *)
      echo "Error: unknown team option: $1" >&2
      exit 1
      ;;
  esac
  shift
done
case "$FORMAT" in
  human|json) ;;
  *)
    echo "Error: unsupported format: $FORMAT" >&2
    exit 1
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Reject team names that would escape teams/ as a path segment (#140).
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/validate.sh"
agmsg_validate_team_name "$TEAM" || exit 1

CONFIG="$SCRIPT_DIR/../teams/$TEAM/config.json"

if [ ! -f "$CONFIG" ]; then
  echo "Team not found: $TEAM"
  exit 1
fi

if [ "$FORMAT" = "json" ]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/lib/storage.sh"
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/lib/roster-contract.sh"
  agmsg_roster_contract_team_json "$CONFIG" "$TEAM"
  exit $?
fi

echo "Team: $TEAM"
echo ""

COUNT=0
# CONFIG_ESCAPED is spliced as a genuine SQL string literal below, NOT bound
# via `.param set`: the sqlite3 shell's dot-command tokenizer does not
# honour SQL '' escaping (unlike a real SQL statement's string literals), so
# `.param set :json '...'` silently mis-parses as soon as the config
# contains any single quote — e.g. an agent name like "al'ice" — and prints
# `.parameter`'s own usage help as if it were query output, with exit 0
# (#87 cluster; see resolve-project.sh's `resolve_team` for the same
# caveat).
CONFIG_ESCAPED=$(sed "s/'/''/g" "$CONFIG")
while IFS='	' read -r name types project registrations; do
  if [ "${registrations:-0}" -gt 1 ]; then
    echo "  $name ($types) — $project (+$((registrations - 1)) more)"
  else
    echo "  $name ($types) — $project"
  fi
  COUNT=$((COUNT + 1))
# tr -d '\r': sqlite3.exe on Windows emits CRLF rows; the trailing CR would make
# the `registrations` field "N\r" and trip the integer test in the loop (#130).
done < <(sqlite3 -separator '	' :memory: \
  "WITH agents AS (
     SELECT
       key AS name,
       CASE
         WHEN json_type(json_extract(value, '\$.registrations')) = 'array' THEN json_extract(value, '\$.registrations')
         ELSE json_array(json_object('type', json_extract(value, '\$.type'), 'project', json_extract(value, '\$.project')))
       END AS registrations
     FROM json_each(json_extract('$CONFIG_ESCAPED', '\$.agents'))
   )
   SELECT
     name,
     group_concat(DISTINCT json_extract(r.value, '\$.type')),
     COALESCE((
       SELECT json_extract(r2.value, '\$.project')
       FROM json_each(agents.registrations) AS r2
       ORDER BY CAST(r2.key AS INTEGER) DESC
       LIMIT 1
     ), '?'),
     json_array_length(registrations)
   FROM agents, json_each(agents.registrations) AS r
   GROUP BY name, registrations;" | tr -d '\r')

echo ""
echo "$COUNT member(s)"
