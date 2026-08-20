#!/usr/bin/env bats

# Tests for scripts/lib/spawn-options.sh (#273): per-agent-type extra CLI
# args spawn.sh injects, configured via a YAML file (AGMSG_SPAWN_OPTIONS_FILE
# or the default ~/.agmsg/config/spawn_options.yaml).

load test_helper

# These paths are captured before setup_test_env replaces HOME. They may be
# overridden for an isolated live-file check, while the ordinary suite remains
# portable on machines that do not install the workstation policy files.
AGMSG_MODEL_ORCHESTRATION_RULE_FILE_DEFAULT="${AGMSG_MODEL_ORCHESTRATION_RULE_FILE:-${HOME:-}/.agents/rules/model-orchestration.rule.md}"
AGMSG_MODEL_ORCHESTRATION_RULE_FILE_WAS_SET="${AGMSG_MODEL_ORCHESTRATION_RULE_FILE+x}"
AGMSG_SPAWN_OPTIONS_FILE_DEFAULT="${AGMSG_SPAWN_OPTIONS_FILE:-${HOME:-}/.agmsg/config/spawn_options.yaml}"
AGMSG_SPAWN_OPTIONS_FILE_WAS_SET="${AGMSG_SPAWN_OPTIONS_FILE+x}"

agmsg_model_orchestration_sync() {
  local policy_file="${1:-}" options_file="${2:-}" rows
  local role harness expected_model expected_effort section actual_model actual_effort
  local checked=0

  [ "$#" -eq 2 ] || {
    printf 'model sync: expected policy and spawn-options paths\n' >&2
    return 2
  }
  [ -f "$policy_file" ] || {
    printf 'model sync: missing policy file: %s\n' "$policy_file" >&2
    return 1
  }
  [ -f "$options_file" ] || {
    printf 'model sync: missing spawn-options file: %s\n' "$options_file" >&2
    return 1
  }

  rows="$TEST_SKILL_DIR/model-orchestration.rows"
  if ! awk -F '|' '
    BEGIN { in_table = 0; rows = 0 }
    $0 ~ /^\|[[:space:]]*役割[[:space:]]*\|[[:space:]]*ハーネス[[:space:]]*\|[[:space:]]*model[[:space:]]*\|[[:space:]]*effort[[:space:]]*\|/ {
      in_table = 1
      next
    }
    in_table && $0 !~ /^\|/ {
      if (rows > 0) exit
      next
    }
    in_table {
      if ($2 ~ /^[[:space:]:-]+$/) next
      role = $2
      harness = $3
      model = $4
      effort = $5
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", role)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", harness)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", model)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", effort)
      gsub(/[*`]/, "", harness)
      gsub(/[*`]/, "", model)
      gsub(/[*`]/, "", effort)
      sub(/[（(].*$/, "", effort)
      gsub(/[[:space:]]+$/, "", effort)
      if (role == "" || harness == "" || model == "" || effort == "") next
      print role "\t" harness "\t" model "\t" effort
      rows++
    }
    END { exit(rows > 0 ? 0 : 1) }
  ' "$policy_file" > "$rows"; then
    printf 'model sync: role table was not found in %s\n' "$policy_file" >&2
    return 1
  fi

  while IFS="$(printf '\t')" read -r role harness expected_model expected_effort; do
    [ "$harness" = "claude-code" ] || continue
    section="${harness}@${role}"
    if ! actual_model="$(AGMSG_SPAWN_OPTIONS_FILE="$options_file" \
      agmsg_spawn_options_section_value "$section" --model 2>/dev/null)"; then
      printf 'model sync: missing --model in %s (expected %s)\n' \
        "$section" "$expected_model" >&2
      return 1
    fi
    if ! actual_effort="$(AGMSG_SPAWN_OPTIONS_FILE="$options_file" \
      agmsg_spawn_options_section_value "$section" --effort 2>/dev/null)"; then
      printf 'model sync: missing --effort in %s (expected %s)\n' \
        "$section" "$expected_effort" >&2
      return 1
    fi
    if [ "$actual_model" != "$expected_model" ] || \
      [ "$actual_effort" != "$expected_effort" ]; then
      printf 'model sync: %s expected model=%s effort=%s actual model=%s effort=%s\n' \
        "$section" "$expected_model" "$expected_effort" \
        "$actual_model" "$actual_effort" >&2
      return 1
    fi
    checked=$((checked + 1))
  done < "$rows"

  [ "$checked" -gt 0 ] || {
    printf 'model sync: no claude-code role rows were checked in %s\n' \
      "$policy_file" >&2
    return 1
  }
}

setup() {
  setup_test_env
  # shellcheck disable=SC1090
  source "$TEST_SKILL_DIR/scripts/lib/spawn-options.sh"
  # The required-overlay validator reads profile resolution rules from the
  # agent-type manifest, just as spawn.sh does.
  # shellcheck disable=SC1090
  source "$TEST_SKILL_DIR/scripts/lib/type-registry.sh"
}

teardown() { teardown_test_env; }

@test "model sync: role table and spawn options are compared" {
  local policy_file="$TEST_SKILL_DIR/model-orchestration.rule.md"
  local options_file="$TEST_SKILL_DIR/spawn_options.yaml"

  cat > "$policy_file" <<'MARKDOWN'
| 役割 | ハーネス | model | effort |
| --- | --- | --- | --- |
| manager | claude-code | `claude-opus-5` | low |
| reviewer | **claude-code** | `claude-sonnet-5` | xhigh（review policy note） |
| programmer | **codex** | `gpt-5.6-terra` | medium |
MARKDOWN
  cat > "$options_file" <<'YAML'
claude-code@manager:
  --model: claude-opus-5
  --effort: low
claude-code@reviewer:
  --model: claude-sonnet-5
  --effort: xhigh
codex@programmer:
  -p: programmer
YAML

  run agmsg_model_orchestration_sync "$policy_file" "$options_file"
  [ "$status" -eq 0 ]
}

@test "model sync: stale model or effort fails closed" {
  local policy_file="$TEST_SKILL_DIR/model-orchestration.rule.md"
  local options_file="$TEST_SKILL_DIR/spawn_options.yaml"

  cat > "$policy_file" <<'MARKDOWN'
| 役割 | ハーネス | model | effort |
| --- | --- | --- | --- |
| reviewer | claude-code | `claude-sonnet-5` | xhigh |
MARKDOWN
  cat > "$options_file" <<'YAML'
claude-code@reviewer:
  --model: claude-sonnet-5
  --effort: xhigh
YAML

  run agmsg_model_orchestration_sync "$policy_file" "$options_file"
  [ "$status" -eq 0 ]

  cat > "$options_file" <<'YAML'
claude-code@reviewer:
  --model: claude-opus-5
  --effort: xhigh
YAML
  run agmsg_model_orchestration_sync "$policy_file" "$options_file"
  [ "$status" -ne 0 ]

  cat > "$options_file" <<'YAML'
claude-code@reviewer:
  --model: claude-sonnet-5
  --effort: medium
YAML
  run agmsg_model_orchestration_sync "$policy_file" "$options_file"
  [ "$status" -ne 0 ]
}

@test "model sync: installed role overlays match the current policy table" {
  local policy_file="$AGMSG_MODEL_ORCHESTRATION_RULE_FILE_DEFAULT"
  local options_file="$AGMSG_SPAWN_OPTIONS_FILE_DEFAULT"

  if [ ! -f "$policy_file" ] || [ ! -f "$options_file" ]; then
    if [ -n "$AGMSG_MODEL_ORCHESTRATION_RULE_FILE_WAS_SET" ] || \
      [ -n "$AGMSG_SPAWN_OPTIONS_FILE_WAS_SET" ]; then
      printf 'model sync: explicit live files are unavailable: %s and %s\n' \
        "$policy_file" "$options_file" >&2
      return 1
    fi
    skip "workstation model policy and spawn-options files are not installed"
  fi

  run agmsg_model_orchestration_sync "$policy_file" "$options_file"
  [ "$status" -eq 0 ]
}

# --- agmsg_spawn_options_file ---

@test "spawn_options_file: defaults to ~/.agmsg/config/spawn_options.yaml under HOME" {
  unset AGMSG_SPAWN_OPTIONS_FILE
  [ "$(agmsg_spawn_options_file)" = "$HOME/.agmsg/config/spawn_options.yaml" ]
}

@test "spawn_options_file: AGMSG_SPAWN_OPTIONS_FILE overrides the default" {
  export AGMSG_SPAWN_OPTIONS_FILE="/tmp/custom-spawn-options.yaml"
  [ "$(agmsg_spawn_options_file)" = "/tmp/custom-spawn-options.yaml" ]
}

# --- agmsg_spawn_options_tokens ---

@test "spawn_options_tokens: missing file yields no tokens" {
  export AGMSG_SPAWN_OPTIONS_FILE="$TEST_SKILL_DIR/does-not-exist.yaml"
  run agmsg_spawn_options_tokens claude-code
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "spawn_options_tokens: missing type section yields no tokens" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
codex:
  --sandbox: workspace-write
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"
  run agmsg_spawn_options_tokens claude-code
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "spawn_options_tokens: a string value expands to a two-token flag+value" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
claude-code:
  --permission-mode: acceptEdits
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"
  run agmsg_spawn_options_tokens claude-code
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "--permission-mode" ]
  [ "${lines[1]}" = "acceptEdits" ]
  [ "${#lines[@]}" -eq 2 ]
}

@test "spawn_options_tokens: a true value expands to a single flag-only token" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
claude-code:
  --dangerously-skip-permissions: true
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"
  run agmsg_spawn_options_tokens claude-code
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "--dangerously-skip-permissions" ]
  [ "${#lines[@]}" -eq 1 ]
}

@test "spawn_options_tokens: a false value is suppressed entirely" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
claude-code:
  --dangerously-skip-permissions: false
  --permission-mode: acceptEdits
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"
  run agmsg_spawn_options_tokens claude-code
  [ "$status" -eq 0 ]
  [[ "$output" != *"--dangerously-skip-permissions"* ]]
  [[ "$output" == *"--permission-mode"* ]]
}

@test "spawn_options_tokens: multiple keys under a section all emit, in order" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
codex:
  --sandbox: workspace-write
  --ask-for-approval: never
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"
  run agmsg_spawn_options_tokens codex
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "--sandbox" ]
  [ "${lines[1]}" = "workspace-write" ]
  [ "${lines[2]}" = "--ask-for-approval" ]
  [ "${lines[3]}" = "never" ]
}

@test "spawn_options_tokens: only the requested type's section is read" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
claude-code:
  --permission-mode: acceptEdits
codex:
  --sandbox: workspace-write
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"
  run agmsg_spawn_options_tokens codex
  [ "$status" -eq 0 ]
  [[ "$output" != *"permission-mode"* ]]
  [[ "$output" == *"--sandbox"* ]]
  [[ "$output" == *"workspace-write"* ]]
}

@test "spawn_options_tokens: a value containing spaces stays a single token" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
claude-code:
  --append-system-prompt: be extra careful with destructive commands
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"
  run agmsg_spawn_options_tokens claude-code
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "--append-system-prompt" ]
  [ "${lines[1]}" = "be extra careful with destructive commands" ]
  [ "${#lines[@]}" -eq 2 ]
}

@test "spawn_options_tokens: empty file yields no tokens" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  : > "$file"
  export AGMSG_SPAWN_OPTIONS_FILE="$file"
  run agmsg_spawn_options_tokens claude-code
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "spawn_options_tokens: type-only golden output remains byte-identical with a role" {
  export AGMSG_SPAWN_OPTIONS_FILE="$BATS_TEST_DIRNAME/fixtures/spawn_options_legacy.yaml"
  local actual="$BATS_TEST_TMPDIR/codex.tokens"

  agmsg_spawn_options_tokens codex architect > "$actual"
  cmp "$BATS_TEST_DIRNAME/fixtures/spawn_options_legacy_codex.tokens" "$actual"
}

@test "spawn_options_tokens: role overlay overrides matching base keys" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
codex:
  --sandbox: workspace-write
  -p: base
codex@architect:
  --sandbox: danger-full-access
  -p: architect
  -c: model_reasoning_effort=xhigh
codex@programmer:
  -p: programmer
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  run agmsg_spawn_options_tokens codex architect
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "--sandbox" ]
  [ "${lines[1]}" = "danger-full-access" ]
  [ "${lines[2]}" = "-p" ]
  [ "${lines[3]}" = "architect" ]
  [ "${lines[4]}" = "-c" ]
  [ "${lines[5]}" = "model_reasoning_effort=xhigh" ]
  [ "${#lines[@]}" -eq 6 ]
}

@test "spawn_options_tokens: role false suppresses the matching base key" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
codex:
  --sandbox: workspace-write
codex@architect:
  --sandbox: false
  -p: architect
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  run agmsg_spawn_options_tokens codex architect
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "-p" ]
  [ "${lines[1]}" = "architect" ]
  [ "${#lines[@]}" -eq 2 ]
}

@test "spawn_options_tokens: role-only keys follow unsuppressed base keys" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
codex:
  --sandbox: workspace-write
codex@architect:
  -p: architect
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  run agmsg_spawn_options_tokens codex architect
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "--sandbox" ]
  [ "${lines[1]}" = "workspace-write" ]
  [ "${lines[2]}" = "-p" ]
  [ "${lines[3]}" = "architect" ]
  [ "${#lines[@]}" -eq 4 ]
}

@test "spawn_options_tokens: type lookup never reads a similarly named role section" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
codex@architect:
  -p: architect
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  run agmsg_spawn_options_tokens codex
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

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
  [ "${lines[1]}" = 'value with spaces ; $HOME * [meta]' ]
  [ "${#lines[@]}" -eq 2 ]
}

@test "spawn_options_tokens: legacy header whitespace and comments preserve base tokens" {
  export AGMSG_SPAWN_OPTIONS_FILE="$BATS_TEST_DIRNAME/fixtures/spawn_options_header_compat.yaml"
  local actual="$BATS_TEST_TMPDIR/codex.tokens"

  agmsg_spawn_options_tokens codex > "$actual"
  cmp "$BATS_TEST_DIRNAME/fixtures/spawn_options_header_compat_codex.tokens" "$actual"
}

@test "spawn_options_tokens: role overlay accepts trailing header whitespace" {
  export AGMSG_SPAWN_OPTIONS_FILE="$BATS_TEST_DIRNAME/fixtures/spawn_options_header_compat.yaml"

  run agmsg_spawn_options_tokens codex architect
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "--sandbox" ]
  [ "${lines[1]}" = "workspace-write" ]
  [ "${lines[2]}" = "-p" ]
  [ "${lines[3]}" = "architect" ]
  [ "${#lines[@]}" -eq 4 ]
}

# --- required role overlay policy ---

@test "required_role_overlay: missing metadata remains a no-op" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
claude-code:
  --permission-mode: acceptEdits
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  run agmsg_spawn_options_validate_required_role_overlay claude-code reviewer
  [ "$status" -eq 0 ]
}

@test "required_role_overlay capability: consumer can detect the literal public definition" {
  run grep -E '^[[:space:]]*agmsg_spawn_options_requires_role_overlay[[:space:]]*\(\)[[:space:]]*\{' \
    "$SCRIPTS/lib/spawn-options.sh"
  [ "$status" -eq 0 ]
}

@test "required_role_overlay capability: true reports the public contract" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
agmsg.require-role-overlay:
  claude-code: true
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

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

@test "required_role_overlay: true rejects a missing role section" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
agmsg.require-role-overlay:
  claude-code: true
claude-code:
  --permission-mode: acceptEdits
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  run agmsg_spawn_options_validate_required_role_overlay claude-code reviewer
  [ "$status" -ne 0 ]
  [[ "$output" == *"claude-code"* ]]
  [[ "$output" == *"reviewer"* ]]
  [[ "$output" == *"role overlay"* ]]
}

@test "required_role_overlay: true rejects an empty role" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
agmsg.require-role-overlay:
  claude-code: true
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  run agmsg_spawn_options_validate_required_role_overlay claude-code ""
  [ "$status" -ne 0 ]
  [[ "$output" == *"claude-code"* ]]
  [[ "$output" == *"--role"* ]]
}

@test "required_role_overlay: false preserves the optional overlay behavior" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
agmsg.require-role-overlay:
  claude-code: false
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  run agmsg_spawn_options_validate_required_role_overlay claude-code reviewer
  [ "$status" -eq 0 ]
}

@test "required_role_overlay: non-boolean metadata fails closed" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
agmsg.require-role-overlay:
  claude-code: yes
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  run agmsg_spawn_options_validate_required_role_overlay claude-code reviewer
  [ "$status" -ne 0 ]
  [[ "$output" == *"true or false"* ]]
}

@test "required_role_overlay: metadata never becomes an argv token" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
agmsg.require-role-overlay:
  claude-code: true
claude-code@reviewer:
  --permission-mode: acceptEdits
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  run agmsg_spawn_options_validate_required_role_overlay claude-code reviewer
  [ "$status" -eq 0 ]
  run agmsg_spawn_options_tokens claude-code reviewer
  [ "$status" -eq 0 ]
  [[ "$output" == *"--permission-mode"* ]]
  [[ "$output" != *"agmsg.require-role-overlay"* ]]
  [[ "$output" != *"claude-code: true"* ]]
}

@test "required_role_overlay: codex accepts an existing profile" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  mkdir -p "$HOME/.codex"
  : > "$HOME/.codex/architect.config.toml"
  cat > "$file" <<'YAML'
agmsg.require-role-overlay:
  codex: true
codex@architect:
  -p: architect
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  agmsg_spawn_options_validate_required_role_overlay codex architect
  [ "$?" -eq 0 ]
  expected_home="$(cd "$HOME/.codex" && pwd -P)"
  [ "$AGMSG_SPAWN_OPTIONS_PROFILE_HOME" = "$expected_home" ]
}

@test "required_role_overlay: codex rejects a missing profile" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  mkdir -p "$HOME/.codex"
  cat > "$file" <<'YAML'
agmsg.require-role-overlay:
  codex: true
codex@architect:
  -p: architect
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  run agmsg_spawn_options_validate_required_role_overlay codex architect
  [ "$status" -ne 0 ]
  [[ "$output" == *"architect.config.toml"* ]]
  [[ "$output" == *"profile"* ]]
}

@test "required_role_overlay: codex rejects a base profile even when overlay suppresses it" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  mkdir -p "$HOME/.codex"
  : > "$HOME/.codex/architect.config.toml"
  cat > "$file" <<'YAML'
agmsg.require-role-overlay:
  codex: true
codex:
  -p: base
codex@architect:
  -p: false
  --sandbox: workspace-write
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  run agmsg_spawn_options_validate_required_role_overlay codex architect
  [ "$status" -ne 0 ]
  [[ "$output" == *"base"* ]]
  [[ "$output" == *"codex"* ]]
}

@test "required_role_overlay: codex accepts the long equals profile form" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  mkdir -p "$HOME/.codex"
  : > "$HOME/.codex/architect.config.toml"
  cat > "$file" <<'YAML'
agmsg.require-role-overlay:
  codex: true
codex@architect:
  --profile=architect: true
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  run agmsg_spawn_options_validate_required_role_overlay codex architect
  [ "$status" -eq 0 ]
}
