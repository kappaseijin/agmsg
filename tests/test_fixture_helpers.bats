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

@test "windows-native fixture helper: capture survives deletion of the target (#169B)" {
  local helper_dir fixture
  helper_dir="$BATS_TEST_DIRNAME"
  fixture="$BATS_TEST_TMPDIR/partial-teardown-fixture"
  mkdir -p "$fixture/run/nested"
  printf 'runtime residue\n' > "$fixture/run/nested/residue.txt"

  run bash -c '
    source "$1/test_helper.bash"
    export TEST_SKILL_DIR="$2"
    rm() {
      target="$2"
      find "$target" -depth -type f -delete
      find "$target" -depth -type d -empty -delete
      printf "sentinel rm stdout\n"
      printf "sentinel rm failure\n" >&2
      return 23
    }
    export -f rm
    set +e
    teardown_test_env
    rc="$?"
    set -e
    printf "child_rm_rc=%s\n" "$rc"
    exit "$rc"
  ' bash "$helper_dir" "$fixture"
  [ "$status" -eq 23 ]
  grep -Fq 'teardown_test_env: rm -rf failed' <<<"$output"
  grep -Fq 'rm exit status: 23' <<<"$output"
  grep -Fq 'sentinel rm stdout' <<<"$output"
  grep -Fq 'sentinel rm failure' <<<"$output"
  refute grep -Fq 'cannot read captured rm stdout' <<<"$output"
  refute grep -Fq 'cannot read captured rm stderr' <<<"$output"
}

@test "windows-native fixture helper: separates MSYS and native PID evidence (#169B)" {
  local helper_dir fixture stub log powershell_log
  helper_dir="$BATS_TEST_DIRNAME"
  fixture="$BATS_TEST_TMPDIR/msys-pid-fixture"
  stub="$BATS_TEST_TMPDIR/msys-pid-stub-bin"
  log="$BATS_TEST_TMPDIR/tasklist-args"
  powershell_log="$BATS_TEST_TMPDIR/powershell-args"
  mkdir -p "$fixture/run" "$stub"
  printf '4242\n' > "$fixture/run/codex-bridge.team.alice.pid"
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'case " $* " in' \
    '  *" -l -p 4242 "*)' \
    '    if [ "$AGMSG_PS_WITHOUT_WINPID" = 1 ]; then' \
    '      printf "UID PID PPID TTY STIME COMMAND\n"' \
    '      printf "u 4242 1 ? now /usr/bin/bash\n"' \
    '    else' \
    '      printf "UID PID PPID WINPID TTY STIME COMMAND\n"' \
    '      printf "u 4242 1 84242 ? now /usr/bin/bash\n"' \
    '    fi' \
    '    ;;' \
    '  *" -A "*)' \
    '    printf " 4242 1 S %s\n" "$AGMSG_SNAPSHOT_ROOT/run/codex-bridge.team.alice.pid"' \
    '    ;;' \
    '  *)' \
    '    printf "unexpected ps args: %s\n" "$*" >&2' \
    '    exit 1' \
    '    ;;' \
    'esac' > "$stub/ps"
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'printf "%s\n" "$*" > "$AGMSG_TEST_TASKLIST_LOG"' \
    'printf "Image Name                     PID Session Name        Session#    Mem Usage\n"' \
    'printf "bash.exe                    84242 Console                    1      1,000 K\n"' \
    > "$stub/tasklist"
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'if [ "$1" = -m ]; then' \
    '  printf "C:/tmp/agmsg-msys-pid-fixture\n"' \
    'else' \
    '  exit 1' \
    'fi' > "$stub/cygpath"
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'printf "%s\n" "$*" > "$AGMSG_TEST_POWERSHELL_LOG"' \
    'printf "ProcessId ParentProcessId Name CommandLine\n"' \
    'printf "84242 1 bash %s %s\n" "$AGMSG_SNAPSHOT_ROOT" "$AGMSG_SNAPSHOT_TEST_ROOT_NATIVE"' \
    > "$stub/powershell.exe"
  chmod +x "$stub"/*

  run env \
    MSYSTEM=MINGW64 \
    AGMSG_PS_WITHOUT_WINPID=0 \
    AGMSG_SNAPSHOT_ROOT="$fixture" \
    AGMSG_TEST_TASKLIST_LOG="$log" \
    AGMSG_TEST_POWERSHELL_LOG="$powershell_log" \
    PATH="$stub:$PATH" \
    bash -c '
      source "$1/test_helper.bash"
      snapshot_test_env_runtime "$2"
    ' bash "$helper_dir" "$fixture"
  [ "$status" -eq 0 ]
  grep -Fq 'msys_pid=4242' <<<"$output"
  grep -Fq 'winpid=84242' <<<"$output"
  grep -Fq 'tasklist record for winpid=84242' <<<"$output"
  grep -Fq 'native process candidates containing' <<<"$output"
  grep -Fq '84242' <<<"$output"
  grep -Fq 'C:/tmp/agmsg-msys-pid-fixture' <<<"$output"
  refute grep -Fq 'process table unavailable' <<<"$output"
  refute grep -Fq 'unexpected ps args' <<<"$output"
  grep -Fq 'Get-CimInstance Win32_Process' "$powershell_log"
  [ "$(cat "$log")" = '/FI PID eq 84242' ]
  refute grep -Fq 'PID eq 4242' "$log"

  : > "$log"
  run env \
    MSYSTEM=MINGW64 \
    AGMSG_PS_WITHOUT_WINPID=1 \
    AGMSG_SNAPSHOT_ROOT="$fixture" \
    AGMSG_TEST_TASKLIST_LOG="$log" \
    AGMSG_TEST_POWERSHELL_LOG="$powershell_log" \
    PATH="$stub:$PATH" \
    bash -c '
      source "$1/test_helper.bash"
      snapshot_test_env_runtime "$2"
    ' bash "$helper_dir" "$fixture"
  [ "$status" -eq 0 ]
  grep -Fq 'WINPID unavailable for msys_pid=4242; tasklist not queried' <<<"$output"
  [ ! -s "$log" ]
  refute grep -Fq 'tasklist record for winpid=' <<<"$output"
}

@test "windows-native fixture helper: runs rm when capture creation fails (#169B)" {
  local helper_dir fixture count_file
  helper_dir="$BATS_TEST_DIRNAME"
  fixture="$BATS_TEST_TMPDIR/capture-unavailable-fixture"
  count_file="$BATS_TEST_TMPDIR/rm-call-count"
  mkdir -p "$fixture/run"

  run env \
    AGMSG_RM_CALL_COUNT="$count_file" \
    bash -c '
      source "$1/test_helper.bash"
      export TEST_SKILL_DIR="$2"
      mktemp() {
        printf "capture setup unavailable\n" >&2
        return 66
      }
      rm() {
        printf "fallback rm stdout\n"
        printf "fallback rm stderr\n" >&2
        printf "called\n" > "$AGMSG_RM_CALL_COUNT"
        return 17
      }
      export -f mktemp rm
      set +e
      teardown_test_env
      rc="$?"
      set -e
      printf "child_rm_rc=%s\n" "$rc"
      exit "$rc"
    ' bash "$helper_dir" "$fixture"
  [ "$status" -eq 17 ]
  [ "$(cat "$count_file")" = called ]
  grep -Fq 'fallback rm stdout' <<<"$output"
  grep -Fq 'fallback rm stderr' <<<"$output"
  grep -Fq 'rm exit status: 17' <<<"$output"
  grep -Fq 'cannot create teardown capture directory (rc=66)' <<<"$output"
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
