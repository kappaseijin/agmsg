---
type: Plan
title: Team-work lease and revision-chain implementation plan
description: Issue #41 の work-item state mutation と lease 実装計画。
tags:
  - agmsg
  - team-work
  - lease
  - issue-41
timestamp: "2026-08-15T05:23:40+09:00"
---

# Team-work Lease and Revision-Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fail-closed, locally durable work-item leases and append-only mutation history to `team-work.sh` for Issue #41.

**Architecture:** Keep #40's Node standard-library contract validator as the command policy engine. The Bash wrapper supplies roster and storage information; the Node engine executes fully quoted `BEGIN IMMEDIATE` SQLite transactions that conditionally mutate `team_work_current`, whose triggers append `team_work_revisions` snapshots atomically.

**Tech Stack:** Bash 3.2+, Node.js standard library (`crypto`, `fs`, `child_process`), SQLite JSON1, Bats.

**Spec:** `docs/superpowers/specs/2026-08-15-team-work-lease-design.md`

## Global Constraints

- Preserve #40 `validate` and `self-check` behavior and their no-mutation guarantee.
- Use no npm package or Node database binding; invoke the existing `sqlite3` CLI.
- Support macOS Bash 3.2, Linux, and Git Bash.
- Authorize from exact roster metadata only: `kind: "seat"`, declared `ownerSeat`, and exact `role: "manager"`.
- Keep message transport tables (`message_claims`, `message_receipts`) untouched.
- Each rejected mutation must leave both latest state and revision history unchanged.
- README must fully document user-facing commands and limitations.

---

## File structure

| File | Responsibility |
| --- | --- |
| `scripts/internal/init-db.sh` | Create current-state, revision-history, and append-only trigger schema. |
| `scripts/team-work.sh` | Parse read-only versus mutating public command forms; obtain roster and DB path. |
| `scripts/lib/team-work.js` | Validate contract/actor/arguments, generate quoted conditional SQLite transactions, emit result JSON. |
| `tests/test_team_work_state.bats` | Public CLI concurrency, authority, expiry, revision, and non-interference coverage. |
| `README.md` | Mutation syntax, authority/lease semantics, outputs, and no-GitHub boundary. |

### Task 1: Lock the public mutation behavior with red tests

**Files:**

- Create: `tests/test_team_work_state.bats`

**Interfaces:**

- Consumes: `tests/test_helper.bash`, `join.sh`, and #40's contract pack form.
- Produces: public acceptance tests for all seven Issue #41 mutations.

- [ ] **Step 1: Build isolated roster and pack helpers**

Create a `setup()` that joins `owner` and `other` as `kind=seat`, and joins
`dispatch` as `kind=seat --role manager`. Define `write_pack <path>` with one
`issue:41` item owned by `owner`, then define `json_field` and `state_sql` test
helpers. Keep the source fixture `kappaseijin/agmsg#41` and the valid SHA-256
classification digest constant.

- [ ] **Step 2: Add a first failing claim/ack chain test**

```bash
@test "team-work state: owner claim and ack append revisions" {
  local pack="$BATS_TEST_TMPDIR/pack.json"
  write_pack "$pack"

  run bash "$SCRIPTS/team-work.sh" claim demo "$pack" issue:41 owner 60
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" revision)" = "1" ]
  [ "$(json_field "$output" state)" = "claimed" ]

  run bash "$SCRIPTS/team-work.sh" ack demo "$pack" issue:41 owner received
  [ "$status" -eq 0 ]
  [ "$(json_field "$output" revision)" = "2" ]
  [ "$(json_field "$output" state)" = "acknowledged" ]
  [ "$(state_sql 'SELECT count(*) FROM team_work_revisions WHERE team="demo" AND work_item_id="issue:41";')" = "2" ]
}
```

- [ ] **Step 3: Run only the first test and verify red**

Run: `BATS_SHELL=/bin/bash bats tests/test_team_work_state.bats --filter 'owner claim and ack'`

Expected: FAIL because `team-work.sh` does not accept `claim`.

- [ ] **Step 4: Add negative and concurrency cases before implementation**

Add tests that (a) launch `owner` and `dispatch` claims concurrently and count
one zero exit status, (b) reject `other` for ack/renew/release without changing
the row count or revision, (c) claim with TTL `0` then reclaim as `dispatch`,
and (d) assert each stored revision is contiguous with its current snapshot.

- [ ] **Step 5: Add state, PR-link, writeback, and isolation cases**

Assert `set-state ... in_progress`, one `link-pr ... 47 contributes`, and one
`writeback ... local-evidence` each increment revision. Assert a duplicate PR
link fails. Capture hashes/counts for the input pack, team config, `messages`,
`message_claims`, and `message_receipts`; assert the work-item mutation path
does not change any of them.

- [ ] **Step 6: Commit the red suite**

```bash
git add tests/test_team_work_state.bats
git commit -m "test: cover team-work leases"
```

### Task 2: Create durable latest-state and history schema

**Files:**

- Modify: `scripts/internal/init-db.sh`
- Test: `tests/test_team_work_state.bats`

**Interfaces:**

- Produces: `team_work_current` keyed by `(team, work_item_id)` and immutable
  `team_work_revisions` keyed by `(team, work_item_id, revision)`.
- Produces: insert/update triggers which serialize a resulting current snapshot
  to history in the same transaction.

- [ ] **Step 1: Add the new tables and indexes**

Add `team_work_current` with required digests, owner/source, positive revision,
state, nullable lease, JSON text arrays for PRs/writebacks, last action/actor,
and timestamps. Add `team_work_revisions` with `previous_revision`, action,
actor, and `snapshot_json`; add an expiry lookup index on current leases.

- [ ] **Step 2: Add append-only triggers**

For both insert and update of `team_work_current`, insert exactly one history
row. Use a static `json_object(...)` with the resulting `NEW` fields; update
history only by insertion and never expose a DELETE/UPDATE command.

- [ ] **Step 3: Verify the schema directly before command parsing exists**

Run the database initializer through the same mutation-path helper, then use
`sqlite3` to list the two new tables. The public claim test remains red until
Task 3 adds command parsing; this step does not treat a parser rejection as a
schema assertion.

- [ ] **Step 4: Commit the schema**

```bash
git add scripts/internal/init-db.sh
git commit -m "feat: persist team-work revisions"
```

### Task 3: Add command parsing, authority, and atomic conditional mutations

**Files:**

- Modify: `scripts/team-work.sh`
- Modify: `scripts/lib/team-work.js`
- Test: `tests/test_team_work_state.bats`

**Interfaces:**

- `team-work.sh` passes roster JSON on stdin and the initialized DB path through
  `AGMSG_TEAM_WORK_DB` only for mutation commands.
- `team-work.js` accepts the command syntax from the design, emits compact JSON,
  and exits 2 on policy rejection.

- [ ] **Step 1: Extend the Bash wrapper without changing read-only paths**

Keep `validate|self-check <team> <pack>` exactly as-is. For mutation commands,
validate the argument count, source `lib/storage.sh`, call
`agmsg_storage_ensure_initialized`, export `AGMSG_TEAM_WORK_DB`, then invoke
the Node engine after obtaining `team.sh <team> --format json`.

- [ ] **Step 2: Add actor and mutation argument validators in Node**

Add exact `seat` and `manager` lookup from the parsed roster. Reject missing
items, non-seat actors, actors who are neither the declared owner nor manager
for `claim`, malformed TTLs, unsupported states, non-positive PR numbers, and
empty evidence before generating SQL.

- [ ] **Step 3: Implement a quoted SQLite transaction runner**

Use `child_process.spawnSync('sqlite3', [db])` with a script containing
`BEGIN IMMEDIATE`, one `INSERT ... ON CONFLICT DO UPDATE ... WHERE ...`,
`SELECT changes()`, one compact `json_object` state result, and `COMMIT`.
Quote string literals by replacing `'` with `''`; interpolate only validated
integer values. Treat `changes() != 1` as `schema error: mutation rejected`.

- [ ] **Step 4: Implement claim and active-holder mutations**

`claim` inserts at the contract item's revision or takes only an absent/expired
lease while incrementing revision. All remaining commands require an exact
unexpired lease owner and matching stored contract/envelope digests. `ack` sets
`acknowledged`; `renew` refreshes expiry; `release` clears lease; `set-state`
sets an allowed state; `link-pr` appends one distinct local relation; and
`writeback` appends local evidence.

- [ ] **Step 5: Run the state test file until green**

Run: `BATS_SHELL=/bin/bash bats --print-output-on-failure tests/test_team_work_state.bats`

Expected: all authority, concurrency, expiry, history, and non-interference
cases pass.

- [ ] **Step 6: Commit the mutation engine**

```bash
git add scripts/team-work.sh scripts/lib/team-work.js
git commit -m "feat: add team-work leases"
```

### Task 4: Document and verify the public contract

**Files:**

- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-15-team-work-lease.md`

**Interfaces:**

- Documents every #41 command, authority rule, expiry behavior, and local-only
  boundary without requiring a reader to open `docs/`.

- [ ] **Step 1: Add README usage and safety rules**

Place mutation examples immediately after the existing read-only team-work
section. Explain exact owner/manager authorization, active-holder protection,
reclaim after expiry, revision history, and the fact that `link-pr`/
`writeback` record local state rather than writing to GitHub.

- [ ] **Step 2: Run regression and static checks**

Run:

```bash
BATS_SHELL=/bin/bash bats --print-output-on-failure tests/test_team_work_state.bats tests/test_team_work.bats tests/test_team.bats tests/test_claims.bats
shellcheck -s bash scripts/team-work.sh scripts/internal/init-db.sh
node --check scripts/lib/team-work.js
git diff --check origin/main...HEAD
```

Expected: all Bats cases pass, static syntax checks pass, and no whitespace
errors remain.

- [ ] **Step 3: Record actual verification evidence and commit docs**

Replace this plan's checklist with the exact commands and result counts, then:

```bash
git add README.md docs/superpowers/plans/2026-08-15-team-work-lease.md
git commit -m "docs: document team-work leases"
```
