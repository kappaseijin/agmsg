#!/usr/bin/env bash
set -euo pipefail

# Usage: claim.sh next <team> <agent> <owner> [ttl_seconds]
#        claim.sh claim <message_id> <owner> [ttl_seconds]
#        claim.sh release <message_id> <owner>
#        claim.sh ack <message_id> <owner> [evidence]

ACTION="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/claims.sh"

case "$ACTION" in
  next)
    TEAM="${2:?Usage: claim.sh next <team> <agent> <owner> [ttl_seconds]}"
    AGENT="${3:?Missing agent}"
    OWNER="${4:?Missing owner}"
    TTL="${5:-30}"
    [ "$#" -le 5 ] || { echo "Usage: claim.sh next <team> <agent> <owner> [ttl_seconds]" >&2; exit 2; }
    agmsg_claim_next "$TEAM" "$AGENT" "$OWNER" "$TTL"
    ;;
  claim)
    MESSAGE_ID="${2:?Usage: claim.sh claim <message_id> <owner> [ttl_seconds]}"
    OWNER="${3:?Missing owner}"
    TTL="${4:-30}"
    [ "$#" -le 4 ] || { echo "Usage: claim.sh claim <message_id> <owner> [ttl_seconds]" >&2; exit 2; }
    agmsg_claim_id "$MESSAGE_ID" "$OWNER" "$TTL"
    ;;
  release)
    MESSAGE_ID="${2:?Usage: claim.sh release <message_id> <owner>}"
    OWNER="${3:?Missing owner}"
    [ "$#" -eq 3 ] || { echo "Usage: claim.sh release <message_id> <owner>" >&2; exit 2; }
    agmsg_release_claim "$MESSAGE_ID" "$OWNER"
    ;;
  ack)
    MESSAGE_ID="${2:?Usage: claim.sh ack <message_id> <owner> [evidence]}"
    OWNER="${3:?Missing owner}"
    EVIDENCE="${4:-host_handoff}"
    [ "$#" -le 4 ] || { echo "Usage: claim.sh ack <message_id> <owner> [evidence]" >&2; exit 2; }
    agmsg_ack_claim "$MESSAGE_ID" "$OWNER" "$EVIDENCE"
    ;;
  *)
    echo "Usage: claim.sh next <team> <agent> <owner> [ttl_seconds]" >&2
    echo "       claim.sh claim <message_id> <owner> [ttl_seconds]" >&2
    echo "       claim.sh release <message_id> <owner>" >&2
    echo "       claim.sh ack <message_id> <owner> [evidence]" >&2
    exit 2
    ;;
esac
