#!/usr/bin/env bats

bats_require_minimum_version 1.5.0
load test_helper

setup() {
  setup_test_env

  mkdir -p "$TEST_SKILL_DIR/docs/decisions"

  cp \
    "$BATS_TEST_DIRNAME/../docs/decisions/issue383-capability-manifest.json" \
    "$TEST_SKILL_DIR/docs/decisions/issue383-capability-manifest.json"

  cp \
    "$BATS_TEST_DIRNAME/../docs/decisions/issue379-provider-manifest.json" \
    "$TEST_SKILL_DIR/docs/decisions/issue379-provider-manifest.json"

  bash "$SCRIPTS/join.sh" \
    demo \
    pilot \
    codex \
    /tmp/pilot \
    >/dev/null

  bash "$SCRIPTS/join.sh" \
    demo \
    worker \
    codex \
    /tmp/worker \
    >/dev/null
}

teardown() {
  teardown_test_env
}

write_bindings() {
  local path="$1"
  shift

  python3 - "$path" "$@" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
values = sys.argv[2:]

if len(values) % 2:
    raise SystemExit("bindings must be key/value pairs")

bindings = {}

for index in range(0, len(values), 2):
    key = values[index]
    value = values[index + 1]

    if not key or not value:
        raise SystemExit("binding key/value must be non-empty")

    if key in bindings:
        raise SystemExit("duplicate binding: " + key)

    bindings[key] = value

path.write_text(
    json.dumps(bindings, separators=(",", ":")) + "\n",
    encoding="utf8",
)
PY
}

json_field() {
  local document="$1"
  local field="$2"

  python3 - "$document" "$field" <<'PY'
import json
import sys

value = json.loads(sys.argv[1])
field = sys.argv[2]

result = value.get(field)

if not isinstance(result, str) or not result:
    raise SystemExit("missing string field: " + field)

print(result)
PY
}

assert_json_shape() {
  local document="$1"
  shift

  python3 - "$document" "$@" <<'PY'
import json
import sys

value = json.loads(sys.argv[1])
required = sys.argv[2:]

if not isinstance(value, dict):
    raise SystemExit("response is not a JSON object")

missing = [
    field
    for field in required
    if field not in value
]

if missing:
    raise SystemExit(
        "missing fields: " + ",".join(missing)
    )

for field in required:
    item = value[field]

    if field == "schemaVersion":
        if item != 1:
            raise SystemExit(
                "schemaVersion must be 1"
            )
        continue

    if not isinstance(item, str) or not item:
        raise SystemExit(
            "field must be a non-empty string: " + field
        )
PY
}

assert_json_values() {
  local document="$1"
  shift

  python3 - "$document" "$@" <<'PY'
import json
import sys

value = json.loads(sys.argv[1])
pairs = sys.argv[2:]

if len(pairs) % 2:
    raise SystemExit(
        "expected field/value pairs"
    )

for index in range(0, len(pairs), 2):
    field = pairs[index]
    expected = pairs[index + 1]
    actual = value.get(field)

    if str(actual) != expected:
        raise SystemExit(
            "%s: expected %r, got %r"
            % (field, expected, actual)
        )
PY
}

@test "p2 provider: real manifest expands argv and real team completes the six-capability flow" {
  bindings="$TEST_SKILL_DIR/send-input-bindings.json"

  write_bindings \
    "$bindings" \
    team demo \
    from pilot \
    recipient worker \
    requestId request-1 \
    body input-body

  run python3 \
    "$SCRIPTS/issue383_provider_contract.py" \
    --capability message-send-v1 \
    --bindings "$bindings"

  [ "$status" -eq 0 ]

  input_send="$output"

  run assert_json_shape \
    "$input_send" \
    schemaVersion \
    state \
    messageId \
    requestId \
    team \
    from \
    to

  [ "$status" -eq 0 ]

  run assert_json_values \
    "$input_send" \
    state queued \
    requestId request-1 \
    team demo \
    from pilot \
    to worker

  [ "$status" -eq 0 ]

  input_id="$(json_field "$input_send" messageId)"
  [ -n "$input_id" ]

  bindings="$TEST_SKILL_DIR/peek-bindings.json"

  write_bindings \
    "$bindings" \
    team demo \
    recipient worker

  run python3 \
    "$SCRIPTS/issue383_provider_contract.py" \
    --capability message-peek-v1 \
    --bindings "$bindings"

  [ "$status" -eq 0 ]

  peek="$output"

  run assert_json_shape \
    "$peek" \
    schemaVersion \
    state \
    messageId \
    from \
    to \
    body \
    createdAt

  [ "$status" -eq 0 ]

  run assert_json_values \
    "$peek" \
    state ok \
    messageId "$input_id" \
    from pilot \
    to worker \
    body input-body

  [ "$status" -eq 0 ]

  bindings="$TEST_SKILL_DIR/claim-bindings.json"

  write_bindings \
    "$bindings" \
    team demo \
    messageId "$input_id" \
    owner owner-1

  run python3 \
    "$SCRIPTS/issue383_provider_contract.py" \
    --capability message-claim-v1 \
    --bindings "$bindings"

  [ "$status" -eq 0 ]

  claim="$output"

  run assert_json_shape \
    "$claim" \
    schemaVersion \
    state \
    messageId \
    owner

  [ "$status" -eq 0 ]

  run assert_json_values \
    "$claim" \
    state claimed \
    messageId "$input_id" \
    owner owner-1

  [ "$status" -eq 0 ]

  bindings="$TEST_SKILL_DIR/release-bindings.json"

  write_bindings \
    "$bindings" \
    team demo \
    messageId "$input_id" \
    owner owner-1

  run python3 \
    "$SCRIPTS/issue383_provider_contract.py" \
    --capability message-release-v1 \
    --bindings "$bindings"

  [ "$status" -eq 0 ]

  released="$output"

  run assert_json_shape \
    "$released" \
    schemaVersion \
    state \
    messageId \
    owner

  [ "$status" -eq 0 ]

  run assert_json_values \
    "$released" \
    state released \
    messageId "$input_id" \
    owner owner-1

  [ "$status" -eq 0 ]

  bindings="$TEST_SKILL_DIR/reclaim-bindings.json"

  write_bindings \
    "$bindings" \
    team demo \
    messageId "$input_id" \
    owner owner-1

  run python3 \
    "$SCRIPTS/issue383_provider_contract.py" \
    --capability message-claim-v1 \
    --bindings "$bindings"

  [ "$status" -eq 0 ]

  reclaimed="$output"

  run assert_json_values \
    "$reclaimed" \
    state claimed \
    messageId "$input_id" \
    owner owner-1

  [ "$status" -eq 0 ]

  bindings="$TEST_SKILL_DIR/send-result-bindings.json"

  write_bindings \
    "$bindings" \
    team demo \
    from worker \
    recipient pilot \
    requestId request-1 \
    body result-body

  run python3 \
    "$SCRIPTS/issue383_provider_contract.py" \
    --capability message-send-v1 \
    --bindings "$bindings"

  [ "$status" -eq 0 ]

  delegate_send="$output"

  run assert_json_shape \
    "$delegate_send" \
    schemaVersion \
    state \
    messageId \
    requestId \
    team \
    from \
    to

  [ "$status" -eq 0 ]

  run assert_json_values \
    "$delegate_send" \
    state queued \
    requestId request-1 \
    team demo \
    from worker \
    to pilot

  [ "$status" -eq 0 ]

  delegate_id="$(json_field "$delegate_send" messageId)"
  [ -n "$delegate_id" ]
  [ "$delegate_id" != "$input_id" ]

  bindings="$TEST_SKILL_DIR/receipt-bindings.json"

  write_bindings \
    "$bindings" \
    team demo \
    inputMessageId "$input_id" \
    requestId request-1 \
    delegateMessageId "$delegate_id"

  run python3 \
    "$SCRIPTS/issue383_provider_contract.py" \
    --capability handoff-receipt-v1 \
    --bindings "$bindings"

  [ "$status" -eq 0 ]

  receipt_response="$output"

  run assert_json_shape \
    "$receipt_response" \
    schemaVersion \
    state \
    inputMessageId \
    requestId \
    delegateMessageId \
    receiptId \
    team

  [ "$status" -eq 0 ]

  run assert_json_values \
    "$receipt_response" \
    state recorded \
    inputMessageId "$input_id" \
    requestId request-1 \
    delegateMessageId "$delegate_id" \
    team demo

  [ "$status" -eq 0 ]

  receipt_id="$(json_field "$receipt_response" receiptId)"
  [ -n "$receipt_id" ]

  bindings="$TEST_SKILL_DIR/ack-bindings.json"

  write_bindings \
    "$bindings" \
    team demo \
    messageId "$input_id" \
    owner owner-1 \
    receiptId "$receipt_id"

  run python3 \
    "$SCRIPTS/issue383_provider_contract.py" \
    --capability message-ack-v1 \
    --bindings "$bindings"

  [ "$status" -eq 0 ]

  ack="$output"

  run assert_json_shape \
    "$ack" \
    schemaVersion \
    state \
    messageId \
    owner \
    receiptId

  [ "$status" -eq 0 ]

  run assert_json_values \
    "$ack" \
    state acked \
    messageId "$input_id" \
    owner owner-1 \
    receiptId "$receipt_id"

  [ "$status" -eq 0 ]

  run bash \
    "$SCRIPTS/p2-provider.sh" \
    message-peek \
    demo \
    worker

  [ "$status" -eq 0 ]

  absent="$output"

  run assert_json_shape \
    "$absent" \
    schemaVersion \
    state

  [ "$status" -eq 0 ]

  run assert_json_values \
    "$absent" \
    state absent

  [ "$status" -eq 0 ]
}

@test "p2 provider: real manifest validates against the G1 provider pin" {
  run python3 \
    "$SCRIPTS/issue383_provider_contract.py" \
    --validate-only

  [ "$status" -eq 0 ]

  validated="$output"

  run python3 - "$validated" <<'PY'
import json
import sys

value = json.loads(sys.argv[1])

if value.get("schemaVersion") != 1:
    raise SystemExit("wrong schemaVersion")

if value.get("state") != "valid":
    raise SystemExit("manifest did not validate")

commit = value.get("providerCommit")

if (
    not isinstance(commit, str)
    or len(commit) != 40
    or any(
        character not in "0123456789abcdef"
        for character in commit
    )
):
    raise SystemExit("invalid provider commit")

expected = {
    "message-peek-v1",
    "message-claim-v1",
    "message-release-v1",
    "message-ack-v1",
    "message-send-v1",
    "handoff-receipt-v1",
}

actual = value.get("requiredCapabilities")

if not isinstance(actual, list):
    raise SystemExit("requiredCapabilities is not a list")

if set(actual) != expected:
    raise SystemExit("wrong requiredCapabilities")
PY

  [ "$status" -eq 0 ]
}

@test "p2 provider: undeclared operation and unknown message id fail closed" {
  run bash \
    "$SCRIPTS/p2-provider.sh" \
    history \
    demo

  [ "$status" -eq 2 ]

  run bash \
    "$SCRIPTS/p2-provider.sh" \
    message-claim \
    demo \
    unknown-id \
    owner-1

  [ "$status" -ne 0 ]
}

@test "p2 provider: a different owner cannot release another owner's claim" {
  sent="$(
    bash "$SCRIPTS/p2-provider.sh" \
      message-send \
      demo \
      pilot \
      worker \
      request-owner \
      owner-body
  )"

  input_id="$(json_field "$sent" messageId)"
  [ -n "$input_id" ]

  run bash \
    "$SCRIPTS/p2-provider.sh" \
    message-claim \
    demo \
    "$input_id" \
    owner-1

  [ "$status" -eq 0 ]

  claimed="$output"

  run assert_json_values \
    "$claimed" \
    state claimed \
    messageId "$input_id" \
    owner owner-1

  [ "$status" -eq 0 ]

  run bash \
    "$SCRIPTS/p2-provider.sh" \
    message-release \
    demo \
    "$input_id" \
    owner-other

  [ "$status" -ne 0 ]

  run bash \
    "$SCRIPTS/p2-provider.sh" \
    message-release \
    demo \
    "$input_id" \
    owner-1

  [ "$status" -eq 0 ]

  released="$output"

  run assert_json_values \
    "$released" \
    state released \
    messageId "$input_id" \
    owner owner-1

  [ "$status" -eq 0 ]
}

@test "p2 provider: ack requires the matching durable handoff receipt" {
  input_send="$(
    bash "$SCRIPTS/p2-provider.sh" \
      message-send \
      demo \
      pilot \
      worker \
      request-receipt \
      input
  )"

  input_id="$(json_field "$input_send" messageId)"
  [ -n "$input_id" ]

  delegate_send="$(
    bash "$SCRIPTS/p2-provider.sh" \
      message-send \
      demo \
      worker \
      pilot \
      request-receipt \
      result
  )"

  delegate_id="$(json_field "$delegate_send" messageId)"
  [ -n "$delegate_id" ]

  run bash \
    "$SCRIPTS/p2-provider.sh" \
    message-claim \
    demo \
    "$input_id" \
    owner-1

  [ "$status" -eq 0 ]

  run bash \
    "$SCRIPTS/p2-provider.sh" \
    message-ack \
    demo \
    "$input_id" \
    owner-1 \
    receipt-missing

  [ "$status" -ne 0 ]

  receipt_response="$(
    bash "$SCRIPTS/p2-provider.sh" \
      handoff-receipt \
      demo \
      "$input_id" \
      request-receipt \
      "$delegate_id"
  )"

  run assert_json_shape \
    "$receipt_response" \
    schemaVersion \
    state \
    inputMessageId \
    requestId \
    delegateMessageId \
    receiptId \
    team

  [ "$status" -eq 0 ]

  run assert_json_values \
    "$receipt_response" \
    state recorded \
    inputMessageId "$input_id" \
    requestId request-receipt \
    delegateMessageId "$delegate_id" \
    team demo

  [ "$status" -eq 0 ]

  receipt_id="$(json_field "$receipt_response" receiptId)"
  [ -n "$receipt_id" ]

  run bash \
    "$SCRIPTS/p2-provider.sh" \
    message-ack \
    demo \
    "$input_id" \
    owner-1 \
    "$receipt_id"

  [ "$status" -eq 0 ]

  ack="$output"

  run assert_json_shape \
    "$ack" \
    schemaVersion \
    state \
    messageId \
    owner \
    receiptId

  [ "$status" -eq 0 ]

  run assert_json_values \
    "$ack" \
    state acked \
    messageId "$input_id" \
    owner owner-1 \
    receiptId "$receipt_id"

  [ "$status" -eq 0 ]
}
