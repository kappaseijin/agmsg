#!/usr/bin/env bats

load test_helper

setup() {
  BUNDLE_ROOT="$BATS_TEST_TMPDIR/bundle-root"
  BUNDLE_ORIGIN="$BATS_TEST_TMPDIR/origin.git"
  BUNDLE_REF="v9.9.9"
  REAL_GIT="$(agmsg_test_real_git)"

  mkdir -p "$BUNDLE_ROOT/app/scripts"
  cp "$BATS_TEST_DIRNAME/../app/scripts/bundle-core.sh" "$BUNDLE_ROOT/app/scripts/"
  printf '%s\n' "$BUNDLE_REF" > "$BUNDLE_ROOT/app/AGMSG_CORE_REF"
  "$REAL_GIT" init --bare -q "$BUNDLE_ORIGIN"
  "$REAL_GIT" -C "$BUNDLE_ROOT" init -q
  "$REAL_GIT" -C "$BUNDLE_ROOT" remote add origin "$BUNDLE_ORIGIN"
  BUNDLE="$BUNDLE_ROOT/app/scripts/bundle-core.sh"
}

publish_pinned_core() {
  local source="$BATS_TEST_TMPDIR/core-source"

  "$REAL_GIT" init -q "$source"
  "$REAL_GIT" -C "$source" config user.email test@example.com
  "$REAL_GIT" -C "$source" config user.name test
  mkdir -p "$source/scripts/drivers/types/agmsg-app"
  touch "$source/scripts/api.sh" "$source/scripts/drivers/types/agmsg-app/type.conf"
  touch "$source/install.sh" "$source/uninstall.sh"
  printf '1.0.0\n' > "$source/VERSION"
  "$REAL_GIT" -C "$source" add scripts install.sh uninstall.sh VERSION
  "$REAL_GIT" -C "$source" commit -qm "add core files"
  "$REAL_GIT" -C "$source" tag "$BUNDLE_REF"
  "$REAL_GIT" -C "$source" remote add origin "$BUNDLE_ORIGIN"
  "$REAL_GIT" -C "$source" push -q origin "refs/tags/$BUNDLE_REF"
}

@test "bundle-core: says how to synchronize a pinned tag absent from origin" {
  run bash "$BUNDLE"

  [ "$status" -ne 0 ]
  [[ "$output" == *"origin is missing pinned tag '$BUNDLE_REF' required by app/AGMSG_CORE_REF"* ]]
  [[ "$output" == *"git push origin $BUNDLE_REF"* ]]
}

@test "bundle-core: does not call a broken origin a missing tag" {
  "$REAL_GIT" -C "$BUNDLE_ROOT" remote set-url origin "$BATS_TEST_TMPDIR/nonexistent.git"
  run bash "$BUNDLE"

  [ "$status" -ne 0 ]
  [[ "$output" != *"origin is missing pinned tag"* ]]
  [[ "$output" != *"git push origin"* ]]
}

@test "bundle-core: bundles a pinned tag present in origin" {
  publish_pinned_core
  run bash "$BUNDLE"

  [ "$status" -eq 0 ]
  [[ "$output" != *"origin is missing pinned tag"* ]]
  [ -f "$BUNDLE_ROOT/app/src-tauri/resources/agmsg-core/scripts/api.sh" ]
}
