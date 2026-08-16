---
type: Plan
title: Issue #63 — gh 宛先 resolver 失敗時の fail-closed 診断
description: gh owner guard の resolver 失敗を上位の拒否処理へ正しく返し、無出力終了を防ぐ。
tags:
  - issue-63
  - security
  - gh
  - bugfix
status: complete
timestamp: "2026-08-16"
---

# Issue #63 — gh 宛先 resolver 失敗時の fail-closed 診断

## 目的

default repository、cwd repository、明示 repository の resolver が失敗したとき、guard が元の `gh` writer を実行せず、上位の `die` へ制御を返して非 0 と診断を出す状態を保証する。

## 再現した原因

resolver 関数が内部で `set +e` の後に `set -e` を実行していた。resolver の非 0 return が呼び出し元の status capture より先に errexit を発火させ、`resolve_destination` の `die` が実行されない経路があった。

## 実装範囲

1. resolver の外側の errexit 状態を変更せず、条件付き command substitution で status を取得する。
2. default / cwd / explicit の失敗経路を guard の標準エラー診断へ到達させる。
3. fake `gh` を使い、空・失敗・不正 resolver output と明示 repository の失敗を回帰試験する。
4. 明示 `--repo` の正常経路と既存の読み取り allowlist を変更しない。

## 受け入れ条件

- resolver が失敗または曖昧な場合、実 `gh` writer を起動しない。
- guard は非 0 で終了し、標準エラーに `gh-write-owner-guard` の診断を出す。
- explicit `--repo` の正常経路と既存の owner / host 検証は維持する。
- default / cwd / explicit の失敗を隔離 fixture で再現できる。
- credential、共有 agmsg 設定、live agent、spawn は変更しない。

## 検証

- `bats tests/test_gh_write_owner_guard.bats`
- `bats tests/test_gh_write_owner_guard.bats tests/test_enforced_assertions.bats` (32/32)
- `bats tests/` (1688/1688)
- `bash -n scripts/guards/gh-write-owner-guard.sh`
- `git diff --check`
