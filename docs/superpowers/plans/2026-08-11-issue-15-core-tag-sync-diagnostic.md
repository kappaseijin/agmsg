---
type: ImplementationPlan
title: Issue #15 agmsg-core タグ同期忘れ診断の実装計画
description: origin に固定タグが無い状態を事前検出し、復旧操作を示す実装計画。
tags:
  - agmsg
  - app
  - ci
  - issue-15
timestamp: "2026-08-11T18:56:40+09:00"
---

# Issue #15 Core Tag Sync Diagnostic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `AGMSG_CORE_REF` のタグが `origin` に無いとき、bundle を始める前に同期忘れと復旧操作を示して失敗させる。

**Architecture:** `app/scripts/bundle-core.sh` は既存の fetch の前に `origin` の正確なタグ ref を照合する。タグなしを表す Git の exit 2 だけを利用者が修復できる同期忘れとして独自診断し、認証・通信などの別の Git エラーはその終了状態のまま返す。Bats はタグのない local bare repository を `origin` にして、この分岐を実行する。

**Tech Stack:** Bash、Git、Bats、Markdown。

## Global Constraints

- `origin` は `kappaseijin/agmsg` のみを指す。第三者リポジトリへ書き込まない。
- 固定参照はタグのまま維持する。commit SHA 化や CI skip 条件の変更をしない。
- 不在タグの Git exit 2 だけを同期忘れとして診断し、その他の fetch 前エラーを誤診しない。
- README は pin の場所、同期順序、push 先を自己完結で示す。

---

### Task 1: タグ不在を再現する Bats 回帰テスト

**Files:**
- Create: `tests/test_bundle_core.bats`
- Test: `tests/test_bundle_core.bats`

**Interfaces:**
- Consumes: `app/scripts/bundle-core.sh`、`app/AGMSG_CORE_REF`、Git の `ls-remote --exit-code --refs`。
- Produces: tag が無い `origin` での stderr 診断を固定する回帰テスト。

- [ ] **Step 1: 壊れる変更を定義する**

`bundle-core.sh` から tag 存在照合または `git push origin <tag>` を示す診断を削除すると、このテストの出力 assertion が失敗する。期待値は script の helper を使わず、`v9.9.9` という fixture 値から手で導く。

- [ ] **Step 2: 失敗テストを書く**

```bash
#!/usr/bin/env bats

setup() {
  BUNDLE_ROOT="$BATS_TEST_TMPDIR/bundle-root"
  BUNDLE_ORIGIN="$BATS_TEST_TMPDIR/origin.git"
  BUNDLE_REF="v9.9.9"

  mkdir -p "$BUNDLE_ROOT/app/scripts"
  cp "$BATS_TEST_DIRNAME/../app/scripts/bundle-core.sh" "$BUNDLE_ROOT/app/scripts/"
  printf '%s\n' "$BUNDLE_REF" > "$BUNDLE_ROOT/app/AGMSG_CORE_REF"
  git init --bare -q "$BUNDLE_ORIGIN"
  git -C "$BUNDLE_ROOT" init -q
  git -C "$BUNDLE_ROOT" remote add origin "$BUNDLE_ORIGIN"
  BUNDLE="$BUNDLE_ROOT/app/scripts/bundle-core.sh"
}

@test "bundle-core: says how to synchronize a pinned tag absent from origin" {
  run bash "$BUNDLE"

  [ "$status" -ne 0 ]
  [[ "$output" == *"origin is missing pinned tag '$BUNDLE_REF' required by app/AGMSG_CORE_REF"* ]]
  [[ "$output" == *"git push origin $BUNDLE_REF"* ]]
}
```

併せて、fixture 内で有効な tag と必須ファイルを bare `origin` へ公開する
`publish_pinned_core` helper を置き、次の 2 test を追加する。

```bash
@test "bundle-core: does not call a broken origin a missing tag" {
  git -C "$BUNDLE_ROOT" remote set-url origin "$BATS_TEST_TMPDIR/nonexistent.git"
  run bash "$BUNDLE"

  [ "$status" -ne 0 ]
  [[ "$output" != *"origin is missing pinned tag"* ]]
  [[ "$output" != *"git push origin"* ]]
}

@test "bundle-core: bundles a pinned tag present in origin" {
  publish_pinned_core
  run bash "$BUNDLE"

  [ "$status" -eq 0 ]
  [[ "$output" != *"origin is missing pinned tag"* ]]
  [ -f "$BUNDLE_ROOT/app/src-tauri/resources/agmsg-core/scripts/api.sh" ]
}
```

- [ ] **Step 3: RED を確認する**

Run: `bats --print-output-on-failure tests/test_bundle_core.bats`

Expected: 初回の tagless test は FAIL。既存 script は `git fetch` の `fatal: couldn't find remote ref` を返し、同期忘れと push 操作を示す assertion を満たさない。追加 2 test は現行実装で PASS し、後続の変異試験で有効性を確認する。

- [ ] **Step 4: テストをコミットする**

```bash
git add tests/test_bundle_core.bats
git commit -m "test: cover missing bundled core tag"
```

### Task 2: `origin` の固定タグを事前照合する

**Files:**
- Modify: `app/scripts/bundle-core.sh:28-35`
- Test: `tests/test_bundle_core.bats`

**Interfaces:**
- Consumes: `REF`（空白を除去した `AGMSG_CORE_REF` の値）と `origin`。
- Produces: `refs/tags/$REF` が不存在なら exit 1 と同期手順、存在なら既存の fetch・archive 経路。

- [ ] **Step 1: 最小実装を書く**

`cd "$ROOT_DIR"` の直後、既存の `git fetch origin tag "$REF" --no-tags` の前に次を追加する。

```bash
if git ls-remote --exit-code --refs origin "refs/tags/$REF" > /dev/null; then
  :
else
  git_status=$?
  if [ "$git_status" -eq 2 ]; then
    echo "bundle-core: origin is missing pinned tag '$REF' required by app/AGMSG_CORE_REF." >&2
    echo "bundle-core: synchronize it to this fork, then retry:" >&2
    echo "  git push origin $REF" >&2
    exit 1
  fi
  exit "$git_status"
fi
```

既存の fetch とその後の archive・必須パス検査は変更しない。

- [ ] **Step 2: GREEN を確認する**

Run: `bats --print-output-on-failure tests/test_bundle_core.bats`

Expected: PASS。tagless local `origin` で exit 1 になり、tag 名・`AGMSG_CORE_REF`・`git push origin v9.9.9` を含む診断を返す。

- [ ] **Step 3: 負のコントロールを確認する**

事前照合 block を一時的に除外して同じ test を実行する。

Run: `bats --print-output-on-failure tests/test_bundle_core.bats`

Expected: FAIL（`fatal: couldn't find remote ref` だけで、同期診断 assertion を満たさない）。block を復元して同じ command を再実行し、PASS を確認する。さらに `[ "$git_status" -eq 2 ]` を `-ne 0` へ変えると壊れた origin の test が FAIL し、tag があっても診断する変異では positive test が FAIL する。各変異を `KILLED` として記録する。

- [ ] **Step 4: shell 構文と対象テストを確認する**

Run: `bash -n app/scripts/bundle-core.sh && bats --print-output-on-failure tests/test_bundle_core.bats`

Expected: exit 0、Bats の全 test が PASS。

- [ ] **Step 5: 実装をコミットする**

```bash
git add app/scripts/bundle-core.sh tests/test_bundle_core.bats
git commit -m "fix(app): diagnose missing core tag"
```

### Task 3: pin 更新の同期手順を README に書く

**Files:**
- Modify: `app/README.md: Releasing` の直後

**Interfaces:**
- Consumes: `app/AGMSG_CORE_REF` と `origin` のタグ。
- Produces: pin を更新して CI を走らせる前に実行する、読み取り元からの tag fetch と自フォーク `origin` への tag push 手順。

- [ ] **Step 1: リリース節へ手順を追加する**

`## Releasing` の直後に `### Updating the bundled agmsg-core pin` 節を追加し、次を含める。

````markdown
The app bundles the exact tag in `app/AGMSG_CORE_REF`; CI intentionally reads
that tag from this fork's `origin`, never at build time from another repository.
Before committing a new pin, synchronize its released tag to `origin`:

```sh
tag=vX.Y.Z
git fetch https://github.com/fujibee/agmsg.git tag "$tag" --no-tags
git push origin "$tag"  # origin must be your fork, never fujibee/agmsg
printf '%s\n' "$tag" > app/AGMSG_CORE_REF
```
````

- [ ] **Step 2: README の差分を確認する**

Run: `git diff --check && git diff -- app/README.md`

Expected: whitespace error が無く、pin の場所・読み取り元・`origin` への push・書き込み禁止の対象が明記されている。

- [ ] **Step 3: 文書をコミットする**

```bash
git add app/README.md
git commit -m "docs(app): explain core tag synchronization"
```

### Task 4: 完全検証と CI への引き渡し

**Files:**
- Verify: `app/scripts/bundle-core.sh`
- Verify: `tests/test_bundle_core.bats`
- Verify: `app/README.md`

**Interfaces:**
- Consumes: local Bats suite、同期済み `origin` tag、GitHub `tests` workflow。
- Produces: review 可能な branch と、main run を確認するための PR。

- [ ] **Step 1: ローカル完全検証を実行する**

Run: `bash -n app/scripts/bundle-core.sh && bats --print-output-on-failure tests/*.bats`

Expected: exit 0。全 Bats test が PASS。

- [ ] **Step 2: origin の必要タグを再照合する**

Run: `git ls-remote --tags origin 'refs/tags/v1.1.12'`

Expected: `6248bb054f49d074e1aa6598fddae33b6c87932b` と `refs/tags/v1.1.12` が 1 行で出る。

- [ ] **Step 3: PR を作成して Claude reviewer へ依頼する**

Run: `GH_CONFIG_DIR=~/.config/gh-4codex gh pr create --repo kappaseijin/agmsg --base main --head fix/issue-15-core-tag-diagnostic --title "fix(app): diagnose missing core tag" --body $'Closes #15\n\n- Synchronize v1.1.12 to the fork.\n- Diagnose an origin tag missing from AGMSG_CORE_REF before fetch.\n- Add a tagless-origin Bats regression test and pin update instructions.'`

Expected: PR URL を得る。`agmsg_reviewer_claude` へ PR URL、検証 command、`KILLED` の負のコントロールを送る。

- [ ] **Step 4: main の workflow 成功を確認する**

PR の Claude review approve と全 required check 成功後、approve された head SHA を `--match-head-commit` に渡して squash merge する。merge commit の main run について、PR check ではなく `main` の `tests` workflow が `success` であることを確認する。
