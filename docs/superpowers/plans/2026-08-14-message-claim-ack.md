---
type: ImplementationPlan
title: Issue #37 message claim/ack 実施計画
description: #18 の親子 Issue #37 を、SQLite claim/lease/ack と各 receiver adapter でローカル解決する。
tags:
  - agmsg
  - delivery
  - sqlite
  - issue-37
timestamp: "2026-08-14T23:37:57+09:00"
---

# Issue #37 Message Claim/Ack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan inline. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 送信・claim・host handoff を別状態に保存し、未起動または未確認の receiver を成功と表示しない。

**Architecture:** `message_claims` は短期の排他的 lease、`message_receipts` は handoff の永続証跡に分ける。Bash receiver は library を直接 source し、Codex bridge は thin CLI を呼ぶ。`read_at` は互換 marker に留め、receipt が無い既存 row は unknown と表示する。

**Tech Stack:** Bash、SQLite、Node.js、Bats。

**Spec:** `docs/superpowers/specs/2026-08-14-message-claim-ack-design.md`

## Global Constraints

- この PR は Issue #37 だけを closes し、#18 は #37 が closed になるまで開いたままとする。
- upstream `fujibee/agmsg` へ書き込まず、PR #372 を merge / cherry-pick / fetch しない。
- ACK は receiver の host handoff の証跡であり、LLM の業務タスク完了を意味しない。
- 期限切れ・owner 不一致・host handoff 未確認を success と表示しない。
- 実チーム・実席を使わず、`setup_test_env` の使い捨て SQLite store で検証する。

---

### Task 1: SQLite schema と atomic claim library を追加する

**Files:**
- Modify: `scripts/internal/init-db.sh`
- Create: `scripts/lib/claims.sh`
- Create: `scripts/claim.sh`
- Create: `tests/test_claims.bats`

**Interfaces:**
- Consumes: `messages(id, team, from_agent, to_agent, body, created_at, read_at)`。
- Produces: `message_claims`、`message_receipts`、`agmsg_claim_next`、`agmsg_claim_id`、`agmsg_ack_claim`、`agmsg_release_claim`。

- [x] **Step 1: claim exclusion の failing test を書く**

`tests/test_claims.bats` に、同一 message を daemon-a が claim した後 daemon-b が空 output を受ける test を追加する。

```bash
first="$(agmsg_claim_next team alice daemon-a 60)"
[ -n "$first" ]
run agmsg_claim_next team alice daemon-b 60
[ "$status" -eq 0 ]
[ -z "$output" ]
```

- [x] **Step 2: failing test を実行する**

Run: `bats tests/test_claims.bats`

Expected: FAIL because `scripts/lib/claims.sh` does not exist.

- [x] **Step 3: idempotent schema と claim API を実装する**

`init-db.sh` に次を追加し、`claims.sh` では `BEGIN IMMEDIATE` 内で expired claim を削除してから `INSERT OR IGNORE` する。

```sql
CREATE TABLE IF NOT EXISTS message_claims (
  message_id INTEGER PRIMARY KEY,
  owner TEXT NOT NULL,
  claimed_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS message_receipts (
  message_id INTEGER PRIMARY KEY,
  owner TEXT NOT NULL,
  handed_off_at TEXT NOT NULL,
  evidence TEXT NOT NULL
);
```

- [x] **Step 4: release、wrong-owner ACK、TTL reclaim の tests を追加して green にする**

Run: `bats tests/test_claims.bats`

Expected: claim exclusion、release/reclaim、wrong owner rejection、TTL reclaim、escaped body の全 case が PASS。

- [x] **Step 5: thin CLI を追加する**

`claim.sh` は `next`、`claim`、`ack`、`release` を library に 1 対 1 で委譲する。

```bash
case "$ACTION" in
  next) agmsg_claim_next "$TEAM" "$AGENT" "$OWNER" "${TTL:-30}" ;;
  claim) agmsg_claim_id "$MESSAGE_ID" "$OWNER" "${TTL:-30}" ;;
  ack) agmsg_ack_claim "$MESSAGE_ID" "$OWNER" "${EVIDENCE:-host_handoff}" ;;
  release) agmsg_release_claim "$MESSAGE_ID" "$OWNER" ;;
esac
```

### Task 2: queue 表示、receipt status、history semantics を追加する

**Files:**
- Modify: `scripts/send.sh`
- Create: `scripts/message-status.sh`
- Modify: `scripts/history.sh`
- Modify: `tests/test_messaging.bats`

**Interfaces:**
- Consumes: Task 1 の claim / receipt table。
- Produces: message id を含む queue output、JSON/human receipt status、正しい history legend。

- [x] **Step 1: send output の failing test を書く**

`tests/test_messaging.bats` の送信 test を次の期待へ変更する。

```bash
[[ "$output" =~ "Queued message" ]]
[[ "$output" =~ "delivery not yet acknowledged" ]]
```

- [x] **Step 2: failing test を実行する**

Run: `bats tests/test_messaging.bats`

Expected: FAIL because `send.sh` still prints `Sent to`.

- [x] **Step 3: send と status を実装する**

同一 sqlite invocation で `INSERT` の直後に `SELECT last_insert_rowid();` を実行し、取得した numeric id を queue output に入れる。`message-status.sh --format json` は以下を返す。

```json
{
  "schemaVersion": 1,
  "queued": 1,
  "claimed": 0,
  "handedOff": 0,
  "unknown": 0,
  "ackSemantics": "receiver_handoff_not_task_completion"
}
```

- [x] **Step 4: history の legacy marker test を追加する**

receipt 無しで `read_at` だけが非 null の fixture を作り、`history.sh` が `?` と legend を表示することを assert する。

- [x] **Step 5: focused tests を green にする**

Run: `bats tests/test_claims.bats tests/test_messaging.bats`

Expected: queue wording、JSON counts、legacy marker、既存 messaging behavior が PASS。

### Task 3: Bash receiver を claim → handoff → ack へ移行する

**Files:**
- Modify: `scripts/inbox.sh`
- Modify: `scripts/check-inbox.sh`
- Modify: `scripts/watch.sh`
- Modify: `tests/test_inbox.bats`
- Modify: `tests/test_watch.bats`

**Interfaces:**
- Consumes: Task 1 の Shell library。
- Produces: successful stdout の後だけ receipt を作る receiver behavior。

- [x] **Step 1: inbox handoff receipt の failing test を書く**

`inbox.sh` 実行後に、対象 message の `message_receipts` が 1 row で `read_at` も非 null になる test を追加する。

```bash
run bash "$SCRIPTS/inbox.sh" testteam alice
[ "$status" -eq 0 ]
[ "$(sqlite3 "$DBPATH" 'SELECT COUNT(*) FROM message_receipts;')" -eq 1 ]
```

- [x] **Step 2: failing test を実行する**

Run: `bats tests/test_inbox.bats`

Expected: FAIL because existing receiver updates only `read_at`.

- [x] **Step 3: inbox と check-inbox を実装する**

各 row を claim してから output buffer に追加し、output の successful write 後に `agmsg_ack_claim` を呼ぶ。buffer / write error は `agmsg_release_claim` を呼び、未読 row を残す。

- [x] **Step 4: watch の delivery path を実装する**

`watch.sh` の normal row と `ctrl:despawn` row は、列挙済み id を `agmsg_claim_id` で claim してから ack する。stdout `printf` が失敗した場合は release して watermark を更新せず exit する。broad/exclusive ready guard はそのまま維持する。

- [x] **Step 5: focused tests を green にする**

Run: `bats tests/test_inbox.bats tests/test_watch.bats`

Expected: existing watermark / readiness tests と追加 receipt tests が PASS。

### Task 4: Codex inline bridge を lease-aware にする

**Files:**
- Modify: `scripts/drivers/types/codex/codex-bridge.js`
- Modify: `tests/test_codex_bridge.bats`

**Interfaces:**
- Consumes: `claim.sh next|ack|release` と existing `turn/start` request。
- Produces: `--inline-inbox` の message body handoff receipt。

- [x] **Step 1: inline inbox receipt の failing integration test を書く**

既存 fake app-server test に message を事前投入し、`turn/start` が inline body を含むことと、成功後 `message_receipts` が 1 row になることを追加する。

```bash
bash "$SCRIPTS/send.sh" team bob alice "inline body reaches prompt" >/dev/null
run node "$TYPES/codex/codex-bridge.js" ... --inline-inbox
[ "$(sqlite3 "$TEST_SKILL_DIR/db/messages.db" 'SELECT COUNT(*) FROM message_receipts;')" -eq 1 ]
```

- [x] **Step 2: failing test を実行する**

Run: `bats tests/test_codex_bridge.bats --filter 'inline-inbox'`

Expected: FAIL because bridge currently calls `inbox.sh` and has no receipt protocol.

- [x] **Step 3: claim / ack / release flow を実装する**

`readInboxForPrompt` を `{ text, claims }` return に変え、`turn/start` success の後だけ全 claim を ack する。`turn/start` rejection、eligible lookup failure、空 prompt は acquired claim を release する。inline ではない wake prompt は claim/ack を行わない。

- [x] **Step 4: Node syntax と focused test を green にする**

Run:

```bash
node --check scripts/drivers/types/codex/codex-bridge.js
bats tests/test_codex_bridge.bats --filter 'inline-inbox'
```

Expected: syntax check と inline receipt integration test が PASS。

### Task 5: README、full verification、PR を完成させる

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-14-message-claim-ack-design.md`
- Modify: `docs/superpowers/plans/2026-08-14-message-claim-ack.md`

**Interfaces:**
- Consumes: Tasks 1–4 の behavior と test output。
- Produces: README だけで queue / status / limitation を利用できる説明と #37 専用 PR。

- [x] **Step 1: README の user-facing usage を更新する**

`message-status.sh` の command、queued/claimed/handed-off/unknown の表、ACK が task completion ではないこと、`history.sh` legend を README に追加する。古い「claim table is on the roadmap」FAQ は delivery claim と work-item ownership を区別した記述へ置換する。

- [x] **Step 2: full verification を実行する**

Run:

```bash
git diff --check
bats --count tests
bats tests
node --check scripts/drivers/types/codex/codex-bridge.js
```

Expected: whitespace error なし、test count と PASS count が一致、Node syntax PASS。

実測（2026-08-15T01:24:31+09:00）:

- `bats --count tests`: `1002`
- `bats tests`: `1002/1002 PASS`、exit `0`
- `git diff --check`: PASS
- `bash -n`（変更した Bash command / library）: PASS
- `node --check scripts/drivers/types/codex/codex-bridge.js`: PASS
- focused Bats: `test_claims.bats`、`test_messaging.bats`、`test_inbox.bats`、`test_watch.bats`、Codex bridge の inline-inbox / default wake case: PASS

macOS compatibility correction（2026-08-15T02:00:58+09:00）:

- GitHub macOS `/bin/bash 3.2` で `set -u` と空 `CLAIM_IDS` の EXIT trap が unbound variable になることを、system Bash を明示する regression test で RED にした。
- `release_claims` の空配列を length guard し、`test_inbox.bats`（8/8）と `test_dispatch.bats`（6/6）を GREEN にした。
- `bats --count tests`: `1003`、`bats tests`: `1003/1003 PASS`、exit `0`。
- GitHub Actions tests run `31820464249`: 15/15 SUCCESS（macOS / Ubuntu / Windows を含む）。

- [x] **Step 3: implementation notes を実測値で更新する**

本計画書に test count、focused tests、full suite、PR head SHA を追記する。spec に実装と異なる API 名や state があれば実装に合わせて更新する。

実績:

- PR 作成時 head: `ba610bacfa46ac2f3f27ba4523fd020a85d5a0bf`
- compatibility fix head: `cf70d290632fd213f95eb2dc36c59916fde0fd1d`
- PR: [#44](https://github.com/kappaseijin/agmsg/pull/44)
- spec は実装に合わせ、実在しない receipt API、status format、legacy `read_at`、ACK persistence failure、Codex rejection path を修正済み。

- [x] **Step 4: #37 だけを closes する PR を作成する**

Run:

```bash
git push -u origin issue/37-message-claim-ack
gh pr create --base main --head issue/37-message-claim-ack --title 'feat: add message claim/ack delivery receipts' --body 'Closes #37'
```

Expected: PR 本文は #37 だけを closes し、#18 は #37 merge 後に別操作で close する。

実績: [PR #44](https://github.com/kappaseijin/agmsg/pull/44) を `main` 向けに作成し、本文の closing reference は `Closes #37` のみ。#18 は open のまま維持する。
