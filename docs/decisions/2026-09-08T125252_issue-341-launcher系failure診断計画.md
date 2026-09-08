---
type: Plan
title: Issue #341 launcher系failure診断計画
timestamp: "2026-09-08T12:52:52+09:00"
updated: "2026-09-08T12:52:52+09:00"
issue: 341
---

# Issue #341 launcher系failure診断計画

## 結論

既知の失敗を、少なくとも次の2シグネチャに分けて診断する。

1. `launcher: reaps owned launcher and bridge but leaves foreign controls` の `owned-inclusion` 失敗
2. `launcher: reaps a same-(project,role) orphan the pidfile lost, converging to one (#937)` の置換PID未観測

前者には、プロセススナップショット取得後にbridge PIDがpidfileへ現れ、後から読んだPIDを古いスナップショットへ照合しているという有力な観測競合仮説がある。
後者は、旧プロセス終了までに共有deadlineを消費し、置換PIDを期限内に観測できなかった別シグネチャである。
決定的な対照実験が終わるまでは同一起因と断定しない。
`Broken pipe` は入口が異なるため本計画の対象外とする。

## 既知の一次観測

| シグネチャ | 実行 | OS / shard | 観測 |
|---|---|---|---|
| `owned-inclusion` | PR #338 run `34129227211` attempt 2 / job `101783989472` | macOS / 2 | snapshot時はbridge空、直後のbridge-readyでは新PID、snapshot候補は更新されない |
| `owned-inclusion` | PR #316 run `34083803912` attempt 1 / job `101623959597` | Ubuntu / 5 | 同テストが`owned-inclusion`で失敗 |
| `owned-inclusion` | PR #350 run `34177637482` attempt 1 / job `101910140823` | Ubuntu / 5 | snapshot候補にbridge PIDが無いまま、直後にbridge PIDが出現 |
| `#937` | PR #338 run `34129227211` attempt 1 / job `101765302572` | macOS / 2 | 15秒経過時にold PIDは既知、new PIDは未観測、`last_count=1` |

末尾の`ok 265`はTAPのテスト番号であり、全試験成功件数として扱わない。
Issue #317はこの誤読を前提としていたため`CLOSED / NOT_PLANNED`となり、当該失敗はIssue #341の既知群へ戻す。

## 診断の実施順

### 1. 失敗ジョブごとの証拠パケットを固定する

次を1ジョブ1レコードで保存する。

- PR、run、attempt、job、固定HEAD、OS、shard
- 失敗した完全なテスト名とTAPの`not ok`
- 診断phase、経過時間、deadline
- dispatcher、bridge、foreign、old、newのPIDとstart token
- pidfile値、プロセススナップショット、各値の観測順序
- `value / cutoff / source / command`

最終行や終了コードだけで分類せず、対象テスト自身の`not ok`を必須証拠とする。

### 2. `owned-inclusion`を同一時点の観測へ分解する

polling iterationごとに、単一の時刻とiteration IDへ次を束ねる。

- process tableの取得時刻と候補PID集合
- snapshot前後のpidfile値
- dispatcher / bridgeのPIDとstart token
- `snapshot_contains_dispatcher` / `snapshot_contains_bridge`
- foreign controlの生存条件

「snapshot後に読んだbridge PIDが、snapshot時点で存在した」とみなさない。
分類用計測は追加してよいが、本番reaperの条件や待機時間は変更しない。

### 3. `owned-inclusion`の正負対照を作る

疑っている失敗モードを確実に再現するため、テスト専用barrierで「process snapshot取得後、pidfile再読前」にbridge PID公開を止める。

- 正の対照: 古いsnapshotと新しいpidfile値の組を決定的に作り、stale tuple分類が発火する
- 負の対照: bridge PID公開後にsnapshotを取得し、同じsnapshotにbridge PIDが含まれる
- 検査の対照: 分類フィールドを欠落させたfixtureを拒否し、計測漏れによる偽陰性を防ぐ

既存barrierがこの境界を固定できるかを先に実測し、固定できなければテスト側だけに診断seamを設ける。

### 4. `#937`を共有deadlineの各phaseへ分解する

次の初回観測時刻を個別に記録する。

- old identity安定
- old process終了
- replacement spawn event
- lease / pidfile公開
- replacement PIDとstart token
- process countの変化

正の対照では前段phaseに時間を消費させ、残り時間より後にreplacementを公開する。
負の対照ではold終了とreplacement公開を直ちに行う。
単なるtimeout延長は診断にならないため行わない。

### 5. OS横断で最小行列を比較する

UbuntuとmacOSのそれぞれで、対象テスト単体、対象ファイル、元のshardの順に比較する。
最初は決定的barrier付き単体試験で観測契約を確認し、その後だけ周辺負荷の影響を見る。
同一の観測tupleとbarrier条件でOS差が残った場合に限り、OS固有仮説へ進む。

## 判定基準

| 観測 | 判定 |
|---|---|
| snapshot後にbridge PIDが公開され、古いsnapshotにだけbridgeが無い | `owned-inclusion`の観測競合を確認 |
| 同一時点snapshotにowned PIDが揃うがreap後の生存条件が破れる | product reaper側の別診断へ進む |
| old終了までに共有deadlineを消費し、replacementが期限後に現れる | `#937`の共有budget枯渇を確認 |
| replacement自体が生成されない | spawn / lease公開経路の別診断へ進む |
| 同一barrier・同一tupleでもOSだけで結果が分かれる | OS固有仮説を採用候補にする |

## 偽陰性と完了条件

この検査が偽陰性を返す主な条件は、観測値を別々の時点から集めながら同一時点の状態として保存する場合である。
そのため、iteration ID、観測時刻、snapshot前後のpidfile値を一つの証拠パケットに含める。

診断実装の完了条件は次のとおり。

1. 注入したstale tupleを両OSで確実に検出できる
2. bridge公開後の負の対照はstale tupleと分類されない
3. `#937`でold終了、replacement生成、観測deadlineのどこが律速か区別できる
4. 既知の失敗ログを2シグネチャへ無理なく対応付けられる
5. 本番挙動、timeout、reap条件を変更していない
6. 生ログを秘匿情報なしのdurable artifactとして取得できる

## 対象外

- 原因仮説に基づく修正
- 無変更でのCI rerun
- `Broken pipe`との共通原因の先決め
- 他Issueの並行診断

READMEへの変更はない。

Refs #341
