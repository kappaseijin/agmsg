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
