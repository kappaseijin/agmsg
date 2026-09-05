#!/usr/bin/env bats

# The collector is deliberately isolated from the production reaper and from
# test_helper's teardown.  This suite is the Windows preflight for the
# diagnostic packet only.

load test_helper

setup() {
  :
}

@test "windows collector records a lifecycle and rejects a packet missing stop" {
  skip_unless_windows "the collector requires Windows WMI process trace events"

  local collector="$BATS_TEST_DIRNAME/windows/process-lifetime-collector.ps1"
  local root="$BATS_TEST_TMPDIR/lifetime-fixture"
  local packet="$BATS_TEST_TMPDIR/lifetime-packet.jsonl"

  run powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass \
    -File "$collector" -Mode preflight -Root "$root" -PacketPath "$packet"
  [ "$status" -eq 0 ]
  [ -s "$packet" ]
  grep -Fq '"record_type":"subscription-ready"' "$packet"
  grep -Fq '"record_type":"subscription-ended"' "$packet"
  grep -Fq '"record_type":"taskkill"' "$packet"
  grep -Fq '"event_generated_time_utc"' "$packet"
  grep -Fq '"event_received_time_utc"' "$packet"
  grep -Fq '"actor_time_utc"' "$packet"
  ! grep -Fq 'CommandLine' "$packet"

  run powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass \
    -File "$collector" -Mode evaluate -PacketPath "$packet"
  [ "$status" -eq 0 ]
  printf '%s\n' "$output" | grep -Fq '"comparison":"known"'
  printf '%s\n' "$output" | grep -Fq '"reaper_judgment":"unknown"'
  printf '%s\n' "$output" | grep -Fq '"termination_actor":"not-determined"'

  run powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass \
    -File "$collector" -Mode evaluate -PacketPath "$packet" -DropStopEvent
  [ "$status" -eq 0 ]
  printf '%s\n' "$output" | grep -Fq '"comparison":"unknown"'
  printf '%s\n' "$output" | grep -Fq '"reason":"missing-stop-event"'
}
