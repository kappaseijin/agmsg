---
type: ImplementationPlan
title: Issue #35 upstream main 取り込み実施計画
description: Issue #32 の隔離検証を実作業へ適用し、upstream/main を origin/main へ安全に取り込む。
tags:
  - agmsg
  - git
  - upstream
  - issue-35
timestamp: "2026-08-14T21:57:38+09:00"
---

# Issue #35 Upstream Main Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan inline. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 現在の `upstream/main` を、隔離した `origin/main` 起点の branch へ取り込み、検証済みの専用PRとして main へ反映する。

**Architecture:** 本体作業ツリーを一切変更せず、一時cloneの `issue/35-upstream-main-sync` だけで upstream を fetch・merge する。実際の競合に限り `git checkout --theirs -- <path>` で upstream 側を選び、Git が生成したマージ結果と本計画書だけをPRへ含める。

**Tech Stack:** Git、Bash、Bats、GitHub Issues / Pull Requests。

**Spec:** GitHub Issue #35。事前検証は #32 のコメントを参照する。

## Global Constraints

- 書き込み先は `kappaseijin/agmsg` の `origin` だけであり、`fujibee/agmsg` へ push しない。
- 本体 `/Users/kappa/Dropbox/data/dev/agmsg` は変更しない。
- `upstream/main` と `origin/main` の両方が進んでいるため、fast-forward 前提にしない。
- 競合ファイルだけは upstream の内容を採用し、非競合の origin 固有変更を消さない。
- 現在の一時的な単一エージェント方針に従い、別LLMレビューは行わない。

---

### Task 1: 隔離cloneの基準とマージ対象を固定する

**Files:**
- Create: `docs/superpowers/plans/2026-08-14-upstream-main-sync.md`
- Verify: `origin/main`、`upstream/main`

**Interfaces:**
- Consumes: `origin` (`kappaseijin/agmsg`) と `upstream` (`fujibee/agmsg`) の `main` ref。
- Produces: `issue/35-upstream-main-sync` と、PRに記録する両ref SHA。

- [x] **Step 1: origin と upstream を最新化する**

Run:

```bash
git fetch --no-tags origin main
git fetch --no-tags upstream main
git rev-parse origin/main
git rev-parse upstream/main
```

Expected: 2つのSHAを取得し、以後のマージ元・先を確定できる。

- [x] **Step 2: 分岐状態を確認する**

Run:

```bash
git merge-base --is-ancestor upstream/main HEAD || true
git merge-base --is-ancestor HEAD upstream/main || true
git log --oneline --left-right --cherry-pick upstream/main...HEAD
```

Expected: fast-forward可否と、両系統の固有commitを記録できる。

### Task 2: upstream/main をマージし、競合時だけ upstream を採用する

**Files:**
- Modify: Gitが生成する merge result のファイル群
- Modify: `docs/superpowers/plans/2026-08-14-upstream-main-sync.md`

**Interfaces:**
- Consumes: `upstream/main`。
- Produces: `upstream/main` と開始時 `origin/main` を両方の祖先に持つ merge commit。

- [x] **Step 1: 通常マージを実行する**

Run:

```bash
git merge --no-ff --no-edit upstream/main
```

Expected: 無競合ならmerge commitを作成する。競合時は未解決ファイル一覧を取得して次の手順へ進む。

- [x] **Step 2: 実際に競合したファイルだけを upstream 優先で解決する（競合なしのため不要）**

Run:

```bash
git diff --name-only --diff-filter=U
git checkout --theirs -- <conflicted-path>
git add -- <conflicted-path>
git commit --no-edit
```

Expected: `git diff --name-only --diff-filter=U` が空になり、各競合ファイルは upstream の内容と一致する。競合が無い場合はこの手順を実行しない。

- [x] **Step 3: merge result を記録する**

本計画書の完了記録へ、開始時の2 SHA、merge commit、競合の有無、競合があった場合のファイル名と upstream 優先解決を追記する。

## 実施結果（マージ時点）

- 開始時 `origin/main`: `e6e5d7c20d90e729192ac3ea5ba2e82a8935903c`
- 取り込み元 `upstream/main`: `f271cd45242ce1cca4b94f69084e6217e0eb6b48`
- 計画書コミット: `473bd382d9fb14c3e4d93a26408291fbc75f0d20`
- マージコミット: `1ddb8d23e722ebe7f42b43b5283eab0e056e45a0`
- 競合: なし。`tests/test_codex_bridge_launcher.bats` と `tests/test_resolve_project.bats` はGitにより自動マージされ、未解決ファイルは残らなかった。
- マージ前ベースライン: `bats --count tests/` は943件、`bats tests/` は943/943成功。

### Task 3: 完全検証、専用PR、取り込み確認

**Files:**
- Verify: merge result の全変更
- Verify: `docs/superpowers/plans/2026-08-14-upstream-main-sync.md`

**Interfaces:**
- Consumes: merge commit と Bats suite。
- Produces: #35だけを閉じるPRと、mainへの取り込み証跡。

- [x] **Step 1: 差分と祖先関係を検証する**

Run:

```bash
git diff --check origin/main...HEAD
git merge-base --is-ancestor origin/main HEAD
git merge-base --is-ancestor upstream/main HEAD
```

Expected: whitespace errorなし、双方のmainが merge result の祖先。

- [x] **Step 2: Bats全件を実行する**

Run:

```bash
bats --count tests/
bats tests/
```

Expected: count と実行成功件数が一致し、失敗なし。

## 検証結果（PR作成前）

- `git diff --check origin/main...HEAD`: 成功（whitespace errorなし）。
- `git merge-base --is-ancestor origin/main HEAD`: 成功。
- `git merge-base --is-ancestor upstream/main HEAD`: 成功。
- `bats --count tests/`: 990件。
- `bats tests/`: 990/990成功。

- [ ] **Step 3: 1 Issue / 1 PR を作成してmainへ取り込む**

Run:

```bash
git push -u origin issue/35-upstream-main-sync
gh pr create --base main --head issue/35-upstream-main-sync --title "chore: merge upstream main" --body "Closes #35"
```

Expected: PR本文に開始SHA、upstream SHA、merge commit、競合有無、Bats結果を記録し、#35だけを閉じる。検証済みhead SHAのままready化・マージ後、Issue #35がclosedになる。
