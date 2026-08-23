#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
  bash "$SCRIPTS/join.sh" demo owner codex /tmp/demo-owner --role programmer --kind seat >/dev/null
  bash "$SCRIPTS/join.sh" demo manager codex /tmp/demo-manager --role manager --kind seat >/dev/null

  setup_g4_fixture
}

teardown() {
  teardown_test_env
}

write_g4_pack() {
  local path="$1" shape="${2:-two}"
  G4_PACK_PATH="$path" G4_PACK_SHAPE="$shape" G4_OWNER_SEAT="${G4_OWNER_SEAT:-owner}" node <<'NODE'
const crypto = require("crypto");
const fs = require("fs");

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (isObject(value)) {
    const result = {};
    for (const key of Object.keys(value).sort()) result[key] = canonicalize(value[key]);
    return result;
  }
  return value;
}
function digest(value) {
  return `sha256:${crypto.createHash("sha256").update(JSON.stringify(canonicalize(value)), "utf8").digest("hex")}`;
}
function scope() {
  const value = {
    id: "example-open-issues",
    repository: "kappaseijin/example",
    issueState: "OPEN",
    labelsAll: [],
  };
  return Object.assign({}, value, {
    basis: {
      contentDigest: digest(value),
      refs: [{kind: "git", repository: "kappaseijin/example", commit: "0123456789abcdef0123456789abcdef01234567"}],
    },
  });
}
function entry(number, state = "ready") {
  const value = {
    schemaVersion: 1,
    source: {repository: "kappaseijin/example", number},
    state,
    ownerSeat: process.env.G4_OWNER_SEAT,
    workKinds: ["implementation"],
    basis: {
      contentDigest: "",
      refs: [{kind: "github_issue", repository: "kappaseijin/example", number}],
    },
    revision: 1,
  };
  const basisInput = Object.assign({}, value);
  delete basisInput.basis;
  value.basis.contentDigest = digest(basisInput);
  value.entryDigest = digest(value);
  return value;
}

const pack = {
  schemaVersion: 1,
  team: "demo",
  scopes: [scope()],
  entries: [entry(42), entry(43)],
};
if (process.env.G4_PACK_SHAPE === "one") pack.entries = [entry(42)];
if (process.env.G4_PACK_SHAPE === "empty") pack.entries = [];
fs.writeFileSync(process.env.G4_PACK_PATH, JSON.stringify(pack));
NODE
}

write_predicate_pack() {
  local path="$1" kind="$2"
  write_g4_pack "$path"
  G4_PACK_PATH="$path" G4_PREDICATE_KIND="$kind" node <<'NODE'
const crypto = require("crypto");
const fs = require("fs");
const pack = JSON.parse(fs.readFileSync(process.env.G4_PACK_PATH, "utf8"));

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (isObject(value)) {
    const result = {};
    for (const key of Object.keys(value).sort()) result[key] = canonicalize(value[key]);
    return result;
  }
  return value;
}
function digest(value) {
  return `sha256:${crypto.createHash("sha256").update(JSON.stringify(canonicalize(value)), "utf8").digest("hex")}`;
}
function refreshEntry(entry) {
  const basisInput = Object.assign({}, entry);
  delete basisInput.basis;
  delete basisInput.entryDigest;
  entry.basis.contentDigest = digest(basisInput);
  const entryInput = Object.assign({}, entry);
  delete entryInput.entryDigest;
  entry.entryDigest = digest(entryInput);
}

const predicates = {
  issue_closed: {kind: "issue_closed", repository: "kappaseijin/example", number: 42},
  pull_request_merged: {kind: "pull_request_merged", repository: "kappaseijin/example", number: 42},
  review_approved: {
    kind: "review_approved",
    repository: "kappaseijin/example",
    number: 42,
    headOid: "head-42",
  },
  issue_comment_digest: {
    kind: "issue_comment_digest",
    repository: "kappaseijin/example",
    number: 42,
    commentId: 7,
    contentDigest: digest("release comment"),
  },
};
const predicate = predicates[process.env.G4_PREDICATE_KIND];
if (!predicate) throw new Error(`unknown predicate kind: ${process.env.G4_PREDICATE_KIND}`);

pack.entries[0].state = "blocked";
pack.entries[0].blocker = {
  reasonCode: `${predicate.kind}_gate`,
  releasePredicate: predicate,
};
refreshEntry(pack.entries[0]);
fs.writeFileSync(process.env.G4_PACK_PATH, JSON.stringify(pack));
NODE
}

mutate_g4_pack() {
  local path="$1" mutation="$2"
  G4_PACK_PATH="$path" G4_MUTATION="$mutation" node <<'NODE'
const crypto = require("crypto");
const fs = require("fs");
const pack = JSON.parse(fs.readFileSync(process.env.G4_PACK_PATH, "utf8"));
function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (isObject(value)) {
    const result = {};
    for (const key of Object.keys(value).sort()) result[key] = canonicalize(value[key]);
    return result;
  }
  return value;
}
function digest(value) {
  return `sha256:${crypto.createHash("sha256").update(JSON.stringify(canonicalize(value)), "utf8").digest("hex")}`;
}
function refreshEntry(entry) {
  const basisInput = Object.assign({}, entry);
  delete basisInput.basis;
  delete basisInput.entryDigest;
  entry.basis.contentDigest = digest(basisInput);
  const entryInput = Object.assign({}, entry);
  delete entryInput.entryDigest;
  entry.entryDigest = digest(entryInput);
}

switch (process.env.G4_MUTATION) {
  case "ready-blocker":
    pack.entries[0].blocker = {reasonCode: "review", releasePredicate: {kind: "not_before", at: "2026-08-21T00:00:00+09:00"}};
    refreshEntry(pack.entries[0]);
    break;
  case "blocked-no-predicate":
    pack.entries[0].state = "blocked";
    pack.entries[0].blocker = {reasonCode: "upstream_issue"};
    refreshEntry(pack.entries[0]);
    break;
  case "blocked-predicate":
    pack.entries[0].state = "blocked";
    pack.entries[0].blocker = {
      reasonCode: "not_before_gate",
      releasePredicate: {kind: "not_before", at: process.env.G4_PREDICATE_AT || "2099-01-01T00:00:00+09:00"},
    };
    refreshEntry(pack.entries[0]);
    break;
  case "unknown-blocker":
    pack.entries[0].state = "unknown";
    pack.entries[0].blocker = {reasonCode: "observation", releasePredicate: {kind: "not_before", at: "2026-08-21T00:00:00+09:00"}};
    refreshEntry(pack.entries[0]);
    break;
  case "bad-scope-digest":
    pack.scopes[0].basis.contentDigest = "sha256:0000000000000000000000000000000000000000000000000000000000000000";
    break;
  case "quiescent-entry":
    pack.entries[0].state = "quiescent";
    refreshEntry(pack.entries[0]);
    break;
  case "invalid-ref":
    pack.scopes[0].basis.refs = [];
    break;
  default:
    throw new Error(`unknown G4 mutation: ${process.env.G4_MUTATION}`);
}
fs.writeFileSync(process.env.G4_PACK_PATH, JSON.stringify(pack));
NODE
}

run_g4_audit() {
  local fixture="$1" pack="$2"
  run env PATH="$G4_FAKE_GH_BIN:$PATH" G4_GH_FIXTURE="$fixture" G4_GH_LOG="$G4_GH_LOG" \
    bash "$SCRIPTS/team-work.sh" g4-audit demo "$pack"
}

json_value() {
  JSON_INPUT="$1" JSON_SELECTOR="$2" node -e '
let value = JSON.parse(process.env.JSON_INPUT);
for (const part of process.env.JSON_SELECTOR.split(".")) {
  if (!part) continue;
  const match = /^(.+)\[([0-9]+)\]$/.exec(part);
  value = match ? value[match[1]][Number(match[2])] : value[part];
}
process.stdout.write(typeof value === "string" ? value : JSON.stringify(value));
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

load helpers/g4-fixtures

@test "g4-audit: returns exact two-Issue coverage and deterministic digest" {
  local pack="$BATS_TEST_TMPDIR/g4-pack.json"
  write_g4_pack "$pack"

  run_g4_audit "$G4_FIXTURES/two-open.json" "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "complete" ]
  [ "$(json_value "$output" classificationBasis.coverageCount)" = "2" ]
  [ "$(json_value "$output" coverage[0].number)" = "42" ]
  [ "$(json_value "$output" coverage[1].number)" = "43" ]
  [ "$(json_value "$output" entries[0].entryDigest)" != "" ]
  printf '%s\n' "$(json_value "$output" coverageDigest)" | grep -Eq '^sha256:[0-9a-f]{64}$'
  [ "$(json_value "$output" classificationBasis.reasons)" = "[]" ]
  assert_canonical_json "$output"
}

@test "g4-audit: classifies an empty declared scope as quiescent" {
  local pack="$BATS_TEST_TMPDIR/g4-pack.json"
  write_g4_pack "$pack" empty

  run_g4_audit "$G4_FIXTURES/empty.json" "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "quiescent" ]
  [ "$(json_value "$output" classificationBasis.coverageCount)" = "0" ]
  [ "$(json_value "$output" entries)" = "[]" ]
  [ "$(json_value "$output" ready)" = "[]" ]
}

@test "g4-audit: follows declared scope pagination and preserves canonical result" {
  local pack="$BATS_TEST_TMPDIR/g4-pack.json"
  write_g4_pack "$pack"

  run_g4_audit "$G4_FIXTURES/two-open-two-pages.json" "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "complete" ]
  grep -Fq '"after":"scope-page-2"' "$G4_GH_LOG"
  [ "$(wc -l < "$G4_GH_LOG" | tr -d ' ')" -eq 2 ]
}

@test "g4-audit: coverage mismatch and source failures fail closed" {
  local pack="$BATS_TEST_TMPDIR/g4-pack.json"
  write_g4_pack "$pack" one

  run_g4_audit "$G4_FIXTURES/two-open.json" "$pack"
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "unknown" ]
  [ "$(json_value "$output" ready)" = "[]" ]
  grep -Fq '"code":"coverage_mismatch"' <<<"$output"

  run_g4_audit "$G4_FIXTURES/error.json" "$pack"
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "unknown" ]
  [ "$(json_value "$output" ready)" = "[]" ]
  grep -Fq '"code":"coverage_source_unavailable"' <<<"$output"
}

@test "g4-audit: duplicate or incomplete pagination never becomes ready" {
  local pack="$BATS_TEST_TMPDIR/g4-pack.json"
  write_g4_pack "$pack" one

  run_g4_audit "$G4_FIXTURES/duplicate.json" "$pack"
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "unknown" ]
  [ "$(json_value "$output" ready)" = "[]" ]

  run_g4_audit "$G4_FIXTURES/pagination-failure.json" "$pack"
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "unknown" ]
  [ "$(json_value "$output" ready)" = "[]" ]
}

@test "g4-audit: blocked predicate is observed without changing the pack state" {
  local pack="$BATS_TEST_TMPDIR/g4-pack.json"
  write_g4_pack "$pack"
  G4_PREDICATE_AT="2099-01-01T00:00:00+09:00" mutate_g4_pack "$pack" blocked-predicate
  run env G4_AUDIT_NOW="2026-08-21T00:00:00+09:00" PATH="$G4_FAKE_GH_BIN:$PATH" G4_GH_FIXTURE="$G4_FIXTURES/two-open.json" G4_GH_LOG="$G4_GH_LOG" \
    bash "$SCRIPTS/team-work.sh" g4-audit demo "$pack"
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "unknown" ]
  [ "$(json_value "$output" entries[0].state)" = "blocked" ]
  [ "$(json_value "$output" entries[0].releasePredicate.status)" = "false" ]
  [ "$(json_value "$output" ready)" = "[]" ]
  grep -Fq '"code":"blocked_predicate_false"' <<<"$output"

  write_g4_pack "$pack"
  G4_PREDICATE_AT="2020-01-01T00:00:00+09:00" mutate_g4_pack "$pack" blocked-predicate
  run env G4_AUDIT_NOW="2026-08-21T00:00:00+09:00" PATH="$G4_FAKE_GH_BIN:$PATH" G4_GH_FIXTURE="$G4_FIXTURES/two-open.json" G4_GH_LOG="$G4_GH_LOG" \
    bash "$SCRIPTS/team-work.sh" g4-audit demo "$pack"
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "complete" ]
  [ "$(json_value "$output" entries[0].state)" = "blocked" ]
  [ "$(json_value "$output" entries[0].releasePredicate.status)" = "true" ]
  [ "$(json_value "$output" ready[0].source.number)" = "43" ]
}

@test "g4-audit: read-only predicate fixtures cover positive paths" {
  local pack="$BATS_TEST_TMPDIR/g4-predicate-pack.json" kind operation
  for kind in issue_closed pull_request_merged review_approved issue_comment_digest; do
    write_predicate_pack "$pack" "$kind"
    run_g4_audit "$G4_FIXTURES/predicate-positive.json" "$pack"
    [ "$status" -eq 0 ]
    [ "$(json_value "$output" classificationBasis.status)" = "complete" ]
    [ "$(json_value "$output" entries[0].state)" = "blocked" ]
    [ "$(json_value "$output" entries[0].releasePredicate.status)" = "true" ]
    [ "$(json_value "$output" ready[0].source.number)" = "43" ]
  done

  for operation in G4IssueState G4PullRequestState G4PullRequestReviews G4IssueComments; do
    grep -Fq "\"operation\":\"$operation\"" "$G4_GH_LOG"
  done
  ! grep -q '"kind":"write"' "$G4_GH_LOG"
}

@test "g4-audit: read-only predicate fixtures cover negative paths" {
  local pack="$BATS_TEST_TMPDIR/g4-predicate-pack.json" kind
  for kind in issue_closed pull_request_merged review_approved issue_comment_digest; do
    write_predicate_pack "$pack" "$kind"
    run_g4_audit "$G4_FIXTURES/predicate-negative.json" "$pack"
    [ "$status" -eq 0 ]
    [ "$(json_value "$output" classificationBasis.status)" = "unknown" ]
    [ "$(json_value "$output" entries[0].state)" = "blocked" ]
    [ "$(json_value "$output" entries[0].releasePredicate.status)" = "false" ]
    [ "$(json_value "$output" ready)" = "[]" ]
    grep -Fq '"code":"blocked_predicate_false"' <<<"$output"
  done
}

@test "g4-audit: predicate gh failure is unknown and never ready" {
  local pack="$BATS_TEST_TMPDIR/g4-predicate-pack.json"
  write_predicate_pack "$pack" issue_closed

  run_g4_audit "$G4_FIXTURES/predicate-gh-failure.json" "$pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "unknown" ]
  [ "$(json_value "$output" entries[0].state)" = "unknown" ]
  [ "$(json_value "$output" entries[0].releasePredicate.status)" = "unknown" ]
  [ "$(json_value "$output" ready)" = "[]" ]
  grep -Fq '"code":"blocked_predicate_unknown"' <<<"$output"
  ! grep -q '"kind":"write"' "$G4_GH_LOG"
}

@test "g4-audit: rejects invalid state and digest contracts before live reads" {
  local pack="$BATS_TEST_TMPDIR/g4-pack.json"

  write_g4_pack "$pack"
  mutate_g4_pack "$pack" ready-blocker
  run_g4_audit "$G4_FIXTURES/two-open.json" "$pack"
  [ "$status" -eq 2 ]
  grep -Fq 'ready entry must not have blocker' <<<"$output"

  write_g4_pack "$pack"
  mutate_g4_pack "$pack" blocked-no-predicate
  run_g4_audit "$G4_FIXTURES/two-open.json" "$pack"
  [ "$status" -eq 2 ]
  grep -Fq 'blocked entry requires blocker.reasonCode and blocker.releasePredicate' <<<"$output"

  write_g4_pack "$pack"
  mutate_g4_pack "$pack" bad-scope-digest
  run_g4_audit "$G4_FIXTURES/two-open.json" "$pack"
  [ "$status" -eq 2 ]
  grep -Fq 'scopes[0].basis contentDigest does not match' <<<"$output"
}

@test "g4-audit: rejects unknown owner kind, invalid refs, and quiescent entries" {
  bash "$SCRIPTS/join.sh" demo human codex /tmp/demo-human --role programmer --kind human >/dev/null
  local pack="$BATS_TEST_TMPDIR/g4-pack.json"

  G4_OWNER_SEAT=human write_g4_pack "$pack"
  run_g4_audit "$G4_FIXTURES/two-open.json" "$pack"
  [ "$status" -eq 2 ]
  grep -Fq 'owner must be an exact kind: seat' <<<"$output"

  write_g4_pack "$pack"
  mutate_g4_pack "$pack" invalid-ref
  run_g4_audit "$G4_FIXTURES/two-open.json" "$pack"
  [ "$status" -eq 2 ]
  grep -Fq 'scopes[0].basis.refs must be a non-empty array' <<<"$output"

  write_g4_pack "$pack"
  mutate_g4_pack "$pack" quiescent-entry
  run_g4_audit "$G4_FIXTURES/two-open.json" "$pack"
  [ "$status" -eq 2 ]
  [[ "$output" == *"entries[0].state must be ready, blocked, or unknown"* ]]
}

@test "g4-audit: is read-only and future G4 mutation commands stay unavailable" {
  local pack="$BATS_TEST_TMPDIR/g4-pack.json" before after
  write_g4_pack "$pack"
  before="$(shasum -a 256 "$TEST_SKILL_DIR/db/messages.db" | awk '{print $1}')"

  run_g4_audit "$G4_FIXTURES/two-open.json" "$pack"
  [ "$status" -eq 0 ]
  after="$(shasum -a 256 "$TEST_SKILL_DIR/db/messages.db" | awk '{print $1}')"
  [ "$before" = "$after" ]
  [ "$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" "SELECT COUNT(*) FROM sqlite_master WHERE name IN ('team_work_g4_current', 'team_work_g4_revisions');")" = "2" ]
  if grep -q '"kind":"write"' "$G4_GH_LOG"; then
    false
  fi

  for command in g4-transition g4-pull; do
    run bash "$SCRIPTS/team-work.sh" "$command" demo "$pack"
    [ "$status" -ne 0 ]
    grep -Fq 'unknown team-work command' <<<"$output"
  done
}
