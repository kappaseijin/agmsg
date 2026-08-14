---
type: Decision
title: Issue #23 — codex_product_owner から agmsg_owner_codex を派生
description: agmsg プロジェクト用の Codex 派生席を登録・起動し、受け入れ条件を実測した完了記録。
tags:
  - issue-23
  - agent
  - codex
  - agmsg
status: accepted
timestamp: "2026-08-14T13:10:41+09:00"
---

# Issue #23 — codex_product_owner から agmsg_owner_codex を派生

## 対象

ユーザー依頼に基づき、主人格 `codex_product_owner` から agmsg プロジェクト専用の Codex 派生席を生成する。

| 項目 | 実測値 |
| --- | --- |
| 派生元 | `codex_product_owner` |
| 派生席 | `agmsg_owner_codex` |
| team | `agmsg` |
| type | `codex` |
| model | `gpt-5.6-terra` / effort max |
| GitHub | `kappaseijin4codex` |
| 作業プロジェクト | `/Users/kappa/Dropbox/data/dev/agmsg` |

## 受け入れ条件と結果

### 1. 派生席が agmsg チームへ登録される

`whoami.sh` と `identities.sh` の実測結果:

```text
agent=agmsg_owner_codex teams=agmsg type=codex project=/Users/kappa/Dropbox/data/dev/agmsg
agmsg	agmsg_owner_codex
```

### 2. 1 タブ 1 エージェントで起動される

`herdr tab list --workspace w4N` と `herdr pane list --workspace w4N` を実測した。

- workspace: `w4N`
- agent tab: `w4N:t1`
- agent pane: `w4N:p1` (`codex`, working)
- 監視 pane: `w4N:p5` (`watch:agmsg`)、`w4N:p6` (`watch:agents`)

監視 pane は例外として許可された非エージェント pane であり、agent tab には Codex エージェントを 1 つだけ配置している。

### 3. 起動結果と登録状態を確認できる

起動後に team roster、identity、herdr pane を再確認し、`agmsg_owner_codex` の登録と稼働状態を確認した。今回の一時方針に従い、対向 LLM reviewer や追加エージェントは起動していない。

## 運用方針

別 LLM のレビューは不要というユーザー指定に従い、本 Issue の完了判定は Codex owner のセルフチェックと required CI で行う。Issue と対応 PR は一対一とし、本記録を含むこの PR 以外の PR は本 Issue に紐付けない。

## 完了

実測結果をこの文書に記録し、Issue #23 を `Closes #23` で参照する 1 本の PR として提出する。PR の merge と同時に Issue #23 を close する。
