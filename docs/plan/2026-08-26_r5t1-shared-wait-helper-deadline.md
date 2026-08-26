---
type: Plan
title: "R5-T1: shared test wait helper の実時間 deadline 契約"
status: decided
root_cause: R5
root_issue: "https://github.com/kappaseijin/agmsg/issues/178"
base_commit: "3e74a7dde1876c2f9d2fb81fcc70770bf53f3cec"
timestamp: "2026-08-26T11:21:00+09:00"
---

# R5-T1: shared test wait helper の実時間 deadline 契約

## 結論

5 helper は predicate を変えず、開始時に固定した実時間 deadline だけで終了を決める。`_WAIT_TICKS` / `_WAIT_INTERVAL` は削除し、既定は 10 秒、各呼出しだけが `AGMSG_TEST_WAIT_TIMEOUT_S` で延長する。poll 間隔は終了条件でなく、test-only の `AGMSG_TEST_WAIT_POLL_S`（既定 0.1 秒）として分離する。

`_WAIT_TICKS=600` を指定する `test_watch.bats` の3箇所は、同じ60秒を `AGMSG_TEST_WAIT_TIMEOUT_S=60` で明示する。`tests/test_wait_helpers.bats` の短い負対照も同じ override へ移す。環境全体の deadline を無言で延長する設定は作らない。

## 5 predicate と timeout 時の状態

| helper | 完了 predicate | timeout の状態値 |
| --- | --- | --- |
| `wait_for_file <path>` | `-f <path>` | `absent` |
| `wait_for_missing <path>` | `! -e <path>` | `still-present` |
| `wait_for_file_contains <path> <needle>` | regular file があり `grep -q` が一致 | `absent` または `needle-not-observed` |
| `wait_for_pid_exit <pid>` | `_pid_gone <pid>` | `not-gone` と安全な process stat |
| `wait_for_file_is <path> <expected>` | regular file の全内容が expected と一致 | `absent` または `content-mismatch` |

共通 timeout diagnostic は stderr に一行で `helper`、`predicate`、`target`、固定した `timeout_s`、実測 `elapsed_s`、`poll_s`、`attempts`、上表の `state` を出す。file 内容、expected 値、needle、process command line は出さない。これにより、失敗理由を残しつつ test data 以外を不用意に露出しない。

実装は predicate を確認した直後に elapsed を確認し、deadline 到達後に初めて観測された predicate を成功として返さない。scheduler 停止中に命令を実行すること自体は防げないため、再開時は named timeout と実測 elapsed を返す。attempt count は診断だけであり、終了条件には使わない。

## deadline の選択と #165 からの制約

既定10秒は現在の `100 × 0.1秒` が意図していた上限を保つ値である。ただし現在の実装では poll 本体や scheduler の時間が加算されるため、10秒を保証していない。Bash の `SECONDS` を invocation ごとに固定し、正の整数 timeout を検証して使用する。個別に長い watch-log 待機だけ60秒を明示し、通常の共有 wait をCI全体で延長しない。

PR #165 は primitive・watcher・writer を一つに混ぜたため切り分け不能のままcloseされ、#166 / #167 / #170 に置換された。さらに当時導入された writer gate の `50 attempts` は、現行 source でも各 attempt の SQLite busy timeout と scheduler 遅延を含み得るのに「5秒」と説明している。これは R5-P3 として別PRで直す。R5-T1 はこの誤りを再現せず、tick数を timeout の根拠にしない。

## 一 PR の境界と移行

R5-T1 は一主張「shared test waits の完了契約を predicate + real-time deadline にする」に収まる。変更は `tests/test_helper.bash`、`tests/test_wait_helpers.bats`、および `_WAIT_TICKS` を直接指定する `tests/test_watch.bats` の3呼出しに限る。R5-T2〜T5 の重複 helper や production wait は混ぜない。

5 helper を分割しない理由は、共通の deadline/diagnostic primitive が変更対象であり、helperごとのPRにすると異なる timeout 契約を生むためである。安全性は helper ごとの predicate 表と下記 controls で分離する。実装開始時は、同じ `tests/test_helper.bash` を触る R1 横断PRの merge HEAD を基準に、呼出し数と `_WAIT_TICKS` の残存0件を再確認する。

## 受入・KILLED controls

1. 5 helperそれぞれに、0.2秒後の file 作成・削除・追記・PID終了・内容置換を使う正対照を置き、deadline内に predicate で成功することを確認する。
2. 5 helperそれぞれに never-arrives 負対照を置き、短い `AGMSG_TEST_WAIT_TIMEOUT_S` で非0となり、共通 diagnostic の helper/predicate/elapsed/state を確認する。現行の7件 green は回数内成功しか示さないため、この診断 assertion を新設する。
3. KILLED は `AGMSG_TEST_WAIT_POLL_S=0.25`、deadline 1秒の never-arrives helper を独立 subprocess で走らせ、外部の実時間 watchdog より前に named timeout を出すことを確認する。旧 `for seq 1 100; sleep` mutation では約25秒待つため watchdog に殺され、named timeout assertion がredになる。
4. 正の回帰として、60秒 override を持つ3つの watch-log wait と、既存 `tests/test_wait_helpers.bats` を実行する。shared helper 利用の他 suite は固定HEADのCI全体で確認する。

## 範囲外

fixtureを生かす `sleep`、absence window、R5-P3のgate、R5-T2〜T5の個別重複waitは変更しない。本記録は source、PR #165 / Issue #178 のlive state、focused helper testの現行7/7に基づく設計であり、実装はしていない。
