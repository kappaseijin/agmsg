#!/usr/bin/env bash

# Read the current herdr pane and classify only its observable TUI state.
# Recovery is deliberately outside this script; agent-hard-reset owns it.
#
# Usage: pane-liveness.sh <workspace_id> <pane_id>

set -euo pipefail

if [ "$#" -ne 2 ]; then
  printf 'usage: %s <workspace_id> <pane_id>\n' "${0##*/}" >&2
  exit 2
fi

workspace="$1"
pane="$2"

pane_read_status=ok
pane_content=''
if ! pane_content="$(herdr pane read "$pane" 2>/dev/null)"; then
  pane_read_status=unreadable
fi

pane_bytes="$(printf '%s' "$pane_content" | wc -c | tr -d ' ')"
pane_tail="$(printf '%s\n' "$pane_content" | tail -8)"

# The tail is the visible pane state. Searching the whole buffer would treat
# crash words quoted in an old conversation as a current crash.
live_markers='Queued follow-up inputs|Ask Codex to do anything|esc to interrupt|\? for shortcuts|Try "'
crash_markers='resume_cwd|requires.*--cd|command not found|Segmentation fault|panicked at'

pane_liveness=unknown
if [ "$pane_read_status" = ok ] && [ -n "$pane_content" ]; then
  if printf '%s\n' "$pane_tail" | grep -qE "$live_markers"; then
    pane_liveness=live
  elif printf '%s\n' "$pane_tail" | grep -qE "$crash_markers"; then
    pane_liveness=crashed
  elif printf '%s\n' "$pane_tail" | tail -1 | grep -qE '[$#%>] *$'; then
    pane_liveness=crashed
  elif printf '%s\n' "$pane_tail" | grep -qE '(^|[[:space:]])›[[:space:]]*$'; then
    pane_liveness=live
  fi
fi

printf 'workspace=%s pane=%s pane_bytes=%s pane_read=%s pane_liveness=%s\n' \
  "$workspace" "$pane" "$pane_bytes" "$pane_read_status" "$pane_liveness"
