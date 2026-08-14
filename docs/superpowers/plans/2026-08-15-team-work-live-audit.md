---
type: Plan
title: Team-work GitHub live audit and queue implementation plan
description: Issue #42 の GitHub live audit、queue / observe 判定の実装計画。
tags:
  - agmsg
  - team-work
  - github
  - audit
  - issue-42
timestamp: "2026-08-15T06:23:28+09:00"
---

# Team-work GitHub Live Audit and Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. User instruction for this issue prohibits spawning additional agents, so execute inline.

**Goal:** Add fail-closed, read-only GitHub live audit, `observe`, and `queue`
commands for a validated team-work contract pack.

**Architecture:** Keep `scripts/team-work.sh` as the Bash 3.2 command gate and
extract the shared #40 validation/digest functions into importable exports from
`scripts/lib/team-work.js`.  A new Node standard-library audit engine uses
`gh api graphql` and SQLite read-only queries, normalizes live evidence, and
prints canonical JSON for #43.  The existing `gh` guard receives a narrowly
parsed GraphQL-query exception rather than a general POST exception.

**Tech Stack:** Bash 3.2+, Node.js standard library (`child_process`, `crypto`,
`fs`), SQLite CLI JSON1, GitHub CLI GraphQL, Bats.

**Spec:** `docs/superpowers/specs/2026-08-15-team-work-live-audit-design.md`

## Global Constraints

- Preserve #40 validation and #41 mutation behavior byte-for-byte at their
  public interfaces.
- Do not initialize or write the local SQLite store on the audit path.
- Do not mutate GitHub, leases, messages, roster files, or agent processes.
- Fully paginate both Issue-side and PR-side closing connections; a partial
  response is `unknown`, never an empty relation.
- Treat a stale local row, relation incompleteness, and GitHub failure as
  `unknown` with an empty ready queue.
- Support macOS Bash 3.2, Linux, and Git Bash without npm dependencies.
- README must document all new commands, outputs, and no-side-effect limits.

---

## File structure

| File | Responsibility |
| --- | --- |
| `scripts/team-work.sh` | Parse `observe`, `queue`, and `audit`; derive but do not initialize the local DB path. |
| `scripts/lib/team-work.js` | Export existing contract-validation and canonical-digest helpers without changing its CLI behavior. |
| `scripts/lib/team-work-audit.js` | Fetch/validate paginated GitHub evidence, read local rows, classify, and emit canonical JSON. |
| `scripts/guards/gh-write-owner-guard.sh` | Classify the tightly constrained named GraphQL query path as read-only. |
| `tests/test_team_work_audit.bats` | Fixture-driven end-to-end audit, queue, pagination, stale, and source-error tests. |
| `tests/test_gh_write_owner_guard.bats` | Positive explicit-query and negative mutation guard regression tests. |
| `README.md` | User-facing syntax, output semantics, classification table, and safety boundary. |

### Task 1: Save the approved contract and plan

**Files:**

- Create: `docs/superpowers/specs/2026-08-15-team-work-live-audit-design.md`
- Create: `docs/superpowers/plans/2026-08-15-team-work-live-audit.md`

**Interfaces:**

- Consumes: GitHub Issue #42 acceptance criteria and #40/#41 public contracts.
- Produces: fixed command/output/unknown semantics before production code exists.

- [x] **Step 1: Define source and relation completeness**

Specify that every source Issue pages `closedByPullRequestsReferences`, every
related PR pages `closingIssuesReferences`, and any malformed page, cursor
loop, count mismatch, or one-sided relation is represented as unknown.

- [x] **Step 2: Define zero-ready classification and canonical output**

Document the precise conditions for `ready`, `fully_allocated`, `quiescent`,
and `unknown`, plus `sourceDigest` and `auditDigest` fields that use recursive
key sorting and preserve array order.

- [x] **Step 3: Commit the design documents**

```bash
git add docs/superpowers/specs/2026-08-15-team-work-live-audit-design.md \
  docs/superpowers/plans/2026-08-15-team-work-live-audit.md
git commit -m "docs: plan team-work live audit"
```

### Task 2: Lock the public audit and guard behavior with red tests

**Files:**

- Create: `tests/test_team_work_audit.bats`
- Modify: `tests/test_gh_write_owner_guard.bats`

**Interfaces:**

- Consumes: `tests/test_helper.bash`, the public `team-work.sh` CLI, and a
  PATH-injected fake `gh` process.
- Produces: fixture cases that exercise query names/cursors and inspect parsed
  canonical output instead of calling the network.

- [x] **Step 1: Create reusable fixture helpers and a fake `gh` executable**

The fake command must parse `gh api graphql` arguments, log the named operation
and cursor, and return fixture-selected JSON or a nonzero error.  It should
recognize `TeamWorkIssueClosingRelations` and
`TeamWorkPullRequestClosingRelations` by their query text.

- [x] **Step 2: Add a two-sided, two-page closing-relation test**

```bash
@test "team-work audit: follows both closing relation pages" {
  write_closing_pack "$pack" 777
  write_fixture "$fixture" complete_two_pages
  run env PATH="$fake_bin:$PATH" TEAM_WORK_GH_FIXTURE="$fixture" \
    bash "$SCRIPTS/team-work.sh" audit demo "$pack"
  [ "$status" -eq 0 ]
  [ "$(json_value "$output" classificationBasis.status)" = "quiescent" ]
  assert_logged_cursor issue-page-2
  assert_logged_cursor pr-page-2
}
```

- [x] **Step 3: Run the focused audit test and verify red**

Run: `BATS_SHELL=/bin/bash bats --print-output-on-failure tests/test_team_work_audit.bats --filter 'follows both closing relation pages'`

Expected: FAIL because `team-work.sh` rejects the `audit` command.

- [x] **Step 4: Add queue and unknown classification cases**

Add independent tests for an unleased open source (`ready`), a valid live lease
(`fully_allocated`), a closed complete source (`quiescent`), a fake `gh`
failure, a PR-side missing close, and a changed pack after an existing lease
(`local_state_stale`).  Each unknown case asserts `queue.ready == []`.

- [x] **Step 5: Add guard red tests**

```bash
@test "GHG-21: allows only explicit GraphQL queries" {
  run_guard api graphql -f 'query=query TeamWorkAudit { viewer { login } }'
  [ "$status" -eq 0 ]
  run_guard api graphql -f 'query=mutation Unsafe { updateIssue(input:{}) { clientMutationId } }'
  [ "$status" -ne 0 ]
}
```

The test also retains rejection of `gh api -f q=1 /repos/...`.

- [x] **Step 6: Commit the red suite**

```bash
git add tests/test_team_work_audit.bats tests/test_gh_write_owner_guard.bats
git commit -m "test: cover team-work live audit"
```

### Task 3: Implement the read-only audit engine

**Files:**

- Modify: `scripts/team-work.sh`
- Modify: `scripts/lib/team-work.js`
- Create: `scripts/lib/team-work-audit.js`
- Test: `tests/test_team_work_audit.bats`

**Interfaces:**

- `team-work.sh <observe|queue|audit> <team> <pack>` sends roster JSON on
  stdin and exports `AGMSG_TEAM_WORK_DB` without calling the initializer.
- `team-work-audit.js` exports/uses `runAudit(command, team, pack, roster)` and
  writes one canonical JSON object.
- Each GraphQL page is invoked as a named explicit query with owner, repository,
  number, and optional `after` cursor fields.

- [x] **Step 1: Export reusable validation and canonical helpers**

Guard `main()` in `team-work.js` with `require.main === module` and export
`SchemaError`, `canonicalJson`, `sha256Digest`, `envelopeDigest`, and
`validateContractPack`.  Run the existing contract/state Bats files immediately
afterward to prove no CLI regression.

- [x] **Step 2: Add audit command routing without initialization**

Extend the wrapper's argument case with exactly three positional arguments for
`observe|queue|audit`.  Source `lib/storage.sh`, call only `agmsg_db_path`,
export the result, get roster JSON, and invoke the new Node entrypoint.

- [x] **Step 3: Implement total-checked pagination**

Implement `fetchIssueClosingPages` and `fetchPullRequestClosingPages` around
the two named queries.  For each page, reject GraphQL errors, absent
`pageInfo`, duplicate node, cursor reuse, missing next cursor, or final
`nodes.length !== totalCount` as a normalized source violation.

- [x] **Step 4: Read and validate local current rows**

Use `sqlite3 -readonly` to obtain one compact JSON row per packed item.
Compare stored contract/envelope/source/owner fields to the supplied pack;
represent DB/open/read/parse failure or a mismatch as stable unknown evidence.

- [x] **Step 5: Build relation checks, classification, and canonical output**

Union packed/local PR links, validate both directions for every closing
relation, flag untracked Issue-side closers, then classify only if no
violations.  Canonically emit `observe` item summaries, `queue.ready`, and
`audit` relation checks/violations with `sourceDigest` and `auditDigest`.

- [x] **Step 6: Run the audit suite until green**

Run: `BATS_SHELL=/bin/bash bats --print-output-on-failure tests/test_team_work_audit.bats`

Expected: all pagination, queue, fully-allocated, quiescent, unknown, stale,
and no-side-effect tests pass.

- [x] **Step 7: Commit the audit implementation**

```bash
git add scripts/team-work.sh scripts/lib/team-work.js scripts/lib/team-work-audit.js
git commit -m "feat: add team-work live audit"
```

### Task 4: Permit only the required GraphQL read shape

**Files:**

- Modify: `scripts/guards/gh-write-owner-guard.sh`
- Test: `tests/test_gh_write_owner_guard.bats`

**Interfaces:**

- `is_read_only_operation` accepts only `gh api graphql` with one explicit
  `query=` field whose document starts with `query`; explicit methods/input and
  mutation/subscription documents remain rejected.

- [x] **Step 1: Parse endpoint and query field without weakening REST parsing**

Record the first API endpoint, count/retain `query=` field values across
`-f`, `-F`, `--field`, and `--raw-field` forms, and preserve existing malformed
flag handling.

- [x] **Step 2: Add the conservative GraphQL predicate**

Require endpoint `graphql`, no explicit method/input, exactly one query field,
an explicit leading `query` token, and no standalone `mutation` or
`subscription` token.  Fall back to the current REST GET/no-parameter rule in
all other cases.

- [x] **Step 3: Run the guard suite until green**

Run: `BATS_SHELL=/bin/bash bats --print-output-on-failure tests/test_gh_write_owner_guard.bats`

Expected: the explicit query reaches fake `gh`; the mutation and all existing
write paths remain blocked.

- [x] **Step 4: Commit the guard change**

```bash
git add scripts/guards/gh-write-owner-guard.sh tests/test_gh_write_owner_guard.bats
git commit -m "feat: allow guarded GraphQL queries"
```

### Task 5: Document and verify the public contract

**Files:**

- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-15-team-work-live-audit.md`

**Interfaces:**

- Documents command syntax, dependency requirements, output fields,
  classification meaning, and read-only boundary without requiring `docs/`.

- [x] **Step 1: Add README command examples and classification table**

Place the new command block after the local lease section.  State that the
commands require `gh`, `node`, and `sqlite3`; make GraphQL/SQLite reads only;
explain that unknown is a safety outcome and never means an empty queue.

- [x] **Step 2: Run focused, regression, static, and CI-equivalent checks**

```bash
BATS_SHELL=/bin/bash bats --print-output-on-failure \
  tests/test_team_work.bats tests/test_team_work_state.bats \
  tests/test_team_work_audit.bats tests/test_gh_write_owner_guard.bats
node --check scripts/lib/team-work.js
node --check scripts/lib/team-work-audit.js
shellcheck -s bash -e SC1091 scripts/team-work.sh scripts/guards/gh-write-owner-guard.sh
git diff --check origin/main...HEAD
```

Then run all Bats files through `.github/scripts/shard-tests.sh` in four
shards and record each exit status.

- [x] **Step 3: Record actual evidence and commit docs**

Replace this task's checklist with exact pass counts and commands, then:

```bash
git add README.md docs/superpowers/plans/2026-08-15-team-work-live-audit.md
git commit -m "docs: document team-work live audit"
```

## Verification record

- The initial focused audit test failed as intended with
  `Error: unknown team-work command: audit`; the initial guard test failed
  because GraphQL was not classified as a safe read.  A later `-X GET` test
  also failed before the guard was tightened, proving the explicit-method
  bypass was not accepted.
- `BATS_SHELL=/bin/bash /tmp/agmsg-bats113.iQXKkW/node_modules/.bin/bats --print-output-on-failure tests/test_team_work_audit.bats`
  passed 8/8 fixture-driven audit, pagination, status, stale-state, digest,
  and no-write tests.
- `BATS_SHELL=/bin/bash /tmp/agmsg-bats113.iQXKkW/node_modules/.bin/bats --print-output-on-failure tests/test_gh_write_owner_guard.bats`
  passed 21/21 guard tests.
- The combined #40/#41/#42/guard public suite passed 49/49 tests.
- `node --check scripts/lib/team-work.js`,
  `node --check scripts/lib/team-work-audit.js`, and
  `shellcheck -s bash -e SC1091 scripts/team-work.sh scripts/guards/gh-write-owner-guard.sh`
  passed; `git diff --check` passed.
- CI-equivalent `.github/scripts/shard-tests.sh <shard> 4 | xargs bats`
  completed with shard 1: 263/263, shard 2: 263/263, shard 3: 261/261, and
  shard 4: 263/263 tests passing (1,050 total).

## Execution handoff

The user has already selected inline, one-agent execution for this issue.  Run
Tasks 2–5 in this session, preserving the red/green evidence and without
spawning an agent.
