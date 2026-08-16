---
type: Plan
title: Issue #65 — Codex SessionStart JSON output contract
description: agmsg の Codex SessionStart hook が全 return path で有効な JSON object を返すようにする。
tags:
  - issue-65
  - issue-177
  - codex
  - hooks
status: complete
timestamp: "2026-08-16"
---

# Issue #65 — Codex SessionStart JSON output contract

## 目的

current Codex の SessionStart parser が要求する JSON object を、agmsg の Codex hook が全 return path で stdout へ 1 個だけ返すようにする。

## 受入条件

- no-op は `{}` を返す。
- directive/context は `hookSpecificOutput.hookEventName=SessionStart` と `additionalContext` を持つ envelope へ変換する。
- 診断は stderr へ出し、stdout に plaintext や空出力を残さない。
- JSON の quote、改行、バックスラッシュを含む context を隔離 fixture で検証する。
- source の正性・負性テストを追加し、既存の Codex bridge 引き渡しを維持する。
- installed `/Users/kappa/.agents/skills/agmsg`、global hooks、shared config、spawn、live session、bridge/pidfile/request は変更しない。

## 実装境界

1. `scripts/session-start.sh` の Codex dispatch だけを capture/normalize する。
2. `scripts/drivers/types/codex/_session-start.sh` に Codex JSON envelope helper を置く。
3. Claude その他の delivery hook の stdout 契約は変更しない。
4. installed provenance は upstream release / 正規同期の別タスクとして残す。

## Provenance

- source `scripts/session-start.sh`: `447fd8e52856f0d9a3f7a11f904f90075a32e27d225b9ec1b709ead25abf35c1`
- source `scripts/drivers/types/codex/_session-start.sh`: `cae4cfd2dfa39814a732c1e7b84c63172da33f9f0f6bf026b4b9f10592e6e09b`
- installed `session-start.sh`: `8e29efe99558a5cb366bb6e1c5b4c42e9e30de47a9788a1626a7852505de46f6`（未変更）
- installed Codex driver: `f056c56096f12392da4afbcc59948e512c2d004e9992d748e9535fb30d96df2f`（未変更）

## 検証

- Codex no-op の `{}`。
- plaintext directive の JSON envelope 化。
- context の特殊文字が valid JSON object のまま保持されること。
- hook failure の JSON stdout と stderr diagnostic の分離。
- 既存 Codex bridge fixture と focused/full Bats。
- focused Codex hook: 10/10。
- delivery + close-fds + type-registry: 217/217。
- full `bats tests/`: 1692/1692。
- `bash -n scripts/session-start.sh scripts/drivers/types/codex/_session-start.sh` と `git diff --check`。
