#!/usr/bin/env bash
set -euo pipefail

# Usage: inbox.sh <team> <agent_id> [--quiet]
# Shows unread messages and acknowledges each only after stdout handoff.
# --quiet: only output if there are unread messages (for hooks)

TEAM="${1:?Usage: inbox.sh <team> <agent_id> [--quiet]}"
AGENT="${2:?Missing agent_id}"
QUIET=false
if [ "${3:-}" = "--quiet" ]; then
  QUIET=true
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/lib/storage.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/claims.sh"
DB="$(agmsg_db_path)"

if [ ! -f "$DB" ]; then
  if [ "$QUIET" = true ]; then exit 0; fi
  echo "No messages (DB not initialized)"
  exit 0
fi

_agmsg_sqlesc() { printf %s "$1" | sed "s/'/''/g"; }
TEAM_SQL="$(_agmsg_sqlesc "$TEAM")"
AGENT_SQL="$(_agmsg_sqlesc "$AGENT")"

# Get unread candidates. Each is claimed before it enters the output buffer, so
# another receiver cannot acknowledge it between selection and host handoff.
# Escape newlines/tabs in body to keep one record per line.
UNREAD=$(agmsg_sqlite "$DB" "
  SELECT id || char(31) || from_agent || char(31) || replace(replace(body, char(10), '\n'), char(9), '\t') || char(31) || created_at
  FROM messages WHERE team='$TEAM_SQL' AND to_agent='$AGENT_SQL' AND read_at IS NULL
  ORDER BY created_at ASC;
")

# Keep every successfully claimed id until the process exits. The EXIT trap
# releases leases when output fails, while a successful ACK removes each lease
# before the trap runs.
OWNER="inbox:$$"
CLAIM_IDS=()
release_claims() {
  local id
  [ "${#CLAIM_IDS[@]}" -gt 0 ] || return 0
  for id in "${CLAIM_IDS[@]}"; do
    agmsg_release_claim "$id" "$OWNER" >/dev/null 2>&1 || true
  done
}
trap release_claims EXIT

COUNT=0
OUTPUT=""
while IFS=$'\x1f' read -r id from body ts; do
  case "$id" in
    ''|*[!0-9]*) continue ;; # defensive: never claim an untrusted id
  esac
  if agmsg_claim_id "$id" "$OWNER" 30; then
    CLAIM_IDS+=("$id")
    COUNT=$((COUNT + 1))
    OUTPUT+="  [$ts] $from: $body"$'\n'
  fi
done <<< "$UNREAD"

if [ "$COUNT" -eq 0 ]; then
  if [ "$QUIET" = true ]; then exit 0; fi
  echo "No new messages."
  exit 0
fi

OUTPUT="$COUNT new message(s):"$'\n\n'"$OUTPUT"$'\n'
if ! printf '%s' "$OUTPUT"; then
  release_claims
  trap - EXIT
  printf '%s\n' "agmsg inbox: stdout handoff failed; released claimed messages." >&2 || true
  exit 1
fi

# Test seam: a two-file barrier that lets the race regression test land a
# message deterministically between stdout handoff and acknowledgement. No-op
# unless set.
if [ -n "${AGMSG_TEST_MARK_BARRIER:-}" ]; then
  : > "$AGMSG_TEST_MARK_BARRIER.reached"
  _agmsg_barrier_waited=0
  while [ ! -e "$AGMSG_TEST_MARK_BARRIER.release" ]; do
    sleep 0.05
    _agmsg_barrier_waited=$((_agmsg_barrier_waited + 1))
    [ "$_agmsg_barrier_waited" -ge 200 ] && break # 10s safety cap
  done
fi

# ACK is intentionally after the successful stdout write. An ACK failure does
# not release the claim: the host already received the message, so immediate
# release would create an avoidable duplicate. Its short lease makes it
# recoverable if the receipt cannot be persisted.
for id in "${CLAIM_IDS[@]}"; do
  if ! agmsg_ack_claim "$id" "$OWNER" inbox_stdout; then
    printf 'agmsg inbox: could not acknowledge message %s; it will reappear after its lease expires.\n' "$id" >&2
  fi
done
