#!/usr/bin/env bats

load test_helper

@test "fixture helper: skips a marker-bearing agmsg Git launcher" {
  local bin_dir guard real_git canonical_real_git
  bin_dir="$BATS_TEST_TMPDIR/git-bin"
  guard="$bin_dir/git"
  real_git="$bin_dir/git.exe"
  mkdir -p "$bin_dir"

  printf '#!/bin/sh\n# agmsg git push owner guard launcher\nexit 0\n' > "$guard"
  printf '#!/bin/sh\nexit 0\n' > "$real_git"
  chmod +x "$guard" "$real_git"
  canonical_real_git="$(cd "$(dirname "$real_git")" && pwd -P)/$(basename "$real_git")"

  run agmsg_test_real_git "$bin_dir"
  [ "$status" -eq 0 ]
  [ "$output" = "$canonical_real_git" ]
}

@test "fixture helper: isolates inherited storage before initializing the temp DB" {
  local poisoned helper_dir before
  poisoned="$BATS_TEST_TMPDIR/poisoned"
  mkdir -p "$poisoned"
  printf 'poisoned fixture\n' > "$poisoned/messages.db"
  before="$(cat "$poisoned/messages.db")"
  helper_dir="$BATS_TEST_DIRNAME"

  run bash -c '
    set -e
    export AGMSG_STORAGE_PATH="$1"
    export BATS_TEST_DIRNAME="$2"
    source "$BATS_TEST_DIRNAME/test_helper.bash"
    setup_test_env
    trap teardown_test_env EXIT
    source "$SCRIPTS/lib/storage.sh"
    [ "$AGMSG_STORAGE_PATH" = "$TEST_SKILL_DIR/db" ]
    [ "$DBPATH" = "$TEST_SKILL_DIR/db/messages.db" ]
    [ "$(agmsg_storage_dir)" = "$TEST_SKILL_DIR/db" ]
    [ -f "$TEST_SKILL_DIR/db/messages.db" ]
    [ "$(cat "$1/messages.db")" = "poisoned fixture" ]
  ' bash "$poisoned" "$helper_dir"
  [ "$status" -eq 0 ]
  [ "$(cat "$poisoned/messages.db")" = "$before" ]
}

@test "fixture helper: isolates every inherited execution surface" {
  local poisoned helper_dir before
  poisoned="$BATS_TEST_TMPDIR/poisoned-parent"
  helper_dir="$BATS_TEST_DIRNAME"
  mkdir -p "$poisoned"/{home,tmp,run,db,plugins}
  printf 'poisoned fixture\n' > "$poisoned/db/messages.db"
  printf 'home marker\n' > "$poisoned/home/marker"
  printf 'run marker\n' > "$poisoned/run/marker"
  printf 'tmp marker\n' > "$poisoned/tmp/marker"
  before="$(cat "$poisoned/db/messages.db")"

  run env \
    AGMSG_STORAGE_PATH="$poisoned/db" \
    AGMSG_STORAGE_DRIVER=jsonl \
    AGMSG_CONFIG="$poisoned/config" \
    AGMSG_PLUGIN_DIRS="$poisoned/plugins" \
    AGMSG_AGENT_PID=4242 \
    AGMSG_POISON=parent \
    HOME="$poisoned/home" \
    TMPDIR="$poisoned/tmp" \
    CODEX_HOME="$poisoned/codex-home" \
    SKILL_DIR="$poisoned/skill" \
    RUN_DIR="$poisoned/run" \
    SCRIPTS="$poisoned/scripts" \
    TYPES="$poisoned/types" \
    DBPATH="$poisoned/db/messages.db" \
    HERDR_ENV=parent-herdr \
    HERDR_PANE_ID=parent-pane \
    HERDR_WORKSPACE_ID=parent-workspace \
    TMUX=parent-tmux \
    TMUX_PANE=parent-pane \
    TMUX_TMPDIR="$poisoned/tmp" \
    CLAUDE_CODE_SESSION_ID=parent-claude \
    CODEX_SANDBOX=parent-codex \
    CODEX_THREAD_ID=parent-thread \
    GEMINI_CLI=parent-gemini \
    GEMINI_API_KEY=parent-key \
    GROK_SESSION_ID=parent-grok \
    LC_ALL=en_US.UTF-8 \
    LANG=ja_JP.UTF-8 \
    BATS_TEST_DIRNAME="$helper_dir" \
    bash -c '
      set -e
      source "$BATS_TEST_DIRNAME/test_helper.bash"
      setup_test_env
      trap teardown_test_env EXIT

      under_root() {
        case "$2" in
          "$1"|"$1"/*) return 0 ;;
          *) return 1 ;;
        esac
      }

      [ "$LC_ALL" = C ]
      [ "$LANG" = C ]
      [ "$AGMSG_STORAGE_PATH" = "$TEST_SKILL_DIR/db" ]
      [ "$AGMSG_STORAGE_DRIVER" = sqlite ]
      [ -n "${AGMSG_AGENT_PID+x}" ]
      [ -z "$AGMSG_AGENT_PID" ]
      if env | awk -F= '\''$1 ~ /^AGMSG_/ && $1 != "AGMSG_STORAGE_PATH" && $1 != "AGMSG_STORAGE_DRIVER" && $1 != "AGMSG_AGENT_PID" { bad=1 } END { exit bad }'\''; then :; else exit 1; fi
      if env | awk -F= '\''$1 ~ /^(HERDR_|TMUX)/ { bad=1 } END { exit bad }'\''; then :; else exit 1; fi
      if env | awk -F= '\''$1 ~ /^(CLAUDE_CODE_SESSION_ID|CODEX_SANDBOX|CODEX_THREAD_ID|GEMINI_CLI|GEMINI_API_KEY|GROK_SESSION_ID)$/ { bad=1 } END { exit bad }'\''; then :; else exit 1; fi
      if env | awk -F= '\''$1 == "CODEX_HOME" { bad=1 } END { exit bad }'\''; then :; else exit 1; fi

      under_root "$BATS_TEST_TMPDIR" "$TEST_SKILL_DIR"
      for path in "$HOME" "$TMPDIR" "$SKILL_DIR" "$RUN_DIR" "$SCRIPTS" "$TYPES" "$DBPATH"; do
        under_root "$TEST_SKILL_DIR" "$path"
      done
      [ -d "$TEST_SKILL_DIR/home" ]
      [ -d "$TEST_SKILL_DIR/tmp" ]
      [ -d "$TEST_SKILL_DIR/run" ]
      [ -d "$TEST_SKILL_DIR/db" ]
      [ -d "$TEST_SKILL_DIR/teams" ]
      probe="$(mktemp -d "$TMPDIR/probe.XXXXXX")"
      under_root "$TEST_SKILL_DIR/tmp" "$probe"
      rmdir "$probe"

      source "$SCRIPTS/lib/compat.sh"
      compat_get_comm() { printf '%s\n' sh; }
      compat_get_ppid() { printf '%s\n' 1; }
      source "$SCRIPTS/lib/type-registry.sh"
      source "$SCRIPTS/lib/detect-cli-type.sh"
      [ "$(agmsg_detect_cli_type)" = claude-code ]

      source "$SCRIPTS/lib/resolve-project.sh"
      source "$SCRIPTS/lib/instance-id.sh"
      [ "$(agmsg_instance_id sid claude-code)" = sid ]
    ' bash
  [ "$status" -eq 0 ]
  [ "$(cat "$poisoned/db/messages.db")" = "$before" ]
  [ "$(cat "$poisoned/home/marker")" = 'home marker' ]
  [ "$(cat "$poisoned/run/marker")" = 'run marker' ]
  [ "$(cat "$poisoned/tmp/marker")" = 'tmp marker' ]
}

@test "fixture helper: teardown reports rm failure evidence and preserves status (#169)" {
  local helper_dir fixture stub
  helper_dir="$BATS_TEST_DIRNAME"
  fixture="$BATS_TEST_TMPDIR/teardown-fixture"
  stub="$BATS_TEST_TMPDIR/rm-stub"
  mkdir -p "$fixture/run/nested" "$stub"
  printf 'runtime residue\n' > "$fixture/run/nested/residue.txt"
  cat > "$stub/rm" <<'EOF'
#!/usr/bin/env bash
printf 'sentinel rm stdout\n'
printf 'sentinel rm failure\n' >&2
exit 23
EOF
  chmod +x "$stub/rm"

  run bash -c '
    helper_dir="$1"
    fixture="$2"
    stub="$3"
    source "$helper_dir/test_helper.bash"
    export TEST_SKILL_DIR="$fixture"
    bash -c "while :; do sleep 1; done" "$TEST_SKILL_DIR" &
    holder="$!"
    printf "%s\n" "$holder" > "$TEST_SKILL_DIR/run/codex-bridge.team.alice.pid"
    printf "pid=%s\n" "$holder" > "$TEST_SKILL_DIR/run/codex-bridge-lease.$holder"

    original_path="$PATH"
    set +e
    PATH="$stub:$PATH" teardown_test_env
    rc="$?"
    set -e
    kill "$holder" 2>/dev/null || true
    wait "$holder" 2>/dev/null || true
    PATH="$original_path"
    /bin/rm -rf "$TEST_SKILL_DIR"
    printf "child_rm_rc=%s\n" "$rc"
    exit "$rc"
  ' bash "$helper_dir" "$fixture" "$stub"
  [ "$status" -eq 23 ]
  grep -Fq 'teardown_test_env: rm -rf failed' <<<"$output"
  grep -Fq 'rm exit status: 23' <<<"$output"
  grep -Fq 'sentinel rm stdout' <<<"$output"
  grep -Fq 'sentinel rm failure' <<<"$output"
  grep -Fq 'runtime snapshot before rm' <<<"$output"
  grep -Fq "$fixture/run/codex-bridge.team.alice.pid" <<<"$output"
  grep -Fq "$fixture/run/codex-bridge-lease." <<<"$output"
  grep -Fq "$fixture/run/nested/residue.txt" <<<"$output"
  grep -Fq 'residual tree after rm' <<<"$output"
  grep -Fq "$fixture" <<<"$output"
}

@test "fixture helper: successful teardown stays quiet (#169)" {
  local helper_dir fixture
  helper_dir="$BATS_TEST_DIRNAME"
  fixture="$BATS_TEST_TMPDIR/clean-teardown-fixture"
  mkdir -p "$fixture/run"

  run bash -c '
    source "$1/test_helper.bash"
    export TEST_SKILL_DIR="$2"
    teardown_test_env
  ' bash "$helper_dir" "$fixture"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ ! -e "$fixture" ]
}
