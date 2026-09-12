---
type: Design
title: "Issue #294: watch consume-failure 診断待機の対象競合"
description: >-
  watch の consume failure 診断試験が断続的に失敗する原因を、既存 CI log と main の source から診断する。
  同じ injected seam を通る cursor-only の alice ログを generic な待機が拾い、carol の post-stdout
  consume/log より先に watcher を停止できる test-fixture 競合である。実装は別 PR。
timestamp: "2026-09-13T03:06:45+09:00"
refs:
  - "#294"
  - "#341"
  - "#373"
  - "#353"
---

# Issue #294: watch consume-failure 診断待機の対象競合

## 1. 結論

`watch: consume failure is logged after stdout delivery` の間欠失敗は、production の stdout/consume 順序ではなく、**test の停止前待機が recipient を限定しない**ことによる fixture 競合である。

`tests/test_watch_ownership_log.bats` は `alice` と `carol` を同じ watcher に登録し、`storage_read_cursor_consume` を全呼出しで status 13 に差し替える。watch は message が無い cursor-only page にも consume を呼ぶため、`alice` の失敗ログだけで generic wait が満たされる。一方、`carol` は stdout を書いた直後に consume と log を行うので、親 test は stdout を観測したあと、既に存在する `alice` の generic log を読んで watcher を kill できる。この interleaving では最後の `team/carol` assertion が失敗する。

修正対象は当該 test の wait predicate と、その競合を再現する test control に限る。`scripts/watch.sh`、storage cursor の永続化契約、CI 再実行、Issue #341 の launcher 診断は対象外である。

README への影響は無い。

## 2. 根拠

| 項目 | 値 |
| --- | --- |
| cutoff | `10323629df766eafbe752961cb992c4c5ea3a0d2` |
| source | `tests/test_watch_ownership_log.bats:277-315`、`scripts/watch.sh:909-930` |
| 一次ログ | GitHub Actions job `101947923128` |
| command | `GH_CONFIG_DIR=/Users/kappa/.config/gh-4codex rtk gh api --allow-escape-sequences repos/kappaseijin/agmsg/actions/jobs/101947923128/logs` |

job `101947923128` は `not ok 406` を記録し、失敗位置は test の `tests/test_watch_ownership_log.bats:312`、すなわち次の recipient-specific assertion である。

```bash
run grep -F "team/carol: storage_read_cursor_consume failed" "$log"
[ "$status" -eq 0 ]
```

その前の wait は recipient を含めない。

```bash
wait_for_file_contains "$log" "storage_read_cursor_consume failed"
```

watch は `FINAL_CURSOR` があれば `DELIVERED_IDS` が空でも `storage_read_cursor_consume` を呼び、非0なら `$GATE_LABEL` を含む durable log を書く。したがって、`alice` の cursor-only consume が先に失敗したログは上の generic needle に一致するが、最後の `team/carol` assertion には一致しない。

```mermaid
sequenceDiagram
    participant W as watcher
    participant P as parent test
    W->>W: alice cursor-only consume fails; log team/alice
    W->>P: carol message is written to stdout
    P->>P: stdout wait succeeds
    P->>P: generic log wait matches team/alice
    P->>W: kill watcher
    Note over W: carol consume/log may not run
    P->>P: team/carol log assertion fails
```

一次ログは final assertion failure までを示し、失敗 run の full fixture log は artifact 化していない。このため上記の interleavingは、source の順序と failure positionに基づく診断である。隔離 fixture の focused run は 1 回 pass し、決定論的 failure ではないことを確認した。CI は再実行していない。

## 3. 実装方針

programmer は `watcher` を kill する前の wait を、最終 assertion と同じ recipient-specific stringへ変更する。

1. stdout の `consume-failure-marker` を待つ。
2. `team/carol: storage_read_cursor_consume failed` を待つ。
3. この二つが観測された後にだけ watcher を停止する。
4. status `13` の assertion と receipt-failure の隣接試験は維持する。
5. generic needle が別 recipient の log を満たす競合を、barrier を用いる fixture-bound control で再現して残す。

`watch.sh` の log 文言を変える、alice の cursor-only consume を production で抑止する、固定 sleep を足す案は採らない。前二者は test の観測競合を production 挙動へ誤って広げ、固定 sleep は失敗モードを除去せず timing を隠すだけである。

## 4. 受入条件

| # | verifier の確認 | 期待 |
| --- | --- | --- |
| 1 | carol の stdout 後、carol consume/log 前で watcher を止める barrier と generic needle | generic wait が alice log で通り、最終 carol assertionが失敗する。旧競合を再現する負の対照 |
| 2 | 同じ barrier で recipient-specific needle | carol log が出るまで wait が通らず、早期 kill できない |
| 3 | seam が status 13 を返す正の fixture | stdout marker、`team/carol` diagnostic、status 13 の三つが揃って pass |
| 4 | alice log を削除または別文言にする対照 | test の成否が alice の generic log に依存しない |
| 5 | fixed HEAD の対象 Bats test の独立反復 | execution count、cutoff、raw output とともに記録する。CI rerun はこの受入に含めない |

1 は「generic wait が危険」という語の存在確認ではなく、疑っている早期停止の時系列を実際に作るための対照である。

## 5. 非対象と限界

- job `101947923128` の log には test fixture 内の alice/carol log artifact が無い。原因は source と final assertionからの推論であり、当該失敗 run の scheduler trace は未確認である。
- 滞留プロセス、macOS runner 負荷、Broken pipe は別の失敗族として扱い、本書から共通原因を断定しない。
- consumer failure の retry/receipt semantics は現行 test の目的を越えるため、本件では変更・再定義しない。
- CI の再実行・実装修正・Issue #294 の close は本診断の完了条件ではない。

## 6. 引き継ぎ

実装 PR は programmer が作成する。formal reviewer は `agmsg_reviewer_claude`、独立した受入実測は verifier が担当する。PR 本文では、変更が test fixture の readiness predicate と control に限定されることを明記する。
