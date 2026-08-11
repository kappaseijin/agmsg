#!/usr/bin/env bats

setup() {
  BUNDLE_ROOT="$BATS_TEST_TMPDIR/bundle-root"
  BUNDLE_ORIGIN="$BATS_TEST_TMPDIR/origin.git"
  BUNDLE_REF="v9.9.9"

  mkdir -p "$BUNDLE_ROOT/app/scripts"
  cp "$BATS_TEST_DIRNAME/../app/scripts/bundle-core.sh" "$BUNDLE_ROOT/app/scripts/"
  printf '%s\n' "$BUNDLE_REF" > "$BUNDLE_ROOT/app/AGMSG_CORE_REF"
  git init --bare -q "$BUNDLE_ORIGIN"
  git -C "$BUNDLE_ROOT" init -q
  git -C "$BUNDLE_ROOT" remote add origin "$BUNDLE_ORIGIN"
  BUNDLE="$BUNDLE_ROOT/app/scripts/bundle-core.sh"
}

@test "bundle-core: says how to synchronize a pinned tag absent from origin" {
  run bash "$BUNDLE"

  [ "$status" -ne 0 ]
  [[ "$output" == *"origin is missing pinned tag '$BUNDLE_REF' required by app/AGMSG_CORE_REF"* ]]
  [[ "$output" == *"git push origin $BUNDLE_REF"* ]]
}
