---
type: ArchitectureDecision
title: "Issue #92: team-work の複数チーム展開と二段階 handoff"
description: "既存 team-work.sh を共通の pull 型作業配分として導入するための前提、段階展開、dispatch/dispatch-ack の運用境界を定義する。"
status: proposed
issue: "https://github.com/kappaseijin/agmsg/issues/92"
timestamp: "2026-08-20T17:03:19+09:00"
---

# Issue #92: team-work の複数チーム展開と二段階 handoff

## 決定

`team-work.sh` を agmsg の共通 engine とし、各プロジェクトは versioned roster と versioned contract pack だけを持つ。
project ごとの queue engine、GitHub assignee を claim とみなす運用、または project-local adapter を増やさない。

各 team はまず pull 型を導入し、`dispatch` / `dispatch-ack` は recovery transition が提供されるまで本番で有効化しない。
これは dispatch を不要とする判断ではない。
二段階 handoff の契約と導入条件を先に固定し、期限切れ dispatch を安全に再実行できる状態遷移を別 Issue で追加してから有効化する。

この記録は、`team-work` の不在を前提にした [2026-08-11 の scale_exporter 境界記録](2026-08-11T124720_scale-exporter-work-queue-boundary.md)を、現行実装の確認結果で置き換える。

## 確認済みの前提

`team-work.sh` は pack、roster、共有 SQLite state、GitHub live audit を使う。
`queue` は open source Issue のうち live lease 又は live dispatch が無い item だけを `ready` として返し、競合する live lease は SQLite transaction で拒否する。
GitHub Issue assignee は複数人を追加できるため、claim の正本にしない。

ただし pull は unrestricted な work stealing ではない。
`claim` を実行できるのは pack の `ownerSeat` 又は exact `role: manager` の roster member だけである。
各 work item の owner は pack を作る時点で決める。
owner が不在又は到達不能なら reconciler は `orphan_ready` を返して停止し、他の seat が暗黙に引き取らない。

shared state は同一 team の全 seat が同じ SQLite store を参照しなければならない。
seat ごとに異なる `AGMSG_STORAGE_PATH` を設定すると、lease の atomicity は team 全体に及ばない。

## 調査の証跡と限界

| 主張 | 一次資料・確認方法 | 限界 |
| --- | --- | --- |
| 既存 engine で pull、lease、audit、watchdog、dispatch が利用できる | `scripts/team-work.sh`、`scripts/lib/team-work.js`、`scripts/lib/team-work-audit.js`、`scripts/lib/team-work-reconciler.js`、README の command contract。source と installed skill の `team-work.sh` / reconciler の SHA-256 が一致することを確認 | target team での実運用はまだ行っていない |
| GitHub assignee は atomic claim ではない | [herdr-agent-monitor #185](https://github.com/kappaseijinjp/herdr-agent-monitor/issues/185) の closed test Issue 実測 | GitHub UI 又は assignee を補助表示に使うことは妨げない |
| roster gate が未充足 | `team.sh <team> --format json` を各 target に実行。scale2sheet / scale_exporter は schema v1 error、herdr-agent-monitor は manager role 不在 | delivery runtime の継続監視はしていない |
| dispatch timeout の再実行を public CLI が提供しない | `runDispatch` の INSERT-only transaction、dispatch ledger の `(team, work_item_id)` primary key、期限切れ ACK を拒否する `runDispatchAck` を照合 | expired row を回復する新 command の要件・実装は未着手 |

Issue #92 の [引継ぎ記録](https://github.com/kappaseijin/agmsg/issues/92)は、既存 engine の isolated-storage 実地検証を本設計の前提としている。
本記録ではそれを再実行せず、現在の source、installed parity、live roster、Issue state を再確認した。

## 対象チームの導入前状態

| 対象 | live roster の確認 | 導入判定 | 必要な Phase 0 |
| --- | --- | --- | --- |
| herdr-agent-monitor | `team.sh --format json` は成功するが、PM を含む seat の role が `unassigned` | 不可 | 正式な `manager` role を 1 席だけ設定し、dispatch target となる seat の registration を検証する |
| scale2sheet | `schemaVersion must be integer 1` で roster validation が失敗する | 不可 | schema v1、member `kind`、`role`、registration を正規化する |
| scale_exporter | `schemaVersion must be integer 1` で roster validation が失敗する。既存 `Scripts/team-work-pilot.sh` は別 CLI の `self-check --policy --contract-pack --format json` を呼ぶ | 不可 | roster 正規化後に pilot adapter を移行又は廃止し、agmsg の positional CLI だけを使う |

この確認は設定だけを読み取ったものである。
対象リポジトリの未追跡又は未コミットの変更には触れない。

## 共通の段階展開

```mermaid
flowchart TD
  P0[Phase 0: roster を schema v1 に正規化] --> P1[Phase 1: contract pack を version 管理し隔離 dry run]
  P1 --> P2[Phase 2: owner 指定の pull 運用]
  P2 --> G{dispatch recovery が利用可能か}
  G -->|no| R[dispatch は無効のまま reconcile を監視]
  G -->|yes| P3[Phase 3: 二段階 handoff を opt-in]
```

### Phase 0 — roster gate

各 target manager は次を満たす roster change を、対象 project の独立 Issue として用意する。

- `team.sh <team> --format json` が成功する。
- 全 member が schema v1 の `kind`、空でない `role`、少なくとも一つの registration を持つ。
- `dispatch` を使う team には exact `kind: "seat"`, `role: "manager"` が一席だけある。
- dispatch target は、registration の `delivery.sh status … --format json` で一意に `deliverable: true` かつ `liveness: "alive"` となる。
- roster の候補は一時 HOME / CODEX_HOME で validate してから atomic に切り替える。live team config を直接編集して検証しない。

role、seat 起動、又は delivery の未知状態を `team-work` が修復することはない。
`boot_required`、`unknown`、複数 live registration は dispatch を拒否する正常な安全側の結果である。

### Phase 1 — pack と隔離検証

各 project は tracked path `.team-work/contract-pack.json` に pack を置く。
pack には source Issue、declared `ownerSeat`、work kind、PR relation、classification basis、writeback requirement を記録する。
pack を含む PR は対象 Issue と owner を明示し、source Issue を pack 外から探索しない。

最初の検証は本番 state を使わず、team 共通の隔離 `AGMSG_STORAGE_PATH` と fixture 又は read-only GitHub source で行う。
少なくとも次を確認する。

1. `validate` と `self-check` が同じ roster と pack で成功する。
2. `observe` / `queue` / `audit` が `ready`、`fully_allocated`、`quiescent`、`unknown` を混同しない。
3. 同一 item への二重 `claim` は一方だけが成功し、競合側は exit 2 で state を変えない。
4. `release` 後に item が `ready` へ戻る。
5. `reconcile` と別 process の `watchdog` が、新鮮な heartbeat と stale heartbeat を区別する。

成功してもこの段階では GitHub mutation、agent spawn、herdr 操作、message send を自動化しない。

### Phase 2 — owner 指定の pull 運用

各 active seat は session start / resume、lease release 後、及び手元に active work が無いときに `queue` を読む。
利用できるのは `ownerSeat` が自席に一致する `ready` item だけである。

```mermaid
sequenceDiagram
  participant S as declared owner seat
  participant Q as team-work queue and lease store
  participant G as GitHub live audit
  S->>Q: queue(team, pack)
  Q->>G: read source and PR state
  G-->>Q: classification basis
  Q-->>S: ready item for this owner
  S->>Q: claim(item, owner)
  Q-->>S: claimed or atomic conflict
  S->>Q: ack(item, owner, evidence)
  S->>Q: set-state(in_progress)
  S->>Q: writeback and release after verified closeout
```

`unknown`、`orphan_ready`、expired lease、又は incomplete relation は work を始める根拠ではない。
seat は remediation と evidence reference を manager に送る。
team 間連絡は各 team の manager 間に限定し、他 team の seat を直接 owner にしない。

## dispatch / dispatch-ack の設計

dispatch は push delivery の成功を claim と混同しないための opt-in protocol である。
manager が作業を提案しても、owner の same-epoch ACK までは lease も作業開始権も発生しない。

```mermaid
sequenceDiagram
  participant M as exact manager seat
  participant D as dispatch ledger
  participant T as agmsg transport
  participant O as declared owner seat
  M->>D: dispatch(item, manager, ack TTL)
  D-->>M: dispatching + epoch + expiry + digests
  M->>T: send work id, epoch, expiry, digests
  T-->>O: receiver handoff receipt
  O->>D: dispatch-ack(item, owner, same epoch, evidence)
  D-->>O: claimed lease or fail closed
  Note over O: acknowledged:true の後だけ作業開始
```

### 有効化条件

manager は次のすべてを再確認してから `dispatch` する。

- caller は exact roster manager である。
- item は fresh audit で `ready`、owner は pack の exact `kind: "seat"` である。
- `TEAM_WORK_DISPATCH_ALLOWLIST` は JSON array として設定され、その owner を一度だけ含む。
- owner の delivery evidence は一意に available である。
- dispatch output の `leaseEpoch`、`leaseExpiresAt`、`sourceDigest`、`auditDigest` を送信 message にそのまま含める。

`send.sh` の queued 又は `handedOff` は dispatch ACK ではない。
manager は message ID を delivery evidence として別途記録し、owner は受信後に新しい audit を伴う `dispatch-ack` を実行する。
`acknowledged: true` で `state: "claimed"` を確認するまで実装・レビュー・GitHub write を始めない。

### 現在の有効化 blocker

現行 `dispatch` は `(team, work_item_id)` primary key の dispatch row を INSERT する。
期限切れ row が残っていても public command に expire / release / replace transition はなく、再度の `dispatch` は row の重複として `duplicate_dispatch` になる。
`dispatch-ack` も期限切れ epoch を claim にしない。

したがって、ACK TTL を過ぎた場合の手順を「同じ item を再 dispatch する」と規定してはならない。
reconciler が `expired_lease` を示したら、owner は作業を開始せず、manager は source、dispatch ledger、message delivery を証跡として別 Issue へ記録する。
その Issue で、期限切れ dispatch を append-only history を保ったまま abandon / replace する明示 command と、再 dispatch 成功・古い epoch の ACK 拒否の両方をテストで示す。
この recovery が提供されるまで Phase 3 は本番無効とする。

## ロールアウト順と受入れ

1. roster migration を独立 PR で完了し、target manager が live roster と delivery を確認する。
2. `herdr-agent-monitor` を最初の pull-only pilot とする。監視 pane は observer のままで、queue writer にならない。
3. `scale2sheet` を二番目に導入する。既存の未追跡 `AGENTS.md` には触れない。
4. `scale_exporter` は old pilot adapter の移行又は廃止を先に完了してから導入する。二つの CLI / state authority を並行運用しない。
5. 各 pilot は isolated concurrency test、fresh/stale watchdog の負の対照、source audit の `unknown` 拒否、writeback closeout を記録する。
6. 全 pilot が pull-only で安定し、dispatch recovery Issue が cross-vendor review 済みになった後にだけ Phase 3 を opt-in する。

各 phase の合格は command の発行ではなく、JSON result、SQLite revision chain、GitHub read evidence、必要な負の対照で確認する。
message receipt、spawn、又は pane の存在を作業完了の証拠に置き換えない。

## 非対象と次の Issue

本決定は target project の roster、pack、hook、又は runtime state を変更しない。
また GitHub の未列挙 Issue を発見して自動配分する scheduler も導入しない。

次の作業は以下に分割する。

1. 各 target team の roster schema v1 migration と delivery verification。
2. 各 target project の contract pack と pull-only pilot。
3. dispatch timeout recovery transition、回帰 test、cross-vendor review。
4. その後に限る harness hook の共通 trigger 設計。hook は queue/reconcile を起動するだけで、dispatch、GitHub mutation、seat spawn を自動実行しない。
