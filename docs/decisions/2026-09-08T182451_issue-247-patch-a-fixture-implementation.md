---
type: Implementation
title: Issue 247 Patch A official cutoff isolation fixture
description: >-
  official cutoffへfail-closed owner readerの4 hunkを適用し、旧挙動のRED、回帰、
  副作用、Patch Aに属する変異を隔離環境で確認した記録。
timestamp: "2026-09-08T18:24:51+09:00"
updated: "2026-09-08T18:43:00+09:00"
issues: [247, 222, 236]
---

# Issue 247 Patch A official cutoff isolation fixture

## 実装

`patches/issue247-patch-a.patch`へ、訂正済み台帳がofficialと分類する次の4 hunkを
固定した。

- `scripts/lib/actas-lock.sh`: `2e5aca02e2cb0eab`、`9d6dd19091fda520`
- `tests/test_actas_lock.bats`: `e259257277842e00`、`980b47a20555bd96`

`scripts/issue247_patch_a.py`はGit object
`e58dbafad5a84be625f070385bb0c076c3daa4db`を隔離rootへ展開し、無変更版のRED、
`git apply --unidiff-zero`による4 hunk一括適用、全`test_actas_lock.bats`、owner状態と
副作用を記録する。zero-contextは台帳のhunk境界を維持するための固定条件である。

owner交換は`issue247-owner-swap.reached`と`.release`のtest-only barrierで読取境界を
固定する。時間経過や空stdoutだけを競合成立の証拠にしない。

## 実測

確認日時: `2026-09-08T18:24:51+09:00`

```sh
python3 scripts/issue247_patch_a.py \
  --local-repo /Users/kappa/Dropbox/data/dev/codex_monitor_agents/agmsg-programmer \
  --output /tmp/issue247-fixture4-final5.QoVFZV
```

| value | cutoff | source | command |
| --- | --- | --- | --- |
| `status=pass` | `e58dbafad5a84be625f070385bb0c076c3daa4db` | `/tmp/issue247-fixture4-final5.QoVFZV/report.json` | 上記harness command |
| `patch_shape=source 2 / test 2` | 同上 | 同上 `patch_shape` | 同上 |
| 旧版 `empty/read_error=free, rc=0` | 同上、無変更 | 同上 `red.observation.raw` | 同上 |
| 適用後 `empty/read_error=unknown, rc=1` | 同上＋Patch A | 同上 `green.raw` | 同上 |
| `test_actas_lock.bats=24/24` | 同上＋Patch A | 同上 `focused_test` | 同上 |
| `registration/DB/runtime/process差分=0` | 同上＋Patch A | 同上 `side_effects` | 同上 |

同SID別PIDは完全tokenの`other`、staleは`free`かつclaim保持だった。不正な複数行claimは
既存writer readerの範囲では`legacy_stale`としてclaimを保持する。strictな不正形式分類は
Patch Bの公開snapshot queryへ残し、Patch Aの成功へ読み替えない。

report SHA-256は
`338c5e6a22346ec847e68d25c3067d6a175c2fb7a5d353123293dc78442ce9d7`、
Patch A SHA-256は
`3199da771286cf229d91f7c08d7693b8ed76e9bad60f7c53a3fd6681ccdca49f`。

## 変異検査

```sh
python3 tests/issue247_mutations.py
python3 -m unittest tests/test_issue247_patch_a.py -v
```

Patch Aに属する4変異はすべてKILLEDした。

- 空ownerをfreeへ潰す
- owner read errorをfreeへ潰す
- stale照会後にGCする
- ownerをSIDだけで比較する

fingerprint再確認削除、非regular pathを不存在扱い、競合時に最新ownerへ追従、の3変異は
Patch Bのsnapshot queryに属する。Patch AでKILLしたとは報告せず、`deferred_patch_b`として
機械可読に記録する。

## 範囲

- 現役install、DB、claim、processへ接続していない。
- READMEへの影響はない。公開commandはPatch Bの別PRで扱う。
- 上流投稿、公式採用、P2解除、Issue #247完了を主張しない。

Refs #247, #222, #236
