---
type: Investigation
title: "Issue #255: Windows bridge cleanup失敗の一次診断"
status: awaiting-windows-observation
timestamp: "2026-09-06T02:34:04+09:00"
issue: "https://github.com/kappaseijin/agmsg/issues/255"
source_head: "32f6411e4e7a13d42e0811fc023c2d8d51138cb1"
---

# Windows bridge cleanup失敗の一次診断

## 確定事項と未確定事項

失敗の直接経路はtaskkillの非zero結果をreaperが失敗として保持する分岐である。
taskkillが列挙対象の子processを終了させる途中で「存在しない」と返す根本原因は、現在の証拠だけでは確定しない。
自然終了・先行cleanup・tree kill内の競合を区別するWindows追加観測が必要であり、修正方式はまだ採用しない。
macOSでの静的読取をWindows実測や再現済みとは呼ばない。

producerはagmsg_architect_codex、計測実装は別依頼後のagmsg_programmer_codex、独立実測は既存agmsg_verifier_codex、formal reviewerはagmsg_reviewer_claude。
本手番では製品/試験コード変更、無変更rerun、試験削除、PR #252巻戻しをしない。

## 一次資料

[PR #252失敗job](https://github.com/kappaseijin/agmsg/actions/runs/33979472929/job/101341990942)を`gh run view 33979472929 --repo kappaseijin/agmsg --job 101341990942 --log-failed`で取得した。
statusCheckRollupは21 SUCCESS / 1 FAILURE。
対象は`launcher: windows-native starts the bridge (#567)`、テスト1401行のfalseで失敗している。

| 観測 | 生ログの値 |
| --- | --- |
| taskkill対象native PID | 8136 |
| taskkill結果 | rc=255 |
| エラー対象 | PID 7420、parent PID 8980、`There is no running instance of the task.` |
| 成功報告 | PID 8980、2384、9248、888、8136の終了 |
| 他の試験 | 同jobの残り4試験はok |

成功報告とエラー報告が同じtaskkill結果に混在している。
「ルートPIDのkillが失敗した」「残存processがあった」とは、このログから直ちに言えない。
wait失敗/残存警報の行は取得した失敗出力にはないが、全子processの最終snapshotそのものは記録されていない。

workerが#238/#241の過去job収集を試みたが、対象過去失敗jobの生ログは欠測だった。
取得できた最終Windows jobは#238のrun33951369864/job101269493676と#241のrun33959685522/job101291508507で成功というworker報告である。
過去レビューのflake記述や後続成功を、今回の原因証拠にしない。
収集原資料は`/tmp/agmsg-issue254-255-GDxjcG/`。保存期限による取得不能ならその欠測を保持する。

## ソースからの因果経路

`tests/test_codex_bridge_launcher.bats`の以下を固定ソースで読んだ。

1. `cleanup_windows_native_processes`はMSYS dispatcherとparentへsignalしてwaitする。
2. `_windows_native_bridge_pids`はWin32_Processのcommand lineでrootを絞り、tasklistでnative PIDを確認する。
3. `_windows_native_reap_bridge_root`は各候補へtaskkill /PID /T /Fを実行する。
4. taskkill非zeroで直ちに`reap_status=1`を保持する。その後tasklist消失待機とroot残存確認が成功しても0へ戻さない。
5. 呼出元はtarget cleanup、foreign control、foreign cleanupのいずれか非zeroでfalseへ進む。

この保持分岐は既存の「taskkill失敗後も全候補を試し、最終検査も行う」試験で意図的に検査されている。
従って`|| true`を付けるだけでは既存の失敗検出契約を破る。
PIDはnativeとして取得され、今回のtaskkill出力もnative treeを示す。ただしPID再利用の完全な排除はこのログだけではできない。

## 仮説と識別に必要な観測

| 仮説 | 決め手となる対照 |
| --- | --- |
| H1 子が自然終了してtree killと競合 | child終了をbarrierでkill前/kill中/kill後へ制御し、同じrcと終状態を再現 |
| H2 先行MSYS cleanupまたは別候補のtree killが子を先に終了 | signal/対象列挙/taskkillごとのPID・親・開始識別子・時系列を照合 |
| H3 本当に終了不能なprocessが残る | kill後も同じPID/開始識別子が生存する対照で、最終確認が必ず失敗 |
| H4 PID空間や再利用による誤対象 | MSYS PIDとnative PID対応、CreationDate、root一致の前後snapshotを照合 |

H1/H2は有力な候補だが確定ではない。
今回は失敗保持分岐までの原因経路を確定した段階で、OS側の終了競合の原因確定は次の計測に分ける。

## 次の診断実装への限定手順

programmerは固定HEAD上で計測専用patchを準備し、Windows runnerで同じ試験を一回の診断条件群として実行する。
無変更rerunではなく、以下の不足証拠を補う最小計測を先に加える。

1. cleanup直前の対象rootとforeign rootのprocess treeをPID/PPID/CreationDate/root一致/生存状態で記録する。command line全文や秘密は出さない。
2. MSYS signal前後、候補列挙後、各taskkill前後、最終残存検査で同じmetadataを記録する。
3. taskkill stdout/stderr/rcとtasklist照会結果・時刻を保存する。状態取得不能はunknownとして失敗させる。
4. 既に終了した対象、短命childを持つ対象、cleanup後も生存する対象、foreign rootの4条件を用意する。
5. 自然なsleep揺らぎだけでなく同期barrierで対象の終了順を固定する。生存対照ではfixture-owned processだけを使う。
6. 全候補と捕捉した子孫の最終状態を確認し、別rootは生存したままであることを記録する。

観測したPIDだけを盲目的にkillしない。kill直前にroot/開始識別子の一致を確認できない場合は停止する。
出力に「存在しない」が含まれることだけを成功条件にしない。
残存、状態不明、wrong-root、permission failureをそれぞれ成功へ誤変換しない負の対照を要求する。
診断packetはvalue/cutoff/source/commandと同期条件・生出力を対応させ、verifierが同じWindows失敗モードで独立に検証する。

## 修正方針を確定する条件

終了競合が再現され、実対象と全捕捉子孫が終了済みであると確認できた場合に限り、正常な終了済み状態と真のcleanup失敗を分ける契約変更を検討する。
残存またはunknownがあれば、先にその原因を追う。
rc無条件無視、continue-on-error、試験削除、timeout延長だけでgreenにしない。
原因packetと対照をreviewerへ渡してから修正PRを進め、固定HEAD全差分reviewと必要CI全passを要求する。
既存mergeは巻き戻さない。READMEへの影響はない。
