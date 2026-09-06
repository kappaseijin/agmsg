# Sourced only into a disposable pinned fixture by lifetime_adapter.py.
# Observation failure must not overwrite the subject rc. Missing begin/end
# and publisher diagnostics remain quality failures in the external artifact.
_lifetime_emit() {
  AGMSG_LIFETIME_OBSERVER_STARTED="${EPOCHREALTIME:-}" python "$AGMSG_LIFETIME_TOOLS/lifetime_adapter.py" emit "$@" 2>>"$AGMSG_LIFETIME_OUTPUT/adapter.stderr" || :
  return 0
}
_lifetime_controller() {
  python "$AGMSG_LIFETIME_TOOLS/lifetime_adapter.py" controller "$$" 2>>"$AGMSG_LIFETIME_OUTPUT/adapter.stderr" || :
  return 0
}
_lifetime_begin() {
  _lifetime_seq=$((${_lifetime_seq:-0} + 1))
  _lifetime_op="${BASHPID:-$$}-$_lifetime_seq"
  _lifetime_kind="$1"; _lifetime_namespace="$2"; _lifetime_pid="$3"
  _lifetime_emit operation "operation_id=$_lifetime_op" phase=begin "operation=$1" "namespace=$2" "process_id=$3"
}
_lifetime_end() {
  _lifetime_emit operation "operation_id=$_lifetime_op" phase=end "operation=$_lifetime_kind" "namespace=$_lifetime_namespace" "process_id=$_lifetime_pid" "rc=$1" "output=$2" output_capture=original-merged-stream
}
_lifetime_bind_root() {
  local root="$1" key=foreign native
  [ "$root" != "${TEST_SKILL_DIR:-}" ] || key=target
  [ "$root" != "${AGMSG_WINDOWS_DIAG_ENDED_ROOT:-}" ] || key=ended
  native=$(cygpath -m "$root") || return 0
  MSYS_NO_PATHCONV=1 powershell.exe -NoProfile -NonInteractive -File "$AGMSG_LIFETIME_TOOLS/lifetime-bind.ps1" \
    -Directory "$AGMSG_LIFETIME_CONTROL" -RunId "$AGMSG_LIFETIME_RUN_ID" -Root "$native" -RootKey "$key" \
    2>>"$AGMSG_LIFETIME_OUTPUT/adapter.stderr" || :
  return 0
}

_lifetime_save_fixture() {
  local file
  for file in native-cleanup-diagnostic.log native-cleanup-diagnostic-pids.log native-bridge-events.log native-launcher-events.log already-ended-events.log; do
    if [ -f "$TEST_SKILL_DIR/$file" ]; then
      cp "$TEST_SKILL_DIR/$file" "$AGMSG_LIFETIME_OUTPUT/${BASHPID:-$$}-$file" 2>>"$AGMSG_LIFETIME_OUTPUT/adapter.stderr" || :
    fi
  done
  return 0
}
