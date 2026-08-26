---
type: Plan
title: "R2: 失敗理由が消える経路の横断走査"
status: proposed
root_cause: R2
root_issue: "https://github.com/kappaseijin/agmsg/issues/176"
timestamp: "2026-08-26T09:39:50+09:00"
---

# R2: 失敗理由が消える経路の横断走査

## 結論

R2 は、失敗を成功へ変えること自体ではなく、失敗した運用経路で理由が利用者または後続処理へ届かなくなる問題である。
現行 `origin/main` には、修正済みの正対照（Issue #135、#168、PR #177/#169）がある一方、watch の durable log を迂回する状態遷移、watch の storage 失敗の空結果化、Codex `watch-once` の timeout 化、`despawn` の lock/reset 失敗の成功化が未解決である。

この記録は Issue #176 を起点に PM が R2 親 Issue を一件起票するための走査結果である。
実装・PR 作成はしない。R5（観測可能な完了条件を使わない待機）は Issue #178 の別原因であり、本記録から除外する。

## 走査方法と対照

対象はリポジトリの `scripts/` と `tests/` のみであり、インストール済みの `~/.agents/skills/agmsg` は対象にしない。
`2>/dev/null`、`2>&-`、`&>/dev/null`、`>/dev/null 2>&1`、`|| true`、`|| :` を機械走査した。全体では 51 ファイル・558 件、R2 に近い `watch.sh`、Codex `watch-once.sh`、`despawn.sh` では 42 件を確認した。

| 対照 | 失敗モードを再現できる理由 | 現行の確認結果 |
| --- | --- | --- |
| 正: Issue #168 の DB preflight | watcher の stderr が `/dev/null` でも理由を残せるか | `watch_check_existing_db` は `watch_log` を呼び、test は stderr と `run/watch.<session>.log` の双方を確認している |
| 正: PR #177 / Issue #169 | BATS teardown が non-zero の理由を失わず残すか | `teardown_test_env` は `rm` stderr と runtime snapshot を失敗時に出し、元の non-zero を返す。PR #177 は `main` に merge 済み |
| 負: `watch_log` 自身の log write 失敗 | 診断の保存失敗が delivery を止めるべきでない | log file 作成・追記失敗を nonfatal にするのは意図的。失った保存先自身へ理由を書けないため R2 修正対象にしない |
| 負: watcher test の fd 2 抑制 | 本番 watcher と同じ stderr 不可視条件を測れるか | `watch.sh.*2>/dev/null` の 13 件のうち、既存の failure test は durable watch log を assertion している。新規 R2 test もこの条件を使う |

`watch.sh` の `>&2` は 3 件だった。1 件は `watch_log` の stderr mirror、残る 2 件が下記 R2-A の状態遷移である。この差が「stderr を出した」だけでなく「production で読める場所へ届いた」を判定する負の対照になる。

## 未解決 R2 台帳

| ID | 現在の経路と失われる理由 | 判定 | 一主張の子 PR 単位 | 受入・KILLED control |
| --- | --- | --- | --- | --- |
| R2-A | `scripts/watch.sh` の ownership claim / release（729、742 行付近）は bare `echo >&2`。production watcher の fd 2 は `/dev/null` のため state transition が消える | replace | claim/release の二つの state transition を `watch_log` 経由へ統一する | fd 2 を `/dev/null` にして claim→release を起こし、`run/watch.<session>.log` に両方を確認する。`watch_log` を bare echo に戻す mutation は log assertion を red にする |
| R2-B | watch 起動時の `agmsg_subscription_pairs` と team `agmsg_db_path` が non-zero で直ちに exit する。下流 library の stderr は fd 2 とともに消える | replace | subscription/path 解決失敗を operation と対象を含む `watch_log` へ保存してから fail-closed exit する | copied `identities.sh` を固定 non-zero にし、fd 2 `/dev/null` でも durable log と non-zero を確認する。DB selector failure を「cannot open DB」と誤記しない負対照を置く |
| R2-C1 | `storage_read_cursor_get` と `storage_watch_after` の non-zero を `2>/dev/null || true` で空値にし、cursor `0` / unread なしとして poll を続ける | replace | read/scan storage failure を空結果と区別し、durable log を残してその poll を skip する | driver seam を exit 13 にして、fd 2 `/dev/null` でも log が残り、cursor `0` や no-unread に化けないことを確認する。`|| true` 復元 mutation は red にする |
| R2-C2 | delivery 後の `storage_read_cursor_consume` / receipt 記録の non-zero が捨てられ、再配信・未記録の理由が見えない | replace | post-delivery storage failure を durable log へ残す。delivery/retry semantics は変更しない | consume/receipt seam の失敗で stdout delivery を維持しつつ durable reason を確認する。log 呼出し削除 mutation は red にする |
| R2-D | `scripts/drivers/types/codex/watch-once.sh` は unread / ID query の driver error を `|| true` で空値にし、exit 2 `timeout` を返す。`codex-bridge.js` は 2 を正常 rearm と扱う | replace | query failure を timeout でなく非 2 の error と具体的な stderr として返す | storage stub を non-zero にし、`watch-once` が 2 以外で失敗し bridge が failure path を選ぶことを確認する。`|| true` 復元 mutation は timeout/rearm となり test が red にする |
| R2-E | `scripts/despawn.sh` は lock state 取得失敗を `echo free` にし、registration reset / lock release の失敗も捨てて `status=ok` または `status=forced` を出す | needs context | lock state 不明・reset/release 失敗を成功表示にせず fail-closed とする。terminal/pane kill と idempotent file cleanup は best-effort のままにする | lock/storage seam を non-zero にし、`status=ok` にならず理由が残り、unknown state で destructive cleanup を進めないことを確認する。`|| echo free` mutation は red にする |

R2-C1 と R2-C2 は同じ watcher 内でも、前者が「未読なしとの判定」、後者が「delivery 後の記録失敗」で受入可能な主張が異なるため分割する。R2-E は CLI の既存呼出し互換性と cleanup policy への影響を確認してから着手するため `needs context` とした。

```mermaid
flowchart LR
    E[失敗] --> Q{理由を読める場所へ残すか}
    Q -->|yes| D[durable diagnostic]
    Q -->|no: stderr only| S[fd 2=/dev/null で消失]
    Q -->|no: || true / echo free| F[空結果・timeout・success へ誤分類]
    S --> R2[R2 child PR]
    F --> R2
```

## 意図的なもの・R2 ではないもの

| 範囲 | 分類理由 |
| --- | --- |
| `watch_log` の file create/write failure | delivery の生死を logging availability に依存させない nonfatal cleanup。保存先そのものが失敗しているため同じ経路へ durable reason は残せない |
| watcher の PID / ready / filter cleanup、`kill`、tmux/herdr cleanup、idempotent file removal | これらは delivery success の主張を作らない best-effort cleanup。R2-E の lock/reset は state correctness を決めるためこの扱いに含めない |
| `delivery.sh` の hook JSON 読み込み | unreadable / malformed config を validation 後に `off (unrecognized ...)` と表示しており、診断を捨てていない |
| tests の直接 `run ... 2>/dev/null`（8 件） | file absence、capability probe、success JSON 等の assertion 補助であり、失敗理由を production へ渡す契約ではない |
| Issue #135、#168、#169 / PR #177 | 現行 source/test に修正済み対照がある。#156 は long scan の進捗可視化であり、現行の return/error reason delivery の未修正候補ではない |

## PM 起票用の親 Issue 材料

**題名**: `R2: watcher と lifecycle CLI が失敗理由を空結果・timeout・success に変換しない`

**本文の主張**:

> 本 Issue は、production watcher の stderr が見えない条件を前提に、失敗理由が durable diagnostic へ届かず、空結果・timeout・success として扱われる R2 経路を是正する。実装は R2-A、R2-B、R2-C1、R2-C2、R2-D、R2-E をそれぞれ別 PR に分割する。各 PR は fd 2 `/dev/null` または driver failure の正対照と、その failure を消す mutation（KILLED control）を持つ。R5 の待機・deadline 問題は本 Issue の範囲外とする。

**子 PR の順序**:

1. R2-A — watcher state transition の durable log。
2. R2-B — watcher startup fail-closed diagnostic。
3. R2-C1 — read/scan error を空結果にしない。
4. R2-C2 — post-delivery record failure の証跡。
5. R2-D — Codex `watch-once` query error を timeout にしない。
6. R2-E — `despawn` lifecycle state の fail-closed（着手前に compatibility context を確認）。

## 境界と後続

各子 PR は producer `agmsg_programmer_codex`、formal reviewer `agmsg_reviewer_claude` の一主張として起票時に固定する。PR の review 対象は fixed HEAD の全差分であり、R2 parent が存在しても複数の表の行を同一 PR に混在させない。

本記録は source と GitHub live state の走査であり、実装変更・full test suite は実行していない。docs-only record の受入は、base/head の `git diff --check`、remote branch の commit と document retrieval、続く各子 PR の focused positive/negative/KILLED controls で行う。
