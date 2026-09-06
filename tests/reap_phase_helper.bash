# Diagnostic-only trace for the single owned/foreign launcher test (#261).
_reap_diag_log() {
  # A direct child sees the actual calling shell as PPID, including Bash 3.2
  # asynchronous bodies. Command substitution would insert another shell.
  local log_rc
  sh -c '
    printf "event=%s phase=%s rc=%s actor_pid=%s %s" "$1" "$2" "$3" "$PPID" "$4"
    if [ "$1" = body-result ]; then printf " body_pid=%s" "$PPID"; fi
    printf "\n"
  ' _ "$1" "${_REAP_PHASE:-entry}" "$2" "${3:-}" >> "$_REAP_PACKET"
  log_rc=$?
  return "$log_rc"
}

_reap_diag_phase() {
  _REAP_PHASE="$1"
  _reap_diag_log phase 0
}

_reap_diag_init() {
  _REAP_PACKET=$(mktemp "${RUNNER_TEMP:-${BATS_SUITE_TMPDIR:-${TMPDIR:-/tmp}}}/agmsg-reap-phase.XXXXXX")
  _REAP_PHASE=entry
  _reap_diag_log parent-start 0
  local kernel rc
  if kernel=$(uname -s); then rc=0; else rc=$?; fi
  case "$kernel" in
    Linux|Darwin|MINGW*|MSYS*|CYGWIN*) ;;
    *) kernel=other ;;
  esac
  _reap_diag_log uname "$rc" "kernel=$kernel"
  return "$rc"
}

_reap_diag_helper_failed() {
  local rc="$1"
  _reap_diag_log helper-result "$rc"
  _reap_diag_log parent-end "$rc" 'body_started=false'
  cat "$_REAP_PACKET" >&2
  return "$rc"
}

_reap_diag_exit() {
  local primary_rc="$1" cleanup_rc
  set +e
  _reap_diag_log body-result "$primary_rc"
  _cleanup
  cleanup_rc=$?
  _reap_diag_log cleanup-end "$cleanup_rc" "primary_rc=$primary_rc"
  exit "$primary_rc"
}

_reap_diag_pid_set() {
  local pids='' pid
  for pid in $snapshot; do
    case "$pid" in ''|*[!0-9]*) continue ;; esac
    pids="${pids}${pid},"
  done
  _reap_diag_log pid-set 0 "owned_candidates=$pids dispatcher=$dispatcher bridge=$bridge_pid foreign=$foreign foreign_launcher=$foreign_launcher"
}

_reap_diag_parent_result() {
  local body_pid="$1" wait_rc="$2" report count reported_phase reported_rc
  report=$(awk -v wanted="body_pid=$body_pid" '
    $1 == "event=body-result" {
      matched=0; phase=""; rc=""
      for (i=1;i<=NF;i++) {
        if ($i==wanted) matched=1
        if ($i ~ /^phase=/) phase=substr($i,7)
        if ($i ~ /^rc=/) rc=substr($i,4)
      }
      if (matched) print phase,rc
    }' "$_REAP_PACKET")
  count=$(printf '%s\n' "$report" | awk 'NF {n++} END {print n+0}')
  if [ "$count" -eq 1 ]; then
    read -r reported_phase reported_rc <<< "$report"
    _REAP_PHASE="$reported_phase"
    _reap_diag_log parent-end "$wait_rc" "body_pid=$body_pid body_terminated=true body_rc=$reported_rc"
    if [ "$reported_rc" != "$wait_rc" ]; then
      _reap_diag_log attribution-mismatch 1
      return 1
    fi
  else
    _REAP_PHASE=unknown
    _reap_diag_log parent-end "$wait_rc" "body_pid=$body_pid body_terminated=true body_result_count=$count"
    _reap_diag_log attribution-missing 1
    return 1
  fi
  return "$wait_rc"
}
