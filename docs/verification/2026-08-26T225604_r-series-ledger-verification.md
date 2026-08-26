---
type: Verification
title: R-series ledger and closure の事実検証
status: changes_requested
target: "/Users/kappa/Dropbox/data/dev/agmsg/docs/decisions/2026-08-26T225926_r-series-ledger-and-closure.md"
timestamp: "2026-08-26T22:56:04+09:00"
updated: "2026-08-26T23:02:33+09:00"
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

## 再検証

PMはレビュー後、R5-C1/C2と#189の文言を訂正した。両方ともtarget本文で確認済みである。

ただし、stub無し10並行fan-outのvalid attempt 51で、#181のactual packetが `expected=10` / `actual=9`、child 1件のexit 1、`team-work dispatch migration requires both legacy tables` を示した。これはfinal retry SQLite busyではなく初期化/migration経路である。したがってtargetの#181節は、値の変更を待つ段階ではなく、**retry修復を停止しmigration failureを別に分類する**現在の根拠へ更新しなければならない。

targetのfrontmatter時刻は再検証時にも未来であり、R5-P4〜P7のreopen条件もpath別に未分化のままである。この2点と#181節が残るため、検証結果は引き続き`changes_requested`である。

## 最終再検証

targetは `2026-08-26T225926_r-series-ledger-and-closure.md` へ改名され、frontmatter時刻、R5-P4〜P7のpath別reopen、R5-C1/C2、#189の進行中表現は訂正済みである。

残る指摘は2件である。

1. #181節は実packetの **value** を載せたが、`cutoff`（valid attempt 51で停止）、`source`（PR #201の旧HEAD `ea72704a6f4736932ccde72d077aa7230cbe03dc`）、`command`（stub無しのFRESH Bats反復）を載せていない。live PR #201 HEADは `2d3bb346c11b2271d64315604a11998be977982c` へ進んでおり、旧HEADの観測を新HEADの証拠として扱えない。値と固定HEADを同じ節へ記録すること。
2. GitHubでは #176、#178、#181、#189 がOPENである。#181はactual migration failureを得たが修復範囲のbreaker断定待ち、#189は実装中である。epic close用台帳には「修正」「reopen条件つき決着」に加え、**未解決（Issue、次の決定者、close不可の理由）**を明記する。現状の#181/#189節だけでは、決着済みとの誤読を防げない。

この2件が直るまで、最終判定は`changes_requested`である。

## breaker再断定後の補正

breakerはpacketとsourceを照合し、#181を別Issueにせず、同じIssue内で `init-db.sh` のschema/triggers適用を単一トランザクションへ原子化する修復と断定した。migration guardの `1|0` fail-closedは正しいため変えず、lock・guard fallback・`send.sh` retry契約も変更しない。

よってactive項目の#181行には、producer=programmer、前提=PR #201の診断merge後、修復=初期化の原子化、verification=verifierによるdiagnostic付きfan-out 60回、close不可理由=修復PRとfixed-HEAD検証が未完了、と記す必要がある。#189も実装中のactive項目として同じ区分へ置く。
