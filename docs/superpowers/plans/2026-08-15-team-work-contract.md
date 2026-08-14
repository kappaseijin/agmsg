---
type: Plan
title: Team-work contract validator implementation plan
description: Issue #40 の read-only work-state contract pack validator 実装計画。
tags:
  - agmsg
  - team-work
  - issue-40
timestamp: "2026-08-15T04:05:00+09:00"
---

# Team-work Contract Validator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a fail-closed, read-only `team-work` command that validates versioned work-state contract packs against the #38 roster JSON contract and emits deterministic SHA-256 checks.

**Architecture:** `scripts/team-work.sh` remains a Bash 3.2-compatible CLI wrapper and obtains the roster through the existing `team.sh --format json` contract. `scripts/lib/team-work.js` uses only Node.js standard library modules to parse, validate, canonicalize, and hash the contract pack without writing state. Bats tests exercise the public CLI with isolated team configs.

**Tech Stack:** Bash 3.2+, Node.js standard library (`fs`, `crypto`), SQLite-backed existing `team.sh` roster contract, Bats.

**Spec:** `docs/superpowers/specs/2026-08-15-team-work-contract-design.md`

## Global Constraints

- Support macOS Bash 3.2 and Git Bash; do not use Bash arrays, associative arrays, or `mapfile` in the wrapper.
- Use no npm package or external JSON parser; Node.js standard library is the only new runtime dependency.
- `validate` and `self-check` must not contact GitHub, send messages, mutate work state, or rewrite the input pack.
- Owner authorization is an exact `kind: "seat"` lookup in `team.sh --format json`; never infer a role from a name.
- Valid output is compact JSON on stdout; schema failures are `schema error: <reason>` on stderr and exit 2.
- README remains sufficient to run and understand the feature without consulting `docs/`.

---

## File structure

| File | Responsibility |
| --- | --- |
| `scripts/team-work.sh` | Stable user-facing CLI, argument validation, roster acquisition, Node launcher. |
| `scripts/lib/team-work.js` | JSON schema checks, canonical serializer, SHA-256 digest, compact result emission. |
| `tests/test_team_work.bats` | Public CLI behavior and negative controls using isolated roster fixtures. |
| `README.md` | Command syntax, contract pack v1 fields, success/error semantics, read-only limitation. |

### Task 1: Add public CLI tests and contract fixtures

**Files:**

- Create: `tests/test_team_work.bats`

**Interfaces:**

- Consumes: `tests/test_helper.bash` (`setup_test_env`, `teardown_test_env`) and the existing `join.sh`.
- Produces: executable acceptance cases for `bash "$SCRIPTS/team-work.sh" validate <team> <pack>` and `self-check`.

- [ ] **Step 1: Write the failing valid-pack test**

```bash
@test "team-work validate: accepts a seat-owned contract pack" {
  bash "$SCRIPTS/join.sh" demo alice codex /tmp/demo --role programmer --kind seat
  printf '%s\n' '{"schemaVersion":1,"team":"demo","workItems":[{"schemaVersion":1,"workItem":{"id":"issue:40","source":{"kind":"issue","repository":"kappaseijin/agmsg","number":40}},"ownerSeat":"alice","workKinds":["implementation"],"relations":[{"kind":"pull_request","repository":"kappaseijin/agmsg","number":46,"relation":"contributes"}],"revision":1,"classificationBasis":{"contentDigest":"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","refs":[{"kind":"issue","repository":"kappaseijin/agmsg","number":40}]},"writebackRequired":false}]}' > "$BATS_TEST_TMPDIR/valid.json"
  run bash "$SCRIPTS/team-work.sh" validate demo "$BATS_TEST_TMPDIR/valid.json"
  [ "$status" -eq 0 ]
  [ "$(printf '%s' "$output" | node -e 'let s=""; process.stdin.on("data", c => s += c).on("end", () => process.stdout.write(JSON.parse(s).valid ? "1" : "0"))')" = "1" ]
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `BATS_SHELL=/bin/bash bats tests/test_team_work.bats --filter 'accepts a seat-owned'`

Expected: FAIL because `scripts/team-work.sh` does not exist.

- [ ] **Step 3: Add negative-control tests before implementation**

```bash
@test "team-work validate: rejects an unknown owner seat" {
  write_pack_with ownerSeat '"missing-seat"'
  run bash "$SCRIPTS/team-work.sh" validate demo "$BATS_TEST_TMPDIR/pack.json"
  [ "$status" -eq 2 ]; [[ "$output" == *"owner seat does not exist"* ]]
}

@test "team-work validate: rejects a human or service owner" {
  bash "$SCRIPTS/join.sh" demo human codex /tmp/demo --role programmer --kind human
  write_pack_with ownerSeat '"human"'
  run bash "$SCRIPTS/team-work.sh" validate demo "$BATS_TEST_TMPDIR/pack.json"
  [ "$status" -eq 2 ]; [[ "$output" == *"owner must be a seat"* ]]
}

@test "team-work validate: rejects unknown and duplicate work kinds" {
  write_pack_with workKinds '["implementation","unknown"]'
  run bash "$SCRIPTS/team-work.sh" validate demo "$BATS_TEST_TMPDIR/pack.json"
  [ "$status" -eq 2 ]; [[ "$output" == *"unknown work kind"* ]]
  write_pack_with workKinds '["implementation","implementation"]'
  run bash "$SCRIPTS/team-work.sh" validate demo "$BATS_TEST_TMPDIR/pack.json"
  [ "$status" -eq 2 ]; [[ "$output" == *"workKinds must be unique"* ]]
}

@test "team-work validate: rejects an incomplete closes relation" {
  write_pack_with relations '[{"kind":"pull_request","repository":"kappaseijin/agmsg","number":46,"relation":"closes"}]'
  run bash "$SCRIPTS/team-work.sh" validate demo "$BATS_TEST_TMPDIR/pack.json"
  [ "$status" -eq 2 ]; [[ "$output" == *"closingIssue"* ]]
}

@test "team-work validate: rejects an old schema and malformed basis" {
  write_pack_with schemaVersion 0
  run bash "$SCRIPTS/team-work.sh" validate demo "$BATS_TEST_TMPDIR/pack.json"
  [ "$status" -eq 2 ]; [[ "$output" == *"schemaVersion"* ]]
  write_pack_with classificationBasis '{"contentDigest":"not-a-digest","refs":[]}'
  run bash "$SCRIPTS/team-work.sh" validate demo "$BATS_TEST_TMPDIR/pack.json"
  [ "$status" -eq 2 ]; [[ "$output" == *"classificationBasis"* ]]
}

@test "team-work self-check: ignores object key order and whitespace" {
  write_equivalent_packs "$BATS_TEST_TMPDIR/a.json" "$BATS_TEST_TMPDIR/b.json"
  run bash "$SCRIPTS/team-work.sh" self-check demo "$BATS_TEST_TMPDIR/a.json"; [ "$status" -eq 0 ]; digest_a="$(json_field "$output" contractDigest)"
  run bash "$SCRIPTS/team-work.sh" self-check demo "$BATS_TEST_TMPDIR/b.json"; [ "$status" -eq 0 ]; [ "$digest_a" = "$(json_field "$output" contractDigest)" ]
}

@test "team-work commands: leave pack and roster unchanged" {
  before_pack="$(shasum -a 256 "$BATS_TEST_TMPDIR/pack.json" | awk '{print $1}')"
  before_roster="$(shasum -a 256 "$TEST_SKILL_DIR/teams/demo/config.json" | awk '{print $1}')"
  run bash "$SCRIPTS/team-work.sh" self-check demo "$BATS_TEST_TMPDIR/pack.json"; [ "$status" -eq 0 ]
  [ "$before_pack" = "$(shasum -a 256 "$BATS_TEST_TMPDIR/pack.json" | awk '{print $1}')" ]
  [ "$before_roster" = "$(shasum -a 256 "$TEST_SKILL_DIR/teams/demo/config.json" | awk '{print $1}')" ]
}
```

Define the test helpers before the first test:

```bash
base_pack() {
  printf '%s\n' '{"schemaVersion":1,"team":"demo","workItems":[{"schemaVersion":1,"workItem":{"id":"issue:40","source":{"kind":"issue","repository":"kappaseijin/agmsg","number":40}},"ownerSeat":"alice","workKinds":["implementation"],"relations":[{"kind":"pull_request","repository":"kappaseijin/agmsg","number":46,"relation":"contributes"}],"revision":1,"classificationBasis":{"contentDigest":"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","refs":[{"kind":"issue","repository":"kappaseijin/agmsg","number":40}]},"writebackRequired":false}]}'
}

write_pack_with() {
  local field="$1" value="$2" path="$BATS_TEST_TMPDIR/pack.json"
  base_pack > "$path"
  FIELD="$field" VALUE="$value" PACK="$path" node <<'NODE'
const fs = require("fs");
const pack = JSON.parse(fs.readFileSync(process.env.PACK, "utf8"));
let target = pack.workItems[0];
const parts = process.env.FIELD.split(".");
while (parts.length > 1) target = target[parts.shift()];
target[parts[0]] = JSON.parse(process.env.VALUE);
fs.writeFileSync(process.env.PACK, JSON.stringify(pack));
NODE
}

write_equivalent_packs() {
  printf '%s\n' '{"schemaVersion":1,"team":"demo","workItems":[{"schemaVersion":1,"workItem":{"id":"issue:40","source":{"kind":"issue","repository":"kappaseijin/agmsg","number":40}},"ownerSeat":"alice","workKinds":["implementation"],"relations":[{"kind":"pull_request","repository":"kappaseijin/agmsg","number":46,"relation":"contributes"}],"revision":1,"classificationBasis":{"contentDigest":"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","refs":[{"kind":"issue","repository":"kappaseijin/agmsg","number":40}]},"writebackRequired":false}]}' > "$1"
  printf '%s\n' '{ "workItems" : [ { "writebackRequired" : false , "classificationBasis" : { "refs" : [ { "number" : 40 , "repository" : "kappaseijin/agmsg" , "kind" : "issue" } ] , "contentDigest" : "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef" } , "revision" : 1 , "relations" : [ { "relation" : "contributes" , "number" : 46 , "repository" : "kappaseijin/agmsg" , "kind" : "pull_request" } ] , "workKinds" : [ "implementation" ] , "ownerSeat" : "alice" , "workItem" : { "source" : { "number" : 40 , "repository" : "kappaseijin/agmsg" , "kind" : "issue" } , "id" : "issue:40" } , "schemaVersion" : 1 } ] , "team" : "demo" , "schemaVersion" : 1 }' > "$2"
}

json_field() {
  local json="$1" field="$2"
  printf '%s' "$json" | node -e 'let text=""; process.stdin.on("data", chunk => text += chunk).on("end", () => process.stdout.write(String(JSON.parse(text)[process.argv[1]])))' "$field"
}
```

For the closing negative control, set `relation: "closes"` without an exact `closingIssue` object. Compare `shasum -a 256` before and after the CLI calls for both relevant files.

- [ ] **Step 4: Run the full new test file to keep the red phase explicit**

Run: `BATS_SHELL=/bin/bash bats tests/test_team_work.bats --print-output-on-failure`

Expected: FAIL only because the new command is absent; the test file parses and all assertions are reachable.

- [ ] **Step 5: Commit the test scaffold**

```bash
git add tests/test_team_work.bats
git commit -m "test: cover team-work contract validation"
```

### Task 2: Implement schema, canonical JSON, and digest engine

**Files:**

- Create: `scripts/lib/team-work.js`

**Interfaces:**

- Consumes: command name, requested team, contract-pack path in `process.argv`; roster JSON on stdin.
- Produces: `validateContractPack(pack, roster, team)`, `canonicalJson(value)`, and `sha256Digest(value)` plus compact JSON output.

- [ ] **Step 1: Add the minimum Node entry point needed by the red tests**

```js
const fs = require("fs");
const crypto = require("crypto");

function schemaError(message) {
  process.stderr.write(`schema error: ${message}\n`);
  process.exitCode = 2;
}
```

Parse the pack with `JSON.parse(fs.readFileSync(packPath, "utf8"))` and parse stdin as the roster. Catch each parse error and call `schemaError` with a stable message.

- [ ] **Step 2: Implement recursive canonical JSON and SHA-256**

```js
function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalize(value[key])]));
  }
  return value;
}

function canonicalJson(value) {
  return JSON.stringify(canonicalize(value));
}

function sha256Digest(value) {
  return `sha256:${crypto.createHash("sha256").update(canonicalJson(value), "utf8").digest("hex")}`;
}
```

For per-envelope digests, clone the object and delete only `envelopeDigest` before hashing. Preserve array order.

- [ ] **Step 3: Implement the v1 pack and envelope checks**

Validate exact required types and values:

```text
pack.schemaVersion === 1
pack.team === requested team
pack.workItems is a non-empty array with unique workItem.id values
item.schemaVersion === 1
item.workItem.source.kind === "issue" with non-empty repository and positive number
item.ownerSeat names a roster member where kind === "seat"
item.workKinds is non-empty, unique, and drawn from implementation/writeback/inventory/closeout/reconciliation
item.revision is a positive integer
item.classificationBasis.contentDigest matches /^sha256:[0-9a-f]{64}$/
item.classificationBasis.refs is a non-empty array of issue/pull_request/commit/evidence references
item.writebackRequired is boolean
```

For each pull-request relation, require a positive `number`, non-empty `repository`, and `relation` in `contributes` or `closes`. A `closes` relation additionally requires `closingIssue.repository` and `closingIssue.number` to equal the work item's issue source exactly.

- [ ] **Step 4: Implement compact command results**

`validate` emits:

```js
emit({ schemaVersion: 1, valid: true, team, workItemCount: pack.workItems.length });
```

`self-check` emits the same validation metadata plus `contractDigest` and an `items` array of `{id, envelopeDigest, canonicalJson}`. Reject every unknown command with exit 1 before parsing files.

- [ ] **Step 5: Run the focused test file**

Run: `BATS_SHELL=/bin/bash bats tests/test_team_work.bats --print-output-on-failure`

Expected: PASS for valid pack, digest stability, and all invalid reasons.

- [ ] **Step 6: Commit the engine**

```bash
git add scripts/lib/team-work.js tests/test_team_work.bats
git commit -m "feat: validate team-work contract packs"
```

### Task 3: Add Bash CLI wrapper and public integration

**Files:**

- Create: `scripts/team-work.sh`
- Modify: `tests/test_team_work.bats`

**Interfaces:**

- Consumes: `team-work.sh <validate|self-check> <team> <contract-pack.json>`.
- Produces: roster JSON from `team.sh` on the Node engine's stdin and the engine's exit status unchanged.

- [ ] **Step 1: Keep the wrapper Bash 3.2-compatible**

```bash
#!/usr/bin/env bash
set -euo pipefail

COMMAND="${1:-}"
TEAM="${2:-}"
PACK="${3:-}"
[ "$#" -eq 3 ] || { echo "Usage: team-work.sh <validate|self-check> <team> <contract-pack.json>" >&2; exit 1; }
case "$COMMAND" in validate|self-check) ;; *) echo "Error: unknown team-work command: $COMMAND" >&2; exit 1;; esac
[ -f "$PACK" ] || { echo "Error: contract pack not found: $PACK" >&2; exit 1; }
```

Resolve `SCRIPT_DIR`, verify `node` is on PATH, read roster through `"$SCRIPT_DIR/team.sh" "$TEAM" --format json`, and pass it to `node "$SCRIPT_DIR/lib/team-work.js" "$COMMAND" "$TEAM" "$PACK"` via stdin. Do not create temporary files.

- [ ] **Step 2: Extend tests for wrapper failures**

Add assertions for missing arguments, unknown command, absent pack, missing Node (a PATH stub), and malformed/missing roster. Each must be nonzero and must not turn invalid input into a valid empty contract.

- [ ] **Step 3: Verify shell portability and integration**

Run:

```bash
/bin/bash -n scripts/team-work.sh
shellcheck -s bash scripts/team-work.sh
BATS_SHELL=/bin/bash bats tests/test_team_work.bats --print-output-on-failure
```

Expected: all commands pass with no Bash 3.2 syntax warning.

- [ ] **Step 4: Commit the public CLI**

```bash
git add scripts/team-work.sh tests/test_team_work.bats
git commit -m "feat: add team-work validation CLI"
```

### Task 4: Document and run regression verification

**Files:**

- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-15-team-work-contract.md`

**Interfaces:**

- Consumes: final public CLI and schema from Tasks 2–3.
- Produces: self-contained user instructions and a checked-off execution record.

- [ ] **Step 1: Add a README command section**

Document command syntax, the schema v1 required fields, canonical digest meaning, read-only/no-GitHub limitation, valid output, and error behavior. Include one runnable compact example using a `team.sh --format json` roster already present in the selected team.

- [ ] **Step 2: Run complete relevant verification**

Run:

```bash
BATS_SHELL=/bin/bash bats tests/test_team_work.bats --print-output-on-failure
BATS_SHELL=/bin/bash bats tests/test_team.bats --print-output-on-failure
bats tests
/bin/bash -n scripts/team-work.sh
shellcheck -s bash scripts/team-work.sh
git diff --check
```

Expected: new contract tests, roster regression tests, full Bats suite, syntax/static checks, and whitespace check all pass.

- [ ] **Step 3: Check off completed plan steps and commit docs**

```bash
git add README.md docs/superpowers/plans/2026-08-15-team-work-contract.md
git commit -m "docs: explain team-work contract checks"
```

## Plan self-review

| Spec requirement | Plan coverage |
| --- | --- |
| Versioned envelope and required fields | Task 2 schema checks and Task 1 fixtures. |
| Read-only validate/self-check | Task 3 wrapper plus Task 1 no-write assertion. |
| Canonical JSON and digest | Task 2 canonical serializer, digest engine, and Task 1 equivalent-input test. |
| Exact roster-seat verification | Task 2 seat lookup and Task 1 human/service/missing-owner cases. |
| Incomplete closing relation rejection | Task 2 closingIssue rule and Task 1 negative control. |
| User documentation and regression safety | Task 4 README and full-suite commands. |

The plan contains no external API, state mutation, or lease behavior; those boundaries stay assigned to #41–#43.
