---
type: Plan
title: "Issue #55: Git push guard の短縮 quiet option 対応"
description: "production update 後に発見した -q 拒否を最小の policy 変更と TDD で修正する。"
tags:
  - agmsg
  - git
  - guard
  - regression
  - issue-55
timestamp: "2026-08-15T20:53:20+09:00"
---

# Issue #55: Git push guard の短縮 quiet option 対応

**Goal:** Git の正規 push option `-q` を `--quiet` と同じ non-destination option として受理し、owner authorization を一切弱めずに production update 後の regression を解消する。

**Root cause:** `scripts/guards/git-push-owner-guard.sh` の `push_bool_option` は `--quiet` を許可する一方で `-q` を欠落させている。production install が global Git launcher を有効にした後、`git push -q` が destination resolver 前に fail-closed した。

**Scope:** guard parser と guard-specific Bats のみ。preinstalled launcher を前提に fixture を隔離する作業は Issue #56 で扱い、ここには混在させない。

## Constraints

- short option の受理は destination／owner authorization を bypass してはならない。
- `-q` は destination でも value-taking option でもない。
- third-party GitHub owner、local URL、malformed destination の拒否契約を維持する。
- source behavior 検証では real Git を先頭にした isolated PATH を使う。default PATH の fixture isolation failure は Issue #56 の既知依存として記録し、隠蔽しない。
- README に必要な利用操作・設定変更はない。標準 Git option の parser compatibility に限定する。

---

### Task 1: regression contract を RED で固定する

**Files:**

- Modify: `tests/test_git_push_owner_guard.bats`

- [x] Add GPG-15, which proves an allowlisted push with `-q` reaches the fake SSH transport and a third-party push with `-q` is rejected before transport.
- [x] Run the focused guard suite with the real Git directory ahead of the installed launcher. RED confirmed: new GPG-15 failed with `unsupported push option: -q`.
- [x] Confirm the failure is parser-level and not a fixture or network failure.

### Task 2: add the minimal allowlist entry

**Files:**

- Modify: `scripts/guards/git-push-owner-guard.sh`
- Modify: `tests/test_git_push_owner_guard.bats`

- [x] Add only `-q` next to the existing `--quiet` boolean push option.
- [x] Run focused GREEN with real Git first in PATH: 15/15 GPG tests passed, including GPG-15.
- [x] Run a negative control against a third-party owner with `-q`; fake SSH and push marker remained absent.
- [x] Run shell syntax and whitespace checks (`shellcheck` and `git diff --check`).

### Task 3: validate source and document the dependency boundary

- [x] Run the full source suite with real Git first in PATH: 1,070/1,070 green.
- [x] Run the default-PATH local suite once before this fix. It had 1,053 passing and 16 failing cases: fifteen `-q` parser rejections (this issue) plus the preinstalled-launcher fixture leak tracked by Issue #56.
- [x] Commit only the plan, guard parser, and GPG regression test as `fix: accept short quiet push option`.
- [ ] Open one PR that closes Issue #55. Do not add Issue #56 changes to it.

## Acceptance Evidence

- `git push -q` to an allowlisted GitHub-shaped destination passes the guard and reaches fake SSH.
- the same `-q` with a third-party owner fails before fake SSH.
- the existing `--quiet` and all previous GPG policy cases remain green.
- CI and isolated full Bats are green; any default-PATH fixture isolation result is explicitly tracked by Issue #56.
