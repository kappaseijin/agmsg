---
type: Plan
title: "Issue #56: preinstalled Git guard 下の fixture 隔離"
description: "本番の agmsg Git launcher を実 Git と誤認する Bats fixture を、実行中の guard policy を変えずに隔離する。"
tags:
  - agmsg
  - git
  - tests
  - fixture
  - regression
  - issue-56
timestamp: "2026-08-15T21:38:57+09:00"
---

# Issue #56: preinstalled Git guard 下の fixture 隔離

**Goal:** 本番 install 後に PATH 先頭へ置かれる agmsg Git push guard を Bats fixture の実 Git と誤認させず、default PATH の source suite を hermetic に green に戻す。

**Root cause:** `test_bundle_core.bats` は Git を PATH から直接呼び、`test_git_push_owner_guard.bats` と `test_install.bats` は `command -v git` を実 Git と扱う。その結果、preinstalled launcher 自身を fixture の実 Git として使い、local bare remote の seed push が production guard によって拒否される。

**Scope:** test helper と 3 つの Bats fixture のみ。Git push owner guard の authorization policy は変更しない。短縮 `-q` の parser 対応は Issue #55 で既に完了している。

## Constraints

- fixture は agmsg launcher marker を持つ Git を実 Git として選んではならない。
- CI（launcher 非導入）と macOS / Linux / Git Bash で動く `git` / `git.exe` の探索を維持する。
- allowlisted GitHub destination の transport test と third-party 拒否 test を弱めない。
- source suite は default PATH で実行する。real Git を PATH 先頭に置く回避策を受入根拠にしない。
- README に必要な利用者向け操作・設定変更はない。

---

### Task 1: default-PATH failure を RED として固定する

**Files:**

- Modify: `tests/test_helper.bash`
- Modify: `tests/test_bundle_core.bats`
- Modify: `tests/test_git_push_owner_guard.bats`
- Modify: `tests/test_install.bats`
- Add: `tests/test_fixture_helpers.bats`

- [x] Run bundle-core, Git guard, and install suites with the production launcher first in PATH. Result: 53 passed, 17 failed.
- [x] Classify all failures: one bundle seed push, fifteen Git guard setup seed pushes, and one installer launcher-path assertion.
- [x] Add a focused resolver regression test that places a marker-bearing agmsg launcher before a real Git candidate and proves the resolver skips it.

### Task 2: resolve a real Git binary only for test fixtures

**Files:**

- Modify: `tests/test_helper.bash`
- Modify: `tests/test_bundle_core.bats`
- Modify: `tests/test_git_push_owner_guard.bats`
- Modify: `tests/test_install.bats`
- Add: `tests/test_fixture_helpers.bats`

- [x] Add a shared test helper that scans PATH for executable `git` / `git.exe`, skips only the agmsg launcher marker, and returns a canonical executable path.
- [x] Make bundle-core seed its local bare repository with the helper result.
- [x] Make Git guard and installer fixtures use the helper result instead of `command -v git`.
- [x] Keep the production launcher active for the test process so the fix proves fixture isolation rather than global PATH masking.

### Task 3: verify hermetic default-PATH behavior

- [x] Run the focused 71-case helper, bundle-core, Git guard, and installer suite with default PATH: 71/71 green.
- [x] Run a mutation control that disables marker skipping; the resolver regression failed as expected rather than silently choosing the guard.
- [x] Run the full source Bats suite with default PATH: 1,071/1,071 green.
- [x] Run shell syntax, whitespace, and targeted diff checks. `test_helper.bash` has two pre-existing warnings, excluded consistently while the new helper remains warning-free.
- [x] Commit only the plan and #56 fixture files, then open one PR closing Issue #56.

## Acceptance Evidence

- A marker-bearing agmsg launcher before real Git is skipped by the test helper.
- Bare local seed pushes use a fixed real Git binary and do not reach the production guard.
- All guard policy tests still exercise their generated launcher, including third-party rejection before fake SSH.
- Default-PATH focused and full Bats suites are green after #55 has been installed.
