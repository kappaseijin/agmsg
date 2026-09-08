---
type: Design
title: Issue #246 official registration照会契約と適合計画
description: >-
  forkで受入済みの非集約registration照会を、公式固定版へ提案可能な単一patchとして
  切り出す公開契約、完全性判定、隔離適合試験、最小差分境界を定める。
timestamp: "2026-09-08T20:39:00+09:00"
updated: "2026-09-08T20:39:00+09:00"
issues: [246, 222, 236, 239, 244]
source_cutoff: e58dbafad5a84be625f070385bb0c076c3daa4db
reference_head: eb850a6698ab81986b9ac49830b7dddbdeb75d83
producer: agmsg_architect_codex
reviewer: agmsg_reviewer_claude
---

# Issue #246 official registration照会契約と適合計画

## 結論

fork PR #249で受入済みの契約とPR #251固定HEADの実装を再設計せず、公式固定版へ
適用可能な1件のprovider patchとして切り出す。

```text
scripts/api.sh get teams <team> registrations --schema-version 1
```

このresourceは、team内の全registrationを`agent / type / project / canonicalProject`の
tupleとして集約せずに返す。成功は全件読取・全件検証・全件canonical化・観測整合性の
確認が終わった場合だけである。1件でも不明なら部分配列を返さず、結果全体をunknownにする。

Issue #247のactas owner query、PM/reviewer等の運用role、account選択、G4 policyは含めない。
forkでのpatch完成、公式への提案、公式採用、利用可能化を別の状態として扱う。

## 1. 既存入口が代替にならない理由

| 入口 | 保存されない情報 |
|---|---|
| `api.sh ... members` | typeを集約し、projectを`LIMIT 1`で選ぶためtuple対応と複数projectを失う |
| `identities.sh` | 指定project/typeへ絞り、team/agentを`DISTINCT`にするため完全列挙ではない |
| `whoami.sh` | 解決済みprojectについてnameへ集約するため同cwdの複数席を完全列挙しない |
| `team-list.sh --json` | team metadataでありregistration一覧ではない |

既存CLIはそれぞれの用途では正しい。出力を変更せず、独立したversion付きresourceを追加する。

## 2. 公開契約

### 2.1 command

```text
scripts/api.sh get teams <team> registrations --schema-version 1
```

stdoutは成功・失敗とも一行JSON envelopeとする。
stderrは状態とreasonだけを診断し、設定全文や認証値を出さない。

### 2.2 成功envelope

```json
{
  "schemaVersion": 1,
  "resource": "registrations",
  "team": "team-name",
  "status": "ok",
  "reason": null,
  "complete": true,
  "registrations": [
    {
      "agent": "agent-name",
      "type": "codex",
      "project": "/physical/input/path",
      "canonicalProject": "/canonical/path"
    }
  ]
}
```

tupleの意味は`team / agent / harness type / canonical project`の対応である。
`project`は保存値、`canonicalProject`は提供側の既存解決規則で照会時に得た正規形とする。

次を禁止する。

- `type[]`と単一projectへの集約
- `LIMIT 1`によるproject選択
- `DISTINCT`による重複tupleの消去
- agent、type、projectを別々に集めた直積

同一tupleが重複していれば重複のまま返す。
consumerが0件、一意1件、複数件を判定し、providerが曖昧性を消さない。
順序はagent、type、保存projectのbyte順で安定化するが、順序は一意性を意味しない。

### 2.3 状態と終了値

| 条件 | status / reason | complete | registrations | exit |
|---|---|---:|---|---:|
| 全対象を正常に読取・検証 | `ok / null` | true | 全tuple。0件なら`[]` | 0 |
| teamが正常に不存在 | `not_found / team_not_found` | false | null | 1 |
| JSON、型、必須field不正 | `unknown / data_invalid` | false | null | 1 |
| 未対応保存schema | `unknown / storage_schema_unsupported` | false | null | 1 |
| source読取不能 | `unknown / read_failed` | false | null | 1 |
| project正規化不能 | `unknown / project_unresolvable` | false | null | 1 |
| 照会中にsource変更 | `unknown / concurrent_change` | false | null | 1 |
| API引数不正 | `error / invalid_argument` | false | null | 2 |
| schema version不正 | `error / unsupported_schema_version` | false | null | 2 |

正常な空teamまたは空registrationsは`ok / complete=true / []`であり、未登録team、破損、
読取不能とは異なる。未対応形式や不完全entryを空配列へ変換しない。

## 3. 完全性と観測整合性

1. 指定teamだけを解決し、他teamの破損を走査しない
2. team configのsource identityを取得する
3. source全体を隔離snapshotへ読み取る
4. root、agents、各registration、必須fieldの型を全件検証する
5. 各projectを提供側の既存resolverでcanonical化する
6. 全tupleをbuffer化し、まだstdoutへ出さない
7. source identityと内容を返却直前に再確認する
8. 前後差が無い場合だけ`complete=true`で一括出力する

一件でも失敗したら、正常に処理できたtupleだけを返さない。
source交換を検出したら新しいsourceへ自動追従せず`concurrent_change`とする。

これは一つのteam configに対する一回のsnapshotである。
返却後の登録固定、複数teamを跨ぐtransaction、ABA不在、leaseを保証しない。
consumerは利用直前に再照会する。

## 4. read-only境界

照会は次を行わない。

- join / reset / normalize / migrate
- claim / release / GC
- DBまたはconfigの初期化・書換え
- role-session記録
- engine / watcher / bridge / agent processの起動

照会前後でteam config、DB/runtime、claim、session記録、process一覧の意味的差分が0であることを
適合試験で確認する。通常のread access-timeは所有・登録状態の変更とは分離して扱う。

外側consumerがteam configや内部schemaを直接読むadapterは解決策にしない。
提供側のteam名validation、legacy形式解釈、canonical project resolverを再利用する。

## 5. 上流へ提案可能な最小patch

基準は公式cutoff `e58dbafad5a84be625f070385bb0c076c3daa4db`、参照するfork実装は
PR #251固定HEAD `eb850a6698ab81986b9ac49830b7dddbdeb75d83`とする。

1 PR 1主張として、registration resourceだけを次の5ファイルへ限定する。

| path | 最小差分 |
|---|---|
| `scripts/api.sh` | `registrations` usage、helper読込、route。`actas-owner`を混ぜない |
| `scripts/lib/api-registrations.sh` | schema v1の全件snapshot query |
| `tests/test_api_registrations.bats` | 非集約・完全性・故障・read-only対照 |
| `tests/test_api.bats` | 既存route互換とregistration routeの最小回帰 |
| `README.md` | command、schema、状態、完全性、read-only、snapshot限界 |

現在のmainから差分を作らない。Issue #247その他の後続変更が混ざるため、PR #251の固定差分を
公式cutoffへ移植し、fork固有依存があれば不足symbol単位で除去または提供側既存helperへ置換する。

関連hunkが台帳で`agguild/port`であることは、「上流へ提案禁止」を意味しない。
forkで生じた公開契約なので、公式固定版で適用・適合を新たに証明する必要があるという境界である。

## 6. 隔離適合試験計画

公式cutoffを隔離rootへ展開し、現役install、team、DB、claimへ触れずに次を行う。

1. 無変更公式版で新resourceが未提供であることを確認する
2. 提案patchをdry-run後に隔離rootへ適用する
3. 公開`api.sh`だけから全状態を検査する
4. 設定・DB/runtime・processの前後snapshotを比較する
5. 正常系だけでなく負の変異を実際に当て、各対照が落ちることを確認する

### 6.1 必須対照

| 対照 | 必須結果 |
|---|---|
| 正常1件 | 1 tuple、`complete=true`、exit 0 |
| 同名agentの2 project / 2 type | 全tupleと元の対応を保存。直積なし |
| 同じcwdの複数席・完全重複tuple | 重複を消さず返す。consumerは複数と判定 |
| 正常空team・空registrations | `ok / []`。team不存在と区別 |
| legacy保存形式 | 提供側が受理する既存形式だけ正規化 |
| 未対応schema・一部破損・欠落field | `unknown`、部分配列なし |
| 実効ユーザーでの読取失敗 | `unknown / read_failed` |
| canonical化不能 | `unknown / project_unresolvable` |
| 指定team外の破損 | 対象team照会を汚染しない |
| barrier中のsource交換 | `unknown / concurrent_change`。混合配列なし |
| schema version・未知option | `error`、exit 2 |
| 既存CLI | members、identities、whoami、team-listの出力契約を変更しない |
| read-only | config、DB/runtime、claim、session、processに意味的変更0 |

### 6.2 検査の負の対照

少なくとも次の変異を個別にKILLする。

- `LIMIT 1`でprojectを一つへ縮退する
- `DISTINCT`で重複tupleを消す
- agent/type/projectを別々に集約して直積を作る
- 破損entryを捨てて残りを`complete=true`で返す
- 未対応schemaを正常空配列へ変える
- canonical化失敗を保存projectで代用する
- source再確認を削除する
- buffer前に途中stdoutを出す

正常1件が通るだけでは検査の正当性を示さない。
複数・重複・破損がある入力を正の対照にし、上記変異で対応試験が失敗することを求める。

## 7. 機械が読む状態と証拠

実装担当は結果artifactに少なくとも次を保存する。

```json
{
  "sourceCutoff": "e58dbafad5a84be625f070385bb0c076c3daa4db",
  "patchHead": "<fixed-head>",
  "patchApplied": true,
  "contractStatus": "pass",
  "mutationStatus": "pass",
  "readOnlyStatus": "pass",
  "officialAvailability": "not_adopted"
}
```

本文の実測記録には`value / cutoff / source / command`、対象HEAD、主張に対応する生出力、
負の対照、未確認事項を残す。`patchApplied=true`を公式採用や利用可能化と読まない。

## 8. 完了境界

この設計PRの完了は、公開契約、状態型、完全性、read-only、5ファイルの最小patch境界、
隔離適合試験計画が固定HEADでformal reviewを通ることまでである。

次工程のprogrammerは公式固定版への隔離patchと正負対照を実装する。
verifierは別contextで独立実測し、reviewerは固定HEADの全差分を一括走査する。

次は本設計の範囲外である。

- 実装、現役install/DB/claimの変更
- 公式repositoryへの投稿、merge、release
- Issue #247との統合
- PM/reviewer等の運用role、account選択、G4 policyの移植
- P2接続または解除
- 内部schemaの外側reader、影claim、恒久fork fallback

READMEへの影響はない。本書は`docs/decisions/`の開発用設計である。

Refs #246, #222, #236, #239, #244
