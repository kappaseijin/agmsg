#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env

  export TEAM="pilot-team"
  export PILOT_AGENT="agmsg_pm_pilot_claude"
  export PILOT_TYPE="claude-code"
  export POLICY_VERSION="pm-pilot-pretool-v1"
  export PROVIDER_COMMIT="0b2117c5f91f7950cc196e52edb188748adfa50a"

  export LAUNCHER="$SCRIPTS/pilot-launcher.sh"
  export BINDING_HELPER="$SCRIPTS/lib/pilot-binding.js"
  export SESSION_IDENTITY="$SCRIPTS/session-identity.js"

  [ -f "$LAUNCHER" ]
  [ -f "$BINDING_HELPER" ]
  [ -f "$SESSION_IDENTITY" ]

  export PROJ="$TEST_SKILL_DIR/project"
  mkdir -p "$PROJ/.claude"
  printf '%s\n' '{"hooks":{}}' > "$PROJ/.claude/settings.local.json"

  # Project identity is intentionally canonical in G4-A.
  export CANONICAL_PROJ
  CANONICAL_PROJ="$(canonical_path "$PROJ")"

  export OTHER_PROJ="$TEST_SKILL_DIR/other-project"
  mkdir -p "$OTHER_PROJ"

  # The real pilot guard (#404) is copied with scripts/. G4-B is a separate
  # PR, so this suite still installs a minimal executable broker placeholder
  # for tests which need to exercise the launcher beyond that boundary.
  [ -x "$SCRIPTS/pm-pilot-pretool-guard" ]

  cat > "$SCRIPTS/p2-consumer-broker.sh" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$SCRIPTS/p2-consumer-broker.sh"

  export STUB_BIN="$TEST_SKILL_DIR/stub-bin"
  mkdir -p "$STUB_BIN"

  export FAKE_CLAUDE_LOG="$TEST_SKILL_DIR/fake-claude.jsonl"
  export FAKE_CLAUDE_IDENTITY_LOG="$TEST_SKILL_DIR/fake-claude-identity.jsonl"
  : > "$FAKE_CLAUDE_LOG"
  : > "$FAKE_CLAUDE_IDENTITY_LOG"

  install_fake_claude

  export PATH="$STUB_BIN:$PATH"

  # Source only the lock ABI needed for test-side claim inspection.
  # shellcheck disable=SC1090
  source "$SCRIPTS/lib/actas-lock.sh"
}

teardown() {
  teardown_test_env
}

canonical_path() {
  node -e '
    "use strict";

    const fs = require("fs");

    process.stdout.write(
      fs.realpathSync.native(
        process.argv[1],
      ),
    );
  ' "$1"
}

install_fake_claude() {
  cat > "$STUB_BIN/claude" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

: "${FAKE_CLAUDE_LOG:?}"

export FAKE_CLAUDE_ACTUAL_PID="$$"

node - "$@" <<'NODE'
'use strict';

const fs = require('fs');

const record = {
  actualPid: process.env.FAKE_CLAUDE_ACTUAL_PID || '',
  argv: process.argv.slice(2),
  cwd: process.cwd(),

  sessionId:
    process.env.AGMSG_PM_PILOT_SESSION_ID || '',

  bindingFile:
    process.env.AGMSG_PM_BINDING_FILE || '',

  team:
    process.env.AGMSG_PM_TEAM || '',

  agent:
    process.env.AGMSG_PM_AGENT || '',

  type:
    process.env.AGMSG_PM_TYPE || '',

  processPid:
    process.env.AGMSG_PM_PROCESS_PID || '',

  generation:
    process.env.AGMSG_PM_PROCESS_GENERATION || '',

  processStart:
    process.env.AGMSG_PM_PROCESS_START || '',

  teamsDir:
    process.env.AGMSG_PM_TEAMS_DIR || '',

  claimFile:
    process.env.AGMSG_PM_CLAIM_FILE || '',

  guardPath:
    process.env.AGMSG_PM_GUARD_PATH || '',

  brokerPath:
    process.env.AGMSG_PM_BROKER_PATH || '',
};

fs.appendFileSync(
  process.env.FAKE_CLAUDE_LOG,
  `${JSON.stringify(record)}\n`,
);
NODE

if [ "${FAKE_CLAUDE_VALIDATE_IDENTITY:-0}" = "1" ]; then
  : "${FAKE_CLAUDE_IDENTITY_LOG:?}"
  : "${SKILL_DIR:?}"
  : "${AGMSG_PM_PILOT_SESSION_ID:?}"

  node -e '
    "use strict";

    process.stdout.write(
      JSON.stringify({
        session_id:
          process.env.AGMSG_PM_PILOT_SESSION_ID,

        cwd:
          process.cwd(),

        tool_name:
          "Bash",

        tool_use_id:
          "pilot-schema-compat",

        tool_input: {
          command: "true",
        },
      }),
    );
  ' |
    node \
      "$SKILL_DIR/scripts/session-identity.js" \
      >> "$FAKE_CLAUDE_IDENTITY_LOG"
fi

if [ -n "${FAKE_CLAUDE_ENV_LOG:-}" ]; then
  env | grep '^AGMSG_PM_' > "$FAKE_CLAUDE_ENV_LOG" || true
fi

exit "${FAKE_CLAUDE_EXIT_CODE:-0}"
EOF

  chmod +x "$STUB_BIN/claude"
}

join_pilot() {
  AGMSG_RESOLVE_PROJECT=0 \
    bash "$SCRIPTS/join.sh" \
      "$TEAM" \
      "$PILOT_AGENT" \
      "$PILOT_TYPE" \
      "$PROJ" \
      --role manager \
      --kind seat \
      >/dev/null
}

run_fresh() {
  bash "$LAUNCHER" \
    --team "$TEAM" \
    --project "$PROJ" \
    --fresh
}

run_resume() {
  bash "$LAUNCHER" \
    --team "$TEAM" \
    --project "$PROJ" \
    --resume
}

seat_dir() {
  printf '%s/run/pilot/%s__%s' \
    "$TEST_SKILL_DIR" \
    "$TEAM" \
    "$PILOT_AGENT"
}

state_file() {
  printf '%s/state.json' \
    "$(seat_dir)"
}

bindings_dir() {
  printf '%s/bindings' \
    "$(seat_dir)"
}

binding_file() {
  printf '%s/%s.json' \
    "$(bindings_dir)" \
    "$1"
}

ensure_pilot_runtime_dirs() {
  mkdir -p \
    "$(bindings_dir)"
}

pilot_claim_file() {
  actas_lock_path \
    "$TEAM" \
    "$PILOT_AGENT"
}

fake_launch_count() {
  if [ ! -s "$FAKE_CLAUDE_LOG" ]; then
    printf '0\n'
    return 0
  fi

  wc -l < "$FAKE_CLAUDE_LOG" |
    tr -d '[:space:]'
}

json_file_field() {
  node -e '
    "use strict";

    const fs = require("fs");

    const value =
      JSON.parse(
        fs.readFileSync(
          process.argv[1],
          "utf8",
        ),
      );

    const field =
      process.argv[2];

    const result =
      value[field];

    if (
      typeof result !== "string" &&
      typeof result !== "number"
    ) {
      process.exit(1);
    }

    process.stdout.write(
      String(result),
    );
  ' "$1" "$2"
}

json_log_field() {
  node -e '
    "use strict";

    const fs = require("fs");

    const lines =
      fs.readFileSync(
        process.argv[1],
        "utf8",
      )
        .trim()
        .split(/\n/u)
        .filter(Boolean);

    const index =
      Number(process.argv[2]) - 1;

    if (
      !Number.isSafeInteger(index) ||
      index < 0 ||
      index >= lines.length
    ) {
      process.exit(1);
    }

    const value =
      JSON.parse(lines[index]);

    const result =
      value[process.argv[3]];

    if (
      typeof result !== "string" &&
      typeof result !== "number"
    ) {
      process.exit(1);
    }

    process.stdout.write(
      String(result),
    );
  ' "$1" "$2" "$3"
}

mutate_json_field() {
  node -e '
    "use strict";

    const fs = require("fs");

    const file =
      process.argv[1];

    const field =
      process.argv[2];

    const value =
      process.argv[3];

    const object =
      JSON.parse(
        fs.readFileSync(
          file,
          "utf8",
        ),
      );

    object[field] = value;

    fs.writeFileSync(
      file,
      `${JSON.stringify(object)}\n`,
    );
  ' "$1" "$2" "$3"
}

duplicate_pilot_registration() {
  local config="$TEST_SKILL_DIR/teams/$TEAM/config.json"

  node -e '
    "use strict";

    const fs = require("fs");

    const file =
      process.argv[1];

    const agent =
      process.argv[2];

    const config =
      JSON.parse(
        fs.readFileSync(
          file,
          "utf8",
        ),
      );

    const record =
      config.agents[agent];

    if (
      !record ||
      !Array.isArray(record.registrations) ||
      record.registrations.length !== 1
    ) {
      process.exit(1);
    }

    record.registrations.push({
      ...record.registrations[0],
    });

    fs.writeFileSync(
      file,
      `${JSON.stringify(config)}\n`,
    );
  ' "$config" "$PILOT_AGENT"
}

sha256_test_file() {
  node -e '
    "use strict";

    const crypto = require("crypto");
    const fs = require("fs");

    process.stdout.write(
      crypto
        .createHash("sha256")
        .update(
          fs.readFileSync(
            process.argv[1],
          ),
        )
        .digest("hex"),
    );
  ' "$1"
}

assert_no_fake_launch() {
  [ "$(fake_launch_count)" -eq 0 ]
}

assert_no_claim_file() {
  [ ! -e "$(pilot_claim_file)" ]
}

remove_stale_claim() {
  rm -f \
    "$(pilot_claim_file)"
}

assert_resume_rejected_without_new_launch() {
  local before

  before="$(fake_launch_count)"

  run run_resume

  [ "$status" -ne 0 ]
  [ "$(fake_launch_count)" -eq "$before" ]
}

@test "pilot launcher: first fresh publishes generation 1 and execs native claude with a new session id" {
  join_pilot

  run run_fresh

  [ "$status" -eq 0 ]
  [ "$(fake_launch_count)" -eq 1 ]

  [ -f "$(binding_file 1)" ]
  [ -f "$(state_file)" ]

  local session_id generation provider policy state_generation state_binding
  local logged_session logged_generation logged_team logged_agent logged_type
  local logged_project binding_project

  session_id="$(
    json_file_field \
      "$(binding_file 1)" \
      sessionId
  )"

  generation="$(
    json_file_field \
      "$(binding_file 1)" \
      generation
  )"

  provider="$(
    json_file_field \
      "$(binding_file 1)" \
      providerCommit
  )"

  policy="$(
    json_file_field \
      "$(binding_file 1)" \
      policyVersion
  )"

  binding_project="$(
    json_file_field \
      "$(binding_file 1)" \
      project
  )"

  state_generation="$(
    json_file_field \
      "$(state_file)" \
      latestGeneration
  )"

  state_binding="$(
    json_file_field \
      "$(state_file)" \
      latestBinding
  )"

  logged_session="$(
    json_log_field \
      "$FAKE_CLAUDE_LOG" \
      1 \
      sessionId
  )"

  logged_generation="$(
    json_log_field \
      "$FAKE_CLAUDE_LOG" \
      1 \
      generation
  )"

  logged_team="$(
    json_log_field \
      "$FAKE_CLAUDE_LOG" \
      1 \
      team
  )"

  logged_agent="$(
    json_log_field \
      "$FAKE_CLAUDE_LOG" \
      1 \
      agent
  )"

  logged_type="$(
    json_log_field \
      "$FAKE_CLAUDE_LOG" \
      1 \
      type
  )"

  logged_project="$(
    json_log_field \
      "$FAKE_CLAUDE_LOG" \
      1 \
      cwd
  )"

  [ -n "$session_id" ]
  [ "$generation" = "1" ]
  [ "$state_generation" = "1" ]
  [ "$state_binding" = "$(binding_file 1)" ]

  [ "$provider" = "$PROVIDER_COMMIT" ]
  [ "$policy" = "$POLICY_VERSION" ]

  # Project identity is intentionally canonical, unlike runtime state paths.
  [ "$binding_project" = "$CANONICAL_PROJ" ]
  [ "$logged_project" = "$CANONICAL_PROJ" ]

  [ "$logged_session" = "$session_id" ]
  [ "$logged_generation" = "1" ]
  [ "$logged_team" = "$TEAM" ]
  [ "$logged_agent" = "$PILOT_AGENT" ]
  [ "$logged_type" = "$PILOT_TYPE" ]

  node -e '
    "use strict";

    const fs = require("fs");

    const line =
      fs.readFileSync(
        process.argv[1],
        "utf8",
      )
        .trim();

    const record =
      JSON.parse(line);

    if (
      record.argv.length !== 4 ||
      record.argv[0] !== "--session-id" ||
      record.argv[1] !== process.argv[2] ||
      record.argv[2] !== "--settings" ||
      record.argv[3] !== process.argv[3]
    ) {
      process.exit(1);
    }
  ' \
    "$FAKE_CLAUDE_LOG" \
    "$session_id" \
    "$CANONICAL_PROJ/.claude/settings.local.json"
}

@test "pilot launcher: inherited AGMSG_PM_* is cleared and run logs are created next to the binding" {
  join_pilot

  local env_log="$TEST_SKILL_DIR/fake-claude-env.txt"

  # Negative control: values a live PM session would leak into the pilot.
  run env \
    FAKE_CLAUDE_ENV_LOG="$env_log" \
    AGMSG_PM_EXECUTIONS_FILE=/outside/executions.jsonl \
    AGMSG_PM_DECISIONS_FILE=/outside/decisions.jsonl \
    AGMSG_PM_QQZZ=1 \
    bash "$LAUNCHER" --team "$TEAM" --project "$PROJ" --fresh

  [ "$status" -eq 0 ] || { printf '%s\n' "$output"; return 1; }
  [ "$(fake_launch_count)" -eq 1 ]
  [ -s "$env_log" ]

  # Positive control: a variable the launcher exports is visible.
  grep -q "^AGMSG_PM_BINDING_FILE=$(binding_file 1)\$" "$env_log"

  grep -q '^AGMSG_PM_QQZZ=' "$env_log" && return 1
  grep -q '/outside/' "$env_log" && return 1

  grep -q "^AGMSG_PM_EXECUTIONS_FILE=$(bindings_dir)/1.executions.jsonl\$" "$env_log"
  grep -q "^AGMSG_PM_DECISIONS_FILE=$(bindings_dir)/1.decisions.jsonl\$" "$env_log"

  [ -f "$(bindings_dir)/1.executions.jsonl" ] && [ ! -L "$(bindings_dir)/1.executions.jsonl" ]
  [ -f "$(bindings_dir)/1.decisions.jsonl" ] && [ ! -L "$(bindings_dir)/1.decisions.jsonl" ]
}

@test "pilot launcher: a pre-planted run log for the new generation fails closed before exec" {
  join_pilot
  ensure_pilot_runtime_dirs

  ln -s "$TEST_SKILL_DIR/elsewhere.jsonl" "$(bindings_dir)/1.decisions.jsonl"

  run run_fresh

  [ "$status" -ne 0 ]
  assert_no_fake_launch
  assert_no_claim_file
  [ ! -e "$TEST_SKILL_DIR/elsewhere.jsonl" ]
}

@test "pilot launcher: roster with no matching pilot seat is rejected before claim or exec" {
  run run_fresh

  [ "$status" -ne 0 ]
  assert_no_fake_launch
  assert_no_claim_file
}

@test "pilot launcher: roster with more than one matching registration is rejected before claim or exec" {
  join_pilot
  duplicate_pilot_registration

  run run_fresh

  [ "$status" -ne 0 ]
  assert_no_fake_launch
  assert_no_claim_file
}

@test "pilot launcher: another live actas owner is rejected before binding publication" {
  skip_on_windows \
    "pilot launcher composite PID liveness under Git Bash"

  join_pilot

  local other_session other_owner

  other_session="11111111-1111-4111-8111-111111111111"
  other_owner="${other_session}.$$"

  printf '%s\n' \
    "$other_owner" \
    > "$(pilot_claim_file)"

  run run_fresh

  [ "$status" -ne 0 ]
  assert_no_fake_launch

  [ "$(
    cat "$(pilot_claim_file)"
  )" = "$other_owner" ]

  [ ! -e "$(binding_file 1)" ]
}

@test "pilot launcher: missing profile guard or broker fails closed before claim" {
  join_pilot

  local target backup

  for target in \
    "$PROJ/.claude/settings.local.json" \
    "$SCRIPTS/pm-pilot-pretool-guard" \
    "$SCRIPTS/p2-consumer-broker.sh"
  do
    backup="${target}.test-backup"

    mv "$target" "$backup"

    run run_fresh

    [ "$status" -ne 0 ]
    assert_no_fake_launch
    assert_no_claim_file

    mv "$backup" "$target"
  done
}

@test "pilot launcher: resume preserves session id and advances immutable generation" {
  join_pilot

  run run_fresh
  [ "$status" -eq 0 ]

  local first_session first_generation

  first_session="$(
    json_file_field \
      "$(binding_file 1)" \
      sessionId
  )"

  first_generation="$(
    json_file_field \
      "$(binding_file 1)" \
      generation
  )"

  [ "$first_generation" = "1" ]

  run run_resume

  [ "$status" -eq 0 ]
  [ "$(fake_launch_count)" -eq 2 ]

  local second_session second_generation state_generation

  second_session="$(
    json_file_field \
      "$(binding_file 2)" \
      sessionId
  )"

  second_generation="$(
    json_file_field \
      "$(binding_file 2)" \
      generation
  )"

  state_generation="$(
    json_file_field \
      "$(state_file)" \
      latestGeneration
  )"

  [ "$second_session" = "$first_session" ]
  [ "$second_generation" = "2" ]
  [ "$state_generation" = "2" ]

  [ -f "$(binding_file 1)" ]
  [ -f "$(binding_file 2)" ]

  [ "$(
    json_file_field \
      "$(binding_file 1)" \
      sessionId
  )" = "$first_session" ]

  node -e '
    "use strict";

    const fs = require("fs");

    const lines =
      fs.readFileSync(
        process.argv[1],
        "utf8",
      )
        .trim()
        .split(/\n/u)
        .map(JSON.parse);

    if (lines.length !== 2) {
      process.exit(1);
    }

    const first =
      lines[0];

    const second =
      lines[1];

    if (
      first.argv[0] !== "--session-id" ||
      first.argv[1] !== process.argv[2] ||
      second.argv[0] !== "--resume" ||
      second.argv[1] !== process.argv[2]
    ) {
      process.exit(1);
    }
  ' \
    "$FAKE_CLAUDE_LOG" \
    "$first_session"
}

@test "pilot launcher: resume rejects team agent type and project mismatch in the latest binding" {
  join_pilot

  run run_fresh
  [ "$status" -eq 0 ]

  remove_stale_claim

  local latest="$TEST_SKILL_DIR/latest-original.json"
  local field value

  cp \
    "$(binding_file 1)" \
    "$latest"

  for field in team agent type project
  do
    cp \
      "$latest" \
      "$(binding_file 1)"

    case "$field" in
      team)
        value="other-team"
        ;;

      agent)
        value="other-agent"
        ;;

      type)
        value="other-type"
        ;;

      project)
        value="$OTHER_PROJ"
        ;;

      *)
        return 1
        ;;
    esac

    mutate_json_field \
      "$(binding_file 1)" \
      "$field" \
      "$value"

    assert_resume_rejected_without_new_launch
    assert_no_claim_file
  done
}

@test "pilot launcher: resume rejects rollback to an older generation" {
  join_pilot

  run run_fresh
  [ "$status" -eq 0 ]

  run run_resume
  [ "$status" -eq 0 ]

  [ -f "$(binding_file 1)" ]
  [ -f "$(binding_file 2)" ]

  remove_stale_claim

  node -e '
    "use strict";

    const fs = require("fs");

    const file =
      process.argv[1];

    const binding =
      process.argv[2];

    const state =
      JSON.parse(
        fs.readFileSync(
          file,
          "utf8",
        ),
      );

    state.latestGeneration = "1";
    state.latestBinding = binding;

    fs.writeFileSync(
      file,
      `${JSON.stringify(state)}\n`,
    );
  ' \
    "$(state_file)" \
    "$(binding_file 1)"

  local before
  before="$(fake_launch_count)"

  run run_resume

  [ "$status" -ne 0 ]
  [ "$(fake_launch_count)" -eq "$before" ]
  assert_no_claim_file
}

@test "pilot launcher: resume rejects a previous binding whose PID and pidStart are still live" {
  skip_on_windows \
    "pilot launcher process-start liveness under Git Bash"

  join_pilot

  run run_fresh
  [ "$status" -eq 0 ]

  remove_stale_claim

  local live_start

  live_start="$(
    node \
      "$SESSION_IDENTITY" \
      --process-start \
      "$$"
  )"

  [ -n "$live_start" ]

  mutate_json_field \
    "$(binding_file 1)" \
    pid \
    "$$"

  mutate_json_field \
    "$(binding_file 1)" \
    pidStart \
    "$live_start"

  assert_resume_rejected_without_new_launch
  assert_no_claim_file
}

@test "pilot launcher: resume rejects profile guard and broker digest changes" {
  join_pilot

  run run_fresh
  [ "$status" -eq 0 ]

  remove_stale_claim

  local target backup before

  before="$(fake_launch_count)"

  for target in \
    "$PROJ/.claude/settings.local.json" \
    "$SCRIPTS/pm-pilot-pretool-guard" \
    "$SCRIPTS/p2-consumer-broker.sh"
  do
    backup="${target}.digest-backup"

    cp "$target" "$backup"

    printf '\n# changed by digest test\n' \
      >> "$target"

    run run_resume

    [ "$status" -ne 0 ]
    [ "$(fake_launch_count)" -eq "$before" ]
    assert_no_claim_file

    mv "$backup" "$target"
  done
}

@test "pilot launcher: malformed state is fail-closed and prepare failure releases the acquired claim" {
  join_pilot
  ensure_pilot_runtime_dirs

  printf '{broken-state\n' \
    > "$(state_file)"

  run run_fresh

  [ "$status" -ne 0 ]
  assert_no_fake_launch
  assert_no_claim_file

  [ ! -e "$(binding_file 1)" ]
}

@test "pilot launcher: malformed latest binding is fail-closed on resume without a new exec" {
  join_pilot

  run run_fresh
  [ "$status" -eq 0 ]

  remove_stale_claim

  printf '{broken-binding\n' \
    > "$(binding_file 1)"

  assert_resume_rejected_without_new_launch
  assert_no_claim_file
}

@test "pilot launcher: resume with missing state fails during inspect and never acquires actas" {
  join_pilot

  ensure_pilot_runtime_dirs

  run run_resume

  [ "$status" -ne 0 ]
  assert_no_fake_launch
  assert_no_claim_file

  [ ! -e "$(binding_file 1)" ]
}

@test "pilot launcher: wrong providerCommit in latest binding is rejected" {
  join_pilot

  run run_fresh
  [ "$status" -eq 0 ]

  remove_stale_claim

  [ "$(
    json_file_field \
      "$(binding_file 1)" \
      providerCommit
  )" = "$PROVIDER_COMMIT" ]

  mutate_json_field \
    "$(binding_file 1)" \
    providerCommit \
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

  assert_resume_rejected_without_new_launch
  assert_no_claim_file
}

@test "pilot launcher: generated binding remains schema-compatible with unchanged session-identity.js" {
  skip_on_windows \
    "pilot session-identity process ancestry under Git Bash"

  join_pilot

  export FAKE_CLAUDE_VALIDATE_IDENTITY=1

  run run_fresh

  [ "$status" -eq 0 ]
  [ -s "$FAKE_CLAUDE_IDENTITY_LOG" ]

  node -e '
    "use strict";

    const fs = require("fs");

    const identity =
      JSON.parse(
        fs.readFileSync(
          process.argv[1],
          "utf8",
        )
          .trim(),
      );

    if (
      identity.status !== "ok" ||
      identity.team !== process.argv[2] ||
      identity.agent !== process.argv[3] ||
      identity.type !== "claude-code" ||
      identity.generation !== "1"
    ) {
      process.exit(1);
    }
  ' \
    "$FAKE_CLAUDE_IDENTITY_LOG" \
    "$TEAM" \
    "$PILOT_AGENT"

  [ "$(
    json_file_field \
      "$(binding_file 1)" \
      providerCommit
  )" = "$PROVIDER_COMMIT" ]

  [ "$(
    json_file_field \
      "$(binding_file 1)" \
      schemaVersion
  )" = "1" ]
}

@test "pilot launcher: exec claude preserves the launcher PID used by the binding and claim" {
  skip_on_windows \
    "pilot exec PID identity under Git Bash"

  join_pilot

  run run_fresh

  [ "$status" -eq 0 ]
  [ "$(fake_launch_count)" -eq 1 ]

  local actual_pid process_pid binding_pid claim_owner session_id

  actual_pid="$(
    json_log_field \
      "$FAKE_CLAUDE_LOG" \
      1 \
      actualPid
  )"

  process_pid="$(
    json_log_field \
      "$FAKE_CLAUDE_LOG" \
      1 \
      processPid
  )"

  binding_pid="$(
    json_file_field \
      "$(binding_file 1)" \
      pid
  )"

  session_id="$(
    json_file_field \
      "$(binding_file 1)" \
      sessionId
  )"

  claim_owner="$(
    cat "$(pilot_claim_file)"
  )"

  [ "$actual_pid" = "$process_pid" ]
  [ "$actual_pid" = "$binding_pid" ]
  [ "$claim_owner" = "${session_id}.${actual_pid}" ]
}

@test "pilot launcher: process-start failure after claim releases actas and does not publish a generation" {
  join_pilot

  cat > "$SESSION_IDENTITY" <<'EOF'
#!/usr/bin/env node
'use strict';
process.exit(1);
EOF

  run run_fresh

  [ "$status" -ne 0 ]
  assert_no_fake_launch
  assert_no_claim_file

  [ ! -e "$(binding_file 1)" ]
}

@test "pilot launcher: pre-exec roster change after prepare is fail-closed and releases claim" {
  skip \
    "requires deterministic race injection; covered by prepare revalidation and integration fault gate"
}

@test "pilot binding: providerCommit is exactly the G1 fixed commit and all required schema fields are present" {
  join_pilot

  run run_fresh
  [ "$status" -eq 0 ]

  node -e '
    "use strict";

    const fs = require("fs");

    const binding =
      JSON.parse(
        fs.readFileSync(
          process.argv[1],
          "utf8",
        ),
      );

    const required = [
      "schemaVersion",
      "team",
      "agent",
      "type",
      "project",
      "sessionId",
      "generation",
      "pid",
      "pidStart",
      "profileDigest",
      "policyVersion",
      "guardDigest",
      "brokerDigest",
      "providerCommit",
    ];

    for (const field of required) {
      if (
        !Object.prototype.hasOwnProperty.call(
          binding,
          field,
        )
      ) {
        process.exit(1);
      }
    }

    if (
      binding.schemaVersion !== 1 ||
      binding.providerCommit !== process.argv[2] ||
      binding.policyVersion !== "pm-pilot-pretool-v1" ||
      !/^sha256:[0-9a-f]{64}$/u.test(
        binding.profileDigest,
      ) ||
      !/^sha256:[0-9a-f]{64}$/u.test(
        binding.guardDigest,
      ) ||
      !/^sha256:[0-9a-f]{64}$/u.test(
        binding.brokerDigest,
      )
    ) {
      process.exit(1);
    }
  ' \
    "$(binding_file 1)" \
    "$PROVIDER_COMMIT"
}

@test "pilot binding: state and immutable generation disagreeing on latest binding fail closed" {
  join_pilot

  run run_fresh
  [ "$status" -eq 0 ]

  remove_stale_claim

  local bogus="$TEST_SKILL_DIR/not-the-latest.json"

  mutate_json_field \
    "$(state_file)" \
    latestBinding \
    "$bogus"

  assert_resume_rejected_without_new_launch
  assert_no_claim_file
}

@test "pilot binding: orphan immutable generation without state fails closed even for fresh" {
  join_pilot
  ensure_pilot_runtime_dirs

  cat > "$(binding_file 1)" <<EOF
{
  "schemaVersion": 1,
  "team": "$TEAM",
  "agent": "$PILOT_AGENT",
  "type": "$PILOT_TYPE",
  "project": "$CANONICAL_PROJ",
  "sessionId": "11111111-1111-4111-8111-111111111111",
  "generation": "1",
  "pid": "999999",
  "pidStart": "dead",
  "profileDigest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "policyVersion": "$POLICY_VERSION",
  "guardDigest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "brokerDigest": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "providerCommit": "$PROVIDER_COMMIT"
}
EOF

  run run_fresh

  [ "$status" -ne 0 ]
  assert_no_fake_launch
  assert_no_claim_file
}

@test "pilot binding: generation files are not overwritten by later resume" {
  join_pilot

  run run_fresh
  [ "$status" -eq 0 ]

  local before_hash

  before_hash="$(
    sha256_test_file \
      "$(binding_file 1)"
  )"

  run run_resume
  [ "$status" -eq 0 ]

  [ "$(
    sha256_test_file \
      "$(binding_file 1)"
  )" = "$before_hash" ]

  [ -f "$(binding_file 2)" ]

  [ "$(
    json_file_field \
      "$(binding_file 1)" \
      generation
  )" = "1" ]

  [ "$(
    json_file_field \
      "$(binding_file 2)" \
      generation
  )" = "2" ]
}

@test "pilot binding: resume cannot select a caller-supplied session id" {
  join_pilot

  run run_fresh
  [ "$status" -eq 0 ]

  local session_id

  session_id="$(
    json_file_field \
      "$(binding_file 1)" \
      sessionId
  )"

  run bash "$LAUNCHER" \
    --team "$TEAM" \
    --project "$PROJ" \
    --resume \
    --session-id \
    "22222222-2222-4222-8222-222222222222"

  [ "$status" -ne 0 ]
  [ "$(fake_launch_count)" -eq 1 ]

  [ "$(
    json_file_field \
      "$(binding_file 1)" \
      sessionId
  )" = "$session_id" ]
}

@test "pilot binding helper: prepare requires AGMSG_PM_PILOT_SESSION_ID and still exposes exactly nine CLI options" {
  join_pilot
  ensure_pilot_runtime_dirs

  local pid_start

  pid_start="$(
    node \
      "$SESSION_IDENTITY" \
      --process-start \
      "$$"
  )"

  run env \
    -u AGMSG_PM_PILOT_SESSION_ID \
    node "$BINDING_HELPER" prepare \
      --mode fresh \
      --skill-dir "$TEST_SKILL_DIR" \
      --teams-dir "$TEST_SKILL_DIR/teams" \
      --team "$TEAM" \
      --project "$PROJ" \
      --pid "$$" \
      --pid-start "$pid_start" \
      --state-file "$(state_file)" \
      --bindings-dir "$(bindings_dir)"

  [ "$status" -ne 0 ]
  [ ! -e "$(binding_file 1)" ]

  case "$output" in
    *pilot_session_id_invalid*)
      ;;
    *)
      printf '%s\n' \
        "expected pilot_session_id_invalid, got: $output" \
        >&2
      return 1
      ;;
  esac
}

@test "pilot binding helper: resume prepare rejects an internal session id different from inspect" {
  join_pilot

  run run_fresh
  [ "$status" -eq 0 ]

  remove_stale_claim

  local inspected pid_start

  inspected="$(
    node "$BINDING_HELPER" inspect \
      --skill-dir "$TEST_SKILL_DIR" \
      --teams-dir "$TEST_SKILL_DIR/teams" \
      --team "$TEAM" \
      --project "$PROJ" \
      --state-file "$(state_file)" \
      --bindings-dir "$(bindings_dir)"
  )"

  [ -n "$inspected" ]

  pid_start="$(
    node \
      "$SESSION_IDENTITY" \
      --process-start \
      "$$"
  )"

  run env \
    AGMSG_PM_PILOT_SESSION_ID="22222222-2222-4222-8222-222222222222" \
    node "$BINDING_HELPER" prepare \
      --mode resume \
      --skill-dir "$TEST_SKILL_DIR" \
      --teams-dir "$TEST_SKILL_DIR/teams" \
      --team "$TEAM" \
      --project "$PROJ" \
      --pid "$$" \
      --pid-start "$pid_start" \
      --state-file "$(state_file)" \
      --bindings-dir "$(bindings_dir)"

  [ "$status" -ne 0 ]

  case "$output" in
    *resume_session_mismatch*)
      ;;
    *)
      printf '%s\n' \
        "expected resume_session_mismatch, got: $output" \
        >&2
      return 1
      ;;
  esac

  [ ! -e "$(binding_file 2)" ]
}

@test "pilot binding helper: inspect is read-only and returns only the latest session id" {
  join_pilot

  run run_fresh
  [ "$status" -eq 0 ]

  remove_stale_claim

  local state_before binding_before session_id
  local state_after binding_after

  state_before="$(
    sha256_test_file \
      "$(state_file)"
  )"

  binding_before="$(
    sha256_test_file \
      "$(binding_file 1)"
  )"

  session_id="$(
    json_file_field \
      "$(binding_file 1)" \
      sessionId
  )"

  run node "$BINDING_HELPER" inspect \
    --skill-dir "$TEST_SKILL_DIR" \
    --teams-dir "$TEST_SKILL_DIR/teams" \
    --team "$TEAM" \
    --project "$PROJ" \
    --state-file "$(state_file)" \
    --bindings-dir "$(bindings_dir)"

  [ "$status" -eq 0 ]
  [ "$output" = "$session_id" ]

  state_after="$(
    sha256_test_file \
      "$(state_file)"
  )"

  binding_after="$(
    sha256_test_file \
      "$(binding_file 1)"
  )"

  [ "$state_before" = "$state_after" ]
  [ "$binding_before" = "$binding_after" ]

  [ ! -e "$(binding_file 2)" ]
  assert_no_claim_file
}

@test "pilot launcher: existing spawn and PM implementation files are not modified by a launch" {
  join_pilot

  local spawn_before guard_before identity_before broker_before
  local spawn_after guard_after identity_after broker_after

  spawn_before="$(
    sha256_test_file \
      "$SCRIPTS/spawn.sh"
  )"

  guard_before="$(
    sha256_test_file \
      "$SCRIPTS/pm-pretool-guard"
  )"

  identity_before="$(
    sha256_test_file \
      "$SCRIPTS/session-identity.js"
  )"

  broker_before="$(
    sha256_test_file \
      "$SCRIPTS/pm-broker.js"
  )"

  run run_fresh

  [ "$status" -eq 0 ]

  spawn_after="$(
    sha256_test_file \
      "$SCRIPTS/spawn.sh"
  )"

  guard_after="$(
    sha256_test_file \
      "$SCRIPTS/pm-pretool-guard"
  )"

  identity_after="$(
    sha256_test_file \
      "$SCRIPTS/session-identity.js"
  )"

  broker_after="$(
    sha256_test_file \
      "$SCRIPTS/pm-broker.js"
  )"

  [ "$spawn_before" = "$spawn_after" ]
  [ "$guard_before" = "$guard_after" ]
  [ "$identity_before" = "$identity_after" ]
  [ "$broker_before" = "$broker_after" ]
}

@test "pilot launcher: binding contains raw-byte SHA-256 digests of the exact profile guard and broker files" {
  join_pilot

  run run_fresh
  [ "$status" -eq 0 ]

  local expected_profile expected_guard expected_broker
  local actual_profile actual_guard actual_broker

  expected_profile="$(
    node -e '
      "use strict";

      const crypto = require("crypto");
      const fs = require("fs");

      process.stdout.write(
        "sha256:" +
        crypto
          .createHash("sha256")
          .update(
            fs.readFileSync(
              process.argv[1],
            ),
          )
          .digest("hex"),
      );
    ' "$PROJ/.claude/settings.local.json"
  )"

  expected_guard="$(
    node -e '
      "use strict";

      const crypto = require("crypto");
      const fs = require("fs");

      process.stdout.write(
        "sha256:" +
        crypto
          .createHash("sha256")
          .update(
            fs.readFileSync(
              process.argv[1],
            ),
          )
          .digest("hex"),
      );
    ' "$SCRIPTS/pm-pilot-pretool-guard"
  )"

  expected_broker="$(
    node -e '
      "use strict";

      const crypto = require("crypto");
      const fs = require("fs");

      process.stdout.write(
        "sha256:" +
        crypto
          .createHash("sha256")
          .update(
            fs.readFileSync(
              process.argv[1],
            ),
          )
          .digest("hex"),
      );
    ' "$SCRIPTS/p2-consumer-broker.sh"
  )"

  actual_profile="$(
    json_file_field \
      "$(binding_file 1)" \
      profileDigest
  )"

  actual_guard="$(
    json_file_field \
      "$(binding_file 1)" \
      guardDigest
  )"

  actual_broker="$(
    json_file_field \
      "$(binding_file 1)" \
      brokerDigest
  )"

  [ "$actual_profile" = "$expected_profile" ]
  [ "$actual_guard" = "$expected_guard" ]
  [ "$actual_broker" = "$expected_broker" ]
}

@test "pilot launcher: environment handed to claude names the published binding and fixed pilot identity" {
  join_pilot

  run run_fresh
  [ "$status" -eq 0 ]

  local binding_file_env teams_dir_env claim_file_env
  local guard_path_env broker_path_env

  binding_file_env="$(
    json_log_field \
      "$FAKE_CLAUDE_LOG" \
      1 \
      bindingFile
  )"

  teams_dir_env="$(
    json_log_field \
      "$FAKE_CLAUDE_LOG" \
      1 \
      teamsDir
  )"

  claim_file_env="$(
    json_log_field \
      "$FAKE_CLAUDE_LOG" \
      1 \
      claimFile
  )"

  guard_path_env="$(
    json_log_field \
      "$FAKE_CLAUDE_LOG" \
      1 \
      guardPath
  )"

  broker_path_env="$(
    json_log_field \
      "$FAKE_CLAUDE_LOG" \
      1 \
      brokerPath
  )"

  [ "$binding_file_env" = "$(binding_file 1)" ]
  [ "$teams_dir_env" = "$TEST_SKILL_DIR/teams" ]
  [ "$claim_file_env" = "$(pilot_claim_file)" ]
  [ "$guard_path_env" = "$SCRIPTS/pm-pilot-pretool-guard" ]
  [ "$broker_path_env" = "$SCRIPTS/p2-consumer-broker.sh" ]
}