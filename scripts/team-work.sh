#!/usr/bin/env bash
set -euo pipefail

# Usage: team-work.sh <validate|self-check> <team> <contract-pack.json>
#
# Validates a read-only work-state contract pack against the selected team's
# versioned roster JSON contract. State mutation, GitHub queries, and message
# delivery intentionally belong to later team-work slices.

COMMAND="${1:-}"
TEAM="${2:-}"
PACK="${3:-}"

if [ "$#" -ne 3 ]; then
  echo "Usage: team-work.sh <validate|self-check> <team> <contract-pack.json>" >&2
  exit 1
fi

case "$COMMAND" in
  validate|self-check) ;;
  *)
    echo "Error: unknown team-work command: $COMMAND" >&2
    exit 1
    ;;
esac

if [ ! -f "$PACK" ]; then
  echo "Error: contract pack not found: $PACK" >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "Error: team-work requires node on PATH" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROSTER_JSON=""
if ROSTER_JSON="$(bash "$SCRIPT_DIR/team.sh" "$TEAM" --format json)"; then
  :
else
  roster_status=$?
  exit "$roster_status"
fi

printf '%s' "$ROSTER_JSON" | node "$SCRIPT_DIR/lib/team-work.js" "$COMMAND" "$TEAM" "$PACK"
