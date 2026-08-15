#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env

  export REAL_GIT="$(agmsg_test_real_git)"
  export GUARD="$SCRIPTS/guards/git-push-owner-guard.sh"
  export LAUNCHER_TEMPLATE="$SCRIPTS/guards/git-push-owner-guard-launcher.sh"
  export LAUNCHER="$TEST_SKILL_DIR/git-launcher"
  export BARE="$TEST_SKILL_DIR/remote.git"
  export WORK="$TEST_SKILL_DIR/work"
  export SSH_LOG="$TEST_SKILL_DIR/fake-ssh.log"
  export PUSH_MARKER="$TEST_SKILL_DIR/push-reached"
  export FAKE_SSH="$TEST_SKILL_DIR/fake-ssh"

  chmod +x "$GUARD" "$LAUNCHER_TEMPLATE"
  sed \
    -e "s|__AGMSG_GIT_GUARD_SCRIPT__|$GUARD|g" \
    -e "s|__AGMSG_REAL_GIT__|$REAL_GIT|g" \
    "$LAUNCHER_TEMPLATE" > "$LAUNCHER"
  chmod +x "$LAUNCHER"

  "$REAL_GIT" init --bare -q "$BARE"
  "$REAL_GIT" init -q "$WORK"
  "$REAL_GIT" -C "$WORK" config user.email test@example.invalid
  "$REAL_GIT" -C "$WORK" config user.name agmsg-test
  printf 'fixture\n' > "$WORK/fixture.txt"
  "$REAL_GIT" -C "$WORK" add fixture.txt
  "$REAL_GIT" -C "$WORK" commit -qm initial
  "$REAL_GIT" -C "$WORK" branch -M main

  cat > "$BARE/hooks/pre-receive" <<'HOOK'
#!/bin/sh
printf 'reached\n' > "$AGMSG_PUSH_MARKER"
cat >/dev/null
HOOK
  chmod +x "$BARE/hooks/pre-receive"

  export AGMSG_REAL_GIT="$REAL_GIT"
  export AGMSG_BARE="$BARE"
  export AGMSG_SSH_LOG="$SSH_LOG"
  export AGMSG_PUSH_MARKER="$PUSH_MARKER"
  cat > "$FAKE_SSH" <<'FAKE_SSH'
#!/bin/sh
printf '%s\n' "$*" >> "$AGMSG_SSH_LOG"
exec "$AGMSG_REAL_GIT" receive-pack "$AGMSG_BARE"
FAKE_SSH
  chmod +x "$FAKE_SSH"

  # Seed the scratch bare remote without any network, then use an SSH-shaped
  # GitHub URL for all transport tests. The fake SSH command never contacts
  # github.com or any other external host.
  "$REAL_GIT" -C "$WORK" push -q "$BARE" HEAD:main
 "$REAL_GIT" -C "$BARE" symbolic-ref HEAD refs/heads/main
 "$REAL_GIT" -C "$WORK" remote add origin git@github.com:kappaseijin/fixture.git
  "$REAL_GIT" -C "$WORK" config branch.main.remote origin
  "$REAL_GIT" -C "$WORK" config branch.main.merge refs/heads/main
 printf 'second revision\n' >> "$WORK/fixture.txt"
  "$REAL_GIT" -C "$WORK" add fixture.txt
  "$REAL_GIT" -C "$WORK" commit -qm second
  : > "$SSH_LOG"
  rm -f "$PUSH_MARKER"
  unset BASH_ENV ENV CDPATH GLOBIGNORE 2>/dev/null || :
}

teardown() {
  teardown_test_env
}

run_guard() {
  run env GIT_SSH_COMMAND="$FAKE_SSH" "$LAUNCHER" "$@"
}

assert_rejected() {
  : > "$SSH_LOG"
  rm -f "$PUSH_MARKER"
  run_guard "$@"
  [ "$status" -ne 0 ]
  [ ! -s "$SSH_LOG" ]
  [ ! -e "$PUSH_MARKER" ]
}

@test "GPG-01: rejects a third-party owner before fake SSH starts" {
  assert_rejected -C "$WORK" push git@github.com:thirdparty/fixture.git HEAD:main
}

@test "GPG-02 and GPG-12: allows an uppercase spelling of an allowlisted owner" {
  cd "$WORK"
  run_guard push git@GITHUB.COM:KAPPASEIJIN/fixture.git HEAD:main
  [ "$status" -eq 0 ]
  [ -s "$SSH_LOG" ]
  [ -s "$PUSH_MARKER" ]

  run_guard push
  [ "$status" -eq 0 ]
}

@test "GPG-03: checks every pushurl and rejects a mixed allowlist" {
  "$REAL_GIT" -C "$WORK" remote set-url --push origin git@github.com:kappaseijin/fixture.git
  "$REAL_GIT" -C "$WORK" remote set-url --add --push origin git@github.com:thirdparty/fixture.git

  assert_rejected -C "$WORK" push origin HEAD:main
}

@test "GPG-04: a scratch re-clone keeps the global guard" {
  local clone="$TEST_SKILL_DIR/reclone"
  "$REAL_GIT" clone -q "$BARE" "$clone"
  "$REAL_GIT" -C "$clone" remote set-url origin git@github.com:thirdparty/fixture.git

  assert_rejected -C "$clone" push origin HEAD:main
}

@test "GPG-05: checks insteadOf and pushInsteadOf rewrites for remote and direct URLs" {
  "$REAL_GIT" -C "$WORK" remote set-url origin https://github.com/kappaseijin/fixture.git
  "$REAL_GIT" -C "$WORK" config url."git@github.com:thirdparty/".insteadOf https://github.com/kappaseijin/
  assert_rejected -C "$WORK" push origin HEAD:main

  "$REAL_GIT" -C "$WORK" config --unset-all url."git@github.com:thirdparty/".insteadOf
  "$REAL_GIT" -C "$WORK" config url."git@github.com:thirdparty/".pushInsteadOf https://github.com/kappaseijin/
  assert_rejected -C "$WORK" push origin HEAD:main
  assert_rejected -C "$WORK" push https://github.com/kappaseijin/fixture.git HEAD:main
}

@test "GPG-06: applies command-line config and --repo destination selection" {
 assert_rejected -C "$WORK" -c remote.origin.pushurl=git@github.com:thirdparty/fixture.git push --repo origin HEAD:main
  assert_rejected -C "$WORK" -cremote.origin.pushurl=git@github.com:thirdparty/fixture.git push --repo origin HEAD:main
 assert_rejected -C "$WORK" -c remote.origin.pushurl=git@github.com:thirdparty/fixture.git push --repo=origin HEAD:main
}

@test "GPG-07: rejects Git aliases before they can expand to push" {
  "$REAL_GIT" -C "$WORK" config alias.ship 'push origin HEAD:main'
  assert_rejected -C "$WORK" ship
}

@test "GPG-08: policy and allowlist environment variables cannot widen the guard" {
  export PR_ACCOUNT_POLICY="$TEST_SKILL_DIR/policy.conf"
  export ALLOWED_OWNERS=thirdparty
  printf 'programmer_login=thirdparty\n' > "$PR_ACCOUNT_POLICY"

  assert_rejected -C "$WORK" push git@github.com:thirdparty/fixture.git HEAD:main
}

@test "GPG-09 and GPG-12: rejects local, malformed, and ambiguous URLs" {
  for url in \
    "file:///tmp/fixture.git" \
    "/tmp/fixture.git" \
    "https://user@github.com/kappaseijin/fixture.git" \
    "https://github.com:443/kappaseijin/fixture.git" \
    "https://github.com./kappaseijin/fixture.git" \
    "https://github.com/kappaseijin-evil/fixture.git" \
    "https://github.com/kappaseijin/fixture.git/extra" \
    "ssh://other@github.com/kappaseijin/fixture.git" \
    "ssh://git@github.com:22/kappaseijin/fixture.git"; do
    assert_rejected -C "$WORK" push "$url" HEAD:main
  done
}

@test "GPG-10: rejects send-pack even for an allowlisted-looking URL" {
  assert_rejected send-pack git@github.com:kappaseijin/fixture.git HEAD:main
}

@test "GPG-11: launcher neutralizes BASH_ENV and exported Bash functions" {
  local evil="$TEST_SKILL_DIR/evil.bash"
  export AGMSG_EVIL_MARKER="$TEST_SKILL_DIR/evil-sourced"
  printf 'printf sourced > "$AGMSG_EVIL_MARKER"\nallowed_owner() { return 0; }\n' > "$evil"
  export BASH_ENV="$evil"
  assert_rejected -C "$WORK" push git@github.com:thirdparty/fixture.git HEAD:main
  [ ! -e "$AGMSG_EVIL_MARKER" ]

  unset BASH_ENV
  : > "$SSH_LOG"
  run env GIT_SSH_COMMAND="$FAKE_SSH" \
    'BASH_FUNC_allowed_owner%%=() { return 0; }' \
    "$LAUNCHER" -C "$WORK" push git@github.com:thirdparty/fixture.git HEAD:main
  [ "$status" -ne 0 ]
  [ ! -s "$SSH_LOG" ]
}

@test "GPG-13: preserves -C, --git-dir, --work-tree, and --config-env contexts" {
  local value='git@github.com:thirdparty/fixture.git'
  assert_rejected -C "$WORK" push "$value" HEAD:main
  "$REAL_GIT" -C "$WORK" config remote.origin.pushurl "$value"
  assert_rejected --git-dir="$WORK/.git" --work-tree="$WORK" push origin HEAD:main

  export AGMSG_PUSHURL="$value"
  assert_rejected --git-dir="$WORK/.git" --work-tree="$WORK" \
    --config-env=remote.origin.pushurl=AGMSG_PUSHURL push origin HEAD:main
}

@test "GPG-15: accepts short quiet without weakening owner enforcement" {
  run_guard -C "$WORK" push -q origin HEAD:main
  [ "$status" -eq 0 ]
  [ -s "$SSH_LOG" ]
  [ -s "$PUSH_MARKER" ]

  assert_rejected -C "$WORK" push -q git@github.com:thirdparty/fixture.git HEAD:main
}

@test "GPG-14: fixed Git execution ignores a PATH decoy" {
  local decoy_dir decoy marker
  decoy_dir="$TEST_SKILL_DIR/decoy"
  decoy="$decoy_dir/git"
  marker="$TEST_SKILL_DIR/decoy-used"
  mkdir -p "$decoy_dir"
  cat > "$decoy" <<'DECOY'
#!/bin/sh
printf 'decoy\n' > "$AGMSG_DECOY_MARKER"
exit 99
DECOY
  chmod +x "$decoy"
  export AGMSG_DECOY_MARKER="$marker"

  run env PATH="$decoy_dir:$PATH" GIT_SSH_COMMAND="$FAKE_SSH" \
    "$LAUNCHER" -C "$WORK" push origin HEAD:main
  [ "$status" -eq 0 ]
  [ ! -e "$marker" ]
  [ -s "$PUSH_MARKER" ]
}

@test "launcher rejects unresolved placeholders" {
  run "$LAUNCHER_TEMPLATE" --version
  [ "$status" -ne 0 ]
}
