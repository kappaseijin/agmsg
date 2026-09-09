---
type: Plan
title: "Issue #383: G2 provider capability contract"
timestamp: "2026-09-10T00:00:00+09:00"
updated: "2026-09-10T00:00:00+09:00"
issue: "https://github.com/kappaseijin/agmsg/issues/383"
---

# Issue #383 Provider Capability Contract Implementation Plan

**Goal:** G3 が列挙した6個の capability だけを、固定 provider commit 上で fail-closed に検証する fixture を追加する。

**Architecture:** G1 manifest を再利用して archive した provider に対し、専用の contract runner が公開 command、固定 argv、JSON response、ID scope を検証する。未宣言操作、scope 逸脱、unknown・競合・schema 不正は成功へ縮退させない。

**Tech Stack:** Python 3 standard library, unittest, Bats, Git archive.

**Spec:** `docs/decisions/2026-09-09T234714_issue-380-p2-consumer-contract.md`

## Global Constraints

- provider は `kappaseijin/agmsg` の40桁 commit と一致しなければならない。
- required capability は message-peek/claim/release/ack/send/handoff-receipt だけである。
- `history` と未宣言 capability は使用も成功扱いもしない。
- README は変更しない。

### Task 1: Contract manifest and failing tests

**Files:**
- Create: `docs/decisions/issue383-capability-manifest.json`
- Create: `tests/test_issue383_provider_contract.py`

- [ ] Write tests for the declared six-capability set, provider-pin mismatch, invalid JSON/schema, scope/ID swaps, and unknown/competition failures.
- [ ] Run `python3 -m unittest tests/test_issue383_provider_contract.py` and confirm failure because the runner is absent.

### Task 2: Minimal contract runner

**Files:**
- Create: `scripts/issue383_provider_contract.py`

- [ ] Validate the G1 provider manifest and archive only its fixed commit.
- [ ] Invoke only manifest-declared commands with exact argv; validate one JSON object response and declared ID relations.
- [ ] Emit an evidence artifact including provider commit, manifest digest, command results, negative controls, and review HEAD.
- [ ] Run the focused unittest suite until it passes.

### Task 3: Focused verification

- [ ] Run the focused Issue #379 and Issue #383 unittest suites.
- [ ] Confirm `git diff --check` and inspect the changed-file list before requesting formal review.
