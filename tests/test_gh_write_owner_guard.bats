#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env

  export GUARD="$SCRIPTS/guards/gh-write-owner-guard.sh"
  export LAUNCHER_TEMPLATE="$SCRIPTS/guards/gh-write-owner-guard-launcher.sh"
  export FAKE_BIN="$TEST_SKILL_DIR/fake-bin"
  export REAL_GH="$FAKE_BIN/real-gh"
  export FAKE_WRITE_LOG="$TEST_SKILL_DIR/fake-writes.log"
  export FAKE_READ_LOG="$TEST_SKILL_DIR/fake-reads.log"
  export FAKE_PROMPT_LOG="$TEST_SKILL_DIR/fake-prompts.log"
  export FAKE_DEFAULT_MODE=empty
  export FAKE_CWD_MODE=empty
  export FAKE_EXPLICIT_MODE=allowed
  export FAKE_RUN_MODE=failed
  export FAKE_RUN_ID=12345
  export FAKE_JOB_ID=67890
  mkdir -p "$FAKE_BIN" "$TEST_SKILL_DIR/scratch"
  : > "$FAKE_WRITE_LOG"
  : > "$FAKE_READ_LOG"
  : > "$FAKE_PROMPT_LOG"

  cat > "$REAL_GH" <<'FAKE_GH'
#!/usr/bin/env bash
set -euo pipefail

: "${FAKE_WRITE_LOG:?}"
: "${FAKE_READ_LOG:?}"
: "${FAKE_PROMPT_LOG:?}"

if [ "${1:-}" = repo ] && [ "${2:-}" = set-default ] && [ "${3:-}" = --view ]; then
  if [ "${FAKE_REQUIRE_NONINTERACTIVE:-0}" = 1 ] && [ "${GH_PROMPT_DISABLED:-}" != true ]; then
    printf 'prompt attempted\n' >> "$FAKE_PROMPT_LOG"
    exit 7
  fi
  case "${FAKE_DEFAULT_MODE:-empty}" in
    allowed) printf 'kappaseijin/fixture\n' ;;
    thirdparty) printf 'thirdparty/fixture\n' ;;
    multiline) printf 'kappaseijin/fixture\nthirdparty/fixture\n' ;;
    invalid) printf 'not-a-repository\n' ;;
    empty) exit 1 ;;
    fail) exit 9 ;;
  esac
  exit 0
fi

if [ "${1:-}" = repo ] && [ "${2:-}" = view ] && [ "${3:-}" != --json ]; then
  if [ "${FAKE_REQUIRE_NONINTERACTIVE:-0}" = 1 ] && [ "${GH_PROMPT_DISABLED:-}" != true ]; then
    printf 'prompt attempted\n' >> "$FAKE_PROMPT_LOG"
    exit 7
  fi
  case "${FAKE_EXPLICIT_MODE:-empty}" in
    allowed) printf 'kappaseijin/fixture\thttps://github.com/kappaseijin/fixture\n' ;;
    thirdparty) printf 'thirdparty/fixture\thttps://github.com/thirdparty/fixture\n' ;;
    multiline) printf 'kappaseijin/fixture\thttps://github.com/kappaseijin/fixture\nthirdparty/fixture\thttps://github.com/thirdparty/fixture\n' ;;
    invalid) printf 'not-a-repository\n' ;;
    mismatch) printf 'kappaseijin/fixture\thttps://github.com/thirdparty/fixture\n' ;;
    empty) exit 1 ;;
    fail) exit 9 ;;
  esac
  exit 0
fi

if [ "${1:-}" = repo ] && [ "${2:-}" = view ]; then
  case "${FAKE_CWD_MODE:-empty}" in
    allowed) printf 'kappaseijin/fixture\thttps://github.com/kappaseijin/fixture\n' ;;
    thirdparty) printf 'thirdparty/fixture\thttps://github.com/thirdparty/fixture\n' ;;
    multiline) printf 'kappaseijin/fixture\thttps://github.com/kappaseijin/fixture\nthirdparty/fixture\thttps://github.com/thirdparty/fixture\n' ;;
    invalid) printf 'not-a-repository\n' ;;
    mismatch) printf 'kappaseijin/fixture\thttps://github.com/thirdparty/fixture\n' ;;
    empty) exit 1 ;;
    fail) exit 9 ;;
  esac
  exit 0
fi

if [ "${1:-}" = run ] && [ "${2:-}" = view ]; then
  printf '%s\n' "$*" >> "$FAKE_READ_LOG"
  case "${FAKE_RUN_MODE:-failed}" in
    failed) printf '%s\n%s\tcompleted\tfailure\n' "$FAKE_RUN_ID" "$FAKE_JOB_ID" ;;
    cancelled) printf '%s\n%s\tcompleted\tcancelled\n' "$FAKE_RUN_ID" "$FAKE_JOB_ID" ;;
    timed_out) printf '%s\n%s\tcompleted\ttimed_out\n' "$FAKE_RUN_ID" "$FAKE_JOB_ID" ;;
    success) printf '%s\n%s\tcompleted\tsuccess\n' "$FAKE_RUN_ID" "$FAKE_JOB_ID" ;;
    running) printf '%s\n%s\tin_progress\t\n' "$FAKE_RUN_ID" "$FAKE_JOB_ID" ;;
    missing) printf '%s\n99999\tcompleted\tfailure\n' "$FAKE_RUN_ID" ;;
    mismatch) printf '99999\n%s\tcompleted\tfailure\n' "$FAKE_JOB_ID" ;;
    duplicate) printf '%s\n%s\tcompleted\tfailure\n%s\tcompleted\tfailure\n' "$FAKE_RUN_ID" "$FAKE_JOB_ID" "$FAKE_JOB_ID" ;;
    malformed) printf '%s\nbad\n' "$FAKE_RUN_ID" ;;
    empty) exit 0 ;;
    fail) exit 9 ;;
  esac
  exit 0
fi

if [ "${1:-}" = api ]; then
  printf '%s\n' "$*" >> "$FAKE_READ_LOG"
elif [ "${1:-}" = alias ] && [[ "${2:-}" = list || "${2:-}" = set || "${2:-}" = delete ]]; then
  printf '%s\n' "$*" >> "$FAKE_READ_LOG"
elif [ "${1:-}" = extension ] && [ "${2:-}" = list ]; then
  printf '%s\n' "$*" >> "$FAKE_READ_LOG"
elif [ "${1:-}" = config ] || [ "${1:-}" = auth ]; then
  printf '%s\n' "$*" >> "$FAKE_READ_LOG"
elif [ "${1:-}" = issue ] && [[ "${2:-}" = view || "${2:-}" = list || "${2:-}" = status ]]; then
  printf '%s\n' "$*" >> "$FAKE_READ_LOG"
elif [ "${1:-}" = pr ] && [[ "${2:-}" = view || "${2:-}" = list || "${2:-}" = status || "${2:-}" = diff || "${2:-}" = checks ]]; then
  printf '%s\n' "$*" >> "$FAKE_READ_LOG"
else
  printf '%s\n' "$*" >> "$FAKE_WRITE_LOG"
fi
FAKE_GH
  chmod +x "$REAL_GH"

  export LAUNCHER="$TEST_SKILL_DIR/gh-launcher"
  sed \
    -e "s|__AGMSG_GH_GUARD_SCRIPT__|$GUARD|g" \
    -e "s|__AGMSG_REAL_GH__|$REAL_GH|g" \
    "$LAUNCHER_TEMPLATE" > "$LAUNCHER"
  chmod +x "$LAUNCHER"
}

teardown() {
  teardown_test_env
}

run_guard() {
  run "$LAUNCHER" "$@"
}

assert_rejected() {
  run_guard "$@"
  [ "$status" -ne 0 ]
  [ ! -s "$FAKE_WRITE_LOG" ]
  : > "$FAKE_WRITE_LOG"
}

@test "GHG-01: rejects an issue writer targeting a third-party owner" {
  assert_rejected issue create --repo thirdparty/fixture --title "blocked"
}

@test "GHG-02: allows an owner PR review and preserves the original argv" {
  run_guard pr review 42 --repo kappaseijin/fixture --approve
  [ "$status" -eq 0 ]
  grep -Fq 'pr review 42 --repo kappaseijin/fixture --approve' "$FAKE_WRITE_LOG"
}

@test "GHG-03 and GHG-18: scratch cwd has the same owner guard" {
  cd "$TEST_SKILL_DIR/scratch"
  assert_rejected issue create --repo thirdparty/fixture --title "blocked"

  run_guard issue create --repo kappaseijin/fixture --title "allowed"
  [ "$status" -eq 0 ]
}

@test "GHG-04 and GHG-12: policy and runtime allowlist settings cannot open a third-party path" {
  export PR_ACCOUNT_POLICY="$TEST_SKILL_DIR/does-not-exist.conf"
  export ALLOWED_OWNERS=thirdparty
  assert_rejected issue comment 10 --repo thirdparty/fixture --body "blocked"
}

@test "existing PR account policy can add a rejection after owner authorization" {
  local policy="$TEST_SKILL_DIR/pr-account-policy.conf"
  printf 'map=%s=programmer\nprogrammer_login=expected-login\n' "$(pwd -P)" > "$policy"
  export PR_ACCOUNT_POLICY="$policy"

  run_guard pr review 10 --repo kappaseijin/fixture --approve
  [ "$status" -ne 0 ]
  [ ! -s "$FAKE_WRITE_LOG" ]
}

@test "GHG-05: GH_REPO is resolved before the cwd" {
  run env GH_REPO=thirdparty/fixture "$LAUNCHER" issue comment 10 --body "blocked"
  [ "$status" -ne 0 ]
  [ ! -s "$FAKE_WRITE_LOG" ]
}

@test "GHG-06: explicit --repo wins over GH_REPO" {
  run env GH_REPO=thirdparty/fixture "$LAUNCHER" issue comment 10 --repo kappaseijin/fixture --body "allowed"
  [ "$status" -eq 0 ]
  grep -Fq 'issue comment 10 --repo kappaseijin/fixture --body allowed' "$FAKE_WRITE_LOG"
}

@test "GHG-07: default repository is checked when no explicit destination exists" {
  export FAKE_DEFAULT_MODE=thirdparty
  export FAKE_CWD_MODE=allowed
  assert_rejected issue comment 10 --body "blocked"
}

@test "GHG-08: cwd repository resolver is checked after an absent default" {
  export FAKE_CWD_MODE=thirdparty
  assert_rejected issue comment 10 --body "blocked"
}

@test "GHG-09: parses the equals form of --repo" {
  assert_rejected issue create --repo=thirdparty/fixture --title "blocked"
}

@test "GHG-10: rejects ambiguous repository flags and does not parse flags after --" {
  assert_rejected issue create --repo thirdparty/fixture --repo kappaseijin/fixture --title "blocked"
  assert_rejected issue create --repo --title "blocked"

  export FAKE_DEFAULT_MODE=allowed
  run_guard issue create --title "the text says --repo thirdparty/fixture" -- kappaseijin/fixture
  [ "$status" -eq 0 ]
}

@test "GHG-11: rejects non-GitHub hosts and malformed repository specs" {
  run env GH_HOST=evil.example "$LAUNCHER" issue create --repo kappaseijin/fixture --title blocked
  [ "$status" -ne 0 ]
  [ ! -s "$FAKE_WRITE_LOG" ]

  for spec in \
    'https://user@github.com/kappaseijin/fixture' \
    'https://github.com:443/kappaseijin/fixture' \
    'https://github.com./kappaseijin/fixture' \
    'github.com/kappaseijin/fixture/extra' \
    'not-a-repository'; do
    assert_rejected issue create --repo "$spec" --title blocked
  done

  assert_rejected --hostname evil.example issue create --repo kappaseijin/fixture --title blocked
  run_guard --hostname github.com issue create --repo kappaseijin/fixture --title allowed
  [ "$status" -eq 0 ]
}

@test "GHG-13: aliases and extension execution are fail-closed" {
  assert_rejected alias deploy
  assert_rejected extension exec deploy

  run_guard alias set deploy 'issue create --repo thirdparty/fixture'
  [ "$status" -eq 0 ]
  [ -s "$FAKE_READ_LOG" ]
}

@test "GHG-14: rejects POST API and both short field forms" {
  assert_rejected api --method POST repos/thirdparty/fixture/issues
  assert_rejected api -f q=1 repos/thirdparty/fixture
  assert_rejected api -Fq=1 repos/thirdparty/fixture
  assert_rejected api --raw-field=q=1 repos/thirdparty/fixture
}

@test "GHG-15: allows GET API and ordinary read operations" {
  run_guard api repos/thirdparty/fixture
  [ "$status" -eq 0 ]
  run_guard api --method GET -f q=1 repos/thirdparty/fixture
  [ "$status" -eq 0 ]
  run_guard issue view 10 --repo thirdparty/fixture
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$FAKE_READ_LOG" | tr -d ' ')" -eq 3 ]
  [ ! -s "$FAKE_WRITE_LOG" ]
}

@test "GHG-21: allows an explicit GraphQL query but rejects a mutation" {
  run_guard api graphql -f 'query=query TeamWorkAudit { viewer { login } }'
  [ "$status" -eq 0 ]
  grep -Fq 'api graphql -f query=query TeamWorkAudit { viewer { login } }' "$FAKE_READ_LOG"

  : > "$FAKE_READ_LOG"
  assert_rejected api graphql -f 'query=mutation Unsafe { updateIssue(input:{}) { clientMutationId } }'
  [ ! -s "$FAKE_READ_LOG" ]

  assert_rejected api graphql -X POST -f 'query=query TeamWorkAudit { viewer { login } }'
  assert_rejected api graphql -X GET -f 'query=query TeamWorkAudit { viewer { login } }'
}

@test "GHG-16: launcher neutralizes BASH_ENV before the guard starts" {
  export BASH_ENV="$TEST_SKILL_DIR/evil.bash"
  cat > "$BASH_ENV" <<'EVIL'
allowed_owner() { return 0; }
ALLOWED_HOST=evil.example
EVIL

  run env BASH_ENV="$BASH_ENV" "$LAUNCHER" issue create --repo thirdparty/fixture --title blocked
  [ "$status" -ne 0 ]
  [ ! -s "$FAKE_WRITE_LOG" ]
}

@test "GHG-17: launcher uses its fixed real gh instead of a PATH decoy" {
  local decoy="$TEST_SKILL_DIR/decoy/gh"
  mkdir -p "${decoy%/*}"
  cat > "$decoy" <<'DECOY'
#!/usr/bin/env bash
printf 'decoy\n' >> "$FAKE_WRITE_LOG"
exit 0
DECOY
  chmod +x "$decoy"

  run env PATH="${decoy%/*}:$PATH" "$LAUNCHER" issue create --repo kappaseijin/fixture --title allowed
  [ "$status" -eq 0 ]
  ! grep -Fq decoy "$FAKE_WRITE_LOG"
  grep -Fq 'issue create --repo kappaseijin/fixture --title allowed' "$FAKE_WRITE_LOG"
}

@test "GHG-19: allows the operational PR merge path only for an allowed owner" {
  run_guard pr merge 31 --repo kappaseijin/fixture --squash --delete-branch
  [ "$status" -eq 0 ]
  grep -Fq 'pr merge 31 --repo kappaseijin/fixture --squash --delete-branch' "$FAKE_WRITE_LOG"
}

@test "GHG-22: allows pr ready only for an allowed owner" {
  run_guard pr ready 58 --repo kappaseijin/fixture
  [ "$status" -eq 0 ]
  grep -Fq 'pr ready 58 --repo kappaseijin/fixture' "$FAKE_WRITE_LOG"

  : > "$FAKE_WRITE_LOG"
  assert_rejected pr ready 58 --repo thirdparty/fixture
}

@test "GHG-20: resolver failures and malformed output fail closed" {
  for mode in fail empty multiline invalid; do
    export FAKE_DEFAULT_MODE="$mode"
    export FAKE_CWD_MODE=empty
    assert_rejected issue comment 10 --body blocked
  done

  export FAKE_REQUIRE_NONINTERACTIVE=1
  for mode in fail empty multiline invalid mismatch; do
    export FAKE_EXPLICIT_MODE="$mode"
    assert_rejected issue comment 10 --repo kappaseijin/fixture --body blocked
  done

  export FAKE_DEFAULT_MODE=allowed
  export FAKE_EXPLICIT_MODE=allowed
  run_guard issue comment 10 --body allowed
  [ "$status" -eq 0 ]
  [ ! -s "$FAKE_PROMPT_LOG" ]
}

@test "GHG-24: allows one explicit rerun of a completed failed job" {
  run_guard run rerun "$FAKE_RUN_ID" --job "$FAKE_JOB_ID" --repo kappaseijin/fixture
  [ "$status" -eq 0 ]
  grep -Fq "run view $FAKE_RUN_ID --repo kappaseijin/fixture" "$FAKE_READ_LOG"
  grep -Fq "run rerun $FAKE_RUN_ID --job $FAKE_JOB_ID --repo kappaseijin/fixture" "$FAKE_WRITE_LOG"
}

@test "GHG-25: rerun target must be an existing completed failed job" {
  for mode in fail empty mismatch missing duplicate malformed success running; do
    export FAKE_RUN_MODE="$mode"
    assert_rejected run rerun "$FAKE_RUN_ID" --job "$FAKE_JOB_ID" --repo kappaseijin/fixture
  done
}

@test "GHG-26: rerun rejects ambiguous or non-canonical targets" {
  export FAKE_RUN_MODE=failed
  assert_rejected run rerun --job "$FAKE_JOB_ID" --repo kappaseijin/fixture
  assert_rejected run rerun "$FAKE_RUN_ID" "$FAKE_JOB_ID" --job "$FAKE_JOB_ID" --repo kappaseijin/fixture
  assert_rejected run rerun "$FAKE_RUN_ID" --job "$FAKE_JOB_ID" --job 99999 --repo kappaseijin/fixture
  assert_rejected run rerun "$FAKE_RUN_ID" --job "$FAKE_JOB_ID" --repo kappaseijin/fixture --repo kappaseijin/fixture
  assert_rejected run rerun abc --job "$FAKE_JOB_ID" --repo kappaseijin/fixture
  assert_rejected run rerun "$FAKE_RUN_ID" --job 0 --repo kappaseijin/fixture
  assert_rejected run rerun "$FAKE_RUN_ID" --job "$FAKE_JOB_ID" --repo https://github.com/kappaseijin/fixture
}

@test "GHG-27: rerun requires the minimum explicit job form" {
  export FAKE_RUN_MODE=failed
  assert_rejected run rerun "$FAKE_RUN_ID" --failed --repo kappaseijin/fixture
  assert_rejected run rerun "$FAKE_RUN_ID" --debug --job "$FAKE_JOB_ID" --repo kappaseijin/fixture
  assert_rejected run rerun "$FAKE_RUN_ID" --job "$FAKE_JOB_ID"
  assert_rejected run rerun "$FAKE_RUN_ID" --job "$FAKE_JOB_ID" --repo thirdparty/fixture
  assert_rejected run cancel "$FAKE_RUN_ID" --repo kappaseijin/fixture
}

@test "owner guard launcher rejects unresolved placeholders" {
  run "$LAUNCHER_TEMPLATE" issue view 10
  [ "$status" -ne 0 ]
}
