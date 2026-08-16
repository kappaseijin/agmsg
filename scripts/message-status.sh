#!/usr/bin/env bash
set -euo pipefail

# Usage: message-status.sh <team> <agent> [--format human|json]
# Shows receiver handoff state. A handoff acknowledgement is not task completion.

usage() {
  echo "Usage: message-status.sh <team> <agent> [--format human|json]" >&2
}

FORMAT="human"
POSITIONAL=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --format)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      FORMAT="$2"
      shift 2
      ;;
    --format=*)
      FORMAT="${1#--format=}"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --*)
      echo "message-status.sh: unknown option '$1'." >&2
      usage
      exit 2
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

[ "${#POSITIONAL[@]}" -eq 2 ] || { usage; exit 2; }
case "$FORMAT" in human|json) ;; *)
  echo "message-status.sh: --format must be human or json." >&2
  exit 2
  ;;
esac

TEAM="${POSITIONAL[0]}"
AGENT="${POSITIONAL[1]}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/storage.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/validate.sh"

agmsg_validate_team_name "$TEAM" || exit 1
agmsg_storage_load
storage_init "$TEAM" >/dev/null
DB="$(agmsg_db_path "$TEAM")"

_agmsg_sqlesc() { printf '%s' "$1" | sed "s/'/''/g"; }
TEAM_SQL="$(_agmsg_sqlesc "$TEAM")"
AGENT_SQL="$(_agmsg_sqlesc "$AGENT")"

STATE_CTE="
WITH message_states AS (
  SELECT
    CASE
      WHEN r.message_id IS NOT NULL THEN 'handedOff'
      WHEN m.read_at IS NOT NULL THEN 'unknown'
      WHEN c.message_id IS NOT NULL
       AND c.expires_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now') THEN 'claimed'
      ELSE 'queued'
    END AS state,
    m.created_at
  FROM messages AS m
  LEFT JOIN message_claims AS c ON c.message_id=m.id
  LEFT JOIN message_receipts AS r ON r.message_id=m.id
  WHERE m.team='$TEAM_SQL'
    AND m.to_agent='$AGENT_SQL'
)"

if [ "$FORMAT" = "json" ]; then
  agmsg_sqlite "$DB" "$STATE_CTE
SELECT json_object(
  'schemaVersion', 1,
  'team', '$TEAM_SQL',
  'agent', '$AGENT_SQL',
  'queued', COALESCE(SUM(CASE WHEN state='queued' THEN 1 ELSE 0 END), 0),
  'claimed', COALESCE(SUM(CASE WHEN state='claimed' THEN 1 ELSE 0 END), 0),
  'handedOff', COALESCE(SUM(CASE WHEN state='handedOff' THEN 1 ELSE 0 END), 0),
  'unknown', COALESCE(SUM(CASE WHEN state='unknown' THEN 1 ELSE 0 END), 0),
  'oldestQueuedAt', MIN(CASE WHEN state='queued' THEN created_at END),
  'ackSemantics', 'receiver_handoff_not_task_completion'
)
FROM message_states;"
  exit 0
fi

METRICS="$(agmsg_sqlite "$DB" "$STATE_CTE
SELECT COALESCE(SUM(CASE WHEN state='queued' THEN 1 ELSE 0 END), 0)
       || char(31) || COALESCE(SUM(CASE WHEN state='claimed' THEN 1 ELSE 0 END), 0)
       || char(31) || COALESCE(SUM(CASE WHEN state='handedOff' THEN 1 ELSE 0 END), 0)
       || char(31) || COALESCE(SUM(CASE WHEN state='unknown' THEN 1 ELSE 0 END), 0)
       || char(31) || COALESCE(MIN(CASE WHEN state='queued' THEN created_at END), '')
FROM message_states;")"
IFS=$'\x1f' read -r QUEUED CLAIMED HANDED_OFF UNKNOWN OLDEST_QUEUED <<<"$METRICS"

printf 'Message status: team=%s agent=%s\n' "$TEAM" "$AGENT"
printf '  queued: %s\n' "$QUEUED"
printf '  claimed: %s\n' "$CLAIMED"
printf '  handed off: %s\n' "$HANDED_OFF"
printf '  unknown legacy read marker: %s\n' "$UNKNOWN"
if [ -n "$OLDEST_QUEUED" ]; then
  printf '  oldest queued: %s\n' "$OLDEST_QUEUED"
fi
printf '  acknowledgement: receiver handoff only; task completion is not implied.\n'
