#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env

  export RUNNER="$SCRIPTS/pilot-gate-runner.sh"
  export TEST_ROOT="$TEST_SKILL_DIR/pilot-gate-runner-unit"
  export CALL_LOG="$TEST_ROOT/calls.log"
  export UNIT_SOURCE="$TEST_ROOT/source"
  export UNIT_LIVE="$TEST_ROOT/live"
  export UNIT_ARTIFACT="$TEST_ROOT/artifacts"
  export DUMMY_HELPERS="$TEST_ROOT/helpers"

  mkdir -p \
    "$TEST_ROOT" \
    "$UNIT_SOURCE/scripts" \
    "$UNIT_LIVE/scripts" \
    "$UNIT_ARTIFACT" \
    "$DUMMY_HELPERS"

  : > "$CALL_LOG"

  # pilot-gate-runner.sh is intentionally sourced so its functions can be
  # tested without starting native Claude or any real gate helper. The
  # production file must therefore guard main "$@" with BASH_SOURCE.
  #
  # Preserve the BATS shell-option state because the production runner starts
  # with `set -euo pipefail`.
  local saved_shell_flags="$-"
  local saved_pipefail=0
  if set -o | grep -Eq '^pipefail[[:space:]]+on$'; then
    saved_pipefail=1
  fi

  # shellcheck disable=SC1090
  source "$RUNNER"

  case "$saved_shell_flags" in
    *e*) set -e ;;
    *) set +e ;;
  esac
  case "$saved_shell_flags" in
    *u*) set -u ;;
    *) set +u ;;
  esac
  if [ "$saved_pipefail" -eq 1 ]; then
    set -o pipefail
  else
    set +o pipefail
  fi

  # Restore the test_helper fixture identity overwritten by the sourced runner.
  SKILL_DIR="$TEST_SKILL_DIR"

  # When sourced, the production runner computes SCRIPT_DIR from $0 because
  # direct execution is its normal mode. Rebind only the helper locations used
  # by these unit tests; no production helper is executed by this suite.
  install_dummy_helpers

  SOURCE="$UNIT_SOURCE"
  LIVE_SKILL_DIR="$UNIT_LIVE"
  ARTIFACT_DIR="$UNIT_ARTIFACT"
  CHECK="all"
  SUBCOMMAND="run"

  make_static_input_fixture
}

teardown() {
  teardown_test_env
}

install_dummy_executable() {
  local path="$1"

  cat > "$path" <<'EOF'
#!/bin/sh
exit 0
EOF
  chmod +x "$path"
}

install_dummy_helpers() {
  ISOLATION_HELPER="$DUMMY_HELPERS/pilot-gate-isolation.py"
  I1_HELPER="$DUMMY_HELPERS/pilot-gate-i1.py"
  F1_HELPER="$DUMMY_HELPERS/pilot-gate-f1.py"
  F2_HELPER="$DUMMY_HELPERS/pilot-gate-f2.py"
  F3_HELPER="$DUMMY_HELPERS/pilot-gate-f3.py"
  F4_HELPER="$DUMMY_HELPERS/pilot-gate-f4.py"
  F5_HELPER="$DUMMY_HELPERS/pilot-gate-f5.py"
  CLEANUP_HELPER="$DUMMY_HELPERS/pilot-gate-cleanup.py"

  install_dummy_executable "$ISOLATION_HELPER"
  install_dummy_executable "$I1_HELPER"
  install_dummy_executable "$F1_HELPER"
  install_dummy_executable "$F2_HELPER"
  install_dummy_executable "$F3_HELPER"
  install_dummy_executable "$F4_HELPER"
  install_dummy_executable "$F5_HELPER"
  install_dummy_executable "$CLEANUP_HELPER"
}

make_static_input_fixture() {
  local path

  for path in \
    pilot-launcher.sh \
    p2-consumer-broker.sh \
    pilot-collector.sh
  do
    cat > "$UNIT_SOURCE/scripts/$path" <<'EOF'
#!/bin/sh
exit 0
EOF
  done

  cat > "$UNIT_LIVE/scripts/pm-pretool-guard" <<'EOF'
#!/bin/sh
exit 1
EOF

  git -C "$UNIT_SOURCE" init -q
  git -C "$UNIT_SOURCE" config user.email \
    "pilot-gate-runner-test@example.invalid"
  git -C "$UNIT_SOURCE" config user.name \
    "pilot gate runner test"
  git -C "$UNIT_SOURCE" add scripts
  git -C "$UNIT_SOURCE" commit -qm "fixture"
}

record_call() {
  local name="$1"
  local arg="${2:-}"

  printf '%s|%s\n' "$name" "$arg" >> "$CALL_LOG"
}

call_count() {
  local name="$1"

  awk -F '|' -v wanted="$name" '
    $1 == wanted { count += 1 }
    END { print count + 0 }
  ' "$CALL_LOG"
}

last_call_arg() {
  local name="$1"

  awk -F '|' -v wanted="$name" '
    $1 == wanted { value = $2 }
    END { print value }
  ' "$CALL_LOG"
}

assert_called() {
  local name="$1"
  local expected="$2"
  local actual

  actual="$(call_count "$name")"
  [ "$actual" -eq "$expected" ]
}

stub_status() {
  local variable="$1"
  local value

  eval "value=\${$variable:-0}"
  return "$value"
}

install_orchestration_stubs() {
  run_p0_through_p4() {
    record_call "P0-P4"
    stub_status STUB_P0_P4
  }

  phase_n1() {
    record_call "N1"
    stub_status STUB_N1
  }

  phase_i1() {
    record_call "I1"
    stub_status STUB_I1
  }

  phase_f1() {
    record_call "F1"
    stub_status STUB_F1
  }

  phase_f2() {
    record_call "F2"
    stub_status STUB_F2
  }

  phase_f3() {
    record_call "F3"
    stub_status STUB_F3
  }

  phase_f4() {
    record_call "F4"
    stub_status STUB_F4
  }

  phase_f5() {
    record_call "F5"
    stub_status STUB_F5
  }

  phase_p5_cleanup() {
    record_call "P5"
    stub_status STUB_P5
  }

  phase_p6_live_pm_after() {
    record_call "P6"
    stub_status STUB_P6
  }

  phase_p7_aggregate() {
    record_call "P7" "${1:-}"
    stub_status STUB_P7
  }
}

install_main_stubs() {
  parse_args() {
    SUBCOMMAND="${1:-run}"
    record_call "parse_args" "$SUBCOMMAND"
  }

  require_command() {
    record_call "require_command" "$1"
    return 0
  }

  validate_static_inputs() {
    record_call "validate_static_inputs"
    return 0
  }

  scrub_github_credentials() {
    record_call "scrub_github_credentials"
    return 0
  }

  identify_native_claude() {
    record_call "identify_native_claude"
    return 0
  }

  bootstrap_run() {
    record_call "bootstrap_run"
    return 0
  }

  subcommand_preflight() {
    record_call "subcommand_preflight"
    return "${STUB_SUBCOMMAND_STATUS:-0}"
  }

  subcommand_run() {
    record_call "subcommand_run"
    return "${STUB_SUBCOMMAND_STATUS:-0}"
  }

  subcommand_evaluate() {
    record_call "subcommand_evaluate"
    return "${STUB_SUBCOMMAND_STATUS:-0}"
  }

  subcommand_cleanup() {
    record_call "subcommand_cleanup"
    return "${STUB_SUBCOMMAND_STATUS:-0}"
  }
}

reset_stub_statuses() {
  STUB_P0_P4=0
  STUB_N1=0
  STUB_I1=0
  STUB_F1=0
  STUB_F2=0
  STUB_F3=0
  STUB_F4=0
  STUB_F5=0
  STUB_P5=0
  STUB_P6=0
  STUB_P7=0
}

write_results_json() {
  local execution_status="$1"

  cat > "$ARTIFACT_DIR/results.json" <<EOF
{
  "schemaVersion": 1,
  "executionStatus": "$execution_status"
}
EOF
}

run_parse_args_fresh() {
  SOURCE=""
  LIVE_SKILL_DIR=""
  ARTIFACT_DIR=""
  COLLECTOR_CUTOFF_SECONDS="$DEFAULT_COLLECTOR_CUTOFF_SECONDS"
  CHECK="all"
  SUBCOMMAND="run"

  parse_args "$@"
}

invoke_runner_function() {
  local result

  # The production functions deliberately toggle errexit around child phases.
  # Invoke them in a conditional context so a non-zero return cannot terminate
  # the BATS command-capture subshell before `run` records the status.
  if "$@"; then
    result=0
  else
    result="$?"
  fi

  set +e
  return "$result"
}

@test "runner can be sourced without invoking main" {
  run bash -c '
    source "$1"
    printf "%s\n" sourced
  ' bash "$RUNNER"

  [ "$status" -eq 0 ]
  [ "$output" = "sourced" ]
}

@test "direct execution still invokes main for --help" {
  run "$RUNNER" --help

  [ "$status" -eq 0 ]

  case "$output" in
    *"Usage:"*)
      ;;
    *)
      false
      ;;
  esac
}

@test "parse_args rejects each missing required option with exit 64" {
  run run_parse_args_fresh \
    --live-skill-dir "$UNIT_LIVE" \
    --artifact-dir "$UNIT_ARTIFACT"

  [ "$status" -eq 64 ]
  case "$output" in
    *"--source is required"*)
      ;;
    *)
      false
      ;;
  esac

  run run_parse_args_fresh \
    --source "$UNIT_SOURCE" \
    --artifact-dir "$UNIT_ARTIFACT"

  [ "$status" -eq 64 ]
  case "$output" in
    *"--live-skill-dir is required"*)
      ;;
    *)
      false
      ;;
  esac

  run run_parse_args_fresh \
    --source "$UNIT_SOURCE" \
    --live-skill-dir "$UNIT_LIVE"

  [ "$status" -eq 64 ]
  case "$output" in
    *"--artifact-dir is required"*)
      ;;
    *)
      false
      ;;
  esac
}

@test "parse_args rejects unknown check and lists all eight accepted values" {
  run run_parse_args_fresh \
    --source "$UNIT_SOURCE" \
    --live-skill-dir "$UNIT_LIVE" \
    --artifact-dir "$UNIT_ARTIFACT" \
    --check F6

  [ "$status" -eq 64 ]

  case "$output" in
    *"--check must be one of: all, N1, I1, F1, F2, F3, F4, F5"*)
      ;;
    *)
      false
      ;;
  esac
}

@test "validate_static_inputs rejects every unavailable gate helper" {
  local variable
  local helper_path

  for variable in \
    ISOLATION_HELPER \
    I1_HELPER \
    F1_HELPER \
    F2_HELPER \
    F3_HELPER \
    F4_HELPER \
    F5_HELPER \
    CLEANUP_HELPER
  do
    eval "helper_path=\${$variable}"
    chmod -x "$helper_path"

    run validate_static_inputs

    chmod +x "$helper_path"

    [ "$status" -eq 64 ]

    case "$output" in
      *"$helper_path"*)
        ;;
      *)
        false
        ;;
    esac
  done
}

@test "validate_static_inputs rejects a nonexistent source directory" {
  SOURCE="$TEST_ROOT/does-not-exist"

  run validate_static_inputs

  [ "$status" -eq 64 ]

  case "$output" in
    *"--source is not a directory: $SOURCE"*)
      ;;
    *)
      false
      ;;
  esac
}

@test "main dispatches each subcommand exactly once" {
  local subcommand
  local expected
  local candidate

  install_main_stubs
  STUB_SUBCOMMAND_STATUS=0

  for subcommand in \
    preflight \
    run \
    evaluate \
    cleanup
  do
    : > "$CALL_LOG"

    run invoke_runner_function main "$subcommand"

    [ "$status" -eq 0 ]

    expected="subcommand_$subcommand"

    for candidate in \
      subcommand_preflight \
      subcommand_run \
      subcommand_evaluate \
      subcommand_cleanup
    do
      if [ "$candidate" = "$expected" ]; then
        assert_called "$candidate" 1
      else
        assert_called "$candidate" 0
      fi
    done
  done
}

@test "main passes through supported subcommand exit statuses" {
  local subcommand
  local expected_status

  install_main_stubs

  for subcommand in \
    preflight \
    run \
    evaluate \
    cleanup
  do
    for expected_status in \
      0 \
      1 \
      2 \
      64 \
      70
    do
      : > "$CALL_LOG"
      STUB_SUBCOMMAND_STATUS="$expected_status"

      run invoke_runner_function main "$subcommand"

      [ "$status" -eq "$expected_status" ]
      assert_called "subcommand_$subcommand" 1
    done
  done
}

@test "main maps an unexpected subcommand status to EX_INTERNAL" {
  install_main_stubs
  STUB_SUBCOMMAND_STATUS=5

  run invoke_runner_function main run

  [ "$status" -eq 70 ]
  assert_called subcommand_run 1

  case "$output" in
    *"unexpected internal status=5"*)
      ;;
    *)
      false
      ;;
  esac
}

@test "subcommand_run CHECK=N1 passes immediately after N1 and skips later phases" {
  install_orchestration_stubs
  reset_stub_statuses
  CHECK="N1"

  run invoke_runner_function subcommand_run

  [ "$status" -eq 0 ]
  assert_called "P0-P4" 1
  assert_called "N1" 1
  assert_called "I1" 0
  assert_called "F1" 0
  assert_called "F2" 0
  assert_called "F3" 0
  assert_called "F4" 0
  assert_called "F5" 0
  assert_called "P5" 0
  assert_called "P6" 0
  assert_called "P7" 0
}

@test "subcommand_run CHECK=N1 passes through N1 fail and unknown without cleanup" {
  local n1_status

  install_orchestration_stubs
  CHECK="N1"

  for n1_status in 1 2
  do
    : > "$CALL_LOG"
    reset_stub_statuses
    STUB_N1="$n1_status"

    run invoke_runner_function subcommand_run

    [ "$status" -eq "$n1_status" ]
    assert_called "P0-P4" 1
    assert_called "N1" 1
    assert_called "I1" 0
    assert_called "F1" 0
    assert_called "F2" 0
    assert_called "F3" 0
    assert_called "F4" 0
    assert_called "F5" 0
    assert_called "P5" 0
    assert_called "P6" 0
    assert_called "P7" 0
  done
}

@test "subcommand_run CHECK=all stops fault checks after F2 fail but always runs P5 P6 P7" {
  install_orchestration_stubs
  reset_stub_statuses

  CHECK="all"
  STUB_F2=1
  STUB_P7=1

  run invoke_runner_function subcommand_run

  [ "$status" -eq 1 ]
  assert_called "P0-P4" 1
  assert_called "N1" 1
  assert_called "I1" 1
  assert_called "F1" 1
  assert_called "F2" 1
  assert_called "F3" 0
  assert_called "F4" 0
  assert_called "F5" 0
  assert_called "P5" 1
  assert_called "P6" 1
  assert_called "P7" 1
  [ "$(last_call_arg P7)" = "1" ]
}

@test "subcommand_run CHECK=all returns P7 verdict rather than P5 failure directly" {
  install_orchestration_stubs
  reset_stub_statuses

  CHECK="all"
  STUB_P5=1
  STUB_P7=2

  run invoke_runner_function subcommand_run

  [ "$status" -eq 2 ]
  assert_called "P5" 1
  assert_called "P6" 1
  assert_called "P7" 1
  [ "$(last_call_arg P7)" = "0" ]
}

@test "subcommand_run CHECK=all preserves internal P0-P4 failure while still attempting P5 P6 P7" {
  install_orchestration_stubs
  reset_stub_statuses

  CHECK="all"
  STUB_P0_P4=70
  STUB_P7=0

  run invoke_runner_function subcommand_run

  [ "$status" -eq 70 ]
  assert_called "P0-P4" 1
  assert_called "N1" 0
  assert_called "I1" 0
  assert_called "F1" 0
  assert_called "F2" 0
  assert_called "F3" 0
  assert_called "F4" 0
  assert_called "F5" 0
  assert_called "P5" 1
  assert_called "P6" 1
  assert_called "P7" 1
  [ "$(last_call_arg P7)" = "2" ]
}

@test "subcommand_run CHECK=all keeps going after P5 internal failure and returns EX_INTERNAL" {
  install_orchestration_stubs
  reset_stub_statuses

  CHECK="all"
  STUB_P5=70
  STUB_P6=0
  STUB_P7=0

  run invoke_runner_function subcommand_run

  [ "$status" -eq 70 ]
  assert_called "P5" 1
  assert_called "P6" 1
  assert_called "P7" 1
  [ "$(last_call_arg P7)" = "0" ]
}

@test "subcommand_run CHECK=all returns pass when all checks cleanup live-control and aggregate pass" {
  install_orchestration_stubs
  reset_stub_statuses
  CHECK="all"

  run invoke_runner_function subcommand_run

  [ "$status" -eq 0 ]
  assert_called "P0-P4" 1
  assert_called "N1" 1
  assert_called "I1" 1
  assert_called "F1" 1
  assert_called "F2" 1
  assert_called "F3" 1
  assert_called "F4" 1
  assert_called "F5" 1
  assert_called "P5" 1
  assert_called "P6" 1
  assert_called "P7" 1
  [ "$(last_call_arg P7)" = "0" ]
}

@test "subcommand_evaluate uses unknown execution status when results.json is absent" {
  install_orchestration_stubs
  reset_stub_statuses
  rm -f "$ARTIFACT_DIR/results.json"

  run invoke_runner_function subcommand_evaluate

  [ "$status" -eq 0 ]
  assert_called "P7" 1
  [ "$(last_call_arg P7)" = "2" ]

  assert_called "P0-P4" 0
  assert_called "N1" 0
  assert_called "I1" 0
  assert_called "F1" 0
  assert_called "F2" 0
  assert_called "F3" 0
  assert_called "F4" 0
  assert_called "F5" 0
  assert_called "P5" 0
  assert_called "P6" 0
}

@test "subcommand_evaluate maps recorded executionStatus to P7 execution status" {
  local recorded
  local expected

  install_orchestration_stubs
  reset_stub_statuses

  for recorded in pass fail unknown invalid
  do
    case "$recorded" in
      pass)
        expected=0
        ;;
      fail)
        expected=1
        ;;
      unknown)
        expected=2
        ;;
      *)
        expected=2
        ;;
    esac

    : > "$CALL_LOG"
    write_results_json "$recorded"

    run invoke_runner_function subcommand_evaluate

    [ "$status" -eq 0 ]
    assert_called "P7" 1
    [ "$(last_call_arg P7)" = "$expected" ]
  done
}

@test "subcommand_evaluate treats missing executionStatus as unknown and touches no external phase" {
  install_orchestration_stubs
  reset_stub_statuses

  cat > "$ARTIFACT_DIR/results.json" <<'EOF'
{
  "schemaVersion": 1,
  "pilot_ready": false
}
EOF

  run invoke_runner_function subcommand_evaluate

  [ "$status" -eq 0 ]
  assert_called "P7" 1
  [ "$(last_call_arg P7)" = "2" ]

  assert_called "P0-P4" 0
  assert_called "N1" 0
  assert_called "I1" 0
  assert_called "F1" 0
  assert_called "F2" 0
  assert_called "F3" 0
  assert_called "F4" 0
  assert_called "F5" 0
  assert_called "P5" 0
  assert_called "P6" 0
}

@test "subcommand_cleanup calls only P5 exactly once and passes through its result" {
  local cleanup_status

  install_orchestration_stubs

  for cleanup_status in 0 1 2 70
  do
    : > "$CALL_LOG"
    reset_stub_statuses
    STUB_P5="$cleanup_status"

    run invoke_runner_function subcommand_cleanup

    [ "$status" -eq "$cleanup_status" ]
    assert_called "P5" 1
    assert_called "P0-P4" 0
    assert_called "N1" 0
    assert_called "I1" 0
    assert_called "F1" 0
    assert_called "F2" 0
    assert_called "F3" 0
    assert_called "F4" 0
    assert_called "F5" 0
    assert_called "P6" 0
    assert_called "P7" 0
  done
}