---
type: Plan
title: "Issue #91 lock retake test isolation"
description: "Synchronize the retake-lock regression test with the starter's lock release so macOS scheduling cannot make mkdir fail before the assertion."
tags:
  - agmsg
  - tests
  - issue-91
timestamp: "2026-08-20T14:34:44+09:00"
---

# Issue #91 Lock Retake Test Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `tests/test_remote_engine_start_refusal.bats` wait for the starter to release its registry lock before the test deliberately takes that lock, eliminating the macOS scheduling race reported in Issue #91.

**Architecture:** Keep production locking semantics unchanged. The test already creates an isolated `TEST_SKILL_DIR`; the failure is inside one test because the engine writes its pidfile before the parent reaches its lock-release line. Reuse the existing bounded `wait_for_missing` helper, then retain the test's deliberate `mkdir` holder and cleanup assertions.

**Tech Stack:** Bash, Bats, existing test helper polling utilities.

**Spec:** [Issue #91](https://github.com/kappaseijin/agmsg/issues/91)

## Global Constraints

- Do not change `scripts/remote.sh` or the registry-lock implementation; this issue is test synchronization, not a production lock bug.
- Preserve the test's purpose: another holder must own `.config.lock` while the timed-out starter attempts cleanup.
- Use a bounded condition wait; never add an unbounded loop or fixed sleep as synchronization.
- Keep the test's existing per-test `mktemp -d` isolation and the exact lock-record preservation assertions.
- The PR is opened as a draft before the implementation change, per the manager's handoff instruction; formal review remains cross-vendor.

```mermaid
sequenceDiagram
    participant T as Bats test
    participant S as sync start parent
    participant E as engine child
    T->>S: start in background
    S->>E: spawn and write pidfile
    E-->>T: pidfile becomes visible
    Note over T,S: race window: S still owns .config.lock
    S->>S: release registry lock
    T->>T: bounded wait observes lock absent
    T->>T: mkdir lock deliberately
    T->>S: kill engine; force cleanup retake failure
```

---

### Task 1: Preserve the investigation and publish the draft PR shell

**Files:**
- Create: `docs/superpowers/plans/2026-08-20-issue-91-lock-test-isolation.md`

**Interfaces:**
- Consumes: Issue #91, the existing test and `wait_for_missing` helper.
- Produces: a reviewable plan and branch `issue-91-test-isolation` based on current `main`.

- [x] **Step 1: Confirm the current tree and root cause.** The isolated diagnostic observed `pidfile=yes lock_at_pidfile=present`, while the source shows `agmsg_lock_release` occurs after pidfile creation and before readiness polling.

- [ ] **Step 2: Commit only this plan and push the branch.**

```bash
git add -- docs/superpowers/plans/2026-08-20-issue-91-lock-test-isolation.md
git commit -m "docs: plan issue 91 lock test isolation"
git push -u origin issue-91-test-isolation
```

- [ ] **Step 3: Create one draft PR as `kappaseijin4codex`.** Link Issue #91 and state that the implementation is intentionally not yet included; the draft exists for early cross-vendor review. Do not create a second PR if a matching PR already exists.

---

### Task 2: Add the bounded synchronization to the regression test

**Files:**
- Modify: `tests/test_remote_engine_start_refusal.bats:363-407`
- Test helper: `tests/test_helper.bash:wait_for_missing`

**Interfaces:**
- Consumes: `lock`, `starter`, and the existing `wait_for_missing` helper.
- Produces: a test that takes the lock only after the starter has released it, while still requiring the starter to be alive for the retake-failure scenario.

- [ ] **Step 1: Write the failing/diagnostic assertion before the fix.** Insert a temporary assertion immediately after the pidfile wait:

```bash
[ ! -d "$lock" ]
```

Run: `bats --filter 'cleanup that cannot retake' tests/test_remote_engine_start_refusal.bats`

Expected: on the measured scheduling path, this assertion fails because `pidfile` visibility precedes the parent's `agmsg_lock_release`.

- [ ] **Step 2: Replace the timing assertion with a bounded condition wait.** Use the existing helper and check that the starter remains alive before taking the lock:

```bash
wait_for_missing "$lock"
kill -0 "$starter" 2>/dev/null
mkdir "$lock"
```

This waits for the actual condition, keeps the test bounded by the helper's ten-second ceiling, and fails if the starter exits before the test can exercise cleanup.

- [ ] **Step 3: Run the focused test and verify GREEN.**

Run: `bats --print-output-on-failure --filter 'cleanup that cannot retake' tests/test_remote_engine_start_refusal.bats`

Expected: the test passes and still asserts the diagnostic plus both preserved records.

---

### Task 3: Verify the fix and mutation controls

**Files:**
- Test: `tests/test_remote_engine_start_refusal.bats`

- [ ] **Step 1: Run the full target file.**

Run: `bats --print-output-on-failure tests/test_remote_engine_start_refusal.bats`

Expected: all nine tests pass.

- [ ] **Step 2: Run the negative control for the synchronization.** Temporarily remove the `wait_for_missing "$lock"` line while retaining the `kill -0` assertion, run the focused test repeatedly, and capture a run where the immediate lock assertion fails on the measured path. Restore the wait immediately.

- [ ] **Step 3: Run the stale-lock control.** Temporarily change the wait target to a path that is never created, run the focused test, and verify it fails at the bounded wait rather than reaching the record assertions. Restore the correct `$lock` target.

- [ ] **Step 4: Run relevant repository checks.**

```bash
bats tests/test_remote_engine_start_refusal.bats tests/test_team.bats
bash -n scripts/remote.sh scripts/lib/registry-lock.sh
git diff --check
```

- [ ] **Step 5: Inspect the final diff.** Confirm only the plan and the target test changed; no production lock code or unrelated work is staged.

---

### Task 4: Update the draft PR, request cross-vendor review, and report

**Files:**
- Update: the existing Issue #91 draft PR description

- [ ] **Step 1: Commit the test change and push the same branch.**

```bash
git add -- tests/test_remote_engine_start_refusal.bats
git commit -m "test: wait for registry lock release before retake"
git push
```

- [ ] **Step 2: Update the PR description with measured evidence.** Include the root cause, focused/full target test results, the two negative controls, and the exact changed file. Do not hand-write timestamps; use the GitHub artifact metadata for time.

- [ ] **Step 3: Request formal review from the Claude reviewer account.** The producer is Codex, so the opener is `kappaseijin4codex` and the formal reviewer is `kappaseijin4claude`; do not self-approve.

- [ ] **Step 4: Report the PR URL, head SHA, test counts, and review state to `agmsg_pm_claude`.**
