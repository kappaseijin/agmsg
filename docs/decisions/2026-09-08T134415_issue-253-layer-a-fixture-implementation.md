---
type: Implementation
title: Issue 253 layer A receipt isolation fixture
description: >-
  handoff receipt と既読状態を分ける層 A の受入対照、および inbox/watch の2中断点を
  固定 F source の隔離 root で実装・実測した記録。
timestamp: "2026-09-08T13:44:15+09:00"
issues: [253]
---

# Issue 253 layer A receipt isolation fixture

## 実装

`scripts/issue253_public_path.py` に、固定 F source を本番 DB と分離した root へ展開し、
実際の `inbox.sh`、`watch.sh`、SQLite storage API を通す層 A 対照を追加した。

- handoff 後の receipt、経路名、冪等性
- receipt の無い既読行の `legacy_read`
- receipt 記録失敗時の配送成功と診断
- receipt 削除後の `legacy_read` への遷移
- inbox/watch ごとの「stdout 後・消費前」と「消費後・receipt 前」の中断

中断点は一方ずつ単独で注入する。各点について receipt 行数、三値 status、消費状態、
次回配送を別々に記録する。watch 再開時は実 poll 到達を正の対照にし、起動時間を
再配送失敗と誤認しない。

## 実測

確認日時: `2026-09-08T13:44:15+09:00`

```sh
python3 scripts/issue253_public_path.py \
  --local-repo /Users/kappa/Dropbox/data/dev/codex_monitor_agents/agmsg-programmer \
  --output /tmp/agmsg253-layer-a-full.cQtsvg
```

| value | cutoff | source | command |
| --- | --- | --- | --- |
| `harness_status=completed` | F `7ea795e93683b0e7ede0fabf017fe503fcfe6aea` / O `e58dbafad5a84be625f070385bb0c076c3daa4db` | `/tmp/agmsg253-layer-a-full.cQtsvg/report.json` | 上記 harness command |
| `layer_a=pass` | F | 同上 `layer_a.status` | 同上 |
| `inbox_interrupted=pass` | F | 同上 `layer_a.inbox_interrupted.status` | 同上 |
| `watch_interrupted=pass` | F | 同上 `layer_a.watch_interrupted.status` | 同上 |
| `reference_claims.rc=0` | F | 同上 `reference_claims` | 同上 |

中断点1は両経路とも `receipt_count=0 / receipt_status=none / consumed=false /
replayed=true`、中断点2は `receipt_count=0 / receipt_status=legacy_read /
consumed=true / replayed=false` だった。

report SHA-256 は
`eecfc8f2b02278ccd9f3935a7b6a712821843b962c38bd512ec53da3d0fdc43f`、
harness SHA-256 は
`814fb523f846b496176c0d66a2728a7c71119f1887fac2f0ac693c2005c2951a`。

## 検査の負の対照

```sh
python3 -m unittest tests/test_issue253_public_path.py
python3 tests/issue253_mutations.py
```

単体テスト10件は pass。既存4件に層 A を壊す7件を加えた11変異はすべて KILLED。
receipt 経路名、handoff、冪等性、legacy 判定、失敗診断、削除後判定、2中断点の
再配送差を個別に壊して検出した。

## 限界

- 本実測は隔離 fixture であり、本番 DB、live Monitor、P2 実接続には触れていない。
- 層 B と上流 PR #372 は実装・認定していない。
- F/O の aggregate compatibility は既存の別対照により両方 `incompatible` のままで、
  層 A の pass を B3/P2 全体の受入へ昇格させない。
- `storage_receipt_status` の未該当値は既存 production 契約どおり `none` とした。
