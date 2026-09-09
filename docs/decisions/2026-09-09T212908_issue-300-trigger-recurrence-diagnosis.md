---
type: Diagnosis
title: "Issue #300: main shard 30分cancel再発の判定"
description: >-
  Issue #300に記録されたrevert引き金について、main testsのジョブ単位記録で
  再発を判定し、実装と状態更新の担当境界を定める。
timestamp: "2026-09-09T21:29:08+09:00"
updated: "2026-09-09T21:29:08+09:00"
issue: "https://github.com/kappaseijin/agmsg/issues/300"
source_head: "5cd527a9ef1df4132c0e597fa1f87c694294e52a"
producer: agmsg_architect_codex
reviewer: agmsg_reviewer_claude
---

# Issue #300 main shard cancel 再発判定

## 判定

Issue #300に記録された引き金は成立した。

`main` のtests workflowで、shard jobが30分を超えてcancelledになった。

したがって、`triggered` ラベルを除去してclosed又はNOT_PLANNEDへ移すことはしない。

記録済みの対処どおり、PR #299を取り消すrevert PRを再作成し、formal review後にPMがmergeする。

この文書は再発判定と引継ぎだけを扱う。

revertの作成、CI実行、merge、Issue state更新は含まない。

## 1. 引き金と観測

Issue #300 commentの引き金は、次のとおりである。

> 次にmainのtestsでいずれかのshardが30分でキャンセルされたら、議論せず即座にrevertをマージする。

GitHub Actions run `34155827451` はmainのHEAD
`ac879080587c8e6a0656fe1c093d59facde8aa10` に対するtests workflowである。

| value | cutoff | source | command |
| --- | --- | --- | --- |
| `bats (ubuntu-latest shard 1)` = `cancelled` | shardが`cancelled`であること | [run 34155827451](https://github.com/kappaseijin/agmsg/actions/runs/34155827451) | `gh run view 34155827451 --json jobs` |
| `2026-09-07T19:30:34Z` から `2026-09-07T20:00:50Z`、30分16秒 | 30分以上 | 同job | 同上 |
| workflow branch = `main`、head = `ac879080587c8e6a0656fe1c093d59facde8aa10` | mainであること | 同run | `gh run list --branch main --workflow tests.yml` |

30分16秒は閾値を16秒超える。

この判定はPR #299の因果又は根本原因を断定しない。

記録済みの条件は原因ではなく、main shard cancellationの観測だけを要求している。

## 2. 除外対照

workflow全体がcancelledであっても、shardの30分cancelでなければ引き金にはしない。

| run | cancelled job | duration | 判定 |
| --- | --- | --- | --- |
| `34155827451` | `bats (ubuntu-latest shard 1)` | 30分16秒 | 引き金成立 |
| `34186938236` | `bats (windows-latest, driver input (#817))` | 17分 | shardでなく、30分未満。引き金根拠にしない |

この負の対照により、workflow-level `cancelled` を一律に再発として数える偽陽性を避ける。

## 3. 実施境界

PR #301はclosedだが、GitHub上でbranch `revert/issue-291-pr-299` とhead
`bf20c767643ace3cf7fbd6f0af1b7bf7abe013fd` が残る。

PR本文は`2ec4f13ef3afd40dd9e756062eead3a1cbf1f2c0`をrevertする一コミットである。

| 担当 | 次の操作 | 完了条件 |
| --- | --- | --- |
| programmer | 既存revert branchを現在のmainへ適用可能か検査し、新しいrevert PRを作成する | PRがcurrent mainをbaseにし、対象revert差分とCI対象HEADを明記する |
| reviewer | 新PRの固定HEADと全差分を一括reviewする | `kappaseijin4claude`のAPPROVEとrequired CI成功が同一HEADに対応する |
| PM | review済みPRをmergeし、Issue #300のstateと本文/labelをGitHub上で更新する | merge commit、Issue state、label、参照PRが整合する |

architectはこの診断を根拠に実装又はmergeを行わない。

revert後のIssue #300は、原因分析を継続するならOPENのまま「revert実施済み・原因未確定」とする。

再発対応までを完了条件にするなら、PMは本文とlabelsを更新したうえでCLOSEDにする。

いずれもrevertのGitHub正本を確認する前に確定しない。

Refs #300 #301 #299
