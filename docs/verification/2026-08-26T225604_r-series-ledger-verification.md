---
type: Verification
title: R-series ledger and closure の事実検証
status: passed
target: "/Users/kappa/Dropbox/data/dev/agmsg/docs/decisions/2026-08-26T225926_r-series-ledger-and-closure.md"
timestamp: "2026-08-26T22:56:04+09:00"
updated: "2026-08-26T23:20:46+09:00"
---

# R-series ledger and closure の事実検証

## 結論

**PASS（LGFIN 再検証済み）。** 最終訂正で、3 区分、R5-C1/C2 の分類、#189 の進行中表現、#181 の旧/現 HEAD を分けた実 packet、frontmatter 時刻が揃った。

この PASS は台帳の事実記述に対する判定である。GitHub の #176 / #178 を今 close してよいという判定ではない。#181 / #189 は live で OPEN のままであり、台帳も未解決として正しく明示している。

## 初回指摘（訂正済みの履歴）

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

## 中間再検証（訂正前の履歴）

targetは `2026-08-26T225926_r-series-ledger-and-closure.md` へ改名され、frontmatter時刻、R5-P4〜P7のpath別reopen、R5-C1/C2、#189の進行中表現は訂正済みである。

残る指摘は2件である。

1. #181節は実packetの **value** を載せたが、`cutoff`（valid attempt 51で停止）、`source`（PR #201の旧HEAD `ea72704a6f4736932ccde72d077aa7230cbe03dc`）、`command`（stub無しのFRESH Bats反復）を載せていない。live PR #201 HEADは `2d3bb346c11b2271d64315604a11998be977982c` へ進んでおり、旧HEADの観測を新HEADの証拠として扱えない。値と固定HEADを同じ節へ記録すること。
2. GitHubでは #176、#178、#181、#189 がOPENである。#181はactual migration failureを得たが修復範囲のbreaker断定待ち、#189は実装中である。epic close用台帳には「修正」「reopen条件つき決着」に加え、**未解決（Issue、次の決定者、close不可の理由）**を明記する。現状の#181/#189節だけでは、決着済みとの誤読を防げない。

この2件が直るまで、最終判定は`changes_requested`である。

## breaker再断定後の補正

breakerはpacketとsourceを照合し、#181を別Issueにせず、同じIssue内で `init-db.sh` のschema/triggers適用を単一トランザクションへ原子化する修復と断定した。migration guardの `1|0` fail-closedは正しいため変えず、lock・guard fallback・`send.sh` retry契約も変更しない。

よってactive項目の#181行には、producer=programmer、前提=PR #201の診断merge後、修復=初期化の原子化、verification=verifierによるdiagnostic付きfan-out 60回、close不可理由=修復PRとfixed-HEAD検証が未完了、と記す必要がある。#189も実装中のactive項目として同じ区分へ置く。

## 判定保留だった最終1点（訂正済み）

targetは未解決表を追加した一方、冒頭で「実際には2種類」とし、分類表も修正・決着の2行だけである。未解決は第3区分なので、冒頭を3種類へ改め、`未解決（実害あり、修復または検証中、epic closeで解決扱いにしない）` の表行を追加するまでPASSにしない。

## PR #201 現HEADの独立再測定

verifierはlive HEAD `2d3bb346c11b2271d64315604a11998be977982c` を、旧HEAD `ea72704a6f4736932ccde72d077aa7230cbe03dc` と分けて再測定した。10-wayは60/60成功、40-wayはattempt 6で `expected=40` / `actual=39`、child 1件のmigration stderrを得た。これは現HEADの独立証跡であり、旧HEAD packetを新HEAD根拠に変換したものではない。

targetがpacketを載せるなら、旧HEAD節を上書きせず、この現HEADの `value / cutoff / source / command` を別節として追記する。

## LGFIN 再検証

target の冒頭は **修正 / 決着（reopen 条件つき） / 未解決** の 3 区分となった。未解決には「実害があり、修復・検証が進行中。epic を close しても解決扱いにしない」と明記されている。

`#181` は、旧 HEAD `ea72704a6f4736932ccde72d077aa7230cbe03dc` の 10-way packetと、live PR #201 HEAD `2d3bb346c11b2271d64315604a11998be977982c` の Stage1/Stage2 packetを別 source として記録する。後者は Stage1 10-way が 60/60 pass、Stage2 40-way が attempt 6 で `actual=39 / expected=40`、migration stderr 1件であり、`value / cutoff / source / command` が揃う。

live GitHub 再取得では PR #201 の HEAD は `2d3bb346c11b2271d64315604a11998be977982c`、Issue #176 / #178 / #181 / #189 はすべて OPEN だった。target の #181 / #189 未解決表と、#189 の実装中表現は、open PR #202（registry-lock deadline の契約強化）とも矛盾しない。

**判定: PASS。**
