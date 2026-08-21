#!/usr/bin/env bash
set -euo pipefail

# Usage: team-work.sh <command> <team> <contract-pack.json> [command arguments]
#
# Validates a work-state contract pack against the selected team's versioned
# roster JSON contract. Mutating commands use the local SQLite store; legacy
# audit commands read GitHub and local state without changing either one.
# Phase 1A g4-audit is deliberately GitHub-only and does not initialize/open
# the local SQLite store.

COMMAND="${1:-}"
TEAM="${2:-}"
PACK="${3:-}"

case "$COMMAND" in
  validate|self-check)
    if [ "$#" -ne 3 ]; then
      echo "Usage: team-work.sh <validate|self-check> <team> <contract-pack.json>" >&2
      exit 1
    fi
    ;;
  observe|queue|audit)
    if [ "$#" -ne 3 ]; then
      echo "Usage: team-work.sh <observe|queue|audit> <team> <contract-pack.json>" >&2
      exit 1
    fi
    ;;
  g4-audit)
    if [ "$#" -ne 3 ]; then
      echo "Usage: team-work.sh g4-audit <team> <g4-state-pack.json>" >&2
      exit 1
    fi
    ;;
  reconcile)
    if [ "$#" -ne 3 ] && [ "$#" -ne 4 ]; then
      echo "Usage: team-work.sh reconcile <team> <contract-pack.json> [heartbeat-path]" >&2
      exit 1
    fi
    ;;
  watchdog)
    if [ "$#" -ne 5 ] && [ "$#" -ne 6 ]; then
      echo "Usage: team-work.sh watchdog <team> <contract-pack.json> <heartbeat-path> [stale-seconds]" >&2
      exit 1
    fi
    ;;
  dispatch)
    if [ "$#" -ne 5 ] && [ "$#" -ne 6 ]; then
      echo "Usage: team-work.sh dispatch <team> <contract-pack.json> <work-item-id> <manager-seat> [ack-ttl-seconds]" >&2
      exit 1
    fi
    ;;
  dispatch-ack)
    if [ "$#" -ne 6 ] && [ "$#" -ne 7 ]; then
      echo "Usage: team-work.sh dispatch-ack <team> <contract-pack.json> <work-item-id> <owner-seat> <lease-epoch> [evidence]" >&2
      exit 1
    fi
    ;;
  dispatch-abandon)
    if [ "$#" -ne 7 ]; then
      echo "Usage: team-work.sh dispatch-abandon <team> <contract-pack.json> <work-item-id> <manager-seat> <lease-epoch> <evidence>" >&2
      exit 1
    fi
    ;;
  claim|renew)
    if [ "$#" -ne 5 ] && [ "$#" -ne 6 ]; then
      echo "Usage: team-work.sh $COMMAND <team> <contract-pack.json> <work-item-id> <actor-seat> [ttl-seconds]" >&2
      exit 1
    fi
    ;;
  ack)
    if [ "$#" -ne 5 ] && [ "$#" -ne 6 ]; then
      echo "Usage: team-work.sh ack <team> <contract-pack.json> <work-item-id> <actor-seat> [evidence]" >&2
      exit 1
    fi
    ;;
  release)
    if [ "$#" -ne 5 ]; then
      echo "Usage: team-work.sh release <team> <contract-pack.json> <work-item-id> <actor-seat>" >&2
      exit 1
    fi
    ;;
  set-state)
    if [ "$#" -ne 6 ]; then
      echo "Usage: team-work.sh set-state <team> <contract-pack.json> <work-item-id> <actor-seat> <state>" >&2
      exit 1
    fi
    ;;
  link-pr)
    if [ "$#" -ne 8 ]; then
      echo "Usage: team-work.sh link-pr <team> <contract-pack.json> <work-item-id> <actor-seat> <repository> <number> <contributes|closes>" >&2
      exit 1
    fi
    ;;
  writeback)
    if [ "$#" -ne 6 ]; then
      echo "Usage: team-work.sh writeback <team> <contract-pack.json> <work-item-id> <actor-seat> <evidence>" >&2
      exit 1
    fi
    ;;
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

case "$COMMAND" in
  validate|self-check)
    printf '%s' "$ROSTER_JSON" | node "$SCRIPT_DIR/lib/team-work.js" "$@"
    ;;
  g4-audit)
    # Phase 1A is intentionally independent of the team-work SQLite store.
    # Do not initialize or open a database on this path.
    printf '%s' "$ROSTER_JSON" | node "$SCRIPT_DIR/lib/g4-audit.js" "$@"
    ;;
  observe|queue|audit)
    source "$SCRIPT_DIR/lib/storage.sh"
    agmsg_storage_ensure_initialized
    AGMSG_TEAM_WORK_DB="$(agmsg_db_path "$TEAM")"
    export AGMSG_TEAM_WORK_DB
    printf '%s' "$ROSTER_JSON" | node "$SCRIPT_DIR/lib/team-work-audit.js" "$@"
    ;;
  reconcile|watchdog)
    source "$SCRIPT_DIR/lib/storage.sh"
    agmsg_storage_ensure_initialized
    AGMSG_TEAM_WORK_DB="$(agmsg_db_path "$TEAM")"
    AGMSG_TEAM_WORK_SCRIPT_DIR="$SCRIPT_DIR"
    export AGMSG_TEAM_WORK_DB AGMSG_TEAM_WORK_SCRIPT_DIR
    printf '%s' "$ROSTER_JSON" | node "$SCRIPT_DIR/lib/team-work-reconciler.js" "$@"
    ;;
  dispatch|dispatch-ack|dispatch-abandon)
    source "$SCRIPT_DIR/lib/storage.sh"
    agmsg_storage_ensure_initialized
    AGMSG_TEAM_WORK_DB="$(agmsg_db_path "$TEAM")"
    AGMSG_TEAM_WORK_SCRIPT_DIR="$SCRIPT_DIR"
    export AGMSG_TEAM_WORK_DB AGMSG_TEAM_WORK_SCRIPT_DIR
    printf '%s' "$ROSTER_JSON" | node "$SCRIPT_DIR/lib/team-work-reconciler.js" "$@"
    ;;
  *)
    source "$SCRIPT_DIR/lib/storage.sh"
    agmsg_storage_ensure_initialized
    AGMSG_TEAM_WORK_DB="$(agmsg_db_path "$TEAM")"
    export AGMSG_TEAM_WORK_DB
    printf '%s' "$ROSTER_JSON" | node "$SCRIPT_DIR/lib/team-work.js" "$@"
    ;;
esac
