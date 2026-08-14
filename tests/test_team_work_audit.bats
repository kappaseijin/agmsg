#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
  bash "$SCRIPTS/join.sh" demo owner codex /tmp/demo-owner --role programmer --kind seat >/dev/null
  bash "$SCRIPTS/join.sh" demo dispatch codex /tmp/demo-dispatch --role manager --kind seat >/dev/null

  export AUDIT_FIXTURES="$BATS_TEST_DIRNAME/fixtures/team-work-audit"
  export FAKE_GH_BIN="$BATS_TEST_TMPDIR/fake-gh-bin"
  export TEAM_WORK_GH_LOG="$BATS_TEST_TMPDIR/gh-requests.jsonl"
  mkdir -p "$FAKE_GH_BIN"
  cp "$AUDIT_FIXTURES/gh" "$FAKE_GH_BIN/gh"
  chmod +x "$FAKE_GH_BIN/gh"
  : > "$TEAM_WORK_GH_LOG"
}

teardown() {
  teardown_test_env
}

write_pack() {
  printf '%s\n' '{"schemaVersion":1,"team":"demo","workItems":[{"schemaVersion":1,"workItem":{"id":"issue:42","source":{"kind":"issue","repository":"kappaseijin/agmsg","number":42}},"ownerSeat":"owner","workKinds":["implementation"],"relations":[],"revision":1,"classificationBasis":{"contentDigest":"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","refs":[{"kind":"issue","repository":"kappaseijin/agmsg","number":42}]},"writebackRequired":false}]}' > "$1"
}

write_closing_pack() {
  printf '%s\n' '{"schemaVersion":1,"team":"demo","workItems":[{"schemaVersion":1,"workItem":{"id":"issue:42","source":{"kind":"issue","repository":"kappaseijin/agmsg","number":42}},"ownerSeat":"owner","workKinds":["implementation"],"relations":[{"kind":"pull_request","repository":"kappaseijin/agmsg","number":777,"relation":"closes","closingIssue":{"repository":"kappaseijin/agmsg","number":42}}],"revision":1,"classificationBasis":{"contentDigest":"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","refs":[{"kind":"issue","repository":"kappaseijin/agmsg","number":42}]},"writebackRequired":false}]}' > "$1"
}

json_value() {
  JSON_INPUT="$1" JSON_SELECTOR="$2" node -e '
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
  FILE_PATH="$1" node -e 'const crypto = require("crypto"); const fs = require("fs"); process.stdout.write(crypto.createHash("sha256").update(fs.readFileSync(process.env.FILE_PATH)).digest("hex"));'
}

run_team_work() {
  local fixture="$1" command="$2" pack="$3"
  run env PATH="$FAKE_GH_BIN:$PATH" TEAM_WORK_GH_FIXTURE="$fixture" TEAM_WORK_GH_LOG="$TEAM_WORK_GH_LOG" \
    bash "$SCRIPTS/team-work.sh" "$command" demo "$pack"
}

assert_reason() {
  local json="$1" expected="$2"
  JSON_INPUT="$json" EXPECTED="$expected" node -e '
const value = JSON.parse(process.env.JSON_INPUT);
if (!value.classificationBasis.reasons.some((reason) => reason.code === process.env.EXPECTED)) process.exit(1);
'
}

assert_canonical_json() {
  JSON_INPUT="$1" node -e '
const input = process.env.JSON_INPUT;
const value = JSON.parse(input);
function sort(item) {
  if (Array.isArray(item)) return item.map(sort);
  if (item && typeof item === "object") {
    const result = {};
    for (const key of Object.keys(item).sort()) result[key] = sort(item[key]);
    return result;
  }
  return item;
}
if (input !== JSON.stringify(sort(value))) process.exit(1);
'
}

@test "team-work audit: follows both closing relation pages" {
  local pack="$BATS_TEST_TMPDIR/closing.json"
  write_closing_pack "$pack"

  run_team_work "$AUDIT_FIXTURES/complete-two-pages.json" audit "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "quiescent" ]
  [ "$(json_value "$output" violations)" = "[]" ]
  grep -Fq '"after":"issue-page-2"' "$TEAM_WORK_GH_LOG"
  grep -Fq '"after":"pr-page-2"' "$TEAM_WORK_GH_LOG"
}

@test "team-work queue: returns an unleased open source as ready" {
  local pack="$BATS_TEST_TMPDIR/open.json"
  write_pack "$pack"

  run_team_work "$AUDIT_FIXTURES/open.json" queue "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "ready" ]
  [ "$(json_value "$output" readyCount)" = "1" ]
  [ "$(json_value "$output" ready[0].workItemId)" = "issue:42" ]
}

@test "team-work audit: records fully allocated basis for a live lease" {
  local pack="$BATS_TEST_TMPDIR/allocated.json"
  write_pack "$pack"
  run bash "$SCRIPTS/team-work.sh" claim demo "$pack" issue:42 owner 3600
  [ "$status" -eq 0 ]

  run_team_work "$AUDIT_FIXTURES/open.json" audit "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "fully_allocated" ]
  [ "$(json_value "$output" classificationBasis.allocatedItemCount)" = "1" ]
  [ "$(json_value "$output" classificationBasis.readyCount)" = "0" ]
}

@test "team-work observe: records quiescent basis for a closed source" {
  local pack="$BATS_TEST_TMPDIR/closed.json"
  write_pack "$pack"

  run_team_work "$AUDIT_FIXTURES/closed.json" observe "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "quiescent" ]
  [ "$(json_value "$output" classificationBasis.closedItemCount)" = "1" ]
}

@test "team-work queue: source failure is unknown with no ready item" {
  local pack="$BATS_TEST_TMPDIR/source-error.json"
  write_pack "$pack"

  run_team_work "$AUDIT_FIXTURES/error.json" queue "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "unknown" ]
  [ "$(json_value "$output" ready)" = "[]" ]
  assert_reason "$output" source_unavailable
}

@test "team-work audit: one-sided closing relation is unknown" {
  local pack="$BATS_TEST_TMPDIR/incomplete.json"
  write_closing_pack "$pack"

  run_team_work "$AUDIT_FIXTURES/incomplete-relation.json" audit "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "unknown" ]
  assert_reason "$output" relation_incomplete
}

@test "team-work queue: a stale local row is unknown with no ready item" {
  local pack="$BATS_TEST_TMPDIR/stale.json"
  write_pack "$pack"
  run bash "$SCRIPTS/team-work.sh" claim demo "$pack" issue:42 owner 3600
  [ "$status" -eq 0 ]
  PACK_PATH="$pack" node -e '
const fs = require("fs");
const pack = JSON.parse(fs.readFileSync(process.env.PACK_PATH, "utf8"));
pack.workItems[0].writebackRequired = true;
fs.writeFileSync(process.env.PACK_PATH, JSON.stringify(pack));
'

  run_team_work "$AUDIT_FIXTURES/open.json" queue "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "unknown" ]
  [ "$(json_value "$output" ready)" = "[]" ]
  assert_reason "$output" local_state_stale
}

@test "team-work audit: emits stable canonical JSON without local writes" {
  local pack="$BATS_TEST_TMPDIR/canonical.json"
  local database="$TEST_SKILL_DIR/db/messages.db"
  local before first
  write_pack "$pack"
  before="$(sha256_file "$database")"

  run_team_work "$AUDIT_FIXTURES/open.json" audit "$pack"
  [ "$status" -eq 0 ]
  first="$output"
  assert_canonical_json "$first"
  [[ "$(json_value "$first" sourceDigest)" =~ ^sha256:[0-9a-f]{64}$ ]]
  [[ "$(json_value "$first" auditDigest)" =~ ^sha256:[0-9a-f]{64}$ ]]

  run_team_work "$AUDIT_FIXTURES/open.json" audit "$pack"
  [ "$status" -eq 0 ]
  [ "$first" = "$output" ]
  [ "$before" = "$(sha256_file "$database")" ]
}
