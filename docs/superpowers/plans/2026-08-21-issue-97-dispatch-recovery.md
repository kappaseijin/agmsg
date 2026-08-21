---
type: Plan
title: "Issue #97 dispatch timeout recovery 実装計画"
description: "期限切れ dispatch の abandon・epoch replace・監査修正・SQLite migration を test-first で実装する。"
tags:
  - agmsg
  - team-work
  - dispatch-recovery
  - issue-97
timestamp: "2026-08-21T21:08:25+09:00"
---

# Issue #97 dispatch timeout recovery 実装計画

> **実行者向け:** Issue #97 の正式設計契約に従い、この worktree で test-first に実装する。#98 未完了のため production dispatch は実行せず、G4 table/CLI は含めない。

**Goal:** 期限切れの `dispatching`/`claimed` row を manager の明示的な `dispatch-abandon` で安全に解消し、abandoned row だけを新 epoch へ replace できるようにする。古い epoch の ACK と active claim 競合は fail-closed にし、audit が `local_state_stale` を誤分類しないようにする。

**Architecture:** `team_work_dispatch_current` は current row を保持し、既存の update trigger が `team_work_dispatch_revisions` へ append-only snapshot を残す。schema migration は `agmsg_storage_ensure_initialized` から transactional rebuild と copy validation を呼び出す。reconciler は abandon/replace/ACK を同一 SQLite transaction の guarded mutation として実行し、audit は abandoned を allocation から除外する。

**Tech Stack:** Bash wrapper、Node.js standard library、SQLite CLI、Bats、既存の read-only GitHub fixture。

**Spec:** `docs/decisions/2026-08-21T203441_issue-97-102-dispatch-recovery-g4.md` の Issue #97 節。

## Global Constraints

- 実装対象は Issue #97 のみ。G4 table/CLI、GitHub mutation、message send、seat spawn、herdr 操作は追加しない。
- manager は roster 上の exact `kind: "seat"`, `role: "manager"` に限る。item/pack/owner/epoch/expiry を同一 transaction で照合する。
- active な `team_work_current` claim がある場合は abandon/replace ともに拒否する。
- dispatch ledger は削除せず、`abandoned` を含む revision chain を保持する。old epoch ACK は no mutation とする。
- test は隔離 SQLite を使い、既知の positive/negative control と mutation KILLED の証拠を残す。production dispatch は実行しない。
- README だけで command、manager authority、abandoned の意味、old ACK rejection、migration、Phase 3 prerequisite が分かるようにする。

### Task 1: schema migration と append-only abandoned state

**Files:** `scripts/internal/init-db.sh`, `scripts/internal/migrate-team-work-dispatch.sh`, `scripts/lib/storage.sh`

- [x] 旧 schema を用意した隔離 DB の migration、copy 失敗時 rollback、二回目の idempotence、revision chain preservation の failing tests を追加する。
- [x] current/revisions に `abandoned` と `recovery_evidence` を追加し、snapshot に evidence を含める。
- [x] legacy table を transaction 内で検証・rebuild・copy し、JSON/state/revision chain の不整合は commit 前に fail-closed する。
- [x] fresh DB は新 schema を直接作り、ensure initialization では migration を安全に no-op/適用する。

### Task 2: audit と dispatch candidate の recovery semantics

**Files:** `scripts/lib/team-work-audit.js`, `scripts/lib/team-work-reconciler.js`

- [x] expired dispatch + active G2 claim が `local_state_stale` にならず claim-preferred になる negative control を追加する。
- [x] valid expired abandoned row は terminal evidence として返し、reconciler の ready dispatch candidate に含めない。
- [x] invalid state/digest/future-dated abandoned row は `local_state_stale` の known-present negative control とする。
- [x] `readLocalRows` が `recoveryEvidence` を読み、dispatch-free claim/audit control は従来どおり動かす。

### Task 3: guarded dispatch-abandon、replace、old ACK rejection

**Files:** `scripts/team-work.sh`, `scripts/lib/team-work-reconciler.js`

- [x] `dispatch-abandon <team> <pack> <work-item-id> <manager-seat> <lease-epoch> <evidence>` の usage/schema/manager/epoch/expiry/active-claim guards を failing tests で固定する。
- [x] 成功時は current row を `abandoned` に更新し、`dispatch-abandon` action と evidence を append-only revision に記録する。
- [x] `dispatch` は expired `abandoned` row だけを new epoch で replace し、expired `dispatching`/`claimed` や unexpired abandoned は拒否する。
- [x] old epoch ACK は `dispatch_epoch_invalid` で no mutation、new epoch ACK は通常の claim path を維持する。

### Task 4: README、focused verification、PR handoff

**Files:** `README.md`, focused Bats tests, plan

- [x] README に新 command、JSON output、abandoned は allocation でないこと、old ACK rejection、auto migration、#98/Phase 3 prerequisite を追記する。
- [x] focused Bats、`node --check`、`shellcheck`、`git diff --check` を実行し、negative controls と mutation KILLED を記録する。
- [ ] diff と exact head を確認して `kappaseijin4codex` で push/PR を作成し、agmsg で architect/PM に報告する。PR は formal reviewer の判定前に merge しない。

## Verification evidence

- 2026-08-21T21:33:06+09:00: `bats --print-output-on-failure tests/test_team_work_reconciler.bats tests/test_team_work_dispatch_migration.bats tests/test_team_work_audit.bats tests/test_team_work_state.bats` — 50 tests, 0 failures.
- 2026-08-21T21:33:06+09:00: `bash -n ...`, `node --check ...`, `shellcheck -s bash -e SC1091 ...`, `git diff --check` — exit 0.
- 2026-08-21: expiry predicate reverted to the pre-fix condition — active-claim audit test failed; old epoch comparison removed — stale ACK test failed with `claim_conflict`; both mutations restored.

## Verification matrix

| Claim | Positive control | Negative control |
| --- | --- | --- |
| abandon guard | exact manager/epoch/expired/evidence で E1→abandoned | wrong manager, wrong epoch, unexpired, active claim, empty evidence |
| replace | expired abandoned E1 から E2 dispatch | expired dispatching/claimed、unexpired abandoned |
| ACK safety | E2 ACK が claimed + G2 claim | E1 ACK は state/rows/revisions 全不変 |
| audit | expired dispatch + valid active claim は stale なし | invalid/future abandoned は stale、dispatch-free claim は従来判定 |
| migration | legacy copy + rerun preserves chain | malformed JSON/chain/copy failure は rollback |
