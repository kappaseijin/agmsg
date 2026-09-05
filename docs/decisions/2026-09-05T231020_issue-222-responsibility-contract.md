---
type: Design
title: "Issue #222: 三分割の責務配置と互換契約"
status: proposed-with-public-api-blockers
timestamp: "2026-09-05T23:10:20+09:00"
issue: "https://github.com/kappaseijin/agmsg/issues/222"
source_head: "688f4fdec837488e2326444fcc8ce3954db18a38"
upstream_cutoff: "e58dbafad5a84be625f070385bb0c076c3daa4db"
merge_base: "3d06318de3aff9929cfaf87c092fef6709d2cc8b"
producer: agmsg_architect_codex
reviewer: agmsg_reviewer_claude
---

# 三分割の責務配置と互換契約

> ゲート訂正: [Issue #254の段階区分](2026-09-06T023404_issue-254-ledger-migration.md)を本書の旧ゲート記述より優先する。
> 実接続実装着手には公式提供版・B3採用経路・提供側独立対照・consumer試験計画の受入を要求し、実装後のconsumer統合結果はpilot起動前に要求する。
> 実TUIの運用受入はP2合格条件であり、前段へ循環させない。

## 決定と本書の主張

[breaker断定](https://github.com/kappaseijin/agmsg/issues/222#issuecomment-5552339806)に従い、agmsg・agguild・agguild_poolへの三分割を目標とする。
採否を再提案せず、責務と互換性を先に固定する。
agmsgは公式無改変の通信基盤、agguildは独自運用の実行層、agguild_poolは人格・管理情報のデータ層とする。
人格個別配下の固有executable・設定は例外として許容し、共有の運用実装をpoolへ隠さない。

一つの設計主張は「所属をディレクトリ名でなく、公開契約と保持する挙動で決める」である。
物理移動、稼働DB変更、全席再登録、実装、公式への変更投稿、README更新は本手番では行わない。
起草はarchitect、定型差分収集はworker、独立実測は既存verifier、formal reviewはClaude reviewerとする。
生産者のソース調査を独立実測・formal受入と呼ばない。

責務案は提示できるが、公式cutoffに足りない公開入口を確認したため、P2関連契約の受入完了はまだ宣言しない。
不足を内部schema読取・影claim・恒久fork fallbackで埋めない。
[追加breaker断定](https://github.com/kappaseijin/agmsg/issues/222#issuecomment-5552605626)により、既存提供側readerを活かす最小公開契約を#246と#247で別々に設計・提案準備する経路を採用する。

## 比較基準と一覧

| 比較 | 値 | 意味 |
| --- | --- | --- |
| fork | `688f4fdec837488e2326444fcc8ce3954db18a38` | 調査基準。配布先live版とは区別 |
| 公式比較cutoff | `e58dbafad5a84be625f070385bb0c076c3daa4db` | 比較対象であり採用versionの決定ではない |
| 共通祖先 | `3d06318de3aff9929cfaf87c092fef6709d2cc8b` | 以下の各側差分の起点 |
| 共通祖先→fork | 189 commits、257 files | 独自機能257件ではない |
| 共通祖先→公式cutoff | 19 commits、37 files | fork未取込の公式側変更。fork側一覧と混ぜない |

workerがkappaseijin/agmsgの比較APIを両方向に取得し、architectが保存結果を読み戻した。
原取得は`/tmp/agmsg-issue222-5x0qIR/{forward,reverse}.raw.json`、永続一覧は同ディレクトリの本書に隣接する次の2ファイルとする。

- `2026-09-05T231020_issue-222-forward-inventory.json`: 257件のfilename/status/additions/deletions/previous_filename。
- `2026-09-05T231020_issue-222-reverse-inventory.json`: 37件の同項目。

APIのforwardは2ページ、reverseは1ページというworker報告であり、architectによる独立再測定ではない。
reverse rawのURL・ahead=19/behind=189・merge baseを読み戻し、通知本文のSHA誤記と区別した。
比較コマンドは`gh api repos/kappaseijin/agmsg/compare/<base>...<head>`で、いずれもこのfork repository内に限定した。
ソースは同repositoryから取得した固定Git objectを`git show <HEAD>:<path>`で読み、別repositoryへはアクセスしていない。
graph検索はscripts除外のため当該ソースの直接読取へ切り替えた。
履歴の旧「構想段階」は現在のIssue本文・breaker断定で更新されたものとして扱う。

一覧は未分類の原差分を意図的に保持する。
以下は機能群の責務分類であり、各file/hunkの移植可否を全件確定した台帳ではない。
未分類ファイル・未読hunkを黙ってagguildへ一括移動しない。

## 責務表

| 機能群・具体的な差分 | 配置 | 公開入口／内部依存 | 利用側・保持する挙動 | 回帰・未決 |
| --- | --- | --- | --- | --- |
| send/inbox/history、remote、storage、watch | agmsg公式責務 | `send.sh`、`inbox.sh`、`history.sh`、`api.sh get ... messages`等。内部DB・ack cursorには依存しない | 全席。未読・既読・順序・ID・宛先限定を維持 | forkの配送修正は行単位に公式修正と比較。単純廃棄不可 |
| join/whoami/actas/resetとproject解決 | agmsg公式責務＋agguild登録adapter | 公開join/whoami/actasは存在。forkのJSON roster/role-kind・全project照合は追加契約 | 全席とlauncher/guard。一意性、明示project、曖昧時拒否 | 公開不足B1/B2。登録型やunknownを縮退しない |
| `claim.sh`、`lib/claims.sh`、runtime message claims | 通信ack部分はagmsg責務。運用leaseはagguildへ分離候補 | forkの独自message_claims SQLと既読処理に依存 | bridge/配送consumer。予約とack競合・owner・TTL | actas ownerとは別物。公式inboxへの置換だけで同等とはしない。B3 |
| `team-work.sh`、G4 audit/ledger/transition、dispatch SQL | agguild | GitHub APIは外部公開入口。agmsg DBへの直接独自table更新は除去対象 | PM/breaker。revision・lease・expected owner・blocked状態を維持 | agguild専用stateへ切り離す契約が必要。既存DB移行は別ゲート |
| `pm-pretool-guard`、session-identity、PM binding/claim reader | agguild | Claude hookとOS process。現コードはteams JSON/claim fileへ内部依存 | #236 P2。strict identity、文字列owner、pidStart/世代、通常時deny | B1/B2を解消するまで移植実装不可 |
| PM brokerと限定GitHub/agmsg adapter | agguild | 正式send/inbox/history/APIを固定argvで呼ぶ | #236 P2。限定宛先・Issue・requestId、結果不明時停止 | 現stubを実送信と扱わない。schema別正負対照 |
| collector/audit/watchdog/通知 | agguild | Claude実行metadataとOS/既存通知。agmsg通知は公開send | #236 P2。PostToolUse独立性、60/180秒設定、到達確認 | CLI metadata契約は版固定で回帰。agmsg内部log readerを新設しない |
| `guards/gh-*`、`git-push-*`、account policy、PATH | agguild | gh/git実行file、正式identity API。現gh guardはfork whoami JSONに依存 | #239。vendor由来account、曖昧時拒否、実行先固定 | B1とPATH先頭/別shell/絶対path対照。#239修正自体は別Issue |
| spawn-options、profile、herdr配置、role boot | agguildの運用設定・launcher | 公式spawnの公開optionとherdr CLI。内部boot-commandのsourceを外部ABIにしない | 全席。明示role、1席1タブ、fresh/resume区別 | role optionや復元個体の互換性を測定して採用 |
| install/uninstall・startup-path・safe-dir-sync | 公式installer責務とagguild installer責務へ分割 | 公式installをpatchしない。独自guardのPATH設定はagguildが所有 | symlink target、冪等block、利用者編集保持 | #219相当の公式側修正と比較。remove/install全体を移植しない |
| persona AGENT、project差分、kaizen、管理manifest | agguild_pool | agguildが読む宣言データ。agmsg project pathの正本ではない | 主人格・派生人格・関連記録 | 移動前後で参照解決とresume保持。secretは格納しない |
| persona固有script/設定 | agguild_pool内の限定例外 | persona manifestから明示参照、共有PATHへ自動追加しない | そのpersonaだけ | 他personaに共有されたらagguildへ昇格し、例外放置しない |
| 作業clone、共有skill、DB、runtime claim、session履歴 | poolへの一括移動対象外 | 各所有者の公開契約に従う | Git状態・cwd・未処理依頼を保持 | 名前がcodex_monitor_agents配下でも人格データとは限らない |
| tests/docs/CIその他 | 対応する責務に追随。未分類は保持 | 実装ファイル数から所属を推測しない | 行動上の回帰を所有側で継承 | 257件を全部独自運用と数えない |

根拠例はforkの`api.sh`追加resource、`join.sh`の`--role/--kind`、`whoami.sh`JSON追加、`guards/gh-write-owner-guard.sh`のJSON照合、`lib/team-work.js`のSQLite mutation、`lib/claims.sh`のmessage_claims SQLである。
これらの公開入口の有無と内部依存は固定objectのソース読取で確認した。

## 公式側の未取込変更

37件のうち実行経路にはinstall、check-inbox、delivery、sqlite/sqlite-sync、codex type.conf、inbox、migrate-team-store、remote-sync、storage、session-startが含まれる。
19 commitsにはsymlink経由のCodex設定書込、曖昧なupdate先の拒否、resume時のunfiltered watcher抑止、Codex PostToolUse配送、mark-read競合の報告、storage初期化やsync性能・失敗伝播の修正が含まれる。
これはcommit主題の分類であり、fork修正の代替成立を実測したものではない。
対応する回帰を両tipで比較し、公式側で解消済みのものは独自実装として移植しない。
一方、公式に同名fileがあることや新しい日付だけでは同等性を判定しない。

## 公開APIの不足とbreaker返却事項

| ID | 要求する契約 | 公式cutoffで確認できた入口 | 不足・解除条件 |
| --- | --- | --- | --- |
| B1 | team/agent/type/canonical projectの全registrationと一意/unknown判定 | `whoami.sh <project> <type>`は人向けkey=value、`api.sh ... members`はtypes集約＋project LIMIT 1 | forkの`registrations`resourceと`whoami --format json`相当の完全情報がない。複数登録対照を満たす公開契約の採用決定＋reviewer受入が必要 |
| B2 | 対象actas ownerを副作用なしで照会し、正常不存在・不正・読取不能・競合不明を区別 | 公式`doctor.sh`はread-onlyでownerとalive/STALEを表示する。`actas-claim.sh`は変更系入口 | doctorの空owner→lock=noneや表示省略は認可用契約ではない。#247で提供側readerを活かす厳格な照会を定義する |
| B3 | message lease/ackと配送の競合意味論 | send/inbox/historyはあるがforkの`claim.sh`は公式cutoffにない | fork message_claimsを外側DBへ複写するだけでは原子的ackを保てない。必要挙動と公開対応を個別決定 |

初稿ではdoctorの既存owner照会を責務表に含めていなかった。
公式cutoffの`doctor.sh`のowner読取・空値分岐・alive/STALE表示と、`lib/actas-lock.sh`の`actas_lock_owner`を読み直し訂正した。
「公式にowner照会手段が皆無」ではなく、正常不存在と失敗を潰さない認可用公開契約の不足である。
観測目的でclaim/releaseを呼ばず、doctorの表示が空またはlock=noneであることをPASS認可へ変換しない。
agmsg側の公開拡張を必要とする場合はbreakerへ返し、正式に利用可能になるまで当該接続を止める。
この手番では公式へのPR、隠れたpatch、fork上書き、許可の緩和は実施しない。
「公式無改変」と「同じ内部fileをagguildから読むだけ」を同一視しない。

### #236 / #239の解除契約

#236 launcher・broker・collectorはagguildに所属し、binding/logs/policyはagguildが所有する。
agmsgのclaimと登録をagguild_poolのmanifestへ複製して認可正本にしない。
#239のaccount/guard経路もagguildに所属し、roster seat typeを根拠にvendor/accountを解決する。
personaや環境変数の自己申告だけでaccountを選ばない。
#239の移行後API依存と、現行環境でのPATH・実行先是正は別問題である。
後者をB1/B2の上流提供待ちへ一律に巻き込まない。

P2解除に必要なのは、B1/B2を満たす公式提供版、固定した公開契約、独立した正負対照、consumer統合受入のすべてである。
fork試作、未merge提案、PR #245のAPPROVED/MERGEDは公式API提供の代替にならない。
P2がmessage lease APIを使わないと契約で固定できればB3の全移行完了までは待たない。
ただし現P2経路がB3へ暗黙に依存していないことはproducerの宣言でなく固定HEADの回帰で示す。
公式PR #372がOPENでmessage claim/ackを対象とするという情報は追加breaker断定の引用であり、本手番の再照会ではない。
message claim/ackをactas owner照会の代替にしない。
PR #245の受入と本節の受入の両方が揃って初めて3接続実装を開始する。
本書の所属表だけの承認でB1/B2が解決したことにはしない。

## 互換性manifest

agguildは、agmsgの採用commit/version、使う公開commandと引数/出力/exit code、必須capability、CLI版、回帰結果の固定HEADを互換性manifestに保持する。
文字列versionの新旧比較だけで許可せず、未知schema・欠落capability・複数identityは非対応として停止する。
上流更新は別rootの隔離試験でmanifestを更新・reviewし、稼働rootへ自動更新しない。
未対応版から旧実装へ黙ってfallbackしない。
既存forkの稼働継続は未移行の現状であり、新agmsgの無改変要件を満たした成果とは呼ばない。

## 現役stateを守る移行条件

1. 物理移動前に、正式API経由で登録tupleとID、projectのcanonical path、actas owner、未処理message ID/既読境界、resume SID/process情報の取得可否を確認する。取得できない項目を推測せず移行ゲートを閉じる。
2. persona pathと登録project pathを別項目にする。pool配置変更からjoin/leave/resetを自動実行しない。symlinkなら安全という仮定も置かない。
3. 旧rootの稼働process・claim・未処理messageを残し、隔離fixtureで新構成を評価する。同じlive DBへ旧新writerを同時接続して試さない。
4. 限定pilotは同teamの専用名・専用projectを使い、現席のownerを奪わない。shadow roster/claimやメッセージ全複製で継続を装わない。
5. 将来の切替時は対象席単位に入力の保留点、ack済み境界、未処理ID、旧process終了、新process一意性、resume履歴を確認する。無停止は未実証のため保証しない。
6. rollbackは旧rootと登録pathを保持したまま、新側の入力・writerを止めてから行う。新側に処理済みmessageがあれば古いDB snapshotを戻さない。cursorと未処理IDを照合できなければ停止を維持する。
7. 旧root・旧recordの削除は全対象の受入・復旧確認後の別依頼とする。全席一括移動、未読reset、全claim GCを移行手段にしない。

agmsg DB・runtime fileの物理パスやschemaの互換性は未確定であり、本書をデータ移行コマンドとして実行しない。

## 受入試験と対照

| 観点 | 正の対照 | 疑う失敗を再現する負の対照 |
| --- | --- | --- |
| registration完全性 | 1名1type1projectが一致 | 同名複数project/type。LIMIT 1集約を一意と誤認しない |
| 明示project | 他席のproject指定で正しく登録 | 呼出元SessionStart markerと異なるproject。自分のcwdへ吸収しない |
| actas | 同じcomposite ownerは一意 | 別PID同SID、読取不能、消失競合。unknownをfreeへ変換しない |
| 未処理message | 指定IDが一度だけ配送・ack | ack直前中断、並行writer、再開、二重consumer。件数だけでなくID照合 |
| resume | SID維持・generation更新 | pool path変更だけで別sessionへ紐付くケース。最新SIDへのfallbackを禁止 |
| guard | 正しいvendor/account・固定実行file | 同cwd多席、偽環境変数、PATH逆順、別shell。個人accountへのfallbackなし |
| pool例外 | persona固有scriptはそのpersonaから参照 | 同じscriptを共有運用へ流用。pool全体PATH公開を拒否 |
| 更新・切戻し | fixtureで旧新両版の境界を照合 | 新版処理後に旧snapshotへ巻戻す重複/欠落。無損失と誤判定しない |

試験は隔離rootから始め、value/cutoff/source/commandと主張に対応する生出力を別packetへ残す。
未処理内容・秘密値は公開しない。IDと集計だけで不足する場合は閲覧範囲を限定する。
同commitの偶然のPASS数一致を互換性証拠にせず、各保持挙動の対照を通す。

## READMEと次工程

今回の設計だけでは利用方法を変えないためREADMEは変更しない。
分離実装時にはagguildのREADMEへインストール、agmsg対応版、pool schema、起動・更新・停止・復旧・アンインストールを自己完結で記載する必要がある。
agguild_poolのREADMEはデータ構造とpersona例外・管理手順を記載し、共有実行層として案内しない。
公式agmsg READMEへ独自運用を書き足す方法は採らない。

次工程は#246/#247の独立設計・適合試験計画・提供側最小提案パッチの準備と、未分類hunkの責務別精査である。
実装パッチ作成は設計受入後のprogrammer別依頼とし、本手番で実装や上流投稿はしない。
本書・2方向一覧をmanagerへ渡し、公開入口不足をbreakerへ返却したことを報告する。
実装・物理移動・P2 gate解除・三分割完了はいずれも未達である。
