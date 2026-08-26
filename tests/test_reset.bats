#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
  export PROJ_A="/tmp/agmsg-reset-a"
  export PROJ_B="/tmp/agmsg-reset-b"
  bash "$SCRIPTS/join.sh" team-a alice claude-code "$PROJ_A" >/dev/null
  bash "$SCRIPTS/join.sh" team-b alice claude-code "$PROJ_B" >/dev/null
  source "$SCRIPTS/lib/actas-lock.sh"
}

teardown() {
  teardown_test_env
}

@test "reset: drop removes only current registration but releases target lock in every team" {
  actas_lock_claim team-a alice sid-me
  actas_lock_claim team-b alice sid-me

  run bash "$SCRIPTS/reset.sh" "$PROJ_A" claude-code alice sid-me
  [ "$status" -eq 0 ]
  [ ! -f "$(actas_lock_path team-a alice)" ]
  [ ! -f "$(actas_lock_path team-b alice)" ]

  run bash "$SCRIPTS/identities.sh" "$PROJ_A" claude-code
  [ "$status" -eq 0 ]
  [ -z "$output" ]

  run bash "$SCRIPTS/identities.sh" "$PROJ_B" claude-code
  [ "$status" -eq 0 ]
  [ "$output" = $'team-b\talice' ]
}
