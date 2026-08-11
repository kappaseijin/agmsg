---
type: ImplementationPlan
title: Issue #4 BSD awk 改行互換性修正
description: spawn options の role overlay が macOS 標準 awk でもトークンを生成できるようにする実装計画。
tags:
  - agmsg
  - bash
  - bats
  - bsd-awk
  - issue-4
timestamp: "2026-08-11T12:12:06+09:00"
---

# Issue #4 BSD awk 改行互換性修正 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** macOS 標準 BSD awk で、複数キーの role overlay を含む spawn options を正しい argv トークン列へ変換する。

**Architecture:** `agmsg_spawn_options_section_tokens` は `suppressed_keys` を改行区切りのレコード列として awk の標準入力へ渡し、YAML 本体を第 2 入力ファイルとして解析する。`section` は単一値のまま `-v` で渡し、改行を含み得る key list は `-v` から完全に除外する。

**Tech Stack:** Bash、POSIX awk（macOS `/usr/bin/awk` を含む）、Bats。

## Global Constraints

- `fujibee/agmsg` にはアクセスしない。対象は `kappaseijin/agmsg` の専用クローンのみ。
- gawk を前提にしない。テストでは macOS 標準の `/usr/bin/awk` を優先する。
- spawn options の既存 YAML 形式、トークン順序、空白を含む値の単一 argv-token 性を変えない。
- README の設定スキーマ・利用方法は変わらないため、README の文言変更は行わない。

---

### Task 1: BSD awk を使う回帰テストを追加する

**Files:**
- Modify: `tests/test_spawn_options.bats:198-216`
- Test: `tests/test_spawn_options.bats`

**Interfaces:**
- Consumes: `agmsg_spawn_options_tokens <type> <role>`
- Produces: role overlay が base の複数キーを抑止した、改行区切りの argv token 列

- [x] **Step 1: Write the failing test**

```bash
@test "spawn_options_tokens: BSD awk handles multiline suppression keys with spaces and metacharacters" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
codex:
  --keep: value with spaces ; $HOME * [meta]
  --omit flag: base
  --also$omit: base
codex@architect:
  --omit flag: false
  --also$omit: false
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  local original_path="$PATH"
  PATH="/usr/bin:$PATH"
  run agmsg_spawn_options_tokens codex architect
  PATH="$original_path"

  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "--keep" ]
  [ "${lines[1]}" = "value with spaces ; \$HOME * [meta]" ]
  [ "${#lines[@]}" -eq 2 ]
}
```

このテストを壊す production change は、複数行の `suppressed_keys` を `awk -v` に渡すこと。修正前は BSD awk が `newline in string` で終了するため、期待する 2 token を返せず FAIL する。

- [x] **Step 2: Run test to verify it fails**

Run: `bats tests/test_spawn_options.bats`

Expected: 追加テストが `awk: newline in string` を出して FAIL（KILLED）。

### Task 2: 抑止キー列を awk 標準入力へ移す

**Files:**
- Modify: `scripts/lib/spawn-options.sh:38-64`
- Test: `tests/test_spawn_options.bats`

**Interfaces:**
- Consumes: `section` と、改行区切り `suppressed_keys`
- Produces: base section から overlay に含まれる key を除いた token 列

- [x] **Step 1: Write minimal implementation**

```bash
printf '%s\n' "$suppressed_keys" |
  awk -v section="$section" '
    NR == FNR {
      if ($0 != "") suppressed[$0] = 1
      next
    }
    # existing YAML section parsing and token emission
  ' - "$file"
```

`NR == FNR` で stdin の抑止キーのみを先に読み、YAML ファイルの解析時に `key in suppressed` を判定する。既存の section 解析と出力順序は変えない。

- [x] **Step 2: Run focused test to verify it passes**

Run: `bats tests/test_spawn_options.bats`

Expected: 全テスト PASS。追加テストは `/usr/bin/awk` で 2 token を返す。

### Task 3: 既存の spawn 結合経路と静的検査を確認する

**Files:**
- Verify: `tests/test_spawn.bats`
- Verify: `scripts/lib/spawn-options.sh`

**Interfaces:**
- Consumes: spawn-options token generator
- Produces: spawn.sh が既存の base/role token ordering を保つこと

- [x] **Step 1: Run affected integration tests**

Run: `bats tests/test_spawn_options.bats tests/test_spawn.bats`

Expected: PASS。role overlay、false override、spawn CLI injection の既存仕様を維持する。

- [x] **Step 2: Run shell static analysis**

Run: `shellcheck scripts/lib/spawn-options.sh`

Expected: exit 0。

- [x] **Step 3: Record verification evidence for review**

記録した事実:

- 追加テストは、修正前の実装で macOS `/usr/bin/awk` の `newline in string` により FAIL した（KILLED）。
- 修正後に旧 `-v suppressed_keys` 実装を一時的に戻しても同じ追加テストが FAIL した（KILLED）。直後に修正版へ復元した。
- 修正版では focused test、`bats tests/test_spawn_options.bats tests/test_spawn.bats`（99 tests）、ShellCheck、`git diff --check` が PASS した。
- GitHub Actions の macOS shard 2/4・3/4 は PR の CI 結果で確認する。
