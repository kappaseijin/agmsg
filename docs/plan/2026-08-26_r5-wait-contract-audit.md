---
type: Plan
title: "R5: 観測条件と実時間 deadline を欠く待機の横断走査"
status: proposed
root_cause: R5
root_issue: "https://github.com/kappaseijin/agmsg/issues/178"
base_commit: "9e9882b632a62d149873f38ed57ef0e086a07b91"
timestamp: "2026-08-26T09:58:17+09:00"
---

# R5: 観測条件と実時間 deadline を欠く待機の横断走査

## 結論

R5 は、環境を pin しても消えない。「何を待つか」を時点値・回数・固定 sleep で決め、観測できる状態と実時間の上限を結び付けていない問題である。
`tests/` と `scripts/` の機械走査では、固定 `sleep` が 41 ファイル・270 件見つかった。すべてが不具合ではない。fixture の寿命や「何も起きない」を測る観測窓を除くと、以下の production 7 単位、test 5 単位、context 要確認 2 単位が R5 である。

本記録は Issue #178 の本文差替えと子 Issue/PR 分割の材料である。実装 PR は作成しない。R1（環境漏れ）、R2（失敗理由の消失）、R3（delivery status）は扱わない。

## 走査方法と対照

対象はこの worktree の `scripts/` と `tests/` だけであり、稼働中の `~/.agents/skills/agmsg/` は試験台にしない。
`sleep N`、`seq 1 N`、`for ... $(seq ...)`、`while ... -lt N`、`until`、`retries`、`wc -l` / `grep -c` / `jq length` を検索した。

| 対照 | R5 を検出できる理由 | 現行の確認 |
| --- | --- | --- |
| 正: #88 `cmd_sync_start` | ready marker と started PID を確認し、`SECONDS + ready_timeout_s` で止める | `scripts/remote.sh` は実時間 deadline を使う。1600 回反復は残っていない |
| 正: #151 #937 helper | old identity の exit event・lease 消滅・replacement/survivor state を実時間 deadline まで確認する | `tests/test_codex_bridge_launcher.bats` の `_wait_for_*identity` は `date +%s` deadline を使う。count は補助 assertion である |
| 正: roster-sync / sync-autostart | child の exit sentinel または request status を観測し、`SECONDS` deadline を使う | fixed sleep は poll interval であって終了条件ではない |
| 負: fixed-lifetime fixture | `sleep 30 &` は待機ではなく、生きた偽 PID を供給する | lifecycle assertion の完了をこの sleep で判断していないため R5 ではない |
| 負: absence window | warning が出ないことには arrival event がない | `test_watch.bats` の `run_watcher_for` は「2 秒間 warning がない」を意図して測る。期間の根拠をコメントで残し、状態待機へ偽装しない |

`tests/test_wait_helpers.bats` は現行 HEAD で 7/7 green だった。しかし helper の green は「100 tick 内に見えた」だけで、10 秒の実時間上限を証明しない。この正対照は、回数上限を時間上限と取り違えないために使う。

## 既知 R5 の再判定

| Issue | live state | 現在の実装 | 判定 |
| --- | --- | --- | --- |
| #88 | closed, `root:R5` | `remote.sh cmd_sync_start` は capabilities marker + started PID を `SECONDS` deadline で待つ | 修正済み |
| #151 | closed, `root:R5` | #937 の replacement/survivor helpers は identity、exit event、lease を deadline 内で観測する | 修正済み。full-suite と scoped 実行の過去差は、この helper を「count-only」へ戻す根拠にはならない |
| #164 | closed, `root:R5` | writer mutation は delivery gate で直列化済み | TOCTOU の線形化は修正済み。ただし同じ library の gate acquire/release は 50 attempts を 5 秒と見なしており、下記 R5-P3 として未解決 |

Issue #178 にある「#245」は GitHub Issue 番号としては存在しない。PR #172 にある `watch: delivers a burst of 8 consecutive messages without loss (#245)` の test marker として現行 source を確認した。pidfile は送信前に watcher が armed であることを確認するためだけに使い、完了判定は最後の `BURST-8` の到着である。したがって固有の R5 子 PR は不要で、R5-T1 の shared wait 修正だけが適用される。

## 未解決 R5 台帳

| ID | 現在の待機と誤った判断 | 判定 | 一主張の子 PR 単位 | 受入・KILLED control |
| --- | --- | --- | --- | --- |
| R5-P1 | `spawn.sh` は ready sentinel を `waited >= READY_TIMEOUT` と 1 秒 sleep の回数で timeout とする | replace | `spawn --ready-timeout` を ready sentinel + 実時間 deadline にする | slow scheduler で 1 秒 poll が遅れても指定秒数を越えない。旧 counter loop に戻す mutation は wall-clock budget test を red にする |
| R5-P2 | `despawn.sh` は lock が free になるまで `waited >= TIMEOUT` と 1 秒 sleep を数える | replace | `despawn --timeout` を lock state `free` + 実時間 deadline にする | release fixture は state を観測して success、never-release は deadline で exit 3。counter 復元 mutation は deadline assertion を red にする |
| R5-P3 | `actas_lock_gate_acquire` / `release` は 50 SQL attempts を「5 秒」と扱う。各 SQL busy timeout と scheduler 遅延で実時間が延びる | replace | ownership writer gate の retry budget を実時間 deadline にし、attempt count は diagnostic にだけ残す | live holder が deadline 前に release すれば acquire、release しなければ deadline で fail-closed。50-attempt mutation は slow SQL stub で red にする |
| R5-P4 | JSONL migration と storage lock は 1000 回の mkdir + `sleep 0.01` だけで失敗を決める | replace | JSONL lock/migration を lock ownership condition + configurable real-time deadline にする | held lock / delayed mkdir の正対照で elapsed を上限内に保つ。1000-count 復元 mutation は slow filesystem seam で red にする |
| R5-P5 | remote `unlock` の capabilities wait は 50 回、owned engine reap は signal ごと 100 回で判定する | replace | remote lifecycle の readiness/reap を observed marker/PID exit + deadline にする | marker 出現と TERM/KILL exit をそれぞれ観測する。counter loop 復元 mutation は injected slow process で red にする |
| R5-P6 | Codex bridge launcher は identity discovery を 20 回、reaped bridge exit を `_REAP_WAIT_TICKS=50` 回で打ち切る | replace | bridge launcher の startup identity/reap wait を lifecycle condition + real deadline に統一する | delayed registration と delayed exit で、deadline 内 success / deadline 外 fail-closed を確認する。tick constant 復元 mutation は red にする |
| R5-P7 | Codex monitor は app-server port banner を 100 回 poll して plain Codex fallback を決める | replace | monitor の app-server readiness を banner + process state + real deadline にする | banner が deadline 内に出れば bridge、出なければ fallback。100-count 復元 mutation は slow banner fixture で red にする |
| R5-T1 | `tests/test_helper.bash` の 5 helper は `_WAIT_TICKS=100` × 0.1 秒。多くの suite がこの暗黙の回数契約に依存する | replace | shared test wait helpers を predicate + real-time deadline にし、timeout diagnostics を共通化する | delayed file/PID positive と never-arrives negative を測る。slow poll の下で旧 100 tick loop が deadline を越える mutation を red にする |
| R5-T2 | `test_watch.bats` / `test_watch_install_changed.bats` は shared helper と重複する 100/150/200 tick helpers、cursor polls を持つ | replace | watcher test の duplicate waits を R5-T1 helper へ集約し、cursor/read receipt の到達を predicate にする | watcher armed、cursor advance、read receipt を直接待つ。fixed tick helper 復元 mutation は delayed writer で red にする |
| R5-T3 | `test_delivery.bats`、`test_despawn.bats`、`test_actas_integration.bats` は watcher armed、PID exit、message delivery を `sleep 1..4` 後の snapshot で判定する | replace | lifecycle test の positive state waits を ready sentinel / exit / output/read receipt の condition wait に置換する | delayed watcher/delivery の下でも state 到達時に pass。sleep snapshot 復元 mutation は遅延した正しい transition を false failure にして red にする |
| R5-T4 | `test_remote_engine_start_refusal.bats` と remote lifecycle tests は pidfile、process exit、barrier arrival を 200/400 回の loop で待つ | replace | remote test の pidfile/exit/barrier waits を R5-T1 helper と explicit deadline に統一する | delayed pidfile/TERM fixtureで timeout を実時間に固定する。iteration bound 復元 mutation は slow fixture で red にする |
| R5-T5 | `inbox.sh`、`check-inbox.sh`、`remote.sh`、actas gate、bridge launcher の test-only barrier は release file を 100〜1200 tick 待つ | replace | test barrier seam の timeout を release condition + shared real-time deadline にする | release marker で進行、未releaseで期限切れを明示する。tick cap 復元 mutation は slowed poll で red にする |
| R5-C1 | #937 の legacy/no-lease/malformed-lease tests は dispatcher を起動後に `sleep 3` して fake process が生存すると判定する | needs context | reaper が当該 lease を検査した完了 marker を既存 protocol に出せるか決めてから、absence window でなく marker + survivor liveness にする | marker を追加するなら、marker 無しの旧実装を KILLED。現状は「何も kill しなかった」を確定する観測条件がない |
| R5-C2 | `_session-start.sh` は rollout discovery を最大 2 再走査する。これは session start の best-effort補助で、timeout/成功を外部へ宣言しない | needs context | retry を deadline に変える価値と、thread 未解決時の fail-open 契約を先に決める | 現行の 2-pass 探索は delivery/ownership state を決めない。R5-P6 と混ぜない |

## 意図的なもの・R5 ではないもの

| 範囲 | 理由 |
| --- | --- |
| #88 `cmd_sync_start`、#151 identity waits、registry lock、roster-sync driver、sync-autostart、Codex `watch-once`、perf harness | すでに predicate と `SECONDS` / epoch deadline を持つ。sleep は poll interval であり終了条件ではない |
| fixture の `sleep 5 &` / `sleep 30 &` / `sleep 60 &` | 生きた PID・長寿命 process を作る入力であり、テストの success 時点を決めない |
| `test_watch.bats` の `run_watcher_for` / `run_named_watcher_for` | warning 不在という absence を測る有限の観測窓。期間が短すぎれば弱いので、poll interval と最低二周期の根拠をコメントに残す。positive delivery wait に流用しない |
| `actas_lock_gate_try_acquire` の 4 attempts | watcher poll は live holder を待たず fail-closed でその poll を捨てる短い busy retry。writer wait の R5-P3 と異なり、delivery completion を判定しない |
| `actas_lock_claim` の 3 stale-reclaim tries | sleep を伴わない CAS 再試行であり、外部 state が将来変わるのを待つ timeout ではない |

## Issue #178 差替え用の主張

**題名**: `R5: lifecycle と test harness の待機を観測条件 + 実時間 deadline に統一する`

> R5 は環境 pin では解決しない。未解決の待機を R5-P1〜P7、R5-T1〜T5 として一主張ずつ分割する。各 PR は、完了を表す predicate、開始時に固定する実時間 deadline、deadline での named failure、遅延 fixture を使う正対照、counter/sleep へ戻す KILLED control を必須とする。R5-C1/C2 は protocol/availability の判断を先に行う。#88/#151 は修正済み対照として維持し、#164 の線形化修正は尊重するが、残る writer retry budget は R5-P3 として別に扱う。

## 着手順

1. R5-T1 — shared test helper を real-time deadline 化する。
2. R5-P1 / R5-P2 — public lifecycle timeout の意味を実時間へ戻す。
3. R5-P3 / R5-P4 — ownership/storage lock の実時間 budget。
4. R5-P5 / R5-P6 / R5-P7 — remote/Codex lifecycle.
5. R5-T2〜T5 — focused test suites を shared helper へ移行する。
6. R5-C1 / R5-C2 — protocol decision 後に別 Issue とする。

各子 PR は producer `agmsg_programmer_codex`、formal reviewer `agmsg_reviewer_claude`、fixed HEAD の全差分 review とする。本記録は design-only であり、production source は変更していない。
