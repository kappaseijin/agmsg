---
type: Plan
title: "Issue #59: gh pr ready を宛先検証付きで許可"
description: "gh write-owner guard の分類漏れを最小変更で修正し、通常の guarded CLI PR flow を回復する。"
tags:
  - agmsg
  - github
  - guard
  - pr
  - regression
  - issue-59
timestamp: "2026-08-15T21:50:03+09:00"
---

# Issue #59: gh pr ready を宛先検証付きで許可

**Goal:** `gh pr ready` を issue / PR の既存 destination-checked write と同じ経路へ分類し、allowlisted repository の draft PR を guarded CLI flow だけで ready 化できるようにする。

**Root cause:** `is_destination_checked_write` は `pr create`、`pr comment`、`pr review`、`pr merge`、`pr close`、`pr reopen`、`pr edit`、`pr lock`、`pr unlock` を列挙するが `pr ready` を欠く。そのため repository resolver 前に fail-closed する。

**Scope:** gh write-owner guard の command classification と guard Bats のみ。repository resolver、allowlist、real gh launcher、PR account policy は変更しない。

## Constraints

- `pr ready` は safe read ではなく destination-checked write でなければならない。
- explicit allowed repository は fixed real gh に渡る。
- third-party repository は real gh を起動する前に拒否されなければならない。
- global Git / gh launcher を PATH から外す回避策は検証に使わない。
- README に必要な利用者向け操作・設定変更はない。

---

### Task 1: missing classification を RED で固定する

**Files:**

- Modify: `tests/test_gh_write_owner_guard.bats`

- [x] Add GHG-22: allowed `pr ready` reaches fake real gh, and third-party `pr ready` is rejected before fake real gh.
- [x] Run the focused guard suite. Expected RED: allowed `pr ready` fails as an unsupported operation.
- [x] Confirm fake write log remains empty during the rejected parser path.

### Task 2: permit only the classified command

**Files:**

- Modify: `scripts/guards/gh-write-owner-guard.sh`
- Modify: `tests/test_gh_write_owner_guard.bats`

- [x] Add only `pr ready` to the existing destination-checked PR command list.
- [x] Run focused GREEN and preserve the third-party pre-transport rejection.
- [x] Run shell syntax and whitespace checks.

### Task 3: validate guarded CLI flow

- [x] Run the complete gh write-owner guard suite.
- [x] Run the full source Bats suite with the production launchers present in default PATH.
- [ ] Run an installed launcher smoke check that the allowed repository no longer fails before destination resolution.
- [ ] Commit only the plan, guard, and GHG regression test; open one PR closing Issue #59.

## Acceptance Evidence

- `gh pr ready` is resolved and authorized exactly like other guarded PR writes.
- an allowed repository reaches fixed real gh with original argv intact.
- a third-party repository produces no fake real-gh write.
- default-PATH source Bats remains green.

## Verification record

- RED: GHG-22 failed before the source change because the guard rejected `pr ready` as an unsupported operation.
- GREEN: `bats -f 'GHG-22' tests/test_gh_write_owner_guard.bats` passed after the one-token classification change.
- Guard suite: `bats tests/test_gh_write_owner_guard.bats` passed 22/22.
- Mutation: temporarily removing only `pr ready` made GHG-22 fail; the exact allowlist entry was restored immediately and the focused test passed again.
- Static checks: `shellcheck -s bash -e SC1091 scripts/guards/gh-write-owner-guard.sh` and `git diff --check` passed.
- Default-PATH source regression: `bats tests/` passed 1,072/1,072 with the production launchers still first on PATH.
