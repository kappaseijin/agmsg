---
type: Plan
title: "team-work G3 reconciler・watchdog・dispatch gate 実装計画"
description: "Issue #43 を一つの PR で完了するための、test-first の実装手順。"
tags:
  - agmsg
  - team-work
  - reconciler
  - watchdog
  - dispatch
  - issue-43
timestamp: "2026-08-15T07:07:31+09:00"
---

# team-work G3 reconciler・watchdog・dispatch gate 実装計画

> **実行者向け:** ユーザー指定によりサブエージェントは起動しない。各 task をこの worktree で test-first に実行し、1 Issue / 1 PR を維持する。

**Goal:** live audit を消費する独立 reconciler、heartbeat watchdog、ACK 必須の local dispatch gate を提供する。

**Architecture:** `scripts/lib/team-work-reconciler.js` を G3 の単一 reader/writer にする。G2 audit は export 可能にして共有し、G3 の `dispatching` は専用 SQLite ledger に保存する。同 epoch ACK 時だけ G2 work-item lease を `claimed` にする。

**Tech Stack:** Bash wrapper、Node.js standard library、SQLite CLI、既存 `gh` GraphQL read、Bats。

**Spec:** `docs/superpowers/specs/2026-08-15-team-work-reconciler-dispatch-design.md`

## Global Constraints

- PR は Issue #43 だけを closes し、親 #9 は子 Issue がすべて closed になるまで閉じない。
- GitHub Issue/PR mutation、herdr 操作、message send、agent spawn は実装しない。
- `kind: "seat"`、live delivery、allowlist の全条件が揃わない target を dispatch しない。
- `TEAM_WORK_DISPATCH_ALLOWLIST` は JSON string array とし、不明/不正は deny する。
- `reconcile` / `watchdog` は heartbeat file 以外を変更しない。
- README 単体で command、環境変数、出力、制約を説明する。

---

### Task 1: dispatch ledger schema と immutable history

**Files:**

- Modify: `scripts/internal/init-db.sh`
- Create: `tests/test_team_work_reconciler.bats`

**Interfaces:**

- Produces SQLite tables `team_work_dispatch_current` and `team_work_dispatch_revisions`.
- The current row has `state`, `lease_epoch`, `queue_digest`, `delivery_evidence_json`, `ack_evidence`, and `lease_expires_at`.

- [x] **Step 1: Write the failing schema/history tests**

```bash
sqlite3 "$TEST_SKILL_DIR/db/messages.db" \
  "SELECT name FROM sqlite_master WHERE type='table' AND name='team_work_dispatch_current';"
```

Assert that the table and immutable revision trigger exist after test setup, and that a direct update/delete of a history row fails.

- [x] **Step 2: Run the focused test to verify RED**

Run: `bats tests/test_team_work_reconciler.bats`

Expected: FAIL because `team_work_dispatch_current` does not exist.

- [x] **Step 3: Add the minimal schema and triggers**

Add `CREATE TABLE IF NOT EXISTS` definitions and insert/update history triggers in `scripts/internal/init-db.sh`. Keep the existing G2 tables and triggers unchanged. Store snapshots with `json_object` and reject history update/delete with SQLite triggers.

- [x] **Step 4: Run the focused test to verify GREEN**

Run: `bats tests/test_team_work_reconciler.bats`

Expected: PASS for the schema/history assertions.

- [x] **Step 5: Commit the focused deliverable**

```bash
git add scripts/internal/init-db.sh tests/test_team_work_reconciler.bats
git commit -m "feat: add dispatch ledger schema"
```

### Task 2: make G2 audit dispatch-aware without altering its read-only contract

**Files:**

- Modify: `scripts/lib/team-work-audit.js`
- Modify: `tests/test_team_work_audit.bats`
- Test: `tests/test_team_work_reconciler.bats`

**Interfaces:**

- Exports `runAudit(command, team, pack, roster)` and helpers needed by G3.
- A valid active dispatch row contributes `localState.status: "active"` and a `dispatchState` of `"dispatching"` or `"claimed"`.
- Missing G3 tables remain equivalent to no dispatch rows for old local stores.

- [x] **Step 1: Write failing audit tests**

Create one valid `dispatching` row, run `team-work.sh queue`, and assert `fully_allocated` instead of `ready`. Add a legacy-store fixture where the G3 table is absent and assert the existing ready result remains unchanged.

- [x] **Step 2: Run RED**

Run: `bats tests/test_team_work_audit.bats tests/test_team_work_reconciler.bats`

Expected: FAIL because audit ignores the dispatch row or cannot be imported.

- [x] **Step 3: Export and extend the reader**

Guard `main()` with `require.main === module`, export `runAudit`, and read the optional dispatch table through `sqlite3 -readonly`. Validate contract/envelope/owner/epoch fields; mark malformed rows stale rather than treating them as absent.

- [x] **Step 4: Run GREEN**

Run: `bats tests/test_team_work_audit.bats tests/test_team_work_reconciler.bats`

Expected: PASS, including all existing audit cases.

- [x] **Step 5: Commit**

```bash
git add scripts/lib/team-work-audit.js tests/test_team_work_audit.bats tests/test_team_work_reconciler.bats
git commit -m "feat: include dispatch state in team-work audit"
```

### Task 3: implement the read-only reconciler and watchdog

**Files:**

- Create: `scripts/lib/team-work-reconciler.js`
- Modify: `scripts/team-work.sh`
- Test: `tests/test_team_work_reconciler.bats`

**Interfaces:**

- `reconcile <team> <pack> [heartbeat-path]` emits canonical JSON with `findings`, `remediation`, `sourceDigest`, and `reconcileDigest`.
- `watchdog <team> <pack> <heartbeat-path> [stale-seconds]` emits canonical JSON with `status` of `healthy`, `stale`, or `unknown`.

- [x] **Step 1: Write failing fixture tests**

Use fake GitHub data and temporary SQLite rows to assert each of `expired_lease`, `upstream_closed`, `orphan_ready`, `writeback_required`, and `stale_state`. Add stale and quiescent heartbeat fixtures and assert watchdog does not change the database hash.

- [x] **Step 2: Run RED**

Run: `bats tests/test_team_work_reconciler.bats`

Expected: FAIL with `unknown team-work command: reconcile` or missing module.

- [x] **Step 3: Implement G3 readers**

Reuse `runAudit`, derive seat capability only from `delivery.sh status <type> <project> --format json`, and return remediation objects in stable order. Write the optional heartbeat with temp-file plus rename; watchdog only reads and validates it.

- [x] **Step 4: Run GREEN**

Run: `bats tests/test_team_work_reconciler.bats`

Expected: PASS for all finding, watchdog, canonical-output, and no-side-effect assertions.

- [x] **Step 5: Commit**

```bash
git add scripts/lib/team-work-reconciler.js scripts/team-work.sh tests/test_team_work_reconciler.bats
git commit -m "feat: add team-work reconciler watchdog"
```

### Task 4: implement fail-closed dispatch and same-epoch ACK

**Files:**

- Modify: `scripts/lib/team-work-reconciler.js`
- Modify: `scripts/team-work.sh`
- Test: `tests/test_team_work_reconciler.bats`

**Interfaces:**

- `dispatch <team> <pack> <work-item-id> <manager-seat> [ack-ttl]` creates only an eligible `dispatching` ledger entry.
- `dispatch-ack <team> <pack> <work-item-id> <owner-seat> <lease-epoch> [evidence]` transitions the exact live epoch to `claimed` and creates the G2 lease atomically.

- [x] **Step 1: Write failing dispatch tests**

Assert all negative paths: missing/malformed allowlist, non-manager actor, closed/non-seat target, `deliverable: false`, `deliverable: "unknown"`, no ready item, duplicate dispatch, wrong epoch, and expired ACK. Assert successful dispatch does not call `send.sh`/herdr or create a G2 claim before ACK.

- [x] **Step 2: Run RED**

Run: `bats tests/test_team_work_reconciler.bats`

Expected: FAIL because the `dispatch` and `dispatch-ack` commands do not exist.

- [x] **Step 3: Implement guarded transaction paths**

Parse the JSON allowlist strictly, require an exact manager seat, run a fresh G2 queue audit, and persist queue/lease/delivery evidence in the dispatch ledger. On ACK, require exact owner and epoch plus fresh live delivery, then use one SQLite transaction to create the `claimed` G2 lease and append the dispatch ACK revision.

- [x] **Step 4: Run GREEN**

Run: `bats tests/test_team_work_reconciler.bats tests/test_team_work_state.bats tests/test_team_work_audit.bats`

Expected: PASS. Verify that the success case records `dispatching` before ACK and `claimed` only after the exact ACK.

- [x] **Step 5: Commit**

```bash
git add scripts/lib/team-work-reconciler.js scripts/team-work.sh tests/test_team_work_reconciler.bats
git commit -m "feat: gate dispatch claims on acknowledgement"
```

### Task 5: document and verify the complete feature

**Files:**

- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-15-team-work-reconciler-dispatch.md`
- Test: `tests/test_team_work_reconciler.bats`

**Interfaces:**

- README contains the executable syntax, `TEAM_WORK_DISPATCH_ALLOWLIST` schema, JSON output semantics, heartbeat behavior, and no-spawn/no-GitHub-mutation limits.

- [x] **Step 1: Write failing documentation-surface tests**

Add focused assertions for the wrapper usage errors and command result fields that users need to consume.

- [x] **Step 2: Run RED**

Run: `bats tests/test_team_work_reconciler.bats`

Expected: FAIL until the documented wrapper contract exists.

- [x] **Step 3: Update README and complete plan checkboxes**

Describe every G3 command, default ACK timeout, heartbeat file lifecycle, output findings, allowlist JSON, explicit ACK requirement, and safe remediation behavior. Mark only verified plan steps complete.

- [x] **Step 4: Run complete verification**

Run:

```bash
bats tests/test_team_work.bats tests/test_team_work_state.bats tests/test_team_work_audit.bats tests/test_team_work_reconciler.bats tests/test_gh_write_owner_guard.bats
node --check scripts/lib/team-work.js
node --check scripts/lib/team-work-audit.js
node --check scripts/lib/team-work-reconciler.js
shellcheck -s bash -e SC1091 scripts/team-work.sh scripts/internal/init-db.sh
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/superpowers/plans/2026-08-15-team-work-reconciler-dispatch.md tests/test_team_work_reconciler.bats
git commit -m "docs: document team-work reconciler"
```

## Plan self-review

- [x] All Issue #43 acceptance conditions map to Tasks 2–4.
- [x] No task introduces GitHub mutation, herdr automation, agent spawn, or a second queue parser.
- [x] TDD RED/GREEN steps and exact focused commands are present for every production change.
- [x] The dispatch ledger keeps the existing G2 state machine backward compatible.
