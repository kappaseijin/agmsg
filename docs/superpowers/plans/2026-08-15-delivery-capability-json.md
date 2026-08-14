---
type: Plan
title: "Issue #39: delivery capability status JSON implementation plan"
issue: "https://github.com/kappaseijin/agmsg/issues/39"
timestamp: "2026-08-15T03:38:08+09:00"
---

# Delivery Capability Status JSON Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `delivery.sh status <type> <project> --format json` で、設定だけではない seat ごとの delivery capability を fail-closed に提供する。

**Architecture:** `scripts/lib/delivery-capability.sh` が watcher / bridge / role-session / receipt の根拠を structured JSON にする。`delivery.sh` は human path を保ったまま JSON path を helper へ委譲する。

**Tech Stack:** Bash 3.2、SQLite JSON1、Bats。

**Spec:** `docs/superpowers/specs/2026-08-15-delivery-capability-json-design.md`

## Constraints

- `deliverable: true` は type-specific の live runtime と session binding が確認できる場合だけにする。
- hook configured、app-server started、turn mode、trust prompt 待ちは成功を意味しない。
- 観測不能は推測せず `"unknown"` にする。
- receipt ACK と task completion を混同しない。
- macOS Bash 3.2 と SQLite JSON1 を維持する。
- `delivery.sh status` の human output を変更しない。

### Task 1: JSON contract の RED tests

**Files:**
- Modify: `tests/test_delivery.bats`

- [x] Claude Code: hook-only / turn は live receiver でなく、role-scoped live readiness sentinel だけが `deliverable: true` になる integration tests を書く。
- [x] Codex: live bridge + matching metadata + role-session は true、unstarted seat / dead pid / metadata mismatch は true にならない tests を書く。
- [x] receipt: handoff count と `ackSemantics` が JSON に含まれ、task completion を主張しない test を書く。
- [x] unsupported runtime は `unknown` を返し、human status branch が従来どおり通る test を書く。
- [x] Run targeted Bats and confirm the new tests fail before implementation.

### Task 2: capability helper と status parser

**Files:**
- Create: `scripts/lib/delivery-capability.sh`
- Modify: `scripts/delivery.sh`
- Test: `tests/test_delivery.bats`

- [x] `--format human|json` と二つの positional status argument を parse する。human default を既存 `do_status` に送る。
- [x] config mode、registered identities、`message-status.sh --format json` を structured input として収集する。
- [x] Claude Code readiness sentinel + owner liveness を probe する。
- [x] Codex role-session / bridge pid / metadata project/type/pid を probe する。
- [x] seat JSON、安定順の aggregate JSON、state evidence を生成する。
- [x] Run focused Bats until green.

### Task 3: README と verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-15-delivery-capability-json.md`

- [x] README に JSON command と `true` / `false` / `unknown` の判断方法、receipt ACK の限定を記載する。
- [x] Run `bash -n scripts/delivery.sh scripts/lib/delivery-capability.sh`.
- [x] Run `bats tests/test_delivery.bats`, full `bats tests`, and `git diff --check`.
- [x] Update this plan with actual verification evidence before the PR.

## Verification evidence

- RED: `bats tests/test_delivery.bats --filter 'delivery status JSON:'` failed before the JSON implementation because human status output is not JSON.
- Focused: `BATS_SHELL=/bin/bash bats tests/test_delivery.bats --filter 'delivery status JSON:'` — 9 passed on Bash 3.2.57.
- Delivery regression: `BATS_SHELL=/bin/bash bats tests/test_delivery.bats` — 176 passed.
- Full regression: `bats tests` — 1021 passed.
- Static checks: `/bin/bash -n scripts/delivery.sh scripts/lib/delivery-capability.sh`, `shellcheck -s bash scripts/lib/delivery-capability.sh`, and `git diff --check` passed.

### 2026-08-15T04:46:01+09:00 — macOS CI compatibility follow-up

- GitHub Actions の失敗 job は Bats 1.13.0 と macOS Bash 3.2.57 で動作していた。
- `BASHPID` は Bash 3.2 に存在せず、live Claude watcher test の session ID が空の接尾辞になっていた。portable な `$$` に置き換えた。
- 同 test の独自 `EXIT` trap は Bats の test-result trap を上書きし、失敗を `Executed 254 instead of expected 255` として隠していた。watcher PID は Bats の `teardown` で後始末するようにした。
- watcher の poll interval は readiness の待機条件を変えずに teardown 後の内部 sleep を最大 1 秒へ制限した。
- Verification: Bats 1.13.0 + `/bin/bash` 3.2.57 で `delivery status JSON: only a live Claude role watcher is deliverable` が 1 passed、通常の `BATS_SHELL=/bin/bash bats --print-output-on-failure tests/test_delivery.bats` が 176 passed。
