#!/usr/bin/env bats

# Tests for scripts/lib/spawn-options.sh (#273): per-agent-type extra CLI
# args spawn.sh injects, configured via a YAML file (AGMSG_SPAWN_OPTIONS_FILE
# or the default ~/.agmsg/config/spawn_options.yaml).

load test_helper

setup() {
  setup_test_env
  # shellcheck disable=SC1090
  source "$TEST_SKILL_DIR/scripts/lib/spawn-options.sh"
}

teardown() { teardown_test_env; }

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

@test "spawn_options_tokens: appends only the matching role overlay after base tokens" {
  local file="$TEST_SKILL_DIR/spawn_options.yaml"
  cat > "$file" <<'YAML'
codex:
  --sandbox: workspace-write
codex@architect:
  -p: architect
  -c: model_reasoning_effort=xhigh
codex@programmer:
  -p: programmer
YAML
  export AGMSG_SPAWN_OPTIONS_FILE="$file"

  run agmsg_spawn_options_tokens codex architect
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "--sandbox" ]
  [ "${lines[1]}" = "workspace-write" ]
  [ "${lines[2]}" = "-p" ]
  [ "${lines[3]}" = "architect" ]
  [ "${lines[4]}" = "-c" ]
  [ "${lines[5]}" = "model_reasoning_effort=xhigh" ]
  [ "${#lines[@]}" -eq 6 ]
}

@test "spawn_options_tokens: role false does not suppress the same base flag" {
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
