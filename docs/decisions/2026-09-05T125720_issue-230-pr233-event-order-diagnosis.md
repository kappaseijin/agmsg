---
type: Design
title: "Issue #230 / PR #233: path-keyed試験のイベント順序診断"
status: proposed
timestamp: "2026-09-05T12:57:20+09:00"
diagnostic_head: "81477708872954889f64ca0d47c05f54906dc603"
issue: "https://github.com/kappaseijin/agmsg/issues/230"
---

# PR #233 のイベント順序診断

## 目的と判断の境界

counted barrierの2回目をB、3回目をAとみなす試験の前提を、実際のresourceとイベント順で検証する。
原因は未確定である。製品ロジック正常、Ubuntu固有、試験だけの不具合のいずれも現段階では断定しない。
本書は診断手順だけを定める。fixture実装はprogrammer、独立実測と因果確定は既存verifier、formal reviewはClaude reviewerが担当する。
architectは実測・製品コード修正・CI再実行を行わない。

## 一次資料

- [breaker断定](https://github.com/kappaseijin/agmsg/issues/234#issuecomment-5549176865): 初回はmacOSのlog未生成timeout、直近2回はUbuntuのA解消ログ不存在assertion失敗。同じテストの失敗でも同じ失敗点ではない。
- [固定HEADのbarrier](https://github.com/kappaseijin/agmsg/blob/81477708872954889f64ca0d47c05f54906dc603/scripts/watch.sh#L673): process全体のcounterだけでsentinelを作る。
- [固定HEADの試験](https://github.com/kappaseijin/agmsg/blob/81477708872954889f64ca0d47c05f54906dc603/tests/test_watch.bats#L830): holder終了後にbarrier 2を解放し、barrier 3でB owner不存在とA解消ログ不存在を確認する。

上記ソースは同HEADのローカルcheckoutで読んだ。CIログの観測はbreakerの引用であり、architectの独立実測ではない。
GitHubでPR #233のHEAD一致、OPEN、blocked:reproduction / priority:highを確認した。
コード上は、B acquireがholder中に失敗すると、次pollのAがbarrier 2となる反例が可能である。今回のCIがこの順だった証拠はまだ無い。

## 診断記録契約

診断fixtureは固定HEADから隔離コピーを作る。元HEAD、計測用差分、差分digest、実行コマンド、OS・Bash・sqlite3版をpacketに保存する。
「同一HEAD」とは共通の製品baselineを指し、計測用差分まで無変更とは称さない。前後対照は同一の計測用差分で実施する。
既存のpending更新・release・timeoutの意味を変更せず、観測点とfixture用同期点だけを追加する。

各processは独立したJSONLファイルへ記録し、stdoutの配信streamへ混ぜない。保存先は試験teardown対象の外側に置く。
並行processの時刻だけで全順序を推測せず、process内の連番とcontrollerのack IDで因果順を結ぶ。

| field | 契約 |
| --- | --- |
| run / condition / event / seq | 実行ID、対照名、イベント名、process内単調連番 |
| pid / role / poll / pair | 実際のprocess PID、watcher/holder/controller、poll番号、team/agent |
| resource / path / db | exact runtime resource、gate path、解決済みDB path。A/BのDB同一性を記録 |
| barrier / caller / sync_id | 実際のcounter値、通常終端・skip・stdout失敗・ownership喪失・despawn・cleanup等の呼出し元、同期ack |
| rc / pending_before / pending_after | 対象操作の元の戻り値、pendingのpath配列。未実行はnull、空集合は[] |
| held_path / owner / alive | clear前のheld path、owner観測の値と観測rc、controller側のwatcher生存観測 |

観測点は起動、各pair開始、acquire前後、acquire失敗skip、barrier到達/解放/timeout、release前後、pending更新前後、cleanup開始/終了、process終了。
release直前にpathを退避してから記録し、helperがheld変数をclearした後の空値を対象pathと誤記しない。
loggingの失敗で元のrcを上書きしない。必須record欠落・seq欠番・JSON不正は「診断不成立」でありPASSではない。
cleanupのowner削除と通常release成功はcallerで区別する。owner不存在だけでは成功判定しない。
生存はpidfileだけで判定せず、controllerのprocess観測と、終了時のwait statusを残す。
holderはBEGIN成功ackとCOMMIT成功ackを出す。release要求file作成だけではDB解放を証明しない。
ログはfixtureのIDと状態のみとし、本番messageやcredentialを収集しない。

## 対照の実行順序

共通準備: A/Bのresourceは異なりDBは同じと記録する。watcherのA取得成功とrelease前到達をackで確認してからholderを開始する。
A releaseが非0となりpending={A}へ入ったrecordを待つ。成立しないrunは診断不成立とする。
fixture同期はresource/poll/eventで指定し、counter 2/3を同期対象の識別子にしない。counterは観測値として残す。
各待機は有限deadlineを持ち、timeout時は全recordを保存して終了する。sleep増量を対照にしない。

| condition | 強制する順序 | 観測したいこと |
| --- | --- | --- |
| PRE: B acquireを解放前に進める | holderを保持→B acquire非0とskipのack→COMMIT成功ack→次pollを許可 | 次のbarrierのpathがAになるか。A正常releaseでpendingが解消した後に旧assertionが失敗するか |
| POST: B acquireを解放後に進める | B acquire直前で停止→COMMIT成功ack→B取得・通常release成功→Aの次release直前で停止 | B通常releaseの前後でpending={A}、A解消record無し、watcher生存。続けてA正常releaseでpending=[]・解消record一回 |
| CLEANUP: B所有中の終了 | B取得後にfixtureから終了signal→cleanup recordとwait statusを取得 | B owner消失がcleanupでも起きることを示し、通常release判定がこのrunを拒否するか |

PREではholder解放後も次pollの開始をcontrollerが管理する。Aが再取得できず別のskipが起きた場合も記録し、期待経路を通ったと扱わない。
POSTではB正常release後、Aの解消前という観測窓をackで固定する。第三barrier到達だけではその窓とみなさない。
CLEANUPはfixture watcherだけが対象。teardown由来のsignalも区別し、診断ログを回収した後にfixture processを回収する。
環境差を調べる場合は同じ対照をmacOSとUbuntuで実施し、HEAD・差分・同期条件を固定する。新規CI rerunはこの段階に含めない。

## verifierへの依頼と判定

programmerが診断fixtureと差分を提出した後、既存agmsg_verifier_codexがPRE/POST/CLEANUPを独立に実行する。
各条件の成立record、旧assertionの結果、pathで判定した新しい観測結果を併記する。
報告はvalue / cutoff / source / commandと生ログpathを含め、操作前後のpending集合、barrier番号とpathの対応、caller、rc、生存・終了を表にする。

PREでAがbarrier 2、POSTでBがbarrier 2となり、旧assertionだけがPREで誤判定するなら「counterとpathの同一視を壊す制御反例」が成立する。
これは過去CIの実順序を確定したことにはならない。過去CIとの一致は追加のイベント証拠が無ければ未確定と残す。
POSTでBの通常releaseがAのpendingを消す場合は製品状態更新の疑いとして報告し、本体再設計へ自動的に進まない。
cleanup混入、起動未完了、記録欠落の場合は原因を確定せず、欠けた観測点を具体化してarchitectへ返す。

## 因果確定後の最小修正の選び方

現段階で修正案を確定しない。verifierの証拠に応じて次の方針を選び、別の実装起点依頼へ引き渡す。

1. counter/path取り違えが確定: 試験の同期・assertionをresourceとイベント種別に結び付け、B正常release後・A解消前を直接確認する。
2. cleanup混入が確定: alive、caller=normal、release rc=0を試験の成功条件とし、timeout/EXITを成功扱いしない。
3. 起動前timeoutが確定: startup到達条件を特定してfixtureの待機対象を修正する。単純な時間延長を結論にしない。
4. POSTでもpending更新が誤る: PR #232本体契約との不一致を記録し、manager/breakerへ範囲再判断を戻す。

修正後の必要条件は、正常対照PASS、path状態を壊すmutation KILLED、対象テストを実際に実行したCI、新HEAD全差分のClaude formal reviewである。
この文書の保存は実装・rerun・mergeの開始を意味しない。

## 非対象

Issue #222、PR #232本体ロジックの再設計、テスト削除、sleep増量、無変更rerun、README変更、本番DBやwatcherへの操作。
未確定の原因を説明するためのコード修正は行わない。
