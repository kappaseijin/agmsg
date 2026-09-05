---
type: Design
title: "Issue #254: hunk分類台帳と段階移行の契約"
status: proposed
timestamp: "2026-09-06T02:34:04+09:00"
issue: "https://github.com/kappaseijin/agmsg/issues/254"
fork_cutoff: "7ea795e93683b0e7ede0fabf017fe503fcfe6aea"
official_cutoff: "e58dbafad5a84be625f070385bb0c076c3daa4db"
---

# hunk分類台帳と段階移行の契約

## 主張と範囲

#248のfile一覧を、根拠付きhunk分類・互換性manifest・段階移行へつなぐ契約を定める。
P2関連を先に分類し、未分類の残りを移植済み・不要と扱わない。
物理移動、切替、削除、稼働登録/claim/DB変更は行わない。
producerはagmsg_architect_codex、実装は別依頼後のagmsg_programmer_codex、独立実測は既存agmsg_verifier_codex、formal reviewerはagmsg_reviewer_claude。

## cutoffと台帳

共通祖先は`3d06318de3aff9929cfaf87c092fef6709d2cc8b`。
旧fork=`688f4fdec837488e2326444fcc8ce3954db18a38`、新forkと公式cutoffはfrontmatterに固定する。
旧file一覧を上書きせず、祖先→新fork、祖先→公式、旧fork→新forkの3種を区別する。
`git diff --unified=0`の各file/hunkを原差分として記録し、diff headerだけで意味を分類しない。
binaryやrename-only等のhunkを持たない変更にはfile-level項目を必ず作る。

| 台帳field | 契約 |
| --- | --- |
| id | 比較両HEAD・path・旧新range・patch digestから作る安定ID |
| source | base/head、old/new path・range、patch digest、比較方向 |
| responsibility | agmsg公式 / agguild / pool-data / persona-exception / regression / mixed / unclassified |
| publicDependencies | command/schema/version。内部依存は別internalDependenciesへ明記 |
| retainedBehavior | 残す挙動または廃止候補の理由。未確認はunknown |
| regression | 正対照・負対照・既存test・結果artifact・cutoff |
| owner / reviewer | 調査担当とformal reviewer。個人roleを公開APIへ移植しない |
| disposition | retain / propose-provider / move-candidate / superseded-candidate / undecided |
| evidence / status | 一次資料とclassified/unclassified/accepted。推測だけでacceptedにしない |

mixed hunkは論理単位へ分解して子項目を持たせ、原hunkへのcoverageを維持する。
全原差分項目が一度以上対応付けられ、未分類数・mixed数・未受入数を別々に集計する。
分類済みでも移動可とは限らない。
負の対照として一覧から1 hunkを欠落させ、coverage検査が失敗することを必須にする。

## 現HEADの先行分類

旧fork→新forkは`git diff --stat`で13 files、4124 additions、45 deletionsを確認した。
追加のB1/B2 helperはfork内の提供側実装であり、公式提供済みではない。

| 対象 | 先行所属・判断 | 必要回帰 |
| --- | --- | --- |
| api.sh registrations/actas-owner route | agmsg提供側提案。共通source追加hunkはmixed依存を精査 | 旧API、新envelope、read-only、欠損/競合 |
| lib/api-registrations.sh、lib/api-actas-owner.sh | agmsg提供側提案。上流cutoffへ適用可能性未確定 | #246/#247適合と独立対照 |
| test_api系3ファイル | provider契約のregression | 既存CLI回帰と負の変異 |
| README新API節 | provider利用資料 | 実装とschema一致。独自運用を含めない |
| docs/decisionsとinventory | 設計・追跡資料 | 固定比較方向とcoverage整合 |
| 既存PM identity/broker/collector関連 | agguild。P2優先項目 | B1/B2 public consumer、#253 B3、実TUI |

この表はfile/機能群の先行分類であり、全hunk意味分類完了の宣言ではない。
定型抽出はworker、各hunkの意味分類はarchitect、根拠実測はverifier、受入はreviewerとする。
実際のhunk台帳の未分類を原一覧として保持し、全分類は後続精査で進める。
同prefixのfull-hunks.jsonに734 hunk、increment-hunks.jsonに27 hunkを保存した。
全行は意味分類未完了としてunclassifiedで保持する。上表の機能群候補を各hunkの確定判断へ自動適用しない。
増分は正しい旧fork objectで再収集した13 files/27 hunksを採用し、workerの初回SHA誤記を含む一覧は破棄せず不採用とした。
増分13 filesは全てhunkありというworker確認。全体734 hunkのfile-level特殊変更のcoverageは未確認であり、完全分類台帳の受入はまだ求めない。

## 互換性manifest

manifestはformatVersion、固定code/provider/CLI版、公開capabilityごとのschemaと提供状態、consumer試験計画、提供側独立対照、統合結果、gate別status/evidenceを持つ。
nullやunknownは未達であり、空配列を全passに変換しない。
B1/B2 fork実装は`fork-prototype`、公式版で提供を確認するまで`officialAvailable=false`とする。
B3は#253の採用経路と対照完了までundecidedとする。
変更した本文と同じ手番でmanifestを更新し、readbackする。
本書に隣接する`2026-09-06T023404_issue-254-gates.json`を初期状態とする。

## 循環ゲートの訂正

[breaker断定](https://github.com/kappaseijin/agmsg/issues/222#issuecomment-5553538051)に従い、consumer統合結果を実装着手前に要求していた記述を次の3段階へ置き換える。
PR #245/#248/#249/#250の旧「P2解除」「接続実装前」の記述は、以後この段階区分で読む。

| gate | 必要条件 | まだ要求しないもの |
| --- | --- | --- |
| 実接続実装着手 | 採用設計受入、公式B1/B2提供版、B3採用公開経路、提供側独立対照、consumer試験計画の固定とreviewer受入 | 実装後のconsumer統合結果 |
| pilot起動 | 3接続実装の固定HEAD全差分review、必要試験全pass、隔離consumer統合受入 | 実pilotの運用結果 |
| P2合格 | 実TUI fresh/resume、ID付き業務1周、故障復旧、現PM無変更の受入 | #222全移行完了 |

公式提供待ちでも設計・提案パッチ・隔離調査fixtureは進められる。
現PM変更、私設schema reader、影claim/ack、恒久fork fallbackは引き続き禁止する。

## 段階移行計画

1. 比較原差分とhunk coverageを固定する。P2優先項目を分類し、残りはunclassifiedとして数量を保持する。
2. 提供側提案patchを隔離rootで試す。#246/#247再実装をせず、既存patchの公式cutoff適用・差分依存を検証する。
3. 公開契約と各正負対照の結果をmanifestへ反映し、上記gateごとにreviewerが受入する。
4. 移動前fixtureで登録tuple、actas owner、未処理message ID/ack境界、resume SID/process世代、旧root rollbackを対照検証する。
5. 限定pilotの移行だけを別依頼で行う。現役全席のpath変更・再登録・DB共有writer化をしない。
6. 個々の受入後も物理移動・切替・削除は別の対象確定手順へ渡す。新側がACK済みなら古いsnapshotを戻さずID境界を照合する。

欠測、分類不能、既読境界不明、戻し先不明なら停止する。
移行前の資料作成と、無停止移行が実証済みであることを区別する。
READMEは今回無変更。実際の分離時はagmsg対応版、agguild/pool配置、起動・更新・切戻しを各利用READMEへ自己完結で反映する。
