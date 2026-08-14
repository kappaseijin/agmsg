#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
  bash "$SCRIPTS/join.sh" demo alice codex /tmp/demo --role programmer --kind seat >/dev/null
  bash "$SCRIPTS/join.sh" demo human codex /tmp/demo-human --role manager --kind human >/dev/null
  bash "$SCRIPTS/join.sh" demo service codex /tmp/demo-service --role monitor --kind service >/dev/null
}

teardown() {
  teardown_test_env
}

valid_pack() {
  printf '%s\n' '{"schemaVersion":1,"team":"demo","workItems":[{"schemaVersion":1,"workItem":{"id":"issue:40","source":{"kind":"issue","repository":"kappaseijin/agmsg","number":40}},"ownerSeat":"alice","workKinds":["implementation"],"relations":[{"kind":"pull_request","repository":"kappaseijin/agmsg","number":46,"relation":"contributes"}],"revision":1,"classificationBasis":{"contentDigest":"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","refs":[{"kind":"issue","repository":"kappaseijin/agmsg","number":40}]},"writebackRequired":false}]}'
}

write_valid_pack() {
  valid_pack > "$1"
}

set_pack_field() {
  local path="$1" selector="$2" value="$3"
  PACK_PATH="$path" PACK_SELECTOR="$selector" PACK_VALUE="$value" node -e '
const fs = require("fs");
const pack = JSON.parse(fs.readFileSync(process.env.PACK_PATH, "utf8"));
const root = process.env.PACK_SELECTOR.startsWith("pack.") ? pack : pack.workItems[0];
const parts = process.env.PACK_SELECTOR.replace(/^(pack|item)\./, "").split(".");
let target = root;
while (parts.length > 1) target = target[parts.shift()];
target[parts[0]] = JSON.parse(process.env.PACK_VALUE);
fs.writeFileSync(process.env.PACK_PATH, JSON.stringify(pack));
'
}

json_value() {
  local json="$1" selector="$2"
  JSON_INPUT="$json" JSON_SELECTOR="$selector" node -e '
let value = JSON.parse(process.env.JSON_INPUT);
for (const part of process.env.JSON_SELECTOR.split(".")) {
  if (part.length === 0) continue;
  const match = /^(.+)\[([0-9]+)\]$/.exec(part);
  value = match ? value[match[1]][Number(match[2])] : value[part];
}
process.stdout.write(typeof value === "string" ? value : JSON.stringify(value));
'
}

sha256_file() {
  FILE_PATH="$1" node -e 'const fs = require("fs"); const crypto = require("crypto"); process.stdout.write(crypto.createHash("sha256").update(fs.readFileSync(process.env.FILE_PATH)).digest("hex"));'
}

@test "team-work validate: accepts a seat-owned contract pack" {
  local pack="$BATS_TEST_TMPDIR/valid.json"
  write_valid_pack "$pack"

  run bash "$SCRIPTS/team-work.sh" validate demo "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" valid)" = "true" ]
  [ "$(json_value "$output" team)" = "demo" ]
  [ "$(json_value "$output" workItemCount)" = "1" ]
}

@test "team-work self-check: emits canonical pack and envelope digests" {
  local pack="$BATS_TEST_TMPDIR/valid.json"
  write_valid_pack "$pack"

  run bash "$SCRIPTS/team-work.sh" self-check demo "$pack"

  [ "$status" -eq 0 ]
  [[ "$(json_value "$output" contractDigest)" =~ ^sha256:[0-9a-f]{64}$ ]]
  [ "$(json_value "$output" items[0].id)" = "issue:40" ]
  [[ "$(json_value "$output" items[0].envelopeDigest)" =~ ^sha256:[0-9a-f]{64}$ ]]
  [[ "$(json_value "$output" items[0].canonicalJson)" == \{* ]]
}

@test "team-work self-check: ignores object key order and whitespace" {
  local first="$BATS_TEST_TMPDIR/first.json"
  local second="$BATS_TEST_TMPDIR/second.json"
  local first_digest
  write_valid_pack "$first"

  FIRST_PACK="$first" SECOND_PACK="$second" node -e '
const fs = require("fs");
const value = JSON.parse(fs.readFileSync(process.env.FIRST_PACK, "utf8"));
function reverseKeys(input) {
  if (Array.isArray(input)) return input.map(reverseKeys);
  if (input && typeof input === "object") {
    const result = {};
    for (const key of Object.keys(input).sort().reverse()) result[key] = reverseKeys(input[key]);
    return result;
  }
  return input;
}
fs.writeFileSync(process.env.SECOND_PACK, JSON.stringify(reverseKeys(value), null, 2));
'

  run bash "$SCRIPTS/team-work.sh" self-check demo "$first"
  [ "$status" -eq 0 ]
  first_digest="$(json_value "$output" contractDigest)"

  run bash "$SCRIPTS/team-work.sh" self-check demo "$second"
  [ "$status" -eq 0 ]
  [ "$first_digest" = "$(json_value "$output" contractDigest)" ]
}

@test "team-work validate: rejects an old schema" {
  local pack="$BATS_TEST_TMPDIR/old-schema.json"
  write_valid_pack "$pack"
  set_pack_field "$pack" pack.schemaVersion 0

  run bash "$SCRIPTS/team-work.sh" validate demo "$pack"

  [ "$status" -eq 2 ]
  [[ "$output" == *"schemaVersion must be integer 1"* ]]
}

@test "team-work validate: rejects a missing owner seat" {
  local pack="$BATS_TEST_TMPDIR/missing-owner.json"
  write_valid_pack "$pack"
  set_pack_field "$pack" item.ownerSeat '"missing-seat"'

  run bash "$SCRIPTS/team-work.sh" validate demo "$pack"

  [ "$status" -eq 2 ]
  [[ "$output" == *"owner seat does not exist"* ]]
}

@test "team-work validate: rejects a human or service owner" {
  local pack="$BATS_TEST_TMPDIR/non-seat-owner.json"
  write_valid_pack "$pack"
  set_pack_field "$pack" item.ownerSeat '"human"'

  run bash "$SCRIPTS/team-work.sh" validate demo "$pack"
  [ "$status" -eq 2 ]
  [[ "$output" == *"owner must be a seat"* ]]

  set_pack_field "$pack" item.ownerSeat '"service"'
  run bash "$SCRIPTS/team-work.sh" validate demo "$pack"
  [ "$status" -eq 2 ]
  [[ "$output" == *"owner must be a seat"* ]]
}

@test "team-work validate: rejects unknown and duplicate work kinds" {
  local pack="$BATS_TEST_TMPDIR/work-kinds.json"
  write_valid_pack "$pack"
  set_pack_field "$pack" item.workKinds '["implementation","unknown"]'

  run bash "$SCRIPTS/team-work.sh" validate demo "$pack"
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown work kind: unknown"* ]]

  set_pack_field "$pack" item.workKinds '["implementation","implementation"]'
  run bash "$SCRIPTS/team-work.sh" validate demo "$pack"
  [ "$status" -eq 2 ]
  [[ "$output" == *"workKinds must be unique"* ]]
}

@test "team-work validate: rejects malformed classification basis and revision" {
  local pack="$BATS_TEST_TMPDIR/basis.json"
  write_valid_pack "$pack"
  set_pack_field "$pack" item.classificationBasis '{"contentDigest":"not-a-digest","refs":[]}'

  run bash "$SCRIPTS/team-work.sh" validate demo "$pack"
  [ "$status" -eq 2 ]
  [[ "$output" == *"classificationBasis.contentDigest"* ]]

  write_valid_pack "$pack"
  set_pack_field "$pack" item.revision 0
  run bash "$SCRIPTS/team-work.sh" validate demo "$pack"
  [ "$status" -eq 2 ]
  [[ "$output" == *"revision must be a positive integer"* ]]
}

@test "team-work validate: rejects an incomplete closes relation" {
  local pack="$BATS_TEST_TMPDIR/closes.json"
  write_valid_pack "$pack"
  set_pack_field "$pack" item.relations '[{"kind":"pull_request","repository":"kappaseijin/agmsg","number":46,"relation":"closes"}]'

  run bash "$SCRIPTS/team-work.sh" validate demo "$pack"

  [ "$status" -eq 2 ]
  [[ "$output" == *"closes relation requires closingIssue"* ]]
}

@test "team-work commands: do not mutate the pack or roster" {
  local pack="$BATS_TEST_TMPDIR/valid.json"
  local roster="$TEST_SKILL_DIR/teams/demo/config.json"
  local before_pack before_roster
  write_valid_pack "$pack"
  before_pack="$(sha256_file "$pack")"
  before_roster="$(sha256_file "$roster")"

  run bash "$SCRIPTS/team-work.sh" self-check demo "$pack"

  [ "$status" -eq 0 ]
  [ "$before_pack" = "$(sha256_file "$pack")" ]
  [ "$before_roster" = "$(sha256_file "$roster")" ]
}

@test "team-work wrapper: rejects missing arguments, unknown commands, and absent packs" {
  local pack="$BATS_TEST_TMPDIR/valid.json"
  write_valid_pack "$pack"

  run bash "$SCRIPTS/team-work.sh" validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"Usage: team-work.sh"* ]]

  run bash "$SCRIPTS/team-work.sh" inspect demo "$pack"
  [ "$status" -eq 1 ]
  [[ "$output" == *"unknown team-work command: inspect"* ]]

  run bash "$SCRIPTS/team-work.sh" validate demo "$BATS_TEST_TMPDIR/absent.json"
  [ "$status" -eq 1 ]
  [[ "$output" == *"contract pack not found"* ]]
}

@test "team-work wrapper: fails closed when node or roster JSON is unavailable" {
  local pack="$BATS_TEST_TMPDIR/valid.json"
  local no_node_path="$BATS_TEST_TMPDIR/no-node"
  write_valid_pack "$pack"
  mkdir -p "$no_node_path"

  run env PATH="$no_node_path" /bin/bash "$SCRIPTS/team-work.sh" validate demo "$pack"
  [ "$status" -eq 1 ]
  [[ "$output" == *"requires node on PATH"* ]]

  printf '%s' '{"name":"demo","agents":{}}' > "$TEST_SKILL_DIR/teams/demo/config.json"
  run bash "$SCRIPTS/team-work.sh" validate demo "$pack"
  [ "$status" -eq 2 ]
  [[ "$output" == *"schema error: schemaVersion must be integer 1"* ]]
}
