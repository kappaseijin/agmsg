---
type: Plan
title: Issue #67 — explicit GitHub Actions job rerun guard
description: 既存 workflow run の失敗 job だけを明示 repository 経由で再実行する最小の gh guard write 契約を追加する。
tags:
  - issue-67
  - gh-guard
  - github-actions
status: complete
timestamp: "2026-08-16T23:14:16+09:00"
---

# Issue #67 — explicit GitHub Actions job rerun guard

## 目的

`gh-write-owner-guard` が、既存 run に属する失敗 job 1 件だけを、明示 repository の destination 検証後に再実行できるようにする。これは #65 の Codex SessionStart JSON 修正とは独立した guard の product gap である。

## 正式な許可形

次の形だけを destination-checked write として許可する。

```text
gh run rerun <run-id> --job <job-database-id> --repo <owner>/<repo>
```

guard は実行前に次を read-only で確認する。

- `run-id` と `job-database-id` は正の十進数で、各々一意である。
- `--repo` は長形式で一度だけ指定され、GitHub の許可 owner/host に解決できる。
- `gh run view <run-id> --repo <repo>` の結果で run ID が一致する。
- 指定 job がその run の jobs に存在し、status が `completed`、conclusion が `failure` / `cancelled` / `timed_out` のいずれかである。

## fail-closed 条件

run 全体の再実行、`--failed`、job のない rerun、run/job の欠落・重複・非数値、`--repo` の欠落・重複・不正 owner/host、run と job の不一致、成功・実行中 job、未知の追加 flag、非 rerun 操作、未分類 API endpoint は拒否する。

## 実装境界

1. `scripts/guards/gh-write-owner-guard.sh` に最小 rerun の argv 検証と read-only target verification を追加する。
2. `tests/test_gh_write_owner_guard.bats` の fake gh に run/job fixture を追加し、許可形と各 fail-closed 条件を回帰検証する。
3. 既存の read-only operation、issue/pr write、default/cwd/explicit destination resolution は変更しない。
4. installed skill、shared config、global hook、spawn、live session、bridge/pidfile/request は変更しない。

## 受入条件

- 許可形は明示 repository の既存失敗 job 1 件だけを fake write log に記録する。
- target verification が失敗した場合、write log は空で診断を stderr に出す。
- run 全体 rerun、`--failed`、曖昧な run/job、repoなし、許可外宛先、非 rerun、成功/実行中 job、未知 flag の負性試験がある。
- source focused/full Bats、`bash -n`、`git diff --check` が成功する。
- #65 の branch/PR、installed provenance、live runtime と混在しない。

## 検証結果

- `bats tests/test_gh_write_owner_guard.bats tests/test_enforced_assertions.bats`: 35/35 PASS
- `bats tests/`: 1691/1691 PASS
- `bash -n scripts/guards/gh-write-owner-guard.sh`: PASS
- `git diff --check`: PASS
- 実 GitHub Actions run/job の再実行、installed/shared/live の変更は未実施。

## 一次資料

- [GitHub CLI `gh run rerun` manual](https://cli.github.com/manual/gh_run_rerun)
