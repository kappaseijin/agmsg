# Dedicated to test248's broad-watcher boundary. No production process policy.
# test_helper.bash provides the existing bounded wait and conservative _pid_gone.
_watch_stop_generation() {
  local pid="$1" stat rest stamp
  local -a fields
  if [ -r "/proc/$pid/stat" ]; then
    IFS= read -r stat < "/proc/$pid/stat" || return 1
    rest="${stat##*) }"
    read -r -a fields <<< "$rest"
    [ -n "${fields[19]:-}" ] || return 1
    printf 'proc-%s\n' "${fields[19]}"
  else
    stamp="$(LC_ALL=C ps -o lstart= -p "$pid" 2>/dev/null)" || return 1
    [ -n "$stamp" ] || return 1
    printf 'ps-%s\n' "$stamp" | tr ' ' '_'
  fi
}

_watch_stop_log() {
  printf 'phase=%s seconds=%s actor_time=%s pid=%s owner=%s generation=%s %s\n' \
    "$1" "$SECONDS" "$(TZ=Asia/Tokyo date '+%Y-%m-%dT%H:%M:%S+09:00')" "${_WATCH_STOP_PID:-unknown}" "${_WATCH_STOP_OWNER:-unknown}" \
    "${_WATCH_STOP_GENERATION:-unknown}" "${2:-}" >> "$_WATCH_STOP_PACKET"
}

_watch_stop_signal() { kill "-$1" "$2"; }

_watch_stop_matches() {
  local pid="$1" parent="$2" generation="$3" current ppid
  current="$(_watch_stop_generation "$pid")" || return 1
  [ "$current" = "$generation" ] || return 1
  ppid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
  [ "$ppid" = "$parent" ]
}

_watch_stop_root_matches() {
  local command
  _watch_stop_matches "$_WATCH_STOP_PID" "$_WATCH_STOP_OWNER" "$_WATCH_STOP_GENERATION" || return 1
  command="$(ps -o args= -p "$_WATCH_STOP_PID" 2>/dev/null)" || return 1
  case "$command" in
    *" $_WATCH_STOP_SCRIPT "*|*" $_WATCH_STOP_SCRIPT") return 0 ;;
    *) return 1 ;;
  esac
}

_watch_stop_register() {
  _WATCH_STOP_PID="$1"
  _WATCH_STOP_SCRIPT="$2"
  _WATCH_STOP_PACKET="$3"
  _WATCH_STOP_OWNER="${BASHPID:-$$}"
  _WATCH_STOP_GENERATION="$(_watch_stop_generation "$1")" || return 1
  _watch_stop_root_matches || return 1
  _watch_stop_log registered "ppid=$_WATCH_STOP_OWNER"
}

# Only after a failed graceful stop: freeze the verified fixture root before
# enumerating descendants, so its periodic loop cannot replace the sleep child
# between enumeration and root kill. No process-group or name-wide kill.
_watch_stop_recover() {
  local rc=0 signal_rc=0 pid parent generation depth stat started deadline remaining
  local tree="$_WATCH_STOP_PACKET.tree" identities="$_WATCH_STOP_PACKET.identities"
  _watch_stop_root_matches || { _watch_stop_log recovery-refused 'ownership=unknown'; return 1; }
  _watch_stop_signal STOP "$_WATCH_STOP_PID" 2>>"$_WATCH_STOP_PACKET" || signal_rc=$?
  _watch_stop_log recovery-stop "result=$signal_rc"
  [ "$signal_rc" -eq 0 ] || return 1
  started=$SECONDS
  deadline=$((started + $(_agmsg_test_wait_timeout_s)))
  while [ "$SECONDS" -lt "$deadline" ]; do
    _watch_stop_root_matches || return 1
    stat="$(ps -o stat= -p "$_WATCH_STOP_PID" 2>/dev/null | tr -d ' ')"
    case "$stat" in *T*) break ;; esac
    sleep "$(_agmsg_test_wait_poll_s)"
  done
  case "$stat" in *T*) ;; *) _watch_stop_log recovery-refused 'state=not-stopped'; return 1 ;; esac
  # Save only identity fields, never process arguments or live payloads.
  ps -axo pid=,ppid= | awk -v root="$_WATCH_STOP_PID" '
    {parent[$1]=$2} END {
      depth[root]=0; changed=1
      while(changed) { changed=0; for(p in parent) {
        if(!(p in depth) && (parent[p] in depth)) {depth[p]=depth[parent[p]]+1;changed=1}
      }}
      for(p in depth) if(p!=root) print depth[p],p,parent[p]
    }' | sort -rn > "$tree"
  : > "$identities"
  while read -r depth pid parent; do
    [ -n "$pid" ] || continue
    generation="$(_watch_stop_generation "$pid")" || { rc=1; continue; }
    printf '%s %s %s\n' "$pid" "$parent" "$generation" >> "$identities"
  done < "$tree"
  while read -r pid parent generation; do
    _watch_stop_root_matches || return 1
    if _watch_stop_matches "$pid" "$parent" "$generation"; then
      signal_rc=0
      _watch_stop_signal KILL "$pid" 2>>"$_WATCH_STOP_PACKET" || signal_rc=$?
      _watch_stop_log recovery-child "child=$pid ppid=$parent start=$generation result=$signal_rc"
      [ "$signal_rc" -eq 0 ] || rc=1
    elif ! _pid_gone "$pid"; then
      _watch_stop_log recovery-child "child=$pid ownership=unknown"
      rc=1
    fi
  done < "$identities"
  _watch_stop_root_matches || return 1
  signal_rc=0
  _watch_stop_signal KILL "$_WATCH_STOP_PID" 2>>"$_WATCH_STOP_PACKET" || signal_rc=$?
  _watch_stop_log recovery-root "result=$signal_rc"
  [ "$signal_rc" -eq 0 ] || return 1
  if wait_for_pid_exit "$_WATCH_STOP_PID" >>"$_WATCH_STOP_PACKET" 2>&1; then
    # Gone is positively established before wait; never wait on a live child.
    wait "$_WATCH_STOP_PID" 2>>"$_WATCH_STOP_PACKET" || :
  else
    rc=1
  fi
  while read -r pid parent generation; do
    remaining=$((deadline - SECONDS))
    if [ "$remaining" -le 0 ]; then
      _pid_gone "$pid" || rc=1
    else
      AGMSG_TEST_WAIT_TIMEOUT_S="$remaining" wait_for_pid_exit "$pid" >>"$_WATCH_STOP_PACKET" 2>&1 || rc=1
    fi
  done < "$identities"
  _watch_stop_log recovery-end "result=$rc"
  return "$rc"
}

_watch_stop_owned() {
  local rc=0 signal_rc=0 wait_rc=0 child_rc=0
  _watch_stop_log stop-request 'signal=TERM'
  if ! _pid_gone "$_WATCH_STOP_PID"; then
    if ! _watch_stop_root_matches; then
      _watch_stop_log ownership-unknown 'result=1'
      return 1
    fi
    _watch_stop_signal TERM "$_WATCH_STOP_PID" 2>>"$_WATCH_STOP_PACKET" || signal_rc=$?
    _watch_stop_log signal-result "result=$signal_rc"
    [ "$signal_rc" -eq 0 ] || rc=1
  else
    _watch_stop_log already-exited 'signal=not-sent'
  fi
  _watch_stop_log wait-enter
  wait_for_pid_exit "$_WATCH_STOP_PID" >>"$_WATCH_STOP_PACKET" 2>&1 || wait_rc=$?
  _watch_stop_log wait-exit "result=$wait_rc"
  if [ "$wait_rc" -eq 0 ]; then
    wait "$_WATCH_STOP_PID" 2>>"$_WATCH_STOP_PACKET" || child_rc=$?
    _watch_stop_log child-status "result=$child_rc"
    # watch.sh exits 0 through its TERM trap; 143 is direct SIGTERM termination.
    # Other exit codes (including not-our-child 127) remain failures.
    case "$child_rc" in 0|143) ;; *) rc=1 ;; esac
  else
    rc=1
    _watch_stop_recover || _watch_stop_log recovery-failed 'result=1'
  fi
  _watch_stop_log test-stop-end "result=$rc"
  if [ "$rc" -ne 0 ]; then cat "$_WATCH_STOP_PACKET" >&2; fi
  return "$rc"
}
