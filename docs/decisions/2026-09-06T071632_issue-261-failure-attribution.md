---
type: Investigation
title: "Issue #261: skip表示と実際の失敗境界の分離"
timestamp: "2026-09-06T07:16:32+09:00"
status: proposed-diagnostic
issue: "https://github.com/kappaseijin/agmsg/issues/261"
source_head: "732938a8ccf4d1227359a52c4ebf52c997bf5720"
producer: agmsg_architect_codex
reviewer: agmsg_reviewer_claude
---

# 結論

**保存済みログだけでskip helperのバグ、reaperのkill失敗、#260の副作用のいずれも確定しない。**
最小是正案は、対象試験の失敗位置を親Batsへ明示的に返す限定診断である。
`skip_on_windows`へ無条件return 0を足してgreenを狙う修正は採らない。
根本修正は診断で失敗した条件を特定してから別途採用する。
既存mergeは巻き戻さず、実装・rerunは本手番では行わない。README影響なし。

## 証拠パケット

確認日時はfrontmatter。cutoffは上記固定HEAD、実行はrun33992757299。

| value | source / command | 限界 |
| --- | --- | --- |
| not ok 253。表示はhelper479行/test240行。前後252/254はok | [job 101377861453](https://github.com/kappaseijin/agmsg/actions/runs/33992757299/job/101377861453)の497–501行。`gh api --allow-escape-sequences repos/kappaseijin/agmsg/actions/jobs/101377861453/logs` | 表示stackだけでは失敗commandの実体やsignalを識別できない |
| Bash5.2.21 / Bats1.13.0 | 同log184–186行 | ローカルBats1.14.0の結果をこの環境の再現証拠にしない |
| helperはunameのcase判定のみ。MINGW/MSYS/CYGWINでskip、それ以外に失敗命令なし | 固定HEAD `tests/test_helper.bash:478`。`git show <head>:tests/test_helper.bash` | CI当該呼出のuname出力/rcは未記録 |
| helper直後に`( set -e; ... )`。内側cleanup EXIT trapと多数のassertionがある | 同HEAD `tests/test_codex_bridge_launcher.bats:239`以降 | 内側の最終成功phase、実際に失敗したassertionは保存ログにない |

GitHubは`GH_CONFIG_DIR="$HOME/.config/gh-4codex" rtk gh ...`、固定sourceは`rtk git show`で直接再取得した。
graph検索でhelperを発見し、固定HEADソースで裏取りした。
ctx検索には当該失敗の履歴hitがなく、過去の別CI障害を原因説明へ転用しない。

## なぜskip表示だけで直さないか

Linuxでcaseのどのpatternにも一致しない正常経路を、最後の条件式がfalseになるhelperと混同しない。
case不一致を根拠にreturn 1と説明することは誤りである。
また、前の試験の`export MSYSTEM=MINGW64`をそのまま次試験への漏洩原因としない。Batsは各testを独立processで実行し、このhelperはMSYSTEMでなくunameを読む。

Bats1.13.0の[tracing実装](https://github.com/bats-core/bats-core/blob/v1.13.0/lib/bats-core/tracing.bash)はDEBUGで直前stackを保存し、ERR/EXITから保存stackを使って失敗行を表示する。
直後のsubshellで更新した変数は親へ戻らないため、内側失敗を親が受ける際に直前helperのstackが残る可能性がある。
これは今回の誤帰属仮説であり、同版/Bashでの再現は未実施。
実際のhelper側異常を除外する証拠もないので、まずhelper通過とsubshell内のphaseを区別する。

## 最小の診断是正案

1. 対象testだけにhelper呼出前/通過後のphase markerとuname値・rcを安全な専用ログへ記録する。uname失敗をLinux正常と見なさない。既存Windows skip契約は変更しない。
2. isolated bodyの条件を「snapshot取得」「pidfile ready」「dispatcher/bridge live」「owned包含」「foreign除外」「reap」「消失待機」「foreign生存」のphaseに分け、各失敗時にphase/rcと必要なPID集合を出す。set-eによる無言終了だけに頼らず、失敗を明示returnする。秘密・command line全文は保存しない。
3. 親Batsはbodyの終了値と失敗packetを受け取り、非zeroをそのまま失敗へ伝播する。内側のcleanup trapを維持し、cleanup失敗と主失敗を別fieldにする。cleanupの成功で主失敗を上書きしない。
4. 診断結果が示す条件にだけ最小修正を起草する。準備完了より前のsnapshotなら既存deadline内のnamed predicateへまとめる案、所有集合誤りならそのpredicateだけの是正案へ進む。証拠なく待機延長やkill対象拡大をしない。

起草するPRの主張は「この試験の失敗位置とrcを誤帰属せず返す」に限定する。
別原因の本修正や#262を同じPRへ混ぜない。

## 正負対照と受入

偽陰性は「helperは成功したが内側bodyが失敗し、helper失敗と表示される」条件で起きる。
同じBats1.13.0/Bash5.2の隔離対照で、helper通過後の既知phaseを意図的に失敗させ、nonzeroと正しいphaseの両方を要求する。
helper自体を失敗させる別対照と区別し、内側失敗を消した正対照はbody終了まで成功することを確認する。
foreign controlを誤って候補へ混ぜる負対照は依然として失敗し、reap失敗・wait失敗も成功化しない。
対象自身のok、body/reaperの終了、前後試験、必要CI全passを独立verifierと固定HEAD全差分reviewで受け入れる。
採否はbreaker、実装は別起点のprogrammer。調査保存完了は修正完了ではない。
