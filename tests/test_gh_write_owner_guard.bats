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
  export FAKE_ENV_LOG="$TEST_SKILL_DIR/fake-env.log"
  export FAKE_AUTH_TOKEN_MODE=fail
  export FAKE_API_USER_MODE=empty
  mkdir -p "$FAKE_BIN" "$TEST_SKILL_DIR/scratch"
  : > "$FAKE_WRITE_LOG"
  : > "$FAKE_READ_LOG"
  : > "$FAKE_PROMPT_LOG"
  : > "$FAKE_ENV_LOG"

  cat > "$REAL_GH" <<'FAKE_GH'
#!/usr/bin/env bash
set -euo pipefail

: "${FAKE_WRITE_LOG:?}"
printf 'GH_TOKEN=%s argv=%s\n' "${GH_TOKEN:-}" "$*" >> "${FAKE_ENV_LOG:-/dev/null}"

if [ "${1:-}" = auth ] && [ "${2:-}" = token ] && [ "${3:-}" = --user ]; then
  case "${FAKE_AUTH_TOKEN_MODE:-fail}" in
    known)
      case "${4:-}" in
        kappaseijin4claude) printf 'tok-claude\n' ;;
        kappaseijin4codex) printf 'tok-codex\n' ;;
        *) exit 1 ;;
      esac
      ;;
    empty) printf '\n' ;;
    fail) exit 1 ;;
  esac
  exit 0
fi

if [ "${1:-}" = api ] && [ "${2:-}" = user ]; then
  printf '%s\n' "$*" >> "$FAKE_READ_LOG"
  case "${FAKE_API_USER_MODE:-empty}" in
    personal) printf 'kappaseijin\n' ;;
    claude) printf 'kappaseijin4claude\n' ;;
    codex) printf 'kappaseijin4codex\n' ;;
    empty) : ;;
    fail) exit 1 ;;
  esac
  exit 0
fi
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
    # Real `gh repo set-default --view` shape when nothing has been set:
    # exit 0, empty stdout, the "No default remote repository..." notice on
    # stderr (herdr-agent-monitor#63-adjacent report, verified against a real
    # `gh` in an unset scratch repo). "empty" below is misleadingly named --
    # it actually simulates gh itself FAILING (exit 1), not gh succeeding
    # with nothing to report.
    unset) : ;;
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
    scientific)
      case " $* " in
        *' --template '*)
          printf '3.1950708068e+10\n9.5173637376e+10\tcompleted\tfailure\n'
          ;;
        *' --jq '*)
          printf '%s\n%s\tcompleted\tfailure\n' "$FAKE_RUN_ID" "$FAKE_JOB_ID"
          ;;
        *) exit 9 ;;
      esac
      ;;
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

# Writes a fake whoami.sh and points AGMSG_WHOAMI_SCRIPT at it. The JSON path
# mirrors the versioned machine-readable contract while the plain path keeps
# the human-output fixture available for the explicit contract test.
fake_whoami() {
  local line="$1" json="${2:-}" path="$TEST_SKILL_DIR/fake-whoami.sh"
  export FAKE_WHOAMI_HUMAN="$line"
  export FAKE_WHOAMI_JSON="$json"
  export FAKE_WHOAMI_MODE=output
  cat > "$path" <<'EOF'
#!/usr/bin/env bash
if [ "${2:-}" = --format ] && [ "${3:-}" = json ]; then
  case "${FAKE_WHOAMI_MODE:-output}" in
    output) printf '%s\n' "${FAKE_WHOAMI_JSON:-}" ;;
    empty) : ;;
    malformed) printf '%s\n' '{not-json' ;;
    fail) exit 1 ;;
    *) exit 2 ;;
  esac
else
  printf '%s\n' "${FAKE_WHOAMI_HUMAN:-}"
fi
EOF
  chmod +x "$path"
  export AGMSG_WHOAMI_SCRIPT="$path"
}

whoami_registration() {
  local runtime="$1" project="$2" name="$3" team="$4"
  printf '{"team":"%s","name":"%s","kind":"seat","role":"reviewer","registration":{"type":"%s","project":"%s"}}' \
    "$team" "$name" "$runtime" "$project"
}

whoami_json() {
  local runtime="$1" session_project="$2" registrations="$3" schema="${4:-1}"
  printf '{"schemaVersion":%s,"runtime":"%s","session":{"project":"%s"},"registrations":%s}' \
    "$schema" "$runtime" "$session_project" "$registrations"
}

accepting_personal_policy() {
  local policy="$TEST_SKILL_DIR/pr-account-policy.conf"
  printf 'map=%s=creator\ncreator_login=kappaseijin\n' "$(pwd -P)" > "$policy"
  export PR_ACCOUNT_POLICY="$policy"
  export FAKE_API_USER_MODE=personal
}

assert_pr_write_rejected_without_static_fallback() {
  local -a args=("$@")
  : > "$FAKE_WRITE_LOG"
  : > "$FAKE_READ_LOG"
  run env -u GH_CONFIG_DIR -u GH_TOKEN -u GITHUB_TOKEN "$LAUNCHER" "${args[@]}"
  [ "$status" -ne 0 ]
  [ ! -s "$FAKE_WRITE_LOG" ]
  run grep -Fq 'api user' "$FAKE_READ_LOG"
  [ "$status" -ne 0 ]
}

@test "GHG-01: rejects an issue writer targeting a third-party owner" {
  assert_rejected issue create --repo thirdparty/fixture --title "blocked"
}

@test "GHG-02: allows an owner PR review and preserves the original argv" {
  run env -u GH_TOKEN -u GITHUB_TOKEN GH_CONFIG_DIR=/somewhere "$LAUNCHER" pr review 42 --repo kappaseijin/fixture --approve
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

# `gh repo set-default --view` on a project that never ran `gh repo
# set-default` exits 0 with an empty stdout (the "No default remote
# repository has been set" notice goes to stderr, which the guard discards).
# resolve_default_repository() used to treat that shape the same as "the
# default IS set, but to something unauthorized" (both returned 2, which the
# caller dies on) instead of the same as "gh itself failed" (return 1, which
# falls through to resolve_cwd_repository -- the correct behavior for a
# project that simply never configured a default). Reported independently by
# herdr-agent-monitor and scale_exporter; reproduced directly against a real
# `gh` in an unset scratch repo before writing this fixture.
@test "GHG-08b: an unset default (gh exits 0, empty output) falls through to cwd, not a die" {
  export FAKE_DEFAULT_MODE=unset
  export FAKE_CWD_MODE=allowed
  run_guard issue comment 10 --body "allowed"
  [ "$status" -eq 0 ]
  grep -Fq 'issue comment 10 --body allowed' "$FAKE_WRITE_LOG"
}

@test "GHG-08c: a multi-line default view falls through to cwd, not a die" {
  export FAKE_DEFAULT_MODE=multiline
  export FAKE_CWD_MODE=allowed
  run_guard issue comment 10 --body "allowed"
  [ "$status" -eq 0 ]
  grep -Fq 'issue comment 10 --body allowed' "$FAKE_WRITE_LOG"
}

# A default that DID resolve cleanly (single line, non-empty) but names a
# disallowed owner must still die -- unlike GHG-08b/08c, there is a real
# answer here and it says no, so falling through to cwd would let a
# same-directory project silently override an explicit unauthorized default.
@test "GHG-08d: a cleanly-resolved but disallowed default still dies (does not fall through)" {
  export FAKE_DEFAULT_MODE=thirdparty
  export FAKE_CWD_MODE=allowed
  assert_rejected issue comment 10 --body "blocked"
}

# --- Account selection (Issue #221) ---

@test "GHG-P1: selects the expected account for one Claude registration" {
  local project registration json
  project="$(pwd -P)"
  registration="$(whoami_registration claude-code "$project" alice myteam)"
  json="$(whoami_json claude-code "$project" "[$registration]")"
  fake_whoami "agent=alice teams=myteam type=claude-code project=$project" "$json"
  export FAKE_AUTH_TOKEN_MODE=known
  run env -u GH_CONFIG_DIR -u GH_TOKEN -u GITHUB_TOKEN "$LAUNCHER" pr create --repo kappaseijin/fixture --title allowed
  [ "$status" -eq 0 ]
  grep -q 'argv=auth token --user kappaseijin4claude' "$FAKE_ENV_LOG"
  grep -q '^GH_TOKEN=tok-claude argv=pr create' "$FAKE_ENV_LOG"
}

@test "GHG-P2: selects the expected account for one Codex registration" {
  local project registration json
  project="$(pwd -P)"
  registration="$(whoami_registration codex "$project" alice myteam)"
  json="$(whoami_json codex "$project" "[$registration]")"
  fake_whoami "agent=alice teams=myteam type=codex project=$project" "$json"
  export FAKE_AUTH_TOKEN_MODE=known
  run env -u GH_CONFIG_DIR -u GH_TOKEN -u GITHUB_TOKEN "$LAUNCHER" pr create --repo kappaseijin/fixture --title allowed
  [ "$status" -eq 0 ]
  grep -q '^GH_TOKEN=tok-codex argv=pr create' "$FAKE_ENV_LOG"
}

@test "GHG-P3: explicit credentials are preserved and selector is skipped" {
  local project registration json credential value
  project="$(pwd -P)"
  registration="$(whoami_registration claude-code "$project" alice myteam)"
  json="$(whoami_json claude-code "$project" "[$registration]")"
  fake_whoami "agent=alice teams=myteam type=claude-code project=$project" "$json"
  export FAKE_AUTH_TOKEN_MODE=known

  for credential in GH_CONFIG_DIR GH_TOKEN GITHUB_TOKEN; do
    case "$credential" in
      GH_CONFIG_DIR) value=/somewhere ;;
      GH_TOKEN) value=explicit-token ;;
      GITHUB_TOKEN) value=explicit-token ;;
    esac
    : > "$FAKE_ENV_LOG"
    run env -u GH_CONFIG_DIR -u GH_TOKEN -u GITHUB_TOKEN "$credential=$value" "$LAUNCHER" pr create --repo kappaseijin/fixture --title allowed
    [ "$status" -eq 0 ]
    run grep -q 'auth token' "$FAKE_ENV_LOG"
    [ "$status" -ne 0 ]
  done
}

@test "GHG-P4: every non-exact identity rejects all PR writes before static policy" {
  local project registration json state operation
  project="$(pwd -P)"
  accepting_personal_policy

  for state in not_joined multiple suggest; do
    case "$state" in
      not_joined)
        json="$(whoami_json claude-code "$project" '[]')"
        fake_whoami 'not_joined=true available_teams=myteam' "$json"
        ;;
      multiple)
        registration="$(whoami_registration claude-code "$project" alice myteam)"
        registration="$(whoami_registration claude-code "$project" bob myteam),$registration"
        json="$(whoami_json claude-code "$project" "[$registration]")"
        fake_whoami "agent=alice teams=myteam type=claude-code multiple=true project=$project" "$json"
        ;;
      suggest)
        json="$(whoami_json claude-code "$project" '[]')"
        fake_whoami 'suggest=true agents=alice teams=myteam type=claude-code project=/elsewhere available_teams=myteam' "$json"
        ;;
    esac

    for operation in create comment review; do
      case "$operation" in
        create) assert_pr_write_rejected_without_static_fallback pr create --repo kappaseijin/fixture --title blocked ;;
        comment) assert_pr_write_rejected_without_static_fallback pr comment 10 --repo kappaseijin/fixture --body blocked ;;
        review) assert_pr_write_rejected_without_static_fallback pr review 10 --repo kappaseijin/fixture --approve ;;
      esac
    done
  done
}

@test "GHG-P5: JSON registration count rejects same-name ambiguity" {
  local project registration_one registration_two json
  project="$(pwd -P)"
  registration_one="$(whoami_registration codex "$project" alice myteam)"
  registration_two="$(whoami_registration codex "$project" alice otherteam)"
  json="$(whoami_json codex "$project" "[$registration_one,$registration_two]")"
  fake_whoami "agent=alice teams=myteam,otherteam type=codex project=$project" "$json"
  accepting_personal_policy
  export FAKE_AUTH_TOKEN_MODE=known
  assert_pr_write_rejected_without_static_fallback pr create --repo kappaseijin/fixture --title blocked
}

@test "GHG-P6: malformed, mismatched, unsupported, and unavailable identities reject" {
  local project registration json fixture
  project="$(pwd -P)"
  registration="$(whoami_registration claude-code "$project" alice myteam)"
  accepting_personal_policy

  for fixture in schema empty session runtime unsupported; do
    case "$fixture" in
      schema) json="$(whoami_json claude-code "$project" "[$registration]" 2)" ;;
      empty) json="$(whoami_json claude-code "$project" '[]')" ;;
      session) json="$(whoami_json claude-code "$TEST_SKILL_DIR/other" "[$registration]")" ;;
      runtime)
        json="$(whoami_json claude-code "$project" "[$(whoami_registration codex "$project" alice myteam)]")"
        ;;
      unsupported)
        json="$(whoami_json gemini "$project" "[$(whoami_registration gemini "$project" alice myteam)]")"
        ;;
    esac
    fake_whoami "agent=alice teams=myteam type=claude-code project=$project" "$json"
    assert_pr_write_rejected_without_static_fallback pr create --repo kappaseijin/fixture --title blocked
  done

  fake_whoami "agent=alice teams=myteam type=claude-code project=$project" '{}'
  for mode in malformed empty fail; do
    export FAKE_WHOAMI_MODE="$mode"
    assert_pr_write_rejected_without_static_fallback pr create --repo kappaseijin/fixture --title blocked
  done

  fake_whoami "agent=alice teams=myteam type=claude-code project=$project" "$(whoami_json claude-code "$project" "[$registration]")"
  for mode in fail empty; do
    export FAKE_AUTH_TOKEN_MODE="$mode"
    assert_pr_write_rejected_without_static_fallback pr create --repo kappaseijin/fixture --title blocked
  done
}

@test "GHG-P7: non-PR writes remain not_applicable to account selection" {
  fake_whoami 'not_joined=true available_teams=myteam' '{not-json'
  export FAKE_AUTH_TOKEN_MODE=known
  run env -u GH_CONFIG_DIR -u GH_TOKEN -u GITHUB_TOKEN "$LAUNCHER" issue create --repo kappaseijin/fixture --title allowed
  [ "$status" -eq 0 ]
  grep -Fq 'issue create --repo kappaseijin/fixture --title allowed' "$FAKE_WRITE_LOG"
  run grep -q 'auth token' "$FAKE_ENV_LOG"
  [ "$status" -ne 0 ]
}

@test "GHG-P8: selected Claude account bypasses a conflicting static policy" {
  local project registration json policy
  project="$(pwd -P)"
  registration="$(whoami_registration claude-code "$project" alice myteam)"
  json="$(whoami_json claude-code "$project" "[$registration]")"
  fake_whoami "agent=alice teams=myteam type=claude-code project=$project" "$json"
  policy="$TEST_SKILL_DIR/pr-account-policy.conf"
  printf 'map=%s=creator\ncreator_login=kappaseijin4codex\n' "$project" > "$policy"
  export PR_ACCOUNT_POLICY="$policy"
  export FAKE_AUTH_TOKEN_MODE=known
  run env -u GH_CONFIG_DIR -u GH_TOKEN -u GITHUB_TOKEN "$LAUNCHER" pr create --repo kappaseijin/fixture --title allowed
  [ "$status" -eq 0 ]
  grep -q '^GH_TOKEN=tok-claude argv=pr create' "$FAKE_ENV_LOG"
}

@test "GHG-P9: token lookup failure is unresolved, not a static-policy fallback" {
  local project registration json policy
  project="$(pwd -P)"
  registration="$(whoami_registration claude-code "$project" alice myteam)"
  json="$(whoami_json claude-code "$project" "[$registration]")"
  fake_whoami "agent=alice teams=myteam type=claude-code project=$project" "$json"
  policy="$TEST_SKILL_DIR/pr-account-policy.conf"
  printf 'map=%s=creator\ncreator_login=kappaseijin\n' "$project" > "$policy"
  export PR_ACCOUNT_POLICY="$policy"
  export FAKE_AUTH_TOKEN_MODE=fail
  assert_pr_write_rejected_without_static_fallback pr create --repo kappaseijin/fixture --title blocked
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

@test "GHG-23: resolver failures report a guard diagnostic" {
  export FAKE_DEFAULT_MODE=fail
  export FAKE_CWD_MODE=empty
  run_guard issue close 10 --comment blocked
  [ "$status" -ne 0 ]
  printf '%s\n' "$output" | grep -Fq 'error: gh-write-owner-guard: cwd repository cannot be resolved or is not allowed'
  [ ! -s "$FAKE_WRITE_LOG" ]

  export FAKE_EXPLICIT_MODE=empty
  run_guard issue close 10 --repo kappaseijin/fixture --comment blocked
  [ "$status" -ne 0 ]
  printf '%s\n' "$output" | grep -Fq 'error: gh-write-owner-guard: explicit repository cannot be resolved or is not allowed'
  [ ! -s "$FAKE_WRITE_LOG" ]
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

@test "GHG-28: preserves realistic 11-digit run and job IDs" {
  export FAKE_RUN_MODE=scientific
  export FAKE_RUN_ID=31950708068
  export FAKE_JOB_ID=95173637376
  run_guard run rerun "$FAKE_RUN_ID" --job "$FAKE_JOB_ID" --repo kappaseijin/fixture
  [ "$status" -eq 0 ]
  grep -Fq "run view $FAKE_RUN_ID --repo kappaseijin/fixture" "$FAKE_READ_LOG"
  grep -Fq "run rerun $FAKE_RUN_ID --job $FAKE_JOB_ID --repo kappaseijin/fixture" "$FAKE_WRITE_LOG"
}

@test "GHG-29: treats large decimal IDs as opaque and rejects injection-shaped IDs" {
  export FAKE_RUN_MODE=failed
  export FAKE_RUN_ID=123456789012345678901234567890
  export FAKE_JOB_ID=987654321098765432109876543210
  run_guard run rerun "$FAKE_RUN_ID" --job "$FAKE_JOB_ID" --repo kappaseijin/fixture
  [ "$status" -eq 0 ]
  grep -Fq "run rerun $FAKE_RUN_ID --job $FAKE_JOB_ID --repo kappaseijin/fixture" "$FAKE_WRITE_LOG"
  : > "$FAKE_WRITE_LOG"

  for malicious_id in 1e3 +123 '123;touch /tmp/guard-should-not-run' '123$(id)'; do
    assert_rejected run rerun "$malicious_id" --job "$FAKE_JOB_ID" --repo kappaseijin/fixture
  done
}

@test "GHG-30: allows label create only for an allowed repository owner" {
  run_guard label create blocked:reproduction --repo kappaseijin/fixture --color B60205 --description "runtime state"
  [ "$status" -eq 0 ]
  grep -Fq 'label create blocked:reproduction --repo kappaseijin/fixture --color B60205 --description runtime state' "$FAKE_WRITE_LOG"

  : > "$FAKE_WRITE_LOG"
  run_guard label create blocked:reproduction --repo thirdparty/fixture --color B60205 --description "runtime state"
  [ "$status" -ne 0 ]
  printf '%s\n' "$output" | grep -Fq 'repository owner or host is not allowed'
  [ ! -s "$FAKE_WRITE_LOG" ]
}

@test "GHG-31: keeps label delete rejected as an unclassified write" {
  run_guard label delete blocked:reproduction --repo kappaseijin/fixture
  [ "$status" -ne 0 ]
  printf '%s\n' "$output" | grep -Fq 'operation is not classified as a safe read or destination-checked write: label delete'
  [ ! -s "$FAKE_WRITE_LOG" ]
}

@test "owner guard launcher rejects unresolved placeholders" {
  run "$LAUNCHER_TEMPLATE" issue view 10
  [ "$status" -ne 0 ]
}
