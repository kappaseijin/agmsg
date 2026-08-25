---
type: Plan
title: "Issue #164 PR-3: ownership writer を delivery gate で直列化する"
status: implemented
issue: "https://github.com/kappaseijin/agmsg/issues/164"
timestamp: "2026-08-26T05:00:11+09:00"
updated: "2026-08-26T05:24:48+09:00"
---

# Issue #164 PR-3: ownership writer を delivery gate で直列化する

## 主張

`actas` の ownership writer（claim / release / release-all / stale GC / reset）を、同じ pair の watcher delivery gate と直列化する。gate取得失敗は成功扱いにせず、通常writerはlockを変更せずnon-zero、`actas-claim.sh` は `status=unavailable reason=ownership_gate_unavailable` / exit 3 とする。

## 範囲

- `scripts/lib/actas-lock.sh` の writer mutation
- `scripts/actas-claim.sh`
- `scripts/lib/subscription.sh`
- `scripts/reset.sh`
- `tests/test_actas_lock.bats`
- `tests/test_actas_integration.bats`
- 必要な focused test とこの計画書

`scripts/watch.sh`、`send.sh`、storage ABI、production team data は変更・試験台にしない。

## 実装契約

1. `actas_lock_claim` は stale reclaim を含むlock path mutation全体をgate内で行う。
2. `actas_lock_release` は自分のownerだけをgate内でowner-conditionalに解放する。同じプロセスがwatcher gateを保持したまま再取得しない設計にする。
3. `actas_lock_release_all` と stale GC は列挙した各lock pathごとにgateを取得し、判断不能なlivenessやgate failureでは削除しない。
4. claim/subscription/resetはgate failureをokへ畳み込まず、部分claimはrollbackし、resetは未処理を失敗として返す。
5. despawnのrole drop例外はPR-2のwatcher側契約を維持し、PR-3ではwatcherを変更しない。

## 検証

- RED: writer gate未接続の#683 barrierでhandoverがbarrier中に完了することを確認する。
- GREEN: 実際のclaim/releaseをbarrier processから呼び、handover完了がdelivery gate解放後になることを確認する。
- 負の対照: gate取得失敗writerがlock fileを変更せずnon-zero、actas-claimがexit 3でMonitorを触らないこと。
- mutation: writer側gateを外すと#683 barrier assertionが落ちることをKILLED確認する。
- 回帰: #413 despawn、#263 closed stdout、既存lock/subscription/reset suiteを通す。
- fixed HEAD、実行環境、RED/GREEN/KILLED/restore-GREEN、CI、formal reviewをPR本文へ記録する。
