#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
  export PROJ_A="/tmp/agmsg-identities-a"
  export PROJ_B="/tmp/agmsg-identities-b"
  bash "$SCRIPTS/join.sh" team-a alice claude-code "$PROJ_A" >/dev/null
  bash "$SCRIPTS/join.sh" team-b alice claude-code "$PROJ_B" >/dev/null
  bash "$SCRIPTS/join.sh" team-c alice codex "$PROJ_B" >/dev/null
  bash "$SCRIPTS/join.sh" team-d bob claude-code "$PROJ_A" >/dev/null
}

teardown() {
  teardown_test_env
}

@test "identities: --name --all-projects resolves exact name across teams" {
  run bash "$SCRIPTS/identities.sh" "$PROJ_A" claude-code --name alice --all-projects
  [ "$status" -eq 0 ]
  [ "$output" = $'team-a\talice\nteam-b\talice' ]

  run bash "$SCRIPTS/identities.sh" "$PROJ_A" claude-code
  [ "$status" -eq 0 ]
  [ "$output" = $'team-a\talice\nteam-d\tbob' ]
}

@test "identities: all-projects without an exact name is rejected" {
  run bash "$SCRIPTS/identities.sh" "$PROJ_A" claude-code --all-projects
  [ "$status" -ne 0 ]
}

@test "identities: empty name and unknown options are rejected" {
  run bash "$SCRIPTS/identities.sh" "$PROJ_A" claude-code --name "" --all-projects
  [ "$status" -ne 0 ]

  run bash "$SCRIPTS/identities.sh" "$PROJ_A" claude-code --name alice --unexpected
  [ "$status" -ne 0 ]
}
