---
type: Plan
title: "Issue #38: roster JSON contract implementation plan"
issue: "https://github.com/kappaseijin/agmsg/issues/38"
timestamp: "2026-08-15T02:20:04+09:00"
---

# Roster JSON Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `team.sh` と `whoami.sh` に、推測なし・fail-closed の roster JSON contract を追加する。

**Architecture:** `scripts/lib/roster-contract.sh` に versioned team config の検査、team JSON serialization、project/runtime の exact registration lookup を集約する。`join.sh` は新規 config に schemaVersion と metadata を書き、既存の human command branch はそのままにする。

**Tech Stack:** Bash 3.2、SQLite JSON1、Bats。

**Spec:** `docs/superpowers/specs/2026-08-15-roster-json-contract-design.md`

## Global Constraints

- JSON contract は `schemaVersion: 1`、`kind` は `seat|human|service`、`role` は非空文字列とする。
- role/kind を agent 名または runtime 名から推測しない。
- JSON schema error は stdout を空にし、stderr に `schema error:`、exit 2 を返す。
- legacy config の human output / 操作は変更しない。
- macOS Bash 3.2 と SQLite JSON1 だけに依存する。

---

### Task 1: JSON contract の RED tests

**Files:**
- Modify: `tests/test_team.bats`

**Interfaces:**
- Consumes: `join.sh <team> <agent> <type> <project> [--role role] [--kind kind]`
- Produces: `team.sh <team> --format json` と `whoami.sh <project> <runtime> --format json` の受入テスト。

- [x] **Step 1: normal roster JSON の失敗テストを書く**

  fixture を `join.sh demo zed codex /tmp/z --role reviewer --kind seat` と `join.sh demo amy claude-code /tmp/a --role architect --kind human` で作る。
  `team.sh demo --format json` の `members[0].name == "amy"`、`members[1].role == "reviewer"`、registration の type/project を検査する。

- [x] **Step 2: targeted test を実行して red を確認する**

  Run: `bats tests/test_team.bats --filter 'team json: emits explicit member metadata in stable order'`

  Expected: `--format json` 未実装により FAIL。

- [x] **Step 3: negative と whoami JSON tests を追加する**

  legacy config の JSON mode が exit 2 / 空 stdout / `schema error:` になること、human mode は member を表示し続けることを検査する。
  明示 role/kind の agent について `whoami.sh /tmp/p codex --format json` が config の role をそのまま返すことを検査する。

- [x] **Step 4: JSON test 群を実行して red を確認する**

  Run: `bats tests/test_team.bats --filter 'json:'`

  Expected: implementation 前の success case は FAIL、既存 human tests は PASS。

### Task 2: versioned roster metadata の書き込み

**Files:**
- Modify: `scripts/join.sh`
- Test: `tests/test_team.bats`

**Interfaces:**
- Consumes: positional join arguments と optional `--role` / `--kind` / `--force` flags。
- Produces: new config root `schemaVersion: 1`; new member `kind`, `role`, `registrations` fields。

- [x] **Step 1: join parser と new config builder を最小変更する**

  positional 四引数の後で flags を順不同に parse する。
  `--role` / `--kind` が指定されない new member は `unassigned` / `seat` を JSON に保存する。
  `--kind` は三値以外を拒否し、existing member の metadata は flag がある場合だけ更新する。

- [x] **Step 2: metadata preservation test を実行する**

  Run: `bats tests/test_team.bats --filter 'join:.*metadata|team json:'`

  Expected: second registration の追加後も role/kind が保持される。

- [x] **Step 3: join regression tests を実行する**

  Run: `bats tests/test_team.bats --filter 'join:'`

  Expected: PASS。

### Task 3: fail-closed roster helper と JSON command branches

**Files:**
- Create: `scripts/lib/roster-contract.sh`
- Modify: `scripts/team.sh`
- Modify: `scripts/whoami.sh`
- Test: `tests/test_team.bats`

**Interfaces:**
- Produces: `agmsg_roster_contract_team_json <config> <team>` と `agmsg_roster_contract_matching_json <config> <project> <runtime>`。
- Consumes: `agmsg_sql_readfile_path`、`agmsg_sqlite_mem`、`agmsg_project_sql_in_list` from `scripts/lib/storage.sh`。

- [x] **Step 1: schema validator を実装する**

  JSON validity、schemaVersion=1、config team 名、agents object、member kind/role、registrations array、type/project strings を検査する。
  最初の失敗を `schema error:` として stderr に出し、exit 2 を返す。

- [x] **Step 2: stable serializers を実装する**

  team members は `name COLLATE BINARY` で昇順、member registrations は config 内の array 順を維持する。
  whoami JSON は exact project/runtime registration だけを集め、未一致時は空配列を返す。

- [x] **Step 3: command parser branches を接続する**

  `team.sh demo --format json` と `whoami.sh /tmp/p codex --format json` を JSON helper へ送る。
  no-flag の human branch の出力・suggestion behavior は変更しない。

- [x] **Step 4: focused Bats を実行する**

  Run: `bats tests/test_team.bats`

  Expected: JSON success、schema error、human compatibility を含め PASS。

### Task 4: README と全体検証

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-15-roster-json-contract.md`

**Interfaces:**
- Documents: `join.sh` metadata flags と両 JSON command、legacy config の schema error behavior。

- [x] **Step 1: README に使用手順と JSON examples を追記する**

  `scripts/join.sh demo architect codex "$(pwd)" --role architect --kind seat`、`scripts/team.sh demo --format json`、`scripts/whoami.sh "$(pwd)" codex --format json` を self-contained な利用手順として載せる。

- [x] **Step 2: shell syntax と targeted suite を実行する**

  Run: `bash -n scripts/join.sh scripts/team.sh scripts/whoami.sh scripts/lib/roster-contract.sh && bats tests/test_team.bats`

  Expected: exit 0。

- [x] **Step 3: full suite と diff check を実行する**

  Run: `bats tests && git diff --check`

  Expected: all tests pass and no whitespace error。

- [x] **Step 4: verification results を計画書に記録して commit する**

  `git add scripts tests README.md docs/superpowers` の後に `git commit -m "feat: add roster JSON contract"` を作る。

## Verification Record

- RED: `bats tests/test_team.bats --filter 'team json: emits explicit member metadata in stable order'` は未実装のため失敗することを確認した。
- RED: `bats tests/test_team.bats --filter 'whoami json: returns the explicit registration without name inference'` は未実装のため失敗することを確認した。
- Focused: `bats tests/test_team.bats --filter 'join: writes explicit default roster metadata without inferring the agent name|join: rejects a roster kind outside the explicit contract|json:'` — 9/9 PASS。
- Team suite: `bats tests/test_team.bats` — 77/77 PASS。
- Syntax: `bash -n scripts/join.sh scripts/team.sh scripts/whoami.sh scripts/lib/roster-contract.sh` — PASS。
- Full suite: `bats tests` — 1,012/1,012 PASS。
- Whitespace: `git diff --check` — PASS。
