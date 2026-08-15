---
type: Plan
title: "Issue #53: role-overlay capability 公開の実装計画"
description: "consumer が既存の fail-closed role-overlay 実装を安全に検出できるようにする test-first の手順。"
tags:
  - agmsg
  - spawn-options
  - role-overlay
  - compatibility
  - issue-53
timestamp: "2026-08-15T19:00:46+09:00"
---

# Issue #53: role-overlay capability 公開の実装計画

> **実行者向け:** ユーザー指定によりサブエージェントは起動しない。この worktree で test-first に実行し、PR は Issue #53 だけを closes する。

**Goal:** `agmsg.require-role-overlay` を実際に消費する agmsg runtime を、consumer が公開 capability 関数で安全に検出できるようにする。

**Architecture:** `scripts/lib/spawn-options.sh` に policy query として `agmsg_spawn_options_requires_role_overlay <type>` を追加する。consumer は関数定義の存在を capability marker として利用でき、agmsg 自身と Bats は返り値で type ごとの opt-in 状態を判定できる。既存の `agmsg_spawn_options_validate_required_role_overlay` と `spawn.sh` の pre-join validation 経路は変更しない。

**Tech Stack:** Bash、awk、Bats。

**Spec:** `docs/decisions/2026-08-11T124253_required-role-overlay-and-codex-profile-validation.md`

## Global Constraints

- PR は Issue #53 だけを closes し、追跡 Issue #52 は production update と停滞棚卸しが完了するまで閉じない。
- metadata の `agmsg.require-role-overlay` は CLI argv に混入させない。
- `true` だけを required-policy とみなし、`false`、設定不在、type key 不在、不正値は capability query で成功扱いにしない。
- validator は不正値を従来どおり診断付きで fail-closed にする。
- agent spawn、team registration、GitHub write、実 terminal 起動をテストから行わない。
- README の利用手順は既に schema と fail-closed 動作を記載しているため、ユーザー向け操作を変えないこの internal compatibility contract は README へ追加しない。

---

### Task 1: public capability query の契約を test-first で固定する

**Files:**

- Modify: `tests/test_spawn_options.bats:278-435`
- Test: `tests/test_spawn_options.bats`

**Interfaces:**

- Consumes: `agmsg_spawn_options_section_exists(section)` と `agmsg_spawn_options_section_value(section, key)`。
- Produces: `agmsg_spawn_options_requires_role_overlay <type>`。指定 type の metadata value が厳密に `true` のときだけ exit 0、それ以外は non-zero を返す。

- [x] **Step 1: Write the failing public-contract tests**

`tests/test_spawn_options.bats` の required role overlay section に次の Bats cases を追加する。

```bash
@test "required_role_overlay capability: consumer can detect the literal public definition" {
  run grep -E '^[[:space:]]*agmsg_spawn_options_requires_role_overlay[[:space:]]*\(\)[[:space:]]*\{' \
    "$SCRIPTS/lib/spawn-options.sh"
  [ "$status" -eq 0 ]
}

@test "required_role_overlay capability: true reports the public contract" {
  cat > "$TEST_SKILL_DIR/spawn_options.yaml" <<'YAML'
agmsg.require-role-overlay:
  claude-code: true
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$TEST_SKILL_DIR/spawn_options.yaml"

  run agmsg_spawn_options_requires_role_overlay claude-code
  [ "$status" -eq 0 ]
}

@test "required_role_overlay capability: only true reports enabled policy" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"

  cat > "$file" <<'YAML'
agmsg.require-role-overlay:
  claude-code: false
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"
  run agmsg_spawn_options_requires_role_overlay claude-code
  [ "$status" -eq 1 ]

  cat > "$file" <<'YAML'
agmsg.require-role-overlay:
  codex: true
YAML
  run agmsg_spawn_options_requires_role_overlay claude-code
  [ "$status" -eq 1 ]

  cat > "$file" <<'YAML'
agmsg.require-role-overlay:
  claude-code: yes
YAML
  run agmsg_spawn_options_requires_role_overlay claude-code
  [ "$status" -eq 1 ]

  run agmsg_spawn_options_requires_role_overlay
  [ "$status" -eq 2 ]
}
```

The first case pins the exact literal grep contract used by the consumer. The second case proves its semantic query. Removing or renaming the public function makes both fail. The third case prevents a false-positive capability claim from optional or malformed policy.

- [x] **Step 2: Run the focused RED test**

Run:

```bash
bats tests/test_spawn_options.bats
```

Expected: FAIL because `agmsg_spawn_options_requires_role_overlay` is not defined in the current source.

- [x] **Step 3: Record the expected failure**

Confirm the failure names the missing public function rather than an unrelated fixture or Bats setup error. Do not edit `scripts/lib/spawn-options.sh` before this failure is observed.

### Task 2: add the minimal capability query and prove green compatibility

**Files:**

- Modify: `scripts/lib/spawn-options.sh:114-143`
- Modify: `tests/test_spawn_options.bats:278-435`
- Modify: `docs/superpowers/plans/2026-08-15-role-overlay-capability-contract.md`

**Interfaces:**

- Produces: `agmsg_spawn_options_requires_role_overlay <type>` in `scripts/lib/spawn-options.sh`.
- Preserves: `agmsg_spawn_options_validate_required_role_overlay <type> <role>` and `agmsg_spawn_options_tokens <type> [role]` behavior.
- Consumer contract: a compatible installed skill contains the literal Bash function definition, allowing `herdr-agent-monitor/scripts/install-role-overlay.sh` to identify a runtime that consumes this metadata before argv construction.

- [x] **Step 1: Implement only the query helper**

Add the function immediately after `agmsg_spawn_options_section_value`:

```bash
agmsg_spawn_options_requires_role_overlay() {
  local type="${1:-}" policy
  [ "$#" -eq 1 ] || return 2

  if ! agmsg_spawn_options_section_exists 'agmsg.require-role-overlay' \
      >/dev/null 2>&1; then
    return 1
  fi
  if ! policy="$(agmsg_spawn_options_section_value \
      'agmsg.require-role-overlay' "$type" 2>/dev/null)"; then
    return 1
  fi
  [ "$policy" = true ]
}
```

Do not change token generation. Keep the validator's explicit `true` / `false` / invalid-value branch so it continues to produce its existing fail-closed diagnostic.

- [x] **Step 2: Run focused GREEN and negative control**

Run:

```bash
bats tests/test_spawn_options.bats
```

Expected: PASS. The focused suite must include the false/missing/malformed negative paths and the pre-existing argv-leak assertion.

Then temporarily remove the helper only in a disposable copy of `scripts/lib/spawn-options.sh`, run the new capability test against that copy, and confirm it fails. Do not leave the mutation in the worktree.

- [x] **Step 3: Run integration and full regression verification**

Run:

```bash
bats tests/test_spawn_options.bats tests/test_spawn.bats
bats tests/
shellcheck -s bash -e SC1091 scripts/lib/spawn-options.sh
git diff --check
```

Expected: every command exits 0. The complete suite proves the existing pre-join fail-closed path still runs and metadata is absent from generated argv. `scripts/spawn.sh` is intentionally excluded from shellcheck here: it is unchanged from origin/main and has pre-existing SC2034 and SC2016 findings.

Execution evidence: `bats tests/test_spawn_options.bats tests/test_spawn.bats` passed 114/114; post-change `bats tests/` passed 1,069/1,069 with exit 0; `shellcheck -s bash -e SC1091 scripts/lib/spawn-options.sh` and `git diff --check` both exited 0. A full shellcheck including unchanged `scripts/spawn.sh` still reports the known origin/main SC2034 and SC2016 findings, so it is not part of this change's acceptance condition.

- [ ] **Step 4: Verify the external consumer contract without mutating its config**

Run the installed monitor's capability grep against the worktree source file and against the updated installed skill after production deployment:

```bash
grep -E '^[[:space:]]*agmsg_spawn_options_requires_role_overlay[[:space:]]*\(\)[[:space:]]*\{' scripts/lib/spawn-options.sh
```

Expected: exit 0. Do not run `install-role-overlay.sh` until the installed agmsg provenance confirms the merged source version.

- [x] **Step 5: Commit the completed task**

```bash
git add scripts/lib/spawn-options.sh tests/test_spawn_options.bats docs/superpowers/plans/2026-08-15-role-overlay-capability-contract.md
git commit -m "feat: expose role-overlay capability"
```

Completed as commit `f381434` before PR publication.

## Plan self-review

- [x] The public name requested by the consumer is exact: `agmsg_spawn_options_requires_role_overlay`.
- [x] The query is semantically meaningful and never turns malformed metadata into an enabled policy.
- [x] Existing validator and argv isolation have focused and spawn integration coverage.
- [x] The plan introduces no new runtime side effects or user-facing configuration requirement.
