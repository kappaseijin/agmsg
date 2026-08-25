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
