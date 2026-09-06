---
type: Design
title: "Issue #255: 3固定条件へのcollector最小適用契約"
timestamp: "2026-09-06T07:54:32+09:00"
status: superseded-in-part
issue: "https://github.com/kappaseijin/agmsg/issues/255"
collector_baseline: "732938a8ccf4d1227359a52c4ebf52c997bf5720"
designer: agmsg_architect_codex
producer: agmsg_programmer_codex
verifier: agmsg_verifier_codex
reviewer: agmsg_reviewer_claude
---

# 最小適用契約

本書の通知生成時刻による実寿命・実世代・親子所属・kill前後の確定と比較開始ゲートは撤回した。
[評価器意味論訂正](2026-09-06T082200_issue-255-evaluator-semantics.md)を正本とする。
旧preflightは通知捕捉と欠測対照の範囲に限定され、実時刻・実世代の受入を示さない。
v3の訂正試験が成功しても比較はblockedのままである。以下の旧記述は経緯として保持する。

## 主張・権限・固定条件

1件の主張は「fixture・cleanupの意味を変えず、3固定条件のroot/子孫と操作境界を対応付けたpacketを独立評価できる」とする。
[breaker正本](https://github.com/kappaseijin/agmsg/issues/255#issuecomment-5555124640)に基づく設計である。
programmerは別診断branch/worktreeへ実装する。診断品質の受入前に3条件の比較を開始しない。
本書は実装への契約引渡しであり、collector適合済み・根本原因確定・formal approvalではない。

| condition_id | 被験source HEAD | 固定する性質 |
| --- | --- | --- |
| original | 32f6411e4e7a13d42e0811fc023c2d8d51138cb1 | capture delay/bridge寿命/既存cleanup判定 |
| polling-hold | 28b2b78f4e48297b62a970921a0cc9f6e932fdd7 | 短周期hold・release手順・既存cleanup判定 |
| stable-hold | b0156754dd706bf3ffc178c654f6ccbf23f340d8 | 単一sleep300の既存fixture。正式採用とはしない |

同一collector/evaluator・適用adapterの固定HEAD/hashを3条件で共用する。
条件別adapterの適用結果hashはそれぞれ記録する。被験sourceと適用後treeを区別する。
#259への追加push・merge、rc無視、寿命/hold変更、production reaper変更、実運用変更は禁止。
#261のdiagnostic実行はarchitect担当でなくprogrammerへ訂正済み。本書へ混ぜない。
README影響なし。既存mergeは巻き戻さない。

## 現行collectorの不足と変更範囲

固定baselineの`tests/windows/process-lifetime-collector.ps1`全体と`tests/test_windows_process_lifetime_collector.bats`を読んだ。
現行CLIはpreflight/evaluateのみ。Write-ProcessTraceRecordはTargetPid以外を捨てる。
Get-Generationは同PIDの最後のstartを返し、Drain-TraceEventsはstart sourceを先に、stop sourceを後にdrainする。
Evaluate-Recordsは最初のtarget start/stopとtaskkillを選ぶ単一対象評価である。
したがってTargetPidフィルタを外すだけではPID再利用・遅延配送の世代混同を防げない。
既存Batsは成功時summaryだけを表示し、full packetはBATS_TEST_TMPDIR内にある。

変更はcollector/evaluator、隔離fixture adapter、その対照test、artifact保存に限定する。
既存preflightの単一対象契約を保持し、適用packetをschema_version=2で区別する。
未知schemaを既存target-only evaluatorへfallbackしない。

## 観測入口とライフサイクル

collectorへcollect modeを追加する。入力はPacketPath、run専用ControlDirectory、RunManifestPathとする。
collectは被験processを起動・killせず、購読とcontrol messageの読取・記録だけを担う。
adapterが元の起動/cleanupをそのまま実行し、観測metadataだけを追加する。

1. Batsのsetup/対象root生成より先にcollectorを起動する。run ID付きreadyを購読確認後に発行する。ready未確認なら被験testを起動せず、subject_status=not-started、quality=unknown。
2. adapterは対象root生成後、target/foreign/already-ended等のroot_keyを登録する。起動したfixture controllerのnative PID/CreationDateと役割も登録する。MSYS PIDはnamespaceを明記して別fieldにし、native PIDとして使わない。
3. root照合は既存のroot predicateを観測に再利用するが、kill選択は変えない。CLI文字列は内部照合だけに使い、保存はroot_key・match結果・process metadataに限る。
4. root登録前・native PID判明前からのイベントをcollector内に保留し、後からscopeを付ける。sourceが別々でも受信順に世代を確定しない。開始を購読できなかったprocessはsnapshotがあってもtrace開始済みと偽装しない。
5. rootが終了しても、確定済み子孫の追跡は切らない。親世代に属する子の連鎖を保存する。別rootのcontrolは別scopeを維持する。
6. adapterのsubject終了後も元fixture cleanupの終端まで記録し、stop要求→drain→購読解除確認→packet closeを行う。stop markerだけで未配送eventが無いとは断定しない。必要event不足ならunknown。

control transportはrun専用directoryの一意ファイルとする。
各actorが自分のsequence/operation IDで一時ファイルを書き、同directoryへrenameして公開する。
collectorだけが最終packet JSONLを書き、複数processが同じJSONLへ直接appendしない。
ControlDirectoryは他runと共有しない。既存のfixture barrier/寿命をcontrol messageのack待ちへ置換しない。
追加観測にも時間的影響はあるため「無摂動」とは主張しない。観測コストと元のdeadlineを記録し、勝手に延長しない。

## 世代とerror PIDの対応

generation keyはrun ID/native PID/start event raw timestampの組とする。
Win32_Process.CreationDateとstart event時刻は異なる情報として保存し、数値が同じとの前提を置かない。
start/stopをraw event発生時刻で整理し、PID・親PID・生存区間・root関係が一意に対応した場合のみknownとする。
同PIDに複数世代があるとき、stopを「最後に受信したstart」へ割り当てない。
start欠落、stop欠落、区間重複、同時刻で識別不能、親世代不明はreason付きunknownとする。
parent PIDが一致しただけで別世代の子へ接続しない。

各taskkill出力に現れたPID（対象・成功子・エラー子・親）をoperation IDへひも付ける。
元outputも保存し、parserで認識できない行を成功扱いしない。parse qualityを別fieldにする。
error PIDに対応する世代をoperationの時刻区間から一意に決められなければ、候補列とunknownを残す。
後追いtasklistのgoneだけで以前の世代の終了を補完しない。
未捕捉error PIDはpacketから消さず、required-but-unmatched項目として残す。

## 操作境界とpacket schema v2

signal/taskkillの実呼出は元のfixture側に残す。collectorのpreflight用Invoke-RecordedTaskkillで置換しない。
adapterは元callの直前begin、直後endを記録し、元rc/stdout/stderrをそのまま返す。
ifで呼出全体を包んでset-eの働きを変えたり、計測失敗を被験rcへ混ぜたりしない。
MSYS signal、各native taskkill、wait、fixture cleanupを別operation IDにする。
beginのみでend不在ならoperation incomplete。出力の語から終了値を推定しない。

| record | 必須内容 |
| --- | --- |
| run-manifest | schema、run/condition ID、subject HEAD/tree hash、collector/evaluator/adapter HEADとSHA256、OS/Bash/PowerShell/Bats版、job/artifact対応 |
| subscription | ready/endの確認値、collector自身の開始/終了、clock quality、品質異常 |
| root-binding | root_key、役割、native PID/CreationDate、MSYS/native対応の品質、照合根拠 |
| process-event | start/stop、native PID/PPID、image、TIME_CREATED raw、発生/受信時刻、世代とscopeの評価状態 |
| operation | actor/operation ID、begin/end実行側時刻、namespace、PID/世代候補、rc、stdout/stderr、error PID対応 |
| subject-result | started/completed/aborted/not-started、元Bats/test exit code、対象自身のTAP、元cleanup rc、process終端確認 |
| quality-summary | known/unknownと理由、必要event対応数、未捕捉集合、世代別寿命、operation前/中/後の時間関係、termination_actor=not-determined（別証拠がなければ） |

event時刻・actor時刻・collector受信時刻を混ぜない。
既存raw UTC値は引用値として保持し、新設の人向け日時はJST RFC3339にする。
時計対応が不足する区間は比較不能。taskkill endより後のstopを、begin後というだけで「kill中」と呼ばない。
「known」は必要な対応が評価できた意味であり、全OS event無欠落・cleanup成功・原因確定ではない。

## 成功/失敗双方の保存と独立再評価

PacketPathは被験root/BATS_TEST_TMPDIRの外、RUNNER_TEMP配下の新規run専用directoryに置く。
full packet、operation原出力、manifest、subject stdout/stderr/TAP、summary、hash一覧を成功時も失敗時も保存する。
uploadはalways()経路とし、fixture cleanupで消さない。cancel/強制終了で末尾欠落ならincompleteとして再評価する。
artifact名はcondition/run IDを含み、download後にhash一致を確認する。summaryだけの取得で完了としない。

evaluateはpacketだけを読み、副作用なしで同じ世代対応・summaryを再生成する。
評価実行IDと収集run IDを分け、再評価のたびに収集run IDを書き換えない。
subject_exit_code、collector_quality、artifact_persistenceは独立の値とする。
被験test失敗でも観測品質knownはあり得るが、CI上の被験失敗を0へ変換しない。
診断成立の判定と製品/試験のpass判定を別欄にし、必要CI未達のmerge禁止を維持する。
missing packet、unknown schema、parse failure、欠測、世代/時計不明はunknownである。process rc=0だけをknownとみなさない。

## 比較開始ゲートと対照

同じ適用固定HEADを既存verifierへ渡し、次を先に受け入れる。

1. ready後にfixture-owned短命childがsnapshot間で開始・終了する正対照。rootだけでなくchildのstart/stopと親世代が保存され、downloadしたpacketから再評価できる。
2. そのchildのstopを1件だけ除いた別packetがunknown/missing-stopとなる負対照。他のroot/child stopがあるためknownへ逃げない。
3. 同PID別世代とevent配送順逆転のdeterministic packet対照。世代分離またはunknownを要求し、OSがPID再利用するまで待つ試験で代用しない。
4. subscription不全、start欠落、時計不明、error PID未捕捉、operation end欠落でunknown。正常なforeign controlは対象rootと混ざらない。
5. 被験非zeroとcollector known、被験zeroとcollector unknownを別々に保存する。両方のfull artifactを回収し、主失敗とcleanup結果を分離できる。

受入後に3固定条件を各1回実行する。非再現も結果であり、green待ちrerunをしない。
必要eventが不明なら明示的unknownをarchitectへ返す。全OS eventの無欠落証明は要求しない。

## programmerへの実装引渡し

1. 本書のpath/hashを受領し、別診断branch/worktreeを作る。
2. collect入口・世代評価・adapter・保存を1主張の差分に限定する。baseline preflight回帰も保持する。
3. 適用固定HEAD・差分・対照packetをverifierへ渡す。reviewerは固定HEAD全差分を一括走査する。
4. 3条件の結果をarchitectへ返す。root fixの採否はbreakerへ戻す。
5. 固定HEAD引渡し後の他Issue着手順はPMの最新割り振りに従い、#255診断PRのmerge待ちで席を止めない。

本手番は設計文書のみ。実装/実測/PR作成/mergeは行っていない。
