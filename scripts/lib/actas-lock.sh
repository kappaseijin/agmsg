#!/usr/bin/env bash
# actas-lock.sh — per-(team, agent) exclusivity locks.
#
# Background: agmsg supports a project being registered with multiple agent
# identities of the same type (claude-code/codex/...). Without ownership
# tracking, every concurrent CC session in that project would subscribe to
# every registered identity's messages — duplicate delivery, confused mark-
# read semantics, and the `actas` "exclusive role" model breaking down.
#
# This file implements a small filesystem-based ownership protocol:
#
#   Lock file: $SKILL_DIR/run/actas.<team>__<agent>.session
#   Content  : one line — the owner session_id.
#
# A session_id is alive iff some $SKILL_DIR/run/cc-instance.<pid> file
# currently contains it AND that PID is alive. The same primitive used by
# session-start.sh's orphan-watcher cleanup. Stale locks (owner is no
# longer alive) are reclaimable.
#
# Atomic claim is implemented via `ln` of a per-call tmp file. POSIX
# guarantees the link target either appears or doesn't, even under
# concurrent claim attempts.
#
# Required caller-set variable:
#   SKILL_DIR — agmsg skill root.

: "${SKILL_DIR:?actas-lock.sh requires SKILL_DIR}"

# Owner tokens are per-process instance ids (see instance-id.sh), not bare
# session_ids — this is what keeps parallel --continue/--resume sessions that
# share a session_id from each appearing to own the other's locks (#93). The
# liveness check (actas_lock_sid_alive) delegates to agmsg_instance_alive.
# shellcheck disable=SC1091
. "$SKILL_DIR/scripts/lib/instance-id.sh"

# The delivery gate is backed by the runtime-lock ABI. Keep the dependency
# local because some callers source actas-lock.sh before storage.sh.
if ! declare -F agmsg_runtime_lock_acquire >/dev/null 2>&1; then
  # shellcheck disable=SC1091
  . "$SKILL_DIR/scripts/lib/storage.sh"
fi

_actas_lock_dir() { printf '%s/run' "$SKILL_DIR"; }

# Encode a team or agent name into a filesystem-safe form. Anything outside
# [A-Za-z0-9._-] is percent-encoded byte-by-byte (UTF-8 safe, reversible).
# An earlier underscore-replacement scheme was lossy: "foo bar" and "foo_bar"
# collided on the same lock file, as did every Japanese team name (every
# non-ASCII byte mapped to "_"). #65 review, finding 2.
_actas_lock_encode() {
  printf '%s' "$1" | LC_ALL=C awk '
    BEGIN { for (n = 0; n < 256; n++) ord[sprintf("%c", n)] = n }
    {
      for (i = 1; i <= length($0); i++) {
        c = substr($0, i, 1)
        if (c ~ /[A-Za-z0-9._\-]/) printf "%s", c
        else printf "%%%02X", ord[c]
      }
    }
  '
}

# Compute the lock file path for (team, agent).
actas_lock_path() {
  local team="$1" agent="$2"
  local t a; t="$(_actas_lock_encode "$team")"; a="$(_actas_lock_encode "$agent")"
  printf '%s/actas.%s__%s.session' "$(_actas_lock_dir)" "$t" "$a"
}

# Readiness sentinel path for (team, agent). watch.sh creates this when an
# exclusive (actas) watcher attaches and removes it on exit, so the file is
# present iff a live watcher is currently receiving for that role. `spawn`
# uses it to block until a freshly launched agent is actually listening,
# instead of racing the agent's first push. Same encoding as the lock path so
# both scripts agree without env plumbing. See #108.
agmsg_ready_path() {
  local team="$1" agent="$2"
  local t a; t="$(_actas_lock_encode "$team")"; a="$(_actas_lock_encode "$agent")"
  printf '%s/ready.%s__%s' "$(_actas_lock_dir)" "$t" "$a"
}

# Placement record path for a spawned (team, agent). `spawn` writes the
# member's tmux target id + project + type here at launch time so that
# `despawn --force` can tear the member down (kill its pane/window, drop its
# registration) even when the member's own watcher is dead and can't respond
# to a ctrl:despawn. Same encoding as the lock path. See #109.
agmsg_spawn_path() {
  local team="$1" agent="$2"
  local t a; t="$(_actas_lock_encode "$team")"; a="$(_actas_lock_encode "$agent")"
  printf '%s/spawn.%s__%s' "$(_actas_lock_dir)" "$t" "$a"
}

# Read the owner session_id of a lock file. Empty if no lock or unreadable.
actas_lock_owner() {
  local lock; lock="$(actas_lock_path "$1" "$2")"
  [ -f "$lock" ] || { printf ''; return 0; }
  head -1 "$lock" 2>/dev/null
}

# Return 0 if the given owner token is alive. The token is a per-process
# instance id (composite "<sid>.<pid>" or bare "<sid>" fallback); liveness is
# delegated to agmsg_instance_alive (composite → kill -0 the embedded pid; bare
# → live cc-instance.<pid> scan, with upgrade compat). Kept as a thin wrapper
# so existing callers (gc_stale, watch.sh subscription, session-start GC) need
# no change. Empty token → not alive.
actas_lock_sid_alive() {
  agmsg_instance_alive "$1"
}

# The delivery critical section is keyed by the exact actas lock path. This
# lets release-all and stale GC protect each file without decoding arbitrary
# team/agent bytes back out of its filename.
actas_lock_gate_resource() {
  printf 'actas-delivery:%s' "$1"
}

_actas_lock_gate_classify_error() {
  local error="$1"
  case "$error" in
    *SQLITE_BUSY*|*SQLITE_LOCKED*|*"database is busy"*|*"database"*"locked"*)
      printf 'transient'
      ;;
    *"no such "*|*"unable to open database"*|*"permission denied"*|*"not authorized"*|\
      *"readonly"*|*"malformed"*|*"syntax error"*|*"constraint failed"*|*"disk I/O error"*|\
      *"not a database"*|*"expected one owned row"*)
      printf 'permanent'
      ;;
    *)
      printf 'unknown'
      ;;
  esac
}

_actas_lock_gate_diagnostic() {
  local operation="$1" resource="$2" observed_owner="$3"
  local classification="$4" sqlite_error="$5" reason="${6:-}"
  [ -n "$observed_owner" ] || observed_owner='<none>'
  [ -n "$sqlite_error" ] || sqlite_error='<not-invoked>'
  printf 'actas gate failure operation=%s resource=%s observed_owner=%s pid=%s classification=%s sqlite_error=%s' \
    "$operation" "$resource" "$observed_owner" "$$" "$classification" "$sqlite_error" >&2
  [ -n "$reason" ] && printf ' reason=%s' "$reason" >&2
  printf '\n' >&2
}

# Run exactly one storage ABI operation with a caller-selected SQLite busy
# timeout. The assignment is exported inside this subshell so init scripts and
# sqlite3 itself account for the same timeout as the outer gate deadline.
_actas_lock_gate_run() {
  local operation="$1" resource="$2" owner_pid="$3" expected_owner="$4"
  local busy_timeout="$5"
  AGMSG_BUSY_TIMEOUT="$busy_timeout"
  export AGMSG_BUSY_TIMEOUT
  case "$operation" in
    try-acquire|acquire)
      if [ -n "$expected_owner" ]; then
        agmsg_runtime_lock_acquire "$resource" "$owner_pid" "$expected_owner"
      else
        agmsg_runtime_lock_acquire "$resource" "$owner_pid"
      fi
      ;;
    release)
      agmsg_runtime_lock_release_owned "$resource" "$owner_pid"
      ;;
    *)
      return 1
      ;;
  esac
}

# Capture one attempt's stderr without dropping it. The caller classifies the
# raw text and reports it on every non-zero path.
_actas_lock_gate_attempt() {
  local operation="$1" resource="$2" owner_pid="$3" expected_owner="$4"
  local busy_timeout="$5" error_file output rc
  ACTAS_LOCK_GATE_LAST_OWNER=''
  ACTAS_LOCK_GATE_LAST_SQLITE_ERROR='<not-invoked>'
  error_file="$(mktemp "${TMPDIR:-/tmp}/agmsg-gate.XXXXXX" 2>/dev/null)" || return 1

  if output="$(_actas_lock_gate_run "$operation" "$resource" "$owner_pid" \
    "$expected_owner" "$busy_timeout" 2>"$error_file")"; then
    rc=0
  else
    rc=$?
  fi
  ACTAS_LOCK_GATE_LAST_OWNER="$output"
  if ! ACTAS_LOCK_GATE_LAST_SQLITE_ERROR="$(cat "$error_file" 2>/dev/null)"; then
    ACTAS_LOCK_GATE_LAST_SQLITE_ERROR='<diagnostic-unreadable>'
  fi
  rm -f "$error_file" 2>/dev/null || :
  return "$rc"
}

# Try to acquire a delivery gate for a watcher. A watcher never waits for a
# holder. SQLite BUSY/LOCKED is retried within four 40 ms attempts (<=200 ms);
# all other errors and every non-owned result fail closed for this poll.
actas_lock_gate_try_acquire() {
  local lock_path="$1" resource owner classification attempts=0 rc
  resource="$(actas_lock_gate_resource "$lock_path")"
  while [ "$attempts" -lt 4 ]; do
    _actas_lock_gate_attempt try-acquire "$resource" "$$" '' 40
    rc=$?
    if [ "$rc" -eq 0 ]; then
      owner="$ACTAS_LOCK_GATE_LAST_OWNER"
      if [ "$owner" = "$$" ]; then
        return 0
      fi
      _actas_lock_gate_diagnostic try-acquire "$resource" "$owner" \
        live-holder '<not-invoked>' 'gate-held-by-another-owner'
      return 1
    fi

    classification="$(_actas_lock_gate_classify_error "$ACTAS_LOCK_GATE_LAST_SQLITE_ERROR")"
    _actas_lock_gate_diagnostic try-acquire "$resource" \
      "$ACTAS_LOCK_GATE_LAST_OWNER" "$classification" \
      "$ACTAS_LOCK_GATE_LAST_SQLITE_ERROR" 'sqlite-attempt-failed'
    [ "$classification" = transient ] || return 1
    attempts=$((attempts + 1))
  done
  return 1
}

# Acquire a delivery gate for an ownership writer. A live holder is waited on
# in 100 ms slices; SQLite BUSY/LOCKED consumes the same 100 ms budget per SQL
# attempt. Fifty attempts therefore bound the total wait to five seconds rather
# than adding a second timeout around sqlite's own busy timeout.
actas_lock_gate_acquire() {
  local lock_path="$1" resource owner next_owner classification
  local attempts=0 rc
  resource="$(actas_lock_gate_resource "$lock_path")"

  while [ "$attempts" -lt 50 ]; do
    attempts=$((attempts + 1))
    _actas_lock_gate_attempt acquire "$resource" "$$" '' 100
    rc=$?
    if [ "$rc" -ne 0 ]; then
      classification="$(_actas_lock_gate_classify_error "$ACTAS_LOCK_GATE_LAST_SQLITE_ERROR")"
      _actas_lock_gate_diagnostic acquire "$resource" \
        "$ACTAS_LOCK_GATE_LAST_OWNER" "$classification" \
        "$ACTAS_LOCK_GATE_LAST_SQLITE_ERROR" 'sqlite-attempt-failed'
      [ "$classification" = transient ] || return 1
      continue
    fi

    owner="$ACTAS_LOCK_GATE_LAST_OWNER"
    case "$owner" in
      "$$") return 0 ;;
      ''|*[!0-9]*)
        _actas_lock_gate_diagnostic acquire "$resource" "$owner" \
          permanent '<not-invoked>' 'invalid-owner-result'
        return 1
        ;;
    esac

    if _agmsg_pid_alive_local "$owner"; then
      if [ "$attempts" -lt 50 ]; then
        sleep 0.1 || return 1
      fi
      continue
    fi

    # A confirmed-dead owner may be replaced only by the storage ABI's
    # expected-owner CAS. This is a second SQL attempt and shares the same
    # bounded budget; it never deletes an unknown/live successor.
    if [ "$attempts" -ge 50 ]; then
      break
    fi
    attempts=$((attempts + 1))
    _actas_lock_gate_attempt acquire "$resource" "$$" "$owner" 100
    rc=$?
    if [ "$rc" -ne 0 ]; then
      classification="$(_actas_lock_gate_classify_error "$ACTAS_LOCK_GATE_LAST_SQLITE_ERROR")"
      _actas_lock_gate_diagnostic acquire "$resource" "$owner" \
        "$classification" "$ACTAS_LOCK_GATE_LAST_SQLITE_ERROR" \
        'sqlite-cas-attempt-failed'
      [ "$classification" = transient ] || return 1
      continue
    fi
    next_owner="$ACTAS_LOCK_GATE_LAST_OWNER"
    [ "$next_owner" = "$$" ] && return 0
    case "$next_owner" in
      ''|*[!0-9]*)
        _actas_lock_gate_diagnostic acquire "$resource" "$next_owner" \
          permanent '<not-invoked>' 'invalid-cas-owner-result'
        return 1
        ;;
    esac
  done

  _actas_lock_gate_diagnostic acquire "$resource" "${owner:-}" \
    transient "${ACTAS_LOCK_GATE_LAST_SQLITE_ERROR:-<not-invoked>}" \
    'retry-deadline-exhausted'
  return 1
}

# Release only the current runtime owner. Transient SQLite failures are retried
# under the same five-second writer budget; a logical owner mismatch or an
# unknown error is reported and returned without touching a successor row.
actas_lock_gate_release() {
  local lock_path="$1" resource classification
  local attempts=0 rc
  resource="$(actas_lock_gate_resource "$lock_path")"
  while [ "$attempts" -lt 50 ]; do
    attempts=$((attempts + 1))
    _actas_lock_gate_attempt release "$resource" "$$" '' 100
    rc=$?
    [ "$rc" -eq 0 ] && return 0
    classification="$(_actas_lock_gate_classify_error "$ACTAS_LOCK_GATE_LAST_SQLITE_ERROR")"
    _actas_lock_gate_diagnostic release "$resource" "$$" "$classification" \
      "$ACTAS_LOCK_GATE_LAST_SQLITE_ERROR" 'sqlite-attempt-failed'
    [ "$classification" = transient ] || return 1
  done
  _actas_lock_gate_diagnostic release "$resource" "$$" transient \
    "${ACTAS_LOCK_GATE_LAST_SQLITE_ERROR:-<not-invoked>}" \
    'retry-deadline-exhausted'
  return 1
}

# Internal: attempt one atomic claim. Echoes "ok" on success, "held:<sid>"
# when another sid currently owns it, or "stale" when the existing lock's
# owner is dead (caller should retry after removing).
_actas_lock_try_claim() {
  local team="$1" agent="$2" sid="$3"
  local lock dir tmp existing
  lock="$(actas_lock_path "$team" "$agent")"
  dir="$(_actas_lock_dir)"
  mkdir -p "$dir" 2>/dev/null || true

  tmp="$(mktemp "$dir/.actas-claim.XXXXXX" 2>/dev/null)" || return 1
  printf '%s\n' "$sid" > "$tmp"

  if ln "$tmp" "$lock" 2>/dev/null; then
    rm -f "$tmp"
    echo "ok"
    return 0
  fi
  rm -f "$tmp"

  existing="$(actas_lock_owner "$team" "$agent")"
  if [ "$existing" = "$sid" ]; then
    echo "ok"
    return 0
  fi
  if [ -z "$existing" ] || ! actas_lock_sid_alive "$existing"; then
    echo "stale"
    return 0
  fi
  printf 'held:%s\n' "$existing"
  return 0
}

# Claim (team, agent) for session_id.
# Exit codes:
#   0  — claimed (now owned by this sid, was already ours, or stale-replaced).
#   1  — held by another live session. Stdout: "held:<other_sid>".
actas_lock_claim() {
  local team="$1" agent="$2" sid="$3"
  local attempts=0 result lock_path reclaim_dir _owner
  lock_path="$(actas_lock_path "$team" "$agent")"
  reclaim_dir="${lock_path}.reclaim.d"
  while [ "$attempts" -lt 3 ]; do
    result="$(_actas_lock_try_claim "$team" "$agent" "$sid")"
    case "$result" in
      ok) return 0 ;;
      stale)
        # Stale removal needs a re-check-under-mutex. A naked rm (or even an
        # atomic mv) reads-then-removes whatever sits at lock_path, with no
        # guard that the contents are still the stale value we decided on
        # earlier. So two concurrent callers can both see stale, A can
        # successfully install a live lock, and B's later rm/mv would delete
        # A's fresh lock — the original blocker from #65 review finding 1,
        # and the same hazard the mv-only variant inherited.
        #
        # Per-lock mutex via `mkdir` (atomic on POSIX). Re-check inside it:
        # only remove the lock if its current owner is still dead. If a peer
        # snuck a live owner in between our stale decision and the mutex,
        # leave it — the next try_claim observes it as held.
        if mkdir "$reclaim_dir" 2>/dev/null; then
          _owner="$(actas_lock_owner "$team" "$agent")"
          if [ -z "$_owner" ] || ! actas_lock_sid_alive "$_owner"; then
            rm -f "$lock_path"
          fi
          rmdir "$reclaim_dir" 2>/dev/null
        fi
        # If mkdir failed, another caller is mid-reclaim. Loop without
        # touching anything; the next try_claim sees whichever state they
        # end up in (live → held, or empty → we ln-claim).
        attempts=$((attempts + 1))
        continue
        ;;
      held:*)
        printf '%s\n' "$result"
        return 1
        ;;
    esac
    return 1
  done
  return 1
}

# Release a lock if we own it. Idempotent.
actas_lock_release() {
  local team="$1" agent="$2" sid="$3"
  local lock owner
  lock="$(actas_lock_path "$team" "$agent")"
  [ -f "$lock" ] || return 0
  owner="$(actas_lock_owner "$team" "$agent")"
  [ "$owner" = "$sid" ] && rm -f "$lock"
  return 0
}

# Release every lock currently owned by the given session_id. Used by
# session-end.sh when a CC session exits.
actas_lock_release_all() {
  local sid="$1"
  local dir; dir="$(_actas_lock_dir)"
  [ -d "$dir" ] || return 0
  local f owner
  for f in "$dir"/actas.*.session; do
    [ -f "$f" ] || continue
    owner="$(head -1 "$f" 2>/dev/null || true)"
    [ "$owner" = "$sid" ] && rm -f "$f"
  done
  return 0
}

# Garbage-collect locks whose owner session_id is no longer alive.
# Returns the number of locks reclaimed on stdout (for observability).
actas_lock_gc_stale() {
  local dir; dir="$(_actas_lock_dir)"
  [ -d "$dir" ] || { echo 0; return 0; }
  local f owner count=0
  for f in "$dir"/actas.*.session; do
    [ -f "$f" ] || continue
    owner="$(head -1 "$f" 2>/dev/null || true)"
    if [ -z "$owner" ] || ! actas_lock_sid_alive "$owner"; then
      rm -f "$f"
      count=$((count + 1))
    fi
  done
  echo "$count"
}

# Classify a (team, agent) pair relative to the calling session.
# Echoes one of: free | mine | other:<sid>
actas_lock_state() {
  local team="$1" agent="$2" sid="$3"
  local owner
  owner="$(actas_lock_owner "$team" "$agent")"
  if [ -z "$owner" ]; then
    echo "free"; return 0
  fi
  if [ "$owner" = "$sid" ]; then
    echo "mine"; return 0
  fi
  if actas_lock_sid_alive "$owner"; then
    printf 'other:%s\n' "$owner"
  else
    echo "free"  # stale owner — effectively free, GC will remove it later
  fi
}
