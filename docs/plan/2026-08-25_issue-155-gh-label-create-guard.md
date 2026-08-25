---
type: Plan
title: "Issue #155: gh label create を宛先検証付き write に分類する"
status: proposed
issue: "https://github.com/kappaseijin/agmsg/issues/155"
timestamp: "2026-08-25T15:37:17+09:00"
---

# Issue #155: gh label create を宛先検証付き write に分類する

## 主張

`gh label create` を `scripts/guards/gh-write-owner-guard.sh` の
destination-checked write に分類する。
これにより許可 owner の repository への label 作成だけが、既存の destination resolver を通って
fixed real `gh` へ到達する。

## 根拠

Issue #155 の実測では `kappaseijin` と `kappaseijin4claude` の双方が同じ
`operation is not classified ... label create` で拒否された一方、既存 label の issue への付与は通った。
したがって account routing ではなく command 分類の列挙漏れである。

現行 source でも `label list` だけが safe read であり、`is_destination_checked_write` に
`label` は無い。未分類 operation は destination resolver の前に fail-closed する。
disposable clone での既存 guard suite は 41/41 pass、`bash -n` は pass だった。

## 決定と境界

今回の allowlist 追加は **`label create` のみ** とする。

| operation | 今回の扱い | 理由 |
| --- | --- | --- |
| `label create` | destination-checked write に追加 | Issue #155 の機械可読な状態を作れない直接原因であり、`--repo` は既存 resolver が処理する形である。 |
| `label delete` | 未分類のまま拒否 | 破壊的操作。create の分類漏れ修正に便乗して許可しない。拒否を regression test で固定する。 |
| `label edit` | 未分類のまま拒否 | metadata 更新の独立した write であり、今回の単一主張ではない。 |
| `label clone` | 未分類のまま拒否 | source / destination の二重 repository を含み得るため、単一 destination resolver の適用可否を別途設計する。 |

repository resolver、owner allowlist、host 判定、fixed real-gh launcher、PR account policy は変更しない。
README は利用方法・設定・出力を変えないため更新しない。

## 受入れ証拠

| control | expected | failure mode prevented |
| --- | --- | --- |
| allowed `label create --repo kappaseijin/fixture` | status 0、fake real-gh の write log に元 argv が残る | 分類後に正規の write が不要に遮断されないこと |
| third-party `label create --repo thirdparty/fixture` | status non-zero、`repository owner or host is not allowed`、write log 空 | allowlist を fail-open にしていないこと |
| allowed `label delete --repo kappaseijin/fixture` | status non-zero、未分類 diagnostic、write log 空 | 破壊的 operation を誤って許可しないこと |
| `label create` を一時的に allowlist から外す mutation | create positive test が non-zero（KILLED） | positive test が常時成功する偽陰性でないこと |

すべての検証は disposable clone の `tests/test_gh_write_owner_guard.bats` が作る fake real-gh と
temporary remote response で行う。`~/.agents/bin/gh`、本番 repository、実 GitHub label は試験台にしない。

## 実装計画

### 1. RED を追加する

`tests/test_gh_write_owner_guard.bats` に次の 2 test を追加する。

1. `GHG-30`: allowed `label create blocked:reproduction --repo kappaseijin/fixture --color B60205 --description ...` が
   fake write log へ元 argv を残して status 0 になること、同じ command の `thirdparty/fixture` は
   destination diagnostic と空 write log で拒否されること。
2. `GHG-31`: allowed owner を指定しても `label delete blocked:reproduction --repo kappaseijin/fixture` は
   未分類 diagnostic と空 write log で拒否され続けること。

先に `bats tests/test_gh_write_owner_guard.bats` を実行する。
`GHG-30` は未分類 diagnostic で失敗（RED）、`GHG-31` は pass が期待値である。

### 2. 最小の分類変更を行う

`scripts/guards/gh-write-owner-guard.sh` の `is_destination_checked_write` の既存 list に、
`'label create'` を 1 項目だけ追加する。
safe-read list、destination resolver、argument parser、delete/edit/clone の list は変えない。

### 3. negative control と suite を確定する

1. `GHG-30` / `GHG-31` と guard suite 全体を GREEN にする。
2. `label create` の list 項目だけを一時的に取り除き、`GHG-30` が non-zero になることを確認する（KILLED）。
   変更を残さず復元し、suite をもう一度 GREEN にする。
3. `bash -n scripts/guards/gh-write-owner-guard.sh`、`shellcheck -s bash -e SC1091 scripts/guards/gh-write-owner-guard.sh`、
   `bats tests/`、`git diff --check <base>..<head>` を実行する。`bats tests/` は README と CI の Bats suite
   の正規入口である。実行時の既存失敗は、今回の guard 差分による失敗と混同せず command・rc・対象 test を記録する。

test runner の既存不安定性が現れた場合は、assertion failure と timeout を区別し、command・rc・対象 test を
記録する。green でない HEAD を review へ出さない。

## PR 契約

- 1 PR = 「`gh label create` を destination-checked write として分類する」だけ。
- producer: `agmsg_programmer_codex`、opener: `kappaseijin4codex`。
- formal reviewer: `agmsg_reviewer_claude`、review account: `kappaseijin4claude`。
- review 対象は final `headRefOid` の PR 全差分。final push 後に CI と formal approval を再取得する。
- programmer は CI green と approval を確認して PM へ報告する。merge は PM が行う。

## 非対象

- `label edit`、`label delete` の許可、`label clone` の destination 設計
- owner allowlist、account routing、fixed real-gh launcher の変更
- Issue #144、#138、#156
