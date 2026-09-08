---
type: Design
title: Issue #247 official actas owner照会契約と適合計画
description: >-
  forkで受入済みの副作用なしowner照会を、official分類済み6 hunkを不可分の前提として
  公式固定版へ提案可能にする公開契約、適合試験、最小patch境界を定める。
timestamp: "2026-09-08T16:40:47+09:00"
updated: "2026-09-08T16:40:47+09:00"
issues: [247, 222, 236]
source_cutoff: e58dbafad5a84be625f070385bb0c076c3daa4db
producer: agmsg_architect_codex
reviewer: agmsg_reviewer_claude
---

# Issue #247 official actas owner照会契約と適合計画

## 結論

forkで受入済みの公開契約

```text
api.sh get teams <team> actas-owner <agent> --schema-version 1
```

を再実装せず、公式固定版へ適用可能なpatchとして切り出す。

その前提として、`scripts/lib/actas-lock.sh`のofficial 2 hunkと
`tests/test_actas_lock.bats`のofficial 4 hunk、合計6 hunkを**一組**で扱う。
これは「読めないownerを空、空をfreeへ潰さない」というfail-closedな提供側readerと
その対照である。実装hunkだけ、または試験hunkだけを単独採用しない。

この6 hunkだけでは構造化公開APIは提供されない。
6 hunkはB2公開契約の前提patchであり、公開route・schema・厳格snapshot readerは
fork PR #252の受入済み成果から別patchとして切り出す。
公式への採用、利用可能化、P2解除は本設計の完了とは区別する。

## 1. 公開契約

### 1.1 command

```text
scripts/api.sh get teams <team> actas-owner <agent> --schema-version 1
```

stdoutは一行JSON、stderrは診断、終了値は観測状態の種別を表す。
owner tokenはopaqueであり、SIDだけへ短縮しない。

### 1.2 envelope

```json
{
  "schemaVersion": 1,
  "resource": "actas-owner",
  "team": "team-name",
  "agent": "agent-name",
  "status": "owned",
  "reason": null,
  "owner": "opaque-owner-token",
  "ownerKind": "composite",
  "liveness": "alive",
  "consistency": "observed"
}
```

必須fieldは`schemaVersion/resource/team/agent/status/reason/owner/ownerKind/liveness/consistency`。
ownerが正常に読めない場合、生のclaim内容をstdoutやstderrへ出さない。

### 1.3 状態と終了値

| 条件 | status / reason | exit | owner |
|---|---|---:|---|
| 正常owner、生存確認済み | `owned / null` | 0 | 完全なopaque token |
| 正常owner、死亡確認済み | `stale / owner_dead` | 0 | 完全なopaque token |
| 登録targetが存在し、claimが一意に不存在 | `absent / claim_absent` | 0 | null |
| team/agentが正常に未登録 | `not_found / target_not_found` | 1 | null |
| 空、不正形式、非regular claim | `unknown / claim_invalid` | 1 | null |
| claim、親path、登録の読取不能 | `unknown / read_failed` | 1 | null |
| 照会中のowner/path/登録変更 | `unknown / concurrent_change` | 1 | null |
| 生存確認不能 | `unknown / liveness_unavailable` | 1 | 正常に読めた場合のみ保持 |
| 引数またはschema version不正 | `error / invalid_argument`または`unsupported_schema_version` | 2 | null |

exit 0は「正常に観測した」であり、「認可してよい」ではない。
`absent`をfreeや取得権として表現しない。
`stale`でも削除、GC、再claimを行わない。

## 2. 観測整合性と有効期間

1. 提供側registration readerでtargetの存在を分類する
2. 提供側path encoderでclaim pathを解決する
3. 親directory、file type、内容全体を検査してsnapshotを取る
4. 提供側liveness readerでownerの生死を分類する
5. registration、directory、claimのfingerprintを返却直前に再確認する
6. 前後差があれば結果を追従更新せず`concurrent_change`とする

`absent`は、正常に読める同じ親directoryについて前後とも不存在だった場合だけ返す。
空出力、permission error、非regular pathを`absent`へ変換しない。

結果は上記1回の観測snapshotだけを表す。
TTL、lease、予約、返却後の所有継続、ABA不在、永久の所有権を保証しない。
consumerは利用直前に再照会し、必要なら独自のprocess bindingと照合する。

## 3. 副作用禁止

照会経路は次を呼ばない。

- claim / release / stale GC
- join / reset / role-session再登録
- DB初期化、migration、runtime row更新
- watcher、engine、bridge、agent processの起動

失敗時にも修復、再claim、再登録を代行しない。
確認は所有状態だけでなく、対象file、registration、DB/runtime row、起動processの
前後差分が0であることまで含む。

PM固有のprocess binding、`pidStart`、generation、認可policyはagguild側の責務に残す。
consumerがagmsg内部fileやschemaを直接読むadapterは公開契約の代替にしない。

## 4. official 6 hunkの不可分境界

| path | hunk数 | 役割 |
|---|---:|---|
| `scripts/lib/actas-lock.sh` | 2 | owner読取失敗、存在する空ownerを`unknown`・非0にする。読取中にlockが消えた場合だけ`free`を許す |
| `tests/test_actas_lock.bats` | 4 | 消失競合は`free`、存在する空/読取不能lockは`unknown`であることを固定する |

採用単位は6 hunk全体である。

- source 2 hunkだけでは、将来の回帰を検出できない
- test 4 hunkだけでは、公式固定版の旧挙動で失敗する
- 空をfreeへ潰す旧挙動のまま公開queryを載せると、`absent`と`unknown`を区別できない

この6 hunkは既存B2の前提適合patchであり、新しいquery実装ではない。

## 5. 上流へ提案可能な最小patch列

1 PR 1主張を守るため、提案可能な差分を次の2段へ分ける。

### Patch A: fail-closed owner reader

対象はofficial分類済み6 hunkだけ。
主張は「読めないownerを正常なfreeへ変換しない」。
新API、README、B1 registration APIを含めない。

### Patch B: read-only actas-owner query

Patch A適用済み公式固定版をbaseに、fork PR #252で受入済みの次だけを切り出す。

- `scripts/api.sh`: `actas-owner` read-only route
- `scripts/lib/api-actas-owner.sh`: schema v1のsnapshot query
- `tests/test_api_actas_owner.bats`: 適合対照
- `README.md`: command、schema、終了状態、race境界

doctorの人向け表示、既存writer、B1 registration照会、PM固有policyは変更しない。
Patch Bをforkで完成させても、公式で採用・releaseされるまでは「利用可能」と報告しない。

## 6. 隔離適合試験計画

公式cutoff `e58dbafad5a84be625f070385bb0c076c3daa4db`を隔離rootへ展開し、次の順で測る。

1. 無変更公式版で正負対照のREDを取り、検査が旧fail-open挙動を検出することを示す
2. Patch Aの6 hunkを一括適用し、`test_actas_lock.bats`の対象対照と既存回帰を実行する
3. Patch Bを適用し、公開commandだけからschemaと状態を検査する
4. 各試験の前後で所有状態、registration、DB/runtime、process一覧を比較する
5. Patch Aのみ、Patch A+Bを別々のartifactとして保存する

### 必須対照

| 対照 | 必須結果 |
|---|---|
| 正常owner | `owned`、完全owner、`alive` |
| 正常なclaim不存在 | `absent`。未登録targetと区別 |
| 同SID別PID | 異なるownerとして返す |
| stale owner | `stale`。claim fileを残す |
| 空、不正、複数行、制御文字、directory、symlink | `unknown / claim_invalid` |
| 実効ユーザーでの読取失敗 | `unknown / read_failed` |
| liveness reader失敗 | `unknown / liveness_unavailable` |
| snapshot途中のowner交換・削除・作成 | `unknown / concurrent_change` |
| 登録破損と正常未登録 | `unknown`と`not_found`を区別 |
| 全状態の前後差 | claim/release/GC/登録/DB更新/process起動が0 |

競合対照はtest-only barrierで読取境界を固定する。
sleepだけ、偶然の競合、空stdoutだけを証拠にしない。

### 検査の負の対照

少なくとも次の変異を個別にKILLする。

- 空出力を`absent`へ変える
- read errorを`absent`へ変える
- 前後fingerprint確認を削る
- stale照会後にGCする
- ownerをSIDだけで比較する
- 非regular pathを不存在として扱う
- `concurrent_change`を最後に読んだownerへ追従する

## 7. 完了境界

この設計PRの完了は、公開契約、6 hunkの不可分性、Patch A/Bの境界、適合試験計画が
固定HEADでformal reviewを通ることまでである。

次工程のprogrammerは隔離fixtureとpatch artifactを作る。
verifierは別contextで`value / cutoff / source / command`と正負対照を実測し、
reviewerは固定HEAD全差分を一括走査する。

次は本設計の範囲外である。

- 実装、現役install/DB/claimの変更
- 公式repositoryへの投稿、merge、release
- P2接続または解除
- 内部schemaの外側reader、影claim、恒久fork fallback
- B1 registration照会との統合

READMEへの影響はない。本書は`docs/decisions/`の開発用設計である。

Refs #247, #222, #236
