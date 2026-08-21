---
type: ImplementationPlan
title: "Issue #103 roster schema v1 正規化コマンドの実装計画"
description: "legacy roster の root schemaVersion を、shared validation と atomic write を通して正規化する。"
status: in_progress
issue: "https://github.com/kappaseijin/agmsg/issues/103"
timestamp: "2026-08-21T17:45:08+09:00"
---

# Issue #103 roster schema v1 正規化コマンドの実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** legacy roster を直接編集せず、schemaVersion を integer `1` へ安全に正規化し、machine-readable roster gate を回復する。

**Architecture:** `scripts/roster-normalize.sh` は既存の team-name validator、per-team registry lock、roster contract validator、atomic writer を組み合わせる薄い CLI とする。候補は config の root `schemaVersion` だけを追加し、publish 前後の roster contract と metadata を同じ `team.sh` path で確認する。

**Tech Stack:** Bash、SQLite JSON1、Bats、既存 `scripts/lib/*.sh`。

**Spec:** `docs/decisions/2026-08-21T174508_issue-103-roster-normalizer.md`

## Global Constraints

- `fujibee/agmsg` は読まない・書かない。対象は `kappaseijin/agmsg` の origin だけ。
- config、DB、roster journal をテスト外で直接編集しない。config publish は `agmsg_write_atomic` だけを使う。
- `schemaVersion` 以外の意味上の field を変更しない。role / kind を name や runtime から推測しない。
- `--apply` は per-team lock 内で config を再読込・再検証してから publish する。
- schema failure は exit 2・stdout 空・stderr の先頭 `schema error:`、usage は exit 2、I/O / lock / publish failure は exit 1 とする。
- test は `$TEST_SKILL_DIR` の fixture だけを書き換え、実 HOME と installed skill を変更しない。
- README は利用者向け手順だけを自己完結で載せる。設計経緯と agent 運用は載せない。

---

### Task 1: 正規化失敗を再現する Bats contract を追加する

**Files:**

- Modify: `tests/test_team.bats:212`（`# --- team.sh ---` 節の直前に `# --- roster-normalize.sh ---` 節を追加）
- Test: `tests/test_team.bats`

**Interfaces:**

- Consumes: `$SCRIPTS`, `$TEST_SKILL_DIR`, `sqlite_mem`, `rf`（`tests/test_helper.bash` で提供）
- Produces: `scripts/roster-normalize.sh <team> --check|--apply` の stdout / stderr / exit contract

- [x] **Step 1: normalizable legacy fixture と Bats helper を追加する**

`tests/test_team.bats` に次の helper を追加する。member はすでに `kind`、`role`、`registrations` を持つので、失敗原因は root `schemaVersion` の欠落だけになる。

```bash
write_normalizable_legacy_roster() {
  local team="$1" cfg="$TEST_SKILL_DIR/teams/$team/config.json"
  mkdir -p "$(dirname "$cfg")"
  printf '%s' \
    '{"name":"'"$team"'","agents":{"alice":{"kind":"seat","role":"architect","registrations":[{"type":"codex","project":"/tmp/alice"}]}}}' \
    > "$cfg"
}

config_sha256() {
  shasum -a 256 "$1" | awk '{print $1}'
}
```

- [x] **Step 2: normalizable fixture が normalizer 前に schema error となる負の対照を書く**

```bash
@test "roster-normalize: known missing schemaVersion fails team json before normalization" {
  write_normalizable_legacy_roster legacy

  run bash "$SCRIPTS/team.sh" legacy --format json

  [ "$status" -eq 2 ]
  [[ "$output" == *"schema error: schemaVersion must be integer 1"* ]]
}
```

- [x] **Step 3: `--check` の read-only contract を RED にする**

```bash
@test "roster-normalize: check reports a ready candidate without changing config" {
  write_normalizable_legacy_roster legacy
  local cfg="$TEST_SKILL_DIR/teams/legacy/config.json"
  local before="$(config_sha256 "$cfg")"

  run bash "$SCRIPTS/roster-normalize.sh" legacy --check

  [ "$status" -eq 0 ]
  [ "$output" = '{"schemaVersion":1,"team":"legacy","status":"ready","changed":true}' ]
  [ "$(config_sha256 "$cfg")" = "$before" ]
}
```

- [x] **Step 4: `--apply`、no-op、schema failure、input failure の RED cases を追加する**

追加する case は以下の実行結果を一件ずつ assertion する。

```text
legacy --apply       => rc 0, applied JSON, team.sh JSON succeeds
legacy --apply again => rc 0, already_current JSON, config SHA-256 unchanged
schemaVersion "1"   => rc 2, stdout empty, config SHA-256 unchanged
missing member role  => rc 2, stdout empty, config SHA-256 unchanged
invalid team ../bad  => rc 2, config outside $TEST_SKILL_DIR is absent
```

- [x] **Step 5: RED を確認する**

Run: `bats tests/test_team.bats`

Expected: `roster-normalize.sh` が存在しないため、新規 normalizer cases は FAIL する。既存 team / join tests はその失敗理由を持たない。

- [ ] **Step 6: commit 用に test 差分だけを stage する**

```bash
git add tests/test_team.bats
git commit -m "test: define roster schema normalization contract"
```

PM がこの clone で Git 操作を代理する。programmer は commit 前に PM へ exact changed paths と `bats` result を渡す。

### Task 2: lock 内で検証して atomic publish する CLI を実装する

**Files:**

- Create: `scripts/roster-normalize.sh`
- Modify: `tests/test_team.bats`
- Test: `tests/test_team.bats`

**Interfaces:**

- Consumes: `agmsg_validate_team_name`, `agmsg_lock_acquire`, `agmsg_lock_release`, `agmsg_write_atomic`, `agmsg_roster_contract_team_json`
- Produces: `roster-normalize.sh <team> --check|--apply` with compact JSON stdout

- [x] **Step 1: parse exactly one mode and validate the team before resolving its path**

Implement this public shape. Unknown / missing mode must exit 2 before any config read or lock acquisition.

```bash
TEAM="${1:-}"
MODE="${2:-}"
[ "$#" -eq 2 ] || { echo 'Usage: roster-normalize.sh <team> --check|--apply' >&2; exit 2; }
case "$MODE" in --check|--apply) ;; *) echo "Error: unsupported mode: $MODE" >&2; exit 2;; esac
source "$SCRIPT_DIR/lib/validate.sh"
agmsg_validate_team_name "$TEAM" || exit 2
```

- [x] **Step 2: create one candidate builder with explicit version states**

The helper takes a config path and requested team, returns the candidate JSON on stdout, and never writes. It must reject invalid JSON, missing team config, name mismatch, a present non-integer `schemaVersion`, or any candidate that `agmsg_roster_contract_team_json` rejects.

```bash
case "$schema_state" in
  missing) candidate="$(sqlite3 :memory: "SELECT json_set(CAST(readfile('$cfg') AS TEXT), '\$.schemaVersion', 1);")"; changed=true ;;
  current) candidate="$(cat "$config")"; changed=false ;;
  *) echo 'schema error: schemaVersion must be integer 1' >&2; return 2 ;;
esac
```

Write the candidate to a private sibling temporary file only for shared validator input, delete that file on every return path, and call `agmsg_roster_contract_team_json "$candidate_path" "$TEAM" >/dev/null` before reporting success. Its schema error must remain the command's stderr and stdout must remain empty.

- [x] **Step 3: implement `--check` without acquiring a mutation lock or writing config**

`--check` calls the builder once and prints exactly:

```bash
printf '{"schemaVersion":1,"team":%s,"status":"ready","changed":%s}\n' \
  "$(agmsg_json_quote "$TEAM")" "$changed"
```

Use a local JSON quoting helper implemented with SQLite `json_quote`; do not splice unescaped team input into JSON.

- [x] **Step 4: implement `--apply` as one locked read-build-validate-publish transaction**

Acquire `agmsg_lock_acquire "$TEAM_DIR"`; install a `trap` that releases the lock exactly once on `EXIT`, `INT`, and `TERM`; rebuild the candidate after lock acquisition; then call `agmsg_write_atomic "$CONFIG" "$candidate"` only if `changed=true`.

```bash
if [ "$changed" = true ]; then
  agmsg_write_atomic "$CONFIG" "$candidate" || exit 1
  status=applied
else
  status=already_current
fi
printf '{"schemaVersion":1,"team":%s,"status":"%s","changed":%s}\n' \
  "$(agmsg_json_quote "$TEAM")" "$status" "$changed"
```

The command does not call `join.sh`, edit journal files, mutate remote state, or infer metadata.

- [x] **Step 5: run the focused test until all roster-normalize cases pass**

Run: `bats tests/test_team.bats`

Expected: all existing tests and every new normalizer case PASS.

- [x] **Step 6: run shell syntax and diff checks**

Run: `bash -n scripts/roster-normalize.sh && git diff --check`

Expected: exit 0.

- [ ] **Step 7: commit implementation and tests with explicit paths**

```bash
git add scripts/roster-normalize.sh tests/test_team.bats
git commit -m "feat: normalize roster schema v1 safely"
```

PM performs the Git operation in this clone after receiving the exact paths and validation output.

### Task 3: README だけで安全な migration 手順を利用可能にする

**Files:**

- Modify: `README.md:419-457`
- Test: `tests/test_team.bats`

**Interfaces:**

- Consumes: `scripts/roster-normalize.sh <team> --check|--apply`
- Produces: self-contained documentation of command, isolated validation, result, and recovery boundary

- [x] **Step 1: command reference に CLI を追加する**

`README.md` の scripts command list へ次の一行を追加する。

```text
~/.agents/skills/<cmd>/scripts/roster-normalize.sh <team> --check|--apply
```

- [x] **Step 2: Machine-readable team roster 節に safety sequence を追加する**

既存の `join.sh` / `team.sh --format json` の説明直後へ、scratch copy で `--apply` と `team.sh --format json` を成功させてから live `--apply` を一度実行する四行の command block を追加する。missing role / kind の場合は `join.sh --role --kind --force` を使い、config を手編集しないことを明記する。

- [x] **Step 3: documentation と functional path を確認する**

Run: `rg -n 'roster-normalize\.sh|--check|--apply|direct.*edit' README.md && bats tests/test_team.bats`

Expected: README に command と safety boundary があり、focused Bats は PASS。

- [ ] **Step 4: commit documentation with the feature paths only**

```bash
git add README.md scripts/roster-normalize.sh tests/test_team.bats
git commit -m "docs: explain safe roster normalization"
```

PM が Git 操作を代理する場合も、programmer は staged paths、HEAD、test output を PM に渡す。

### Task 4: isolated migration evidence と cross-vendor review を完了する

**Files:**

- Modify: none before review
- Test: `tests/test_team.bats`, full Bats suite

**Interfaces:**

- Consumes: scratch skill copy, candidate `team.sh --format json` result, feature head
- Produces: Issue #103 evidence packet and independent Claude review request

- [x] **Step 1: scratch copy の normalizer を実行する**

Create a disposable copy outside the installed skill, run `roster-normalize.sh agmsg --apply` in that copy, then run its `team.sh agmsg --format json`. Do not run `--apply` in the real install during this test.

- [x] **Step 2: capture the required negative control**

In the same scratch copy, remove only the root `schemaVersion` from a known-valid fixture, verify `team.sh --format json` returns exit 2, run normalizer `--apply`, then verify `team.sh --format json` returns exit 0. Record command, exit values, and feature HEAD without secret config contents.

- [ ] **Step 3: run regression proportionate to the change**

Run: `bats tests/test_team.bats && bash -n scripts/roster-normalize.sh && git diff --check`

Then run the repository's documented full Bats command exactly once. If it is narrowed or stopped, report the full suite as unverified rather than PASS.

- [ ] **Step 4: request independent formal review**

Send `agmsg_reviewer_claude` the exact feature HEAD, changed files, issue URL, focused/full test commands and results, scratch evidence, and the required negative-control result. Do not ask the producer to approve its own contract.

- [ ] **Step 5: report the evidence packet through PM**

Report `value / cutoff / source / command`: roster JSON success only after normalizer, affected config scope, feature head, test exits, negative control, reviewer state, and any unverified live-install migration. A queued agmsg message is not delivery confirmation or a completed migration.

## Spec coverage self-review

| Design requirement | Plan task |
| --- | --- |
| permanent check/apply CLI | Task 2 |
| schemaVersion-only and no inference | Tasks 1–2 |
| lock, re-read, shared validator, atomic writer | Task 2 |
| scratch-before-live procedure | Tasks 3–4 |
| stdout/stderr/exit contract and negative control | Tasks 1–2 and 4 |
| README-only user workflow | Task 3 |
| cross-vendor review and evidence separation | Task 4 |

No placeholders remain; every implementation interface, file, command, and expected result is explicit.
