#!/usr/bin/env bats

setup() {
  source "$BATS_TEST_DIRNAME/reap_phase_helper.bash"
  _REAP_PACKET="$BATS_TEST_TMPDIR/packet"
  _REAP_PHASE=pid-control
}

@test "reap diagnostic: actual async body PID matches parent wait identity" {
  _reap_diag_log parent-start 0
  (
    _reap_diag_log body-result 0
    exit 0
  ) &
  local body_pid=$! body_rc
  if wait "$body_pid"; then body_rc=0; else body_rc=$?; fi
  _reap_diag_parent_result "$body_pid" "$body_rc"
  local actor
  actor=$(awk '$1 == "event=body-result" {for(i=1;i<=NF;i++) if($i ~ /^actor_pid=/) print substr($i,11)}' "$_REAP_PACKET")
  [ "$actor" = "$body_pid" ]
}
