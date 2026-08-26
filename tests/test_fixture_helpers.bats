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
