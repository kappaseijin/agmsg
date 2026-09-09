---
type: Design
title: "Issue #222: 親Issue最終計画不足の監査"
description: >-
  受入済みの #253/#247/#246/#254/#239/#280/#347 を、三分割の検討完了に必要な
  採用版・公開契約・責務・移行境界へ対応付け、物理移行を開始しない親Issue close条件を定める。
timestamp: "2026-09-09T18:45:00+09:00"
issues: [222, 253, 247, 246, 254, 239, 280, 347]
source_head: "815b7fdc01c1f347968febbb1c9a840a341262e2"
upstream_reference_cutoff: "e58dbafad5a84be625f070385bb0c076c3daa4db"
producer: agmsg_architect_codex
reviewer: agmsg_reviewer_claude
---

# Issue #222 親Issue最終計画不足の監査

## 結論

**親Issue #222 の「検討・受入済み分離計画」は、本書が固定HEADで formal review を受ければ完了としてよい。**
manager はその受入証跡を GitHub 正本で確認してから、手動で #222 を close できる。

これは agmsg / agguild / agguild_pool の物理三分割、上流への提案投稿、現役
`codex_monitor_agents` の移行、又は P2 pilot の完了を意味しない。これらは別の起点依頼が
必要であり、本書によって実行ゲートを開かない。

## 1. 監査基準

| 項目 | 固定値 | 用途 |
| --- | --- | --- |
| 監査対象 main | `815b7fdc01c1f347968febbb1c9a840a341262e2` | 本監査で対応付けた fork の状態 |
| 三分割の比較用 upstream cutoff | `e58dbafad5a84be625f070385bb0c076c3daa4db` | #222/#254 台帳の比較基準。採用済み provider version ではない |
| 台帳の fork cutoff | `7ea795e93683b0e7ede0fabf017fe503fcfe6aea` | `docs/migration/222-hunk-ledger.tsv` の734 hunk比較基準 |
| 台帳の共通祖先 | `3d06318de3aff9929cfaf87c092fef6709d2cc8b` | fork/公式差分の比較起点 |

この監査で採用する version の結論は、**実移行に使う upstream/provider version は未採用**である。
`e58dbaf` は不足を測った参照点であり、fork 内の B1/B2 提案準備を公式提供化した証拠にはしない。
実移行の起点依頼では、agguild が依存する公開 capability、schema、exit status と検証済み
provider commit を互換性 manifest に改めて固定する。

## 2. 受入済み成果物と親計画への対応

| Issue | 受入済み成果物 | 親Issueへ固定する意味 | 過大に読んではならない範囲 |
| --- | --- | --- | --- |
| #254 | `222-hunk-ledger.tsv`、責務分類、段階移行契約 | 734 hunkの所属・移植候補を台帳化し、persona path と登録 project path を分ける | hunk分類は物理移動命令ではない。台帳の比較cutoffも移行先 version ではない |
| #253 | 公開通信経路と lease/ack の最小契約、隔離fixture | handoff と ack、unknown と成功を分ける B3 の判断境界 | 実P2接続、実teamへの lease/ack 導入、上流 message-claim 採用の証明ではない |
| #247 | 副作用なし actas owner 照会の提案・適合対照 | owner / normal absence / stale / unreadable / unknown を潰さない B2 契約 | fork patch を upstream 提供版と扱わない。claim/releaseを観測手段にしない |
| #246 | 非集約 registration 照会の提案・完全性対照 | team/agent/type/canonical project の B1 完全性を要求する | fork patch を upstream 提供版と扱わない。個人 role/account policy は公開APIへ入れない |
| #239 | gh write guard の入口契約 | account選択・実行入口は agguild の運用責務である | 現行 guard 是正を B1提供待ちや三分割完了へ結び付けない |
| #280 | PM role に限る `pr merge` 契約 | producer / reviewer / manager の merge 権限を分離する | physical migration の認可にはならない |
| #347 | 既知 wrapper 後の依存名抽出契約と実装 | 台帳検査の `run` 等の偽陰性を縮め、台帳の回帰境界を補強する | 動的 source 解決や完全な shell parse を保証しない |

各成果物はそれぞれの Issue / PR の固定HEAD review・CIで受入されたものを指す。
本表は別Issueの再検証や再実装を要求しない。ただし上流提供版、実接続、物理移行の受入では、
当該時点の固定HEADを対象に独立対照を取り直す。

## 3. 三分割の責務と公開契約

| 層 | 所有するもの | 依存してよい入口 | 禁止する近道 |
| --- | --- | --- | --- |
| agmsg | 通信、登録、actas、公開API、公式installer | 採用済み provider の公開 command / schema / exit status | agguild の private DB、roster、claim file を公開ABIにすること |
| agguild | role運用、launcher、broker、collector、guard、account policy、互換性manifest | 明示固定した agmsg 公開 capability と OS/CLI 契約 | provider未対応時の内部file読取、影claim、恒久fork fallback |
| agguild_pool | persona、project差分、kaizen、管理manifest、明示された persona 固有設定 | agguild の manifest 経由の参照 | 共有実行層、共有PATH、agmsgの認可正本、live DB の置場 |

public contract の残存不足は「不明」として保持する。B1/B2 は本forkで提案準備まで受入済みだが、
実移行が要求する provider availability は未確認である。B3 は #253 の採用経路・正負対照の
範囲までで、実P2通信を代替しない。したがって #236 は `blocked:dependency` を維持する。

## 4. 実行時の path、停止、再開、rollback 契約

実移行を別途許可するまで、現役の作業clone、agmsg registration の project path、live DB、
runtime claim、session resume 記録は変更しない。特に persona を pool に移すことと
registration project を移すことを同じ手番にしない。

将来の実行依頼は次の順序を守る。

1. 隔離 fixture で provider/version と manifest を固定し、登録tuple、owner、未処理 ID、ack 境界、resume、writer を取得する。
2. 取得不能、unknown、複数identity、未読集合不一致なら停止する。推測で前進しない。
3. 停止可能な1席だけを限定対象にし、新旧 writer が同じ live DB へ同時接続しないことを確認する。
4. 新側で処理済み ID が無いことを確認してから、旧 path / registration へ戻す。新側で処理済みなら古い DB snapshot を復元せず、ID と cursor を照合できるまで停止を維持する。
5. 全席移動、未読 reset、全claim GC、旧root削除は別の明示起点に分ける。

この停止・再開・rollback は設計上の必要条件であり、今回の監査で実測・実行した主張ではない。

## 5. 親Issueを閉じる判定

manager が #222 を close できるのは、次の全てを GitHub 正本から確認できるときだけである。

1. 本PRの固定HEADに対し、`agmsg_reviewer_claude` に対応する Claude account の formal review が APPROVED である。
2. 本PRの required CI が全て成功し、review 対象 commit と `headRefOid` が一致する。
3. 本書が #253/#247/#246/#254/#239/#280/#347 を上表の範囲へ対応付け、upstream採用・物理移行・P2 pilotを未達として明記している。
4. #236 が `blocked:dependency` のままであることを読み戻す。

このcloseは「検討・受入済み分離計画」の完了である。物理三分割・上流提案・現役移行・P2 pilotは
未許可のfollow-upとして残す。#341、#294、#272、#273、#268の診断・小物を本PRへ混ぜない。

## 6. 実装・READMEへの影響

本PRは `docs/decisions/` の監査文書だけを追加する。コード、台帳、設定、README、live agent dataを
変更しない。実際の分離を許可する将来の実装では、agmsg / agguild / agguild_pool の各 README に
対応version、配置、停止、更新、復旧、アンインストールを自己完結で記載する。

Refs #222 #253 #247 #246 #254 #239 #280 #347
