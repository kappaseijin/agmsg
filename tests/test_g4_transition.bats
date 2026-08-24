#!/usr/bin/env bats

load test_helper
load helpers/g4-fixtures

setup() {
  setup_test_env
  bash "$SCRIPTS/join.sh" demo owner codex /tmp/demo-owner --role programmer --kind seat >/dev/null
  bash "$SCRIPTS/join.sh" demo manager codex /tmp/demo-manager --role manager --kind seat >/dev/null
  setup_g4_fixture
}

teardown() {
  teardown_test_env
}

run_g4_bootstrap() {
  local fixture="$1" pack="$2" manager="${3:-manager}" evidence="${4:-https://example.test/g4-bootstrap}"
  run env PATH="$G4_FAKE_GH_BIN:$PATH" G4_GH_FIXTURE="$fixture" G4_GH_LOG="$G4_GH_LOG" \
    bash "$SCRIPTS/team-work.sh" g4-bootstrap demo "$pack" "$manager" "$evidence"
}

run_g4_transition() {
  local fixture="$1" pack="$2" repository="${3:-kappaseijin/example}" number="${4:-42}" \
    expected_revision="${5:-1}" manager="${6:-manager}" evidence="${7-https://example.test/g4-transition}"
  run env PATH="$G4_FAKE_GH_BIN:$PATH" G4_GH_FIXTURE="$fixture" G4_GH_LOG="$G4_GH_LOG" \
    bash "$SCRIPTS/team-work.sh" g4-transition demo "$pack" "$repository" "$number" "$expected_revision" "$manager" "$evidence"
}

write_transition_pack() {
  local path="$1" state="${2:-ready}" revision="${3:-2}"
  G4_PACK_PATH="$path" G4_TRANSITION_STATE="$state" G4_TRANSITION_REVISION="$revision" node <<'NODE'
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
  return "sha256:" + crypto.createHash("sha256")
    .update(JSON.stringify(canonicalize(value)), "utf8").digest("hex");
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

const entry = pack.entries[0];
entry.state = process.env.G4_TRANSITION_STATE;
entry.revision = Number(process.env.G4_TRANSITION_REVISION);
if (entry.state === "ready") {
  delete entry.blocker;
} else if (entry.state === "blocked") {
  entry.blocker = {
    reasonCode: "issue_closed_gate",
    releasePredicate: {kind: "issue_closed", repository: "kappaseijin/example", number: 42},
  };
} else if (entry.state === "unknown") {
  delete entry.blocker;
}
refreshEntry(entry);
fs.writeFileSync(process.env.G4_PACK_PATH, JSON.stringify(pack));
NODE
}

mutate_transition_pack() {
  local path="$1" mutation="$2"
  G4_PACK_PATH="$path" G4_TRANSITION_MUTATION="$mutation" node <<'NODE'
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
  return "sha256:" + crypto.createHash("sha256")
    .update(JSON.stringify(canonicalize(value)), "utf8").digest("hex");
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

const entry = pack.entries[0];
switch (process.env.G4_TRANSITION_MUTATION) {
  case "owner":
    entry.ownerSeat = "manager";
    break;
  case "work-kinds":
    entry.workKinds = ["writeback"];
    break;
  case "basis":
    entry.basis.refs[0].commit = "fedcba9876543210fedcba9876543210fedcba98";
    break;
  case "source":
    entry.source.number = 99;
    entry.basis.refs[0].number = 99;
    break;
  default:
    throw new Error("unknown transition mutation");
}
refreshEntry(entry);
fs.writeFileSync(process.env.G4_PACK_PATH, JSON.stringify(pack));
NODE
}

seed_unknown_current() {
  sqlite3 "$DBPATH" <<'SQL'
INSERT INTO team_work_g4_current(
  team, source_repository, source_number, state, owner_seat,
  work_kinds_json, revision, pack_digest, entry_digest, coverage_digest,
  audit_digest, basis_json, blocker_json, evidence, last_action, last_actor,
  created_at, updated_at
) VALUES (
  'demo', 'kappaseijin/example', 42, 'unknown', 'owner',
  json('["implementation"]'), 1,
  'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
  'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
  'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
  json('{"contentDigest":"sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","refs":[]}'),
  NULL, 'seeded unknown', 'test-seed', 'manager', 100, 100
);
SQL
}

@test "g4-transition: exact manager records true blocked-to-ready revision" {
  local bootstrap_pack="$BATS_TEST_TMPDIR/bootstrap.json" transition_pack="$BATS_TEST_TMPDIR/transition.json"
  write_predicate_pack "$bootstrap_pack" issue_closed
  run_g4_bootstrap "$G4_FIXTURES/predicate-positive.json" "$bootstrap_pack"
  [ "$status" -eq 0 ]
  cp "$bootstrap_pack" "$transition_pack"
  write_transition_pack "$transition_pack" ready 2

  run_g4_transition "$G4_FIXTURES/predicate-positive.json" "$transition_pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" transitioned)" = "true" ]
  [ "$(json_value "$output" state)" = "ready" ]
  [ "$(json_value "$output" revision)" = "2" ]
  [ "$(json_value "$output" previousRevision)" = "1" ]
  [ "$(json_value "$output" source.number)" = "42" ]
  [ "$(json_value "$output" managerSeat)" = "manager" ]
  [ "$(json_value "$output" evidence)" = "https://example.test/g4-transition" ]
  [ "$(json_value "$output" remediation)" = "[]" ]
  printf '%s\n' "$(json_value "$output" packDigest)" | grep -Eq '^sha256:[0-9a-f]{64}$'
  printf '%s\n' "$(json_value "$output" entryDigest)" | grep -Eq '^sha256:[0-9a-f]{64}$'
  printf '%s\n' "$(json_value "$output" coverageDigest)" | grep -Eq '^sha256:[0-9a-f]{64}$'
  printf '%s\n' "$(json_value "$output" auditDigest)" | grep -Eq '^sha256:[0-9a-f]{64}$'
  [ "$(sqlite3 "$DBPATH" "SELECT state || ':' || revision || ':' || last_action || ':' || last_actor || ':' || evidence FROM team_work_g4_current WHERE team = 'demo' AND source_number = 42;")" = "ready:2:g4-transition:manager:https://example.test/g4-transition" ]
  [ "$(sqlite3 "$DBPATH" "SELECT blocker_json IS NULL FROM team_work_g4_current WHERE team = 'demo' AND source_number = 42;")" = "1" ]
  [ "$(sqlite3 "$DBPATH" "SELECT COUNT(*) FROM team_work_g4_revisions WHERE source_number = 42;")" = "2" ]
  [ "$(sqlite3 "$DBPATH" "SELECT previous_revision || ':' || action || ':' || actor FROM team_work_g4_revisions WHERE source_number = 42 AND revision = 2;")" = "1:g4-transition:manager" ]
  assert_canonical_json "$output"
}

@test "g4-transition: pm seat is accepted as the manager role alias" {
  local bootstrap_pack="$BATS_TEST_TMPDIR/bootstrap.json" transition_pack="$BATS_TEST_TMPDIR/transition.json"
  write_predicate_pack "$bootstrap_pack" issue_closed
  run_g4_bootstrap "$G4_FIXTURES/predicate-positive.json" "$bootstrap_pack"
  [ "$status" -eq 0 ]
  bash "$SCRIPTS/join.sh" demo pm codex /tmp/demo-pm --role pm --kind seat >/dev/null
  cp "$bootstrap_pack" "$transition_pack"
  write_transition_pack "$transition_pack" ready 2

  run_g4_transition "$G4_FIXTURES/predicate-positive.json" "$transition_pack" kappaseijin/example 42 1 pm

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" transitioned)" = "true" ]
  [ "$(json_value "$output" managerSeat)" = "pm" ]
  [ "$(json_value "$output" remediation)" = "[]" ]
}

@test "g4-transition: false and unavailable predicates reject without mutation" {
  local bootstrap_pack="$BATS_TEST_TMPDIR/bootstrap.json" transition_pack="$BATS_TEST_TMPDIR/transition.json" fixture before
  write_predicate_pack "$bootstrap_pack" issue_closed
  run_g4_bootstrap "$G4_FIXTURES/predicate-positive.json" "$bootstrap_pack"
  [ "$status" -eq 0 ]
  cp "$bootstrap_pack" "$transition_pack"
  write_transition_pack "$transition_pack" ready 2

  for fixture in predicate-negative.json predicate-gh-failure.json; do
    before="$(shasum -a 256 "$DBPATH" | awk '{print $1}')"
    : > "$G4_GH_LOG"
    run_g4_transition "$G4_FIXTURES/$fixture" "$transition_pack"
    [ "$status" -eq 0 ]
    [ "$(json_value "$output" transitioned)" = "false" ]
    [ "$(json_value "$output" remediation[0].code)" = "predicate_not_true" ]
    [ "$(shasum -a 256 "$DBPATH" | awk '{print $1}')" = "$before" ]
    ! grep -q '"kind":"write"' "$G4_GH_LOG"
  done
}

@test "g4-transition: stale and skipped revisions reject without mutation" {
  local bootstrap_pack="$BATS_TEST_TMPDIR/bootstrap.json" transition_pack="$BATS_TEST_TMPDIR/transition.json" before
  write_predicate_pack "$bootstrap_pack" issue_closed
  run_g4_bootstrap "$G4_FIXTURES/predicate-positive.json" "$bootstrap_pack"
  [ "$status" -eq 0 ]
  cp "$bootstrap_pack" "$transition_pack"
  write_transition_pack "$transition_pack" ready 2
  before="$(shasum -a 256 "$DBPATH" | awk '{print $1}')"

  run_g4_transition "$G4_FIXTURES/predicate-positive.json" "$transition_pack" kappaseijin/example 42 2
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" transitioned)" = "false" ]
  [ "$(json_value "$output" remediation[0].code)" = "revision_mismatch" ]
  [ "$(shasum -a 256 "$DBPATH" | awk '{print $1}')" = "$before" ]

  write_transition_pack "$transition_pack" ready 3
  run_g4_transition "$G4_FIXTURES/predicate-positive.json" "$transition_pack" kappaseijin/example 42 1
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" transitioned)" = "false" ]
  [ "$(json_value "$output" remediation[0].code)" = "revision_mismatch" ]
  [ "$(shasum -a 256 "$DBPATH" | awk '{print $1}')" = "$before" ]
}

@test "g4-transition: only an exact manager with evidence and target source can act" {
  local bootstrap_pack="$BATS_TEST_TMPDIR/bootstrap.json" transition_pack="$BATS_TEST_TMPDIR/transition.json" seat before
  write_predicate_pack "$bootstrap_pack" issue_closed
  run_g4_bootstrap "$G4_FIXTURES/predicate-positive.json" "$bootstrap_pack"
  [ "$status" -eq 0 ]
  cp "$bootstrap_pack" "$transition_pack"
  write_transition_pack "$transition_pack" ready 2
  bash "$SCRIPTS/join.sh" demo human codex /tmp/demo-human --role manager --kind human >/dev/null

  for seat in owner human missing; do
    : > "$G4_GH_LOG"
    before="$(shasum -a 256 "$DBPATH" | awk '{print $1}')"
    run_g4_transition "$G4_FIXTURES/predicate-positive.json" "$transition_pack" kappaseijin/example 42 1 "$seat"
    [ "$status" -eq 0 ]
    [ "$(json_value "$output" transitioned)" = "false" ]
    [ "$(json_value "$output" remediation[0].code)" = "invalid_manager" ]
    [ "$(wc -l < "$G4_GH_LOG" | tr -d ' ')" -eq 0 ]
    [ "$(shasum -a 256 "$DBPATH" | awk '{print $1}')" = "$before" ]
  done

  before="$(shasum -a 256 "$DBPATH" | awk '{print $1}')"
  run_g4_transition "$G4_FIXTURES/predicate-positive.json" "$transition_pack" kappaseijin/example 42 1 manager ""
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" transitioned)" = "false" ]
  [ "$(json_value "$output" remediation[0].code)" = "invalid_input" ]
  [ "$(shasum -a 256 "$DBPATH" | awk '{print $1}')" = "$before" ]

  for target in "other/repo 42" "kappaseijin/example 99"; do
    before="$(shasum -a 256 "$DBPATH" | awk '{print $1}')"
    # shellcheck disable=SC2086
    run_g4_transition "$G4_FIXTURES/predicate-positive.json" "$transition_pack" $target 1 manager
    [ "$status" -eq 0 ]
    [ "$(json_value "$output" transitioned)" = "false" ]
    [ "$(shasum -a 256 "$DBPATH" | awk '{print $1}')" = "$before" ]
  done
}

@test "g4-transition: owner, workKinds, basis, and source changes reject" {
  local bootstrap_pack="$BATS_TEST_TMPDIR/bootstrap.json" transition_pack="$BATS_TEST_TMPDIR/transition.json" mutation before
  write_predicate_pack "$bootstrap_pack" issue_closed
  run_g4_bootstrap "$G4_FIXTURES/predicate-positive.json" "$bootstrap_pack"
  [ "$status" -eq 0 ]
  for mutation in owner work-kinds basis source; do
    cp "$bootstrap_pack" "$transition_pack"
    write_transition_pack "$transition_pack" ready 2
    mutate_transition_pack "$transition_pack" "$mutation"
    before="$(shasum -a 256 "$DBPATH" | awk '{print $1}')"
    run_g4_transition "$G4_FIXTURES/predicate-positive.json" "$transition_pack"
    [ "$status" -eq 0 ]
    [ "$(json_value "$output" transitioned)" = "false" ]
    [ "$(shasum -a 256 "$DBPATH" | awk '{print $1}')" = "$before" ]
  done
}

@test "g4-transition: ready-to-blocked is rejected" {
  local bootstrap_pack="$BATS_TEST_TMPDIR/bootstrap.json" transition_pack="$BATS_TEST_TMPDIR/transition.json" before

  write_g4_pack "$bootstrap_pack" two
  run_g4_bootstrap "$G4_FIXTURES/two-open.json" "$bootstrap_pack"
  [ "$status" -eq 0 ]
  cp "$bootstrap_pack" "$transition_pack"
  write_transition_pack "$transition_pack" blocked 2
  before="$(shasum -a 256 "$DBPATH" | awk '{print $1}')"
  run_g4_transition "$G4_FIXTURES/predicate-positive.json" "$transition_pack"
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" transitioned)" = "false" ]
  [ "$(shasum -a 256 "$DBPATH" | awk '{print $1}')" = "$before" ]
}

@test "g4-transition: blocked-to-unknown is rejected" {
  local bootstrap_pack="$BATS_TEST_TMPDIR/bootstrap.json" transition_pack="$BATS_TEST_TMPDIR/transition.json" before

  write_predicate_pack "$bootstrap_pack" issue_closed
  run_g4_bootstrap "$G4_FIXTURES/predicate-positive.json" "$bootstrap_pack"
  [ "$status" -eq 0 ]
  cp "$bootstrap_pack" "$transition_pack"
  write_transition_pack "$transition_pack" unknown 2
  before="$(shasum -a 256 "$DBPATH" | awk '{print $1}')"
  run_g4_transition "$G4_FIXTURES/predicate-positive.json" "$transition_pack"
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" transitioned)" = "false" ]
  [ "$(shasum -a 256 "$DBPATH" | awk '{print $1}')" = "$before" ]
}

@test "g4-transition: unknown-to-ready is rejected" {
  local transition_pack="$BATS_TEST_TMPDIR/transition.json" before

  seed_unknown_current
  write_predicate_pack "$transition_pack" issue_closed
  write_transition_pack "$transition_pack" ready 2
  before="$(shasum -a 256 "$DBPATH" | awk '{print $1}')"
  run_g4_transition "$G4_FIXTURES/predicate-positive.json" "$transition_pack"
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" transitioned)" = "false" ]
  [ "$(shasum -a 256 "$DBPATH" | awk '{print $1}')" = "$before" ]
}

@test "g4-transition: fake GitHub records reads only" {
  local bootstrap_pack="$BATS_TEST_TMPDIR/bootstrap.json" transition_pack="$BATS_TEST_TMPDIR/transition.json"
  write_predicate_pack "$bootstrap_pack" issue_closed
  run_g4_bootstrap "$G4_FIXTURES/predicate-positive.json" "$bootstrap_pack"
  [ "$status" -eq 0 ]
  cp "$bootstrap_pack" "$transition_pack"
  write_transition_pack "$transition_pack" ready 2

  run_g4_transition "$G4_FIXTURES/predicate-positive.json" "$transition_pack"

  [ "$status" -eq 0 ]
  [ "$(json_value "$output" transitioned)" = "true" ]
  grep -q '"kind":"read"' "$G4_GH_LOG"
  ! grep -q '"kind":"write"' "$G4_GH_LOG"
}
