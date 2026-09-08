---
type: Implementation
title: Issue 247 Patch B official cutoff isolation fixture
description: >-
  Patch A適用済みofficial cutoffへread-only actas-owner queryを適用し、公開schema、
  snapshot競合、fail-closed状態、副作用とPatch B変異を隔離検証するfixture。
timestamp: "2026-09-08T19:49:38+09:00"
updated: "2026-09-08T19:49:38+09:00"
issues: [247, 222, 236]
---

# Issue 247 Patch B official cutoff isolation fixture

## 実装

`patches/issue247-patch-b.patch`は、固定済みPatch Aを前提として次の4ファイルだけを変更する。

- `scripts/api.sh`: 公開`actas-owner` route
- `scripts/lib/api-actas-owner.sh`: schema v1 snapshot query
- `tests/test_api_actas_owner.bats`: 11件の適合対照
- `README.md`: command、状態、終了値、raceと認可境界

PR #252のowner readerはofficial cutoffに存在しない`agmsg_validate_utf8`へ依存していた。
境界を`validate.sh`へ広げず、同じbyte-wise検査をowner readerのprivate関数へ閉じ込めた。
また、PR #252の`api.sh`文脈に含まれたB1 `api-registrations.sh`読込と
`get_registrations` routeは取り込んでいない。古いshared `test_helper.bash`も変更せず、
Patch B固有testのsetup内で`SKILL_DIR`、`RUN_DIR`、`DBPATH`だけを定義する。

`scripts/issue247_patch_b.py`はofficial Git objectを隔離rootへ展開し、固定SHA-256の
Patch A、Patch Bの順に適用する。Patch Aだけでは公開routeが無いRED、全11試験、
永続pathとprocessの前後差、3件の独立変異を機械可読な`report.json`へ保存する。

競合試験は`.reached`と`.release`のtest-only barrierでownerまたはregistration交換の
境界を固定する。sleepや空stdoutだけを競合成立の証拠にしない。

## 実測

確認日時: `2026-09-08T19:49:38+09:00`

```sh
python3 scripts/issue247_patch_b.py \
  --local-repo /Users/kappa/Dropbox/data/dev/codex_monitor_agents/agmsg-programmer \
  --output /tmp/issue247-patchb-final2.SUul5T
```

| value | cutoff | source | command |
| --- | --- | --- | --- |
| `status=pass` | `e58dbafad5a84be625f070385bb0c076c3daa4db`＋固定Patch A | `/tmp/issue247-patchb-final2.SUul5T/report.json` | 上記harness command |
| 公開route RED `rc=1` | Patch Aのみ | 同上 `red` | 同上 |
| `test_api_actas_owner.bats=11/11, rc=0` | Patch A＋Patch B | 同上 `focused_test` | 同上 |
| 永続path/process差分 `0` | Patch A＋Patch B | 同上 `side_effects` | 同上 |
| 変異3件 `KILLED, rc=1` | Patch A＋Patch Bの独立変異 | 同上 `mutations` | 同上 |

report SHA-256は
`fea24e3004ec30d6e6f33a46e24538d3bba8f8ad65881946d6be8f14a1d668a7`、
Patch B SHA-256は
`878e37fcc36904725b5bcbfb21f52310b118bf18838d690f6a15afdbf82d3f91`。

## 適合観点

- `owned`、`stale`、`absent`、`not_found`、全`unknown`理由、CLI errorをschema v1で分類する
- composite ownerをSIDへ短縮せず、legacy ownerも明示的に分類する
- 空、不正byte、複数行、空白・制御文字、directory、symlinkを`claim_invalid`にする
- 実効ユーザーで読めない親pathを`read_failed`にする
- owner/registration交換を`concurrent_change`にする
- claim、registration、DB/runtime、processを変更しない

変異検査はPatch Aからdeferredされた次の3件を一件ずつ反転する。

- 返却前fingerprint/snapshot再確認を外す
- nonregular pathを`absent`へ潰す
- concurrent change後に最新ownerへ追従する

## 範囲

現役install、DB、claim、processへ接続しない。B1 registration API、PM policy、公式投稿、
release、P2解除、Issue closeは行わない。README変更は公開commandの利用方法に限定する。

Refs #247, #222, #236
