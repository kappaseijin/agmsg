#!/usr/bin/env bats

bats_require_minimum_version 1.5.0
load test_helper

setup() {
  setup_test_env
  bash "$SCRIPTS/join.sh" demo pilot codex /tmp/pilot >/dev/null
  bash "$SCRIPTS/join.sh" demo worker codex /tmp/worker >/dev/null
}

teardown() { teardown_test_env; }

@test "p2 provider: declared commands preserve UUID message scope and owner" {
  sent="$(bash "$SCRIPTS/p2-provider.sh" message-send demo pilot worker request-1 body)"
  id="$(printf '%s' "$sent" | sed -n 's/.*"messageId":"\([^"]*\)".*/\1/p')"
  run bash "$SCRIPTS/p2-provider.sh" message-peek demo worker
  [ "$status" -eq 0 ]
  [[ "$output" == *"$id"* ]]
  reverse="$(bash "$SCRIPTS/p2-provider.sh" message-send demo worker pilot request-2 reverse | sed -n 's/.*"messageId":"\([^"]*\)".*/\1/p')"
  run bash "$SCRIPTS/p2-provider.sh" message-peek demo worker
  [ "$status" -eq 0 ]
  [[ "$output" != *"$reverse"* ]]
  run bash "$SCRIPTS/p2-provider.sh" message-claim demo "$id" owner-1
  [ "$status" -eq 0 ]
  run bash "$SCRIPTS/p2-provider.sh" message-release demo "$id" owner-other
  [ "$status" -ne 0 ]
  run bash "$SCRIPTS/p2-provider.sh" message-release demo "$id" owner-1
  [ "$status" -eq 0 ]
}

@test "p2 provider: undeclared operation and unknown id fail closed" {
  run bash "$SCRIPTS/p2-provider.sh" history demo
  [ "$status" -eq 2 ]
  run bash "$SCRIPTS/p2-provider.sh" message-claim demo unknown-id owner-1
  [ "$status" -ne 0 ]
}

@test "p2 provider: ack requires a matching durable handoff receipt" {
  input="$(bash "$SCRIPTS/p2-provider.sh" message-send demo pilot worker request-1 input | sed -n 's/.*"messageId":"\([^"]*\)".*/\1/p')"
  delegate="$(bash "$SCRIPTS/p2-provider.sh" message-send demo worker pilot request-1 result | sed -n 's/.*"messageId":"\([^"]*\)".*/\1/p')"
  bash "$SCRIPTS/p2-provider.sh" message-claim demo "$input" owner-1 >/dev/null
  run bash "$SCRIPTS/p2-provider.sh" message-ack demo "$input" owner-1 receipt-missing
  [ "$status" -ne 0 ]
  receipt="$(bash "$SCRIPTS/p2-provider.sh" handoff-receipt demo "$input" request-1 "$delegate" | sed -n 's/.*"receiptId":"\([^"]*\)".*/\1/p')"
  run bash "$SCRIPTS/p2-provider.sh" message-ack demo "$input" owner-1 "$receipt"
  [ "$status" -eq 0 ]
  [[ "$output" == *'"state":"acked"'* ]]
}
