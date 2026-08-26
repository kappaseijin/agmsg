---
type: Verification
title: R-series ledger and closure の事実検証
status: changes_requested
target: "/Users/kappa/Dropbox/data/dev/agmsg/docs/decisions/2026-08-26T235500_r-series-ledger-and-closure.md"
timestamp: "2026-08-26T22:56:04+09:00"
---

# R-series ledger and closure の事実検証

## 結論

**changes requested。** epic close 前に、R5-C1/C2 の分類、#189 の状態、#181 の判断境界、frontmatter時刻を直す必要がある。

## 指摘

| 優先度 | 対象 | 検証結果 | 必要な訂正 |
| --- | --- | --- | --- |
| blocker | 着手しない項目の最終行 | `R2-C1 / C2 の残り` は誤り。breaker原文は **R5-C1 / C2** であり、R2は6/6完了済み | `R5-C1 / C2` に直し、reopen条件を「症状が観測されたとき」にする。理由喪失というR2の語彙を混ぜない |
| blocker | #189節 | `2026-08-26T22:56:04+09:00` のGitHub stateは Issue #189=OPEN。完了した実装ではない | 「実装した」を「実装を許可した」または「実装中」に直し、close前にGitHubのPR/Issue stateを再取得する |
| major | #181との対比 | 現行設計は診断だけで、final retry、init、send.sh前段のどれが原因か未確定。`再試行回数という値の変更`とは確定していない | 「原因未確定なので実経路packetまでproduction修復を止める」と書く。#189との差は値/契約の二分でなく、#181の修復predicate未確定とP9の契約強化で表す |
| major | timestamp | targetは未コミットのまま、frontmatterの`2026-08-26T23:55:00+09:00`は検証時刻より未来 | 保存時の実時刻へ更新する |
| minor | R5-P4〜P7 reopen | `同種の待ち` は別pathの失敗で全項目を再開しかねない | breaker原文どおり、各pathでtimeout/hangが1回観測されたとき、と個別化する |

## 確認できた事実

- R1は #148/#171/#174 がclosed、PR #191はMERGED。R3はroot:R3の #138/#144/#159/#162/#175 がclosedで、決定記録の「2件で完了」という変更範囲とも矛盾しない。R4の #143 はclosedで、配備をセッション境界に限る決定も一致する。
- R2の「6/6完了」は #176 の進捗記録と一致する。ただし、その事実ゆえR2-C1/C2を残件台帳へ置けない。
- #169の`resource busy` grepは修正前・後とも0件で正の対照を取れず、job成否だけを判定に使った、という記録は #169 close comment と一致する。Issue #169はclosed。
- R5-T2〜T5、P1/P2、P3のreopen条件は #176 のbreaker断定と一致する。

## 証跡と限界

- source: GitHub Issue #176 comments（breaker断定）、Issue #169 close comment、GitHub Issue/PR state、target文書、R5-P8/P9設計。
- command: `gh issue list --label root:R{1,2,3,4,5} --state all`、`gh issue view 176,178,181,189,169`、`gh pr view 191,196`、`gh api repos/kappaseijin/agmsg/issues/{176,169}/comments`。
- limitation: targetは未コミットのためGitHub artifactではない。#181/#189の最終close可否は、この検証では判定しない。
