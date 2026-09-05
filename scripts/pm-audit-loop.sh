#!/usr/bin/env bash
set -euo pipefail

# Long-lived independent audit job. The caller owns process supervision and
# notification; this loop only repeats the bounded audit and never mutates the
# PM session or attempts rollback.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
interval="${AGMSG_PM_AUDIT_INTERVAL_S:-60}"
cycles="${AGMSG_PM_AUDIT_CYCLES:-0}"
case "$interval" in ''|*[!0-9]*) echo 'pm-audit-loop: interval must be seconds' >&2; exit 2 ;; esac
case "$cycles" in ''|*[!0-9]*) echo 'pm-audit-loop: cycles must be a non-negative integer' >&2; exit 2 ;; esac

count=0
last_status=0
while :; do
  set +e
  "$SCRIPT_DIR/pm-audit.sh" --once
  last_status="$?"
  set -e
  count=$((count + 1))
  [ "$cycles" -gt 0 ] && [ "$count" -ge "$cycles" ] && exit "$last_status"
  sleep "$interval"
done
