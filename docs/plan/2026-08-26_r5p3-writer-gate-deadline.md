---
type: Plan
title: "R5-P3: ownership writer gate の実時間 deadline 契約"
status: decided
root_cause: R5
root_issue: "https://github.com/kappaseijin/agmsg/issues/178"
base_commit: "3e74a7dde1876c2f9d2fb81fcc70770bf53f3cec"
timestamp: "2026-08-26T11:28:35+09:00"
---

# R5-P3: ownership writer gate の実時間 deadline 契約

## 結論

writer 用の `actas_lock_gate_acquire` / `actas_lock_gate_release` は、開始時に固定する5秒の実時間 deadline で終える。`50 attempts` は終了条件から削除し、diagnostic の `attempts` だけに残す。deadline 超過、permanent error、unknown error はすべて非0で返し、所有権・filesystem lock の mutation を成功として報告しない。

5秒は現行 source が意図している writer budget を維持する値であり、待機を拡大しない。production での環境変数 override は設けない。KILLED control 専用に `AGMSG_TEST_ACTAS_GATE_DEADLINE_S` を許可し、正の整数だけを受ける。

## 完了 predicate と失敗不変条件

| operation | 成功 predicate | deadline / failure 時に守る状態 |
| --- | --- | --- |
| acquire | 同じ `agmsg_runtime_lock_acquire` transaction が resource owner として caller PID を返す | live holder はそのまま。caller は gate を持たず、上位 writer は lock file を mutation しない |
| release | `agmsg_runtime_lock_release_owned` の owner-conditional DELETE が `changes=1` を返す | caller 所有の gate row は残る。successor owner は決して削除しない |

release 後の空 owner は predicate にしない。成功直後に successor が acquire できるためである。atomic conditional DELETE の `changes=1` が「caller の ownership を離した」唯一の成功根拠になる。

## deadline と SQLite busy timeout

outer gate deadline が優先する。各 storage attempt は既存の短い100ms busy slice を維持するが、残り gate budget を超える busy timeout を渡さない。attempt の完了後、deadline を再確認してから次の SQL / live-holder poll を始める。scheduler 停止中や既に開始した SQLite call を途中で中断することはしないため、再開時は実測 elapsed を含む named failure を返す。

diagnostic は既存の `operation` / `resource` / `observed_owner` / `classification` / `sqlite_error` を保ち、deadline 経路に `reason=retry-deadline-exhausted deadline_s=5 elapsed_s=<actual> attempts=<n>` を追加する。live holder の期限切れは `classification=live-holder`、SQLite busy は `classification=transient` と区別する。attempt 数は調査用で、success / timeout を決めない。

## #165 と R2-E の衝突

PR #165 は primitive、watcher、writer を混在して切り分け不能になり、#166 / #167 / #170 へ置換された。R5-P3 はそのうち writer gate の実時間 budget だけを扱い、watcher の `actas_lock_gate_try_acquire`（4 attempt、pollを捨てる fail-closed 経路）は変更しない。

| 変更 | R2-E | R5-P3 | 判定 |
| --- | --- | --- | --- |
| `actas_lock_owner` / `actas_lock_state` | read failure を free と混同しない | 変更しない | text conflict なし |
| `_actas_lock_gate_run` / `_attempt`、writer acquire/release | 変更しない | deadline、busy slice、diagnostic | text conflict なし |
| `actas_lock_release` の利用 | 非0なら despawn は `partial` | internal gate failure を非0のまま返す | behavior contract を共有 |

したがって実装PRは並行可能で、強制的な順序はない。実際の Lane 順は R2-E が先に着手可能、P3 は R1→T1 後である。R2-E は `actas_lock_release` の非0 / exit 3 を成功へ変えず、P3 は `actas_lock_owner` / `actas_lock_state` とその failure semantics を触らず、上位 `actas_lock_claim` / `actas_lock_release` の gate-unavailable exit 3 を維持する。後から merge する側は相手の固定HEADへ rebaseして、この共有契約を再確認する。

## 一 PR の境界と受入

一主張は「ownership writer gate が retry count ではなく実時間 budget で fail-closed になる」。変更は `scripts/lib/actas-lock.sh` の writer gate helper と `tests/test_actas_lock.bats` に限る。#683 の既存 integration assertion は回帰確認に使うが、watcher、despawn、state-read、storage schema は変更しない。

1. live holder が deadline 前に release すると、acquire が caller PID を atomic result として得る正対照を置く。
2. live holder が release しないと、5秒 deadline で nonzero・`classification=live-holder`・attempts/elapsed を出し、holder と caller の lock file が未変更であることを確認する。
3. caller所有 gate の release は `changes=1` で成功し、直後に successor が入っても successor を消さないことを確認する。slow/busy release の期限切れでは caller row が残る。
4. KILLED は `AGMSG_TEST_ACTAS_GATE_DEADLINE_S=1` と1 attempt 0.25秒の slow-`SQLITE_BUSY` stub を使い、外部3秒 watchdog より前に named deadline failure を確認する。旧50-attempt mutation は約12.5秒残るため watchdog により失敗し、diagnostic assertion がredになる。
5. focused `tests/test_actas_lock.bats` と既存 #683 barrier を含む `tests/test_actas_integration.bats` を実行し、fixed HEAD のCIで writer/watcher全差分を確認する。

## 限界

本日の11件横断での #683 1件失敗と単独 pass は、P3の原因を単独で証明するものではなく、実時間 budget 欠如と整合する観測である。ここでは原因を推測で断定せず、deadline超過を再現可能にする failure contract と KILLED control を設計する。現行 focused baseline は `tests/test_actas_lock.bats` 35/35 pass。実装はしていない。
