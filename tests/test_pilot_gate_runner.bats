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
  PTY_HELPER="$DUMMY_HELPERS/pilot-pty.py"

  install_dummy_executable "$ISOLATION_HELPER"
  install_dummy_executable "$I1_HELPER"
  install_dummy_executable "$F1_HELPER"
  install_dummy_executable "$F2_HELPER"
  install_dummy_executable "$F3_HELPER"
  install_dummy_executable "$F4_HELPER"
  install_dummy_executable "$F5_HELPER"
  install_dummy_executable "$CLEANUP_HELPER"
  install_dummy_executable "$PTY_HELPER"
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

  # #404: the source must carry the pilot profile writer and pilot guard.
  mkdir -p "$UNIT_SOURCE/scripts/lib"
  : > "$UNIT_SOURCE/scripts/lib/pilot-profile.js"
  printf '#!/bin/sh\nexit 2\n' > "$UNIT_SOURCE/scripts/pm-pilot-pretool-guard"

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
  load_existing_run() {
    record_call "load_existing_run"
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
    CLEANUP_HELPER \
    PTY_HELPER
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

    # run/preflight create a new run; cleanup/evaluate act on the existing
    # one and must not mint a new run id (Issue #405).
    case "$subcommand" in
      preflight|run)
        assert_called bootstrap_run 1
        assert_called load_existing_run 0
        ;;
      *)
        assert_called bootstrap_run 0
        assert_called load_existing_run 1
        ;;
    esac
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

@test "subcommand_run CHECK=N1 stops checks after N1 but still runs P5 P6 P7" {
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
  # runbook §33: cleanup is attempted whatever the result or scope.
  assert_called "P5" 1
  assert_called "P6" 1
  assert_called "P7" 1
  [ "$(last_call_arg P7)" = "0" ]
}

@test "subcommand_run CHECK=N1 fail or unknown still runs P5 P6 P7 and returns P7" {
  local n1_status

  install_orchestration_stubs
  CHECK="N1"

  for n1_status in 1 2
  do
    : > "$CALL_LOG"
    reset_stub_statuses
    STUB_N1="$n1_status"
    STUB_P7="$n1_status"

    run invoke_runner_function subcommand_run

    [ "$status" -eq "$n1_status" ]
    assert_called "N1" 1
    assert_called "I1" 0
    assert_called "P5" 1
    assert_called "P6" 1
    assert_called "P7" 1
    [ "$(last_call_arg P7)" = "$n1_status" ]
  done
}

@test "subcommand_run partial check stops after the requested check for every check" {
  local check
  local later

  install_orchestration_stubs

  for check in I1 F1 F2 F3 F4 F5
  do
    : > "$CALL_LOG"
    reset_stub_statuses
    CHECK="$check"

    run invoke_runner_function subcommand_run

    [ "$status" -eq 0 ]
    assert_called "$check" 1
    assert_called "P5" 1
    assert_called "P6" 1
    assert_called "P7" 1

    case "$check" in
      I1) later="F1" ;;
      F1) later="F2" ;;
      F2) later="F3" ;;
      F3) later="F4" ;;
      F4) later="F5" ;;
      F5) later="" ;;
    esac
    if [ -n "$later" ]; then
      assert_called "$later" 0
    fi
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

# --- Issue #405 -------------------------------------------------------------

# A python helper that records calls and writes the artifacts P5/P6/P7 and N1
# would leave behind. The shell dummies above cannot be run by python3.
write_python_helper() {
  local path="$1"
  cat > "$path" <<'PY'
#!/usr/bin/env python3
import json, os, sys
log = os.environ.get("CALL_LOG")
if log:
    with open(log, "a") as fh:
        fh.write("helper|" + " ".join(sys.argv[1:3]) + "\n")
args = sys.argv[1:]
def opt(name):
    return args[args.index(name) + 1] if name in args else None
cmd = args[0] if args else ""
if cmd == "write-n1-result":
    os.makedirs(os.path.dirname(opt("--output")), exist_ok=True)
    with open(opt("--output"), "w") as fh:
        json.dump({"verdict": opt("--verdict"), "reason": opt("--reason")}, fh)
PY
  chmod +x "$path"
}

# Runs the real main() in a fresh bash with the production shell options, so
# errexit behaves as in a real gate run (bats' own invocation disables it).
run_main_production_options() {
  local check="$1"
  run bash -c '
    set -euo pipefail
    RUNNER="$1"; CHECK_ARG="$2"
    source "$RUNNER"
    set -euo pipefail
    parse_args() { SUBCOMMAND=run; CHECK="$CHECK_ARG"; }
    require_command() { :; }
    validate_static_inputs() { :; }
    scrub_github_credentials() { :; }
    identify_native_claude() { :; }
    bootstrap_run() { RUN_ID=unit-run; printf "bootstrap|\n" >> "$CALL_LOG"; }
    run_p0_through_p4() { printf "P0-P4|\n" >> "$CALL_LOG"; }
    launch_n1_case() {
      printf "launch_n1_case|%s\n" "$1" >> "$CALL_LOG"
      # The production launcher used to end with `set -e`, re-enabling
      # errexit inside the set +e window of its caller.
      if [ "${REENABLE_ERREXIT:-0}" = "1" ]; then set -e; fi
      return 2
    }
    phase_p5_cleanup() {
      printf "P5|\n" >> "$CALL_LOG"
      printf "{\"status\": \"pass\"}\n" > "$ARTIFACT_DIR/cleanup.json"
    }
    phase_p6_live_pm_after() {
      printf "P6|\n" >> "$CALL_LOG"
      mkdir -p "$ARTIFACT_DIR/live-pm"
      printf "{\"verdict\": \"pass\"}\n" > "$ARTIFACT_DIR/live-pm/result.json"
    }
    phase_p7_aggregate() {
      printf "P7|%s\n" "$1" >> "$CALL_LOG"
      printf "{\"verdict\": \"unknown\"}\n" > "$ARTIFACT_DIR/results.json"
      return 2
    }
    ISOLATION_HELPER="$HELPER"
    ARTIFACT_DIR="$ART"
    main run
  ' bash "$RUNNER" "$check"
}

@test "#405: a real N1 unknown still reaches P5 P6 P7 and writes results.json" {
  export HELPER="$TEST_ROOT/isolation-helper.py"
  export ART="$TEST_ROOT/prod-artifacts"
  mkdir -p "$ART"
  write_python_helper "$HELPER"

  for check in N1 all
  do
    : > "$CALL_LOG"
    rm -rf "$ART"; mkdir -p "$ART"

    run_main_production_options "$check"

    [ "$status" -eq 2 ]
    # The real phase_n1 ran and recorded its own unknown result.
    assert_called launch_n1_case 1
    grep -q '"verdict": "unknown"' "$ART/N1/result.json"
    # And every P5-P7 artifact is present.
    assert_called P5 1
    assert_called P6 1
    assert_called P7 1
    [ "$(last_call_arg P7)" = "2" ]
    [ -f "$ART/cleanup.json" ]
    [ -f "$ART/live-pm/result.json" ]
    [ -f "$ART/results.json" ]
  done
}

@test "#405: an internal_error abort still attempts P5 P6 P7 and keeps exit 70" {
  run bash -c '
    set -euo pipefail
    source "$1"
    set -euo pipefail
    phase_p5_cleanup() { printf "P5|\n" >> "$CALL_LOG"; }
    phase_p6_live_pm_after() { printf "P6|\n" >> "$CALL_LOG"; }
    phase_p7_aggregate() { printf "P7|%s\n" "$1" >> "$CALL_LOG"; }
    SUBCOMMAND=run
    trap finalize_after_abort EXIT
    FINALIZE_PENDING=1
    internal_error "simulated abort"
  ' bash "$RUNNER"

  [ "$status" -eq 70 ]
  assert_called P5 1
  assert_called P6 1
  assert_called P7 1
  [ "$(last_call_arg P7)" = "2" ]
}

@test "#405: an aborted preflight attempts P5 only" {
  run bash -c '
    set -euo pipefail
    source "$1"
    set -euo pipefail
    phase_p5_cleanup() { printf "P5|\n" >> "$CALL_LOG"; }
    phase_p6_live_pm_after() { printf "P6|\n" >> "$CALL_LOG"; }
    phase_p7_aggregate() { printf "P7|\n" >> "$CALL_LOG"; }
    SUBCOMMAND=preflight
    trap finalize_after_abort EXIT
    FINALIZE_PENDING=1
    internal_error "simulated abort"
  ' bash "$RUNNER"

  [ "$status" -eq 70 ]
  assert_called P5 1
  assert_called P6 0
  assert_called P7 0
}

@test "#405: a normal exit does not trigger the abort finalizer" {
  run bash -c '
    set -euo pipefail
    source "$1"
    phase_p5_cleanup() { printf "P5|\n" >> "$CALL_LOG"; }
    SUBCOMMAND=run
    trap finalize_after_abort EXIT
    FINALIZE_PENDING=0
    exit 0
  ' bash "$RUNNER"

  [ "$status" -eq 0 ]
  assert_called P5 0
}

@test "#405: preflight runs P5 after P0-P4 and returns the worse status" {
  local p0 p5 expected

  install_orchestration_stubs

  for p0 in 0 1 2
  do
    for p5 in 0 1 2
    do
      : > "$CALL_LOG"
      reset_stub_statuses
      STUB_P0_P4="$p0"
      STUB_P5="$p5"

      run invoke_runner_function subcommand_preflight

      if [ "$p0" -eq 1 ] || [ "$p5" -eq 1 ]; then
        expected=1
      elif [ "$p0" -eq 2 ] || [ "$p5" -eq 2 ]; then
        expected=2
      else
        expected=0
      fi
      [ "$status" -eq "$expected" ]
      assert_called "P0-P4" 1
      assert_called "P5" 1
      assert_called "P6" 0
      assert_called "P7" 0
    done
  done
}

@test "#405: worst_status orders internal over fail over unknown over pass" {
  [ "$(worst_status 0 0)" = "0" ]
  [ "$(worst_status 0 2)" = "2" ]
  [ "$(worst_status 2 1)" = "1" ]
  [ "$(worst_status 1 0)" = "1" ]
  [ "$(worst_status 70 0)" = "70" ]
  [ "$(worst_status 1 70)" = "70" ]
  [ "$(worst_status 64 0)" = "70" ]
}

write_state_file() {
  local artifact="$1"
  python3 - "$artifact" "$TEST_ROOT/run root" <<'PY'
import json, sys
artifact, root = sys.argv[1], sys.argv[2]
json.dump({
    "schemaVersion": 1, "part": 1, "runId": "existing-run",
    "runRoot": root, "gateTeam": "agmsg-g4gate-existing",
    "gateRepo": root + "/repo", "gateHome": root + "/home",
    "xdg": {"config": root + "/xdg/config", "cache": root + "/xdg/cache",
            "data": root + "/xdg/data", "state": root + "/xdg/state"},
    "claudeConfigDir": root + "/claude", "artifactDir": artifact,
}, open(artifact + "/part1-state.json", "w"))
PY
}

@test "#405: load_existing_run reads the run without minting or rewriting state" {
  write_state_file "$UNIT_ARTIFACT"
  local before
  before="$(shasum -a 256 "$UNIT_ARTIFACT/part1-state.json")"

  load_existing_run 2>/dev/null

  [ "$RUN_ID" = "existing-run" ]
  [ "$RUN_ROOT" = "$TEST_ROOT/run root" ]
  [ "$GATE_TEAM" = "agmsg-g4gate-existing" ]
  [ "$GATE_REPO" = "$TEST_ROOT/run root/repo" ]
  [ "$GATE_XDG_STATE" = "$TEST_ROOT/run root/xdg/state" ]
  [ "$GATE_CLAUDE_CONFIG" = "$TEST_ROOT/run root/claude" ]
  [ "$(shasum -a 256 "$UNIT_ARTIFACT/part1-state.json")" = "$before" ]
}

@test "#405: load_existing_run rejects missing, broken or foreign state with 64" {
  run load_existing_run
  [ "$status" -eq 64 ]
  # Guarded: a bare non-last [[ ]] is not enforced on bash 3.2 (#670).
  [[ "$output" == *"no existing run"* ]] ||
    { echo "unexpected output: $output" >&2; return 1; }

  printf '{broken\n' > "$UNIT_ARTIFACT/part1-state.json"
  run load_existing_run
  [ "$status" -eq 64 ]

  mkdir -p "$TEST_ROOT/other-artifacts"
  write_state_file "$TEST_ROOT/other-artifacts"
  cp "$TEST_ROOT/other-artifacts/part1-state.json" \
    "$UNIT_ARTIFACT/part1-state.json"
  run load_existing_run
  [ "$status" -eq 64 ]
  [[ "$output" == *"another artifact directory"* ]]
}

@test "#405: a phase that re-enables errexit cannot abort the run before P5" {
  export HELPER="$TEST_ROOT/isolation-helper.py"
  export ART="$TEST_ROOT/prod-artifacts"
  export REENABLE_ERREXIT=1
  rm -rf "$ART"; mkdir -p "$ART"
  write_python_helper "$HELPER"

  run_main_production_options all

  [ "$status" -eq 2 ]
  assert_called launch_n1_case 1
  assert_called P5 1
  assert_called P6 1
  assert_called P7 1
  [ -f "$ART/results.json" ]
}

@test "#405: no function in the runner toggles errexit" {
  # Status is captured with `cmd || status=$?`; a set +e / set -e pair
  # inside a function is what let a nested phase re-enable errexit.
  local offenders
  offenders="$(grep -nE '^[[:space:]]+set [+-]e' "$RUNNER" || true)"
  [ -z "$offenders" ] || { echo "$offenders"; false; }
  # Positive control: the top-level shell options are still there.
  grep -qE '^set -euo pipefail$' "$RUNNER"
}


@test "#405: main maps an unexpected subcommand status to 70 under production options" {
  run bash -c '
    set -euo pipefail
    source "$1"
    set -euo pipefail
    parse_args() { SUBCOMMAND=run; }
    require_command() { :; }
    validate_static_inputs() { :; }
    scrub_github_credentials() { :; }
    identify_native_claude() { :; }
    bootstrap_run() { :; }
    subcommand_run() { FINALIZE_PENDING=0; return 5; }
    main run
  ' bash "$RUNNER"

  [ "$status" -eq 70 ]
  [[ "$output" == *"unexpected internal status=5"* ]]
}

# --- #404: the runner writes the pilot profile through pilot-profile.js -----

@test "validate_static_inputs requires the pilot profile writer and pilot guard" {
  local relative

  for relative in scripts/lib/pilot-profile.js scripts/pm-pilot-pretool-guard
  do
    mv "$UNIT_SOURCE/$relative" "$UNIT_SOURCE/$relative.away"

    run validate_static_inputs

    mv "$UNIT_SOURCE/$relative.away" "$UNIT_SOURCE/$relative"

    [ "$status" -eq 64 ]
    case "$output" in
      *"source $relative not found"*) ;;
      *) echo "unexpected output: $output" >&2; return 1 ;;
    esac
  done
}

make_profile_gate_repo() {
  GATE_REPO="$TEST_ROOT/gate-repo"
  mkdir -p "$GATE_REPO/scripts/lib"
  cp "$SCRIPTS/lib/pilot-profile.js" "$GATE_REPO/scripts/lib/pilot-profile.js"
  printf '#!/bin/sh\nexit 2\n' > "$GATE_REPO/scripts/pm-pilot-pretool-guard"
  chmod +x "$GATE_REPO/scripts/pm-pilot-pretool-guard"
  printf '#!/bin/sh\nexit 0\n' > "$GATE_REPO/scripts/pm-posttool-record"
  chmod +x "$GATE_REPO/scripts/pm-posttool-record"
}

@test "write_pilot_profile routes every tool through the copy's pilot guard" {
  command -v node >/dev/null || skip "node not installed"
  make_profile_gate_repo

  write_pilot_profile

  local profile="$GATE_REPO/.claude/settings.local.json"
  [ -f "$profile" ]
  run python3 -c '
import json, sys
profile, guard = json.load(open(sys.argv[1])), sys.argv[2]
entries = profile["hooks"]["PreToolUse"]
assert len(entries) == 1, entries
assert entries[0]["matcher"] == "*", entries
assert [h["command"] for h in entries[0]["hooks"]] == [guard], entries
assert set(profile["hooks"]) == {"PreToolUse", "PostToolUse"}, profile
post = [h["command"] for g in profile["hooks"]["PostToolUse"] for h in g["hooks"]]
# Exactly one PostToolUse handler: F3 needs one to stop (#415).
assert post == [sys.argv[3]], post
print("ok")
' "$profile" "$GATE_REPO/scripts/pm-pilot-pretool-guard" "$GATE_REPO/scripts/pm-posttool-record"
  [ "$status" -eq 0 ] || { echo "$output" >&2; return 1; }
  [ "$output" = "ok" ]
}

@test "write_pilot_profile stops the run instead of leaving an empty profile" {
  command -v node >/dev/null || skip "node not installed"
  make_profile_gate_repo

  local variant
  for variant in missing symlink not-executable
  do
    rm -rf "$GATE_REPO/.claude"
    rm -f "$GATE_REPO/scripts/pm-pilot-pretool-guard"
    case "$variant" in
      missing)
        ;;
      symlink)
        printf '#!/bin/sh\nexit 2\n' > "$TEST_ROOT/real-guard"
        chmod +x "$TEST_ROOT/real-guard"
        ln -s "$TEST_ROOT/real-guard" "$GATE_REPO/scripts/pm-pilot-pretool-guard"
        ;;
      not-executable)
        printf '#!/bin/sh\nexit 2\n' > "$GATE_REPO/scripts/pm-pilot-pretool-guard"
        chmod -x "$GATE_REPO/scripts/pm-pilot-pretool-guard"
        ;;
    esac

    run write_pilot_profile

    [ "$status" -eq 70 ] || { echo "$variant: status=$status $output" >&2; return 1; }
    [ ! -e "$GATE_REPO/.claude/settings.local.json" ] ||
      { echo "$variant: a profile was left behind" >&2; return 1; }
    grep -q "pilot-profile:" "$ARTIFACT_DIR/P2-pilot-profile.log" ||
      { echo "$variant: no pilot-profile diagnostic" >&2; return 1; }
  done
}

@test "the runner no longer writes an empty hooks profile" {
  # The profile that let N1/F2 bypass the guard (#397).
  run grep -n '"hooks": {}' "$RUNNER"
  [ "$status" -eq 1 ]
}

# --- #415: no live PM state reaches the pilot --------------------------------

@test "scrub_agmsg_pm_environment removes every AGMSG_PM_* and nothing else" {
  export AGMSG_PM_EXECUTIONS_FILE=/live/executions.jsonl
  export AGMSG_PM_DECISIONS_FILE=/live/decisions.jsonl
  export AGMSG_PM_BINDING_FILE=/live/binding.json
  AGMSG_PM_NOT_EXPORTED=1
  export AGMSG_OTHER=keep

  scrub_agmsg_pm_environment

  # Anchor on the variable name: the bats test description itself contains
  # the text AGMSG_PM_ and is exported.
  run bash -c "env | grep '^AGMSG_PM_'"
  [ "$status" -eq 1 ] || { echo "left behind: $output" >&2; return 1; }
  [ -z "${AGMSG_PM_NOT_EXPORTED+set}" ]
  [ "$AGMSG_OTHER" = "keep" ]
}

@test "the pilot launch environment carries no AGMSG_PM_*" {
  GATE_HOME="$TEST_ROOT/home"
  GATE_XDG_CONFIG="$TEST_ROOT/xdg/config"
  GATE_XDG_CACHE="$TEST_ROOT/xdg/cache"
  GATE_XDG_DATA="$TEST_ROOT/xdg/data"
  GATE_XDG_STATE="$TEST_ROOT/xdg/state"
  GATE_CLAUDE_CONFIG="$TEST_ROOT/claude"
  export AGMSG_PM_EXECUTIONS_FILE=/live/executions.jsonl

  # launch_n1_case runs the launcher in a subshell after this call.
  run bash -c '
    source "$1"
    GATE_HOME=h GATE_XDG_CONFIG=c GATE_XDG_CACHE=k GATE_XDG_DATA=d
    GATE_XDG_STATE=s GATE_CLAUDE_CONFIG=cc
    export_isolated_environment
    if env | grep "^AGMSG_PM_"; then exit 3; fi
    printf "%s\n" "$CLAUDE_CONFIG_DIR"
  ' bash "$RUNNER"

  [ "$status" -eq 0 ] || { echo "inherited: $output" >&2; return 1; }
  [ "$output" = "cc" ]
}

@test "P3 records a clean gate environment" {
  scrub_agmsg_pm_environment

  check_agmsg_pm_environment

  run python3 -c '
import json, sys
v = json.load(open(sys.argv[1]))
assert v == {"schemaVersion": 1, "check": "gate-environment-has-no-AGMSG_PM",
             "present": [], "verdict": "pass"}, v
print("ok")
' "$ARTIFACT_DIR/P3-agmsg-pm-environment.json"
  [ "$output" = "ok" ]
}

@test "P3 stops before the isolation helper when an AGMSG_PM_* is left" {
  # Negative control asked for by the breaker: one variable deliberately
  # left behind must stop preflight.
  scrub_agmsg_pm_environment
  export AGMSG_PM_EXECUTIONS_FILE=/live/executions.jsonl
  : > "$CALL_LOG"
  python3() {
    if [ "${1:-}" = "$ISOLATION_HELPER" ]; then
      record_call "isolation-helper" "${2:-}"
    fi
    command python3 "$@"
  }

  run invoke_runner_function phase_p3_isolation_preflight

  [ "$status" -eq 2 ]
  assert_called isolation-helper 0
  grep -q '"AGMSG_PM_EXECUTIONS_FILE"' "$ARTIFACT_DIR/P3-agmsg-pm-environment.json"
  grep -q '"verdict": "fail"' "$ARTIFACT_DIR/P3-agmsg-pm-environment.json"
}

@test "main scrubs AGMSG_PM_* before any subcommand runs" {
  install_main_stubs
  subcommand_run() {
    record_call "subcommand_run" "${AGMSG_PM_EXECUTIONS_FILE-unset}"
    return 0
  }
  export AGMSG_PM_EXECUTIONS_FILE=/live/executions.jsonl

  run invoke_runner_function main run

  [ "$status" -eq 0 ]
  [ "$(last_call_arg subcommand_run)" = "unset" ]
}


# --- #426: N1 starts the native pilot in a terminal ------------------------

# Stands in for pilot-launcher.sh + claude: without a terminal on stdin it
# takes claude's --print path and exits 1, as in the #426 reproduction; with
# one it stays up like an interactive session.
install_tty_checking_launcher() {
  GATE_REPO="$TEST_ROOT/gate-repo"
  mkdir -p "$GATE_REPO/scripts"
  cat > "$GATE_REPO/scripts/pilot-launcher.sh" <<'EOF'
#!/usr/bin/env bash
if [ ! -t 0 ]; then
  printf '%s\n' "Error: Input must be provided either through stdin or as a prompt argument when using --print" >&2
  printf '%s\n' 1 > "$(dirname "$0")/../launcher-exit"
  exit 1
fi
printf '%s\n' "interactive: stdin is a terminal"
exec sleep 60
EOF
  chmod +x "$GATE_REPO/scripts/pilot-launcher.sh"
}

@test "#426: N1 starts the launcher in a terminal and it is still alive after 8 seconds" {
  install_tty_checking_launcher
  ARTIFACT_DIR="$UNIT_ARTIFACT"
  GATE_TEAM="gate-team"
  PTY_HELPER="$SCRIPTS/lib/pilot-pty.py"

  export_isolated_environment() { :; }
  # Observe the launcher 8 seconds after start instead of a real binding.
  wait_for_binding() {
    sleep 8
    if _agmsg_pid_alive_local "$2"; then
      printf '%s\n' "$2" > "$TEST_ROOT/alive-after-8s"
    fi
    # #434: the control socket is bound in the case's private directory.
    if [ -S "$CURRENT_N1_CONTROL_DIR/control.sock" ]; then
      printf '%s\n' "$CURRENT_N1_CONTROL_DIR" > "$TEST_ROOT/control-dir"
    fi
    return 1
  }

  local case_status=0
  launch_n1_case fresh 1 || case_status="$?"

  local case_dir="$ARTIFACT_DIR/N1/fresh"
  [ -s "$TEST_ROOT/alive-after-8s" ] || {
    echo "launcher not alive after 8s; exit=$(cat "$GATE_REPO/launcher-exit" 2>/dev/null)" >&2
    cat "$case_dir/stderr.raw" >&2
    return 1
  }
  [ "$case_status" -eq "$EX_GATE_UNKNOWN" ] ||
    { echo "case status: $case_status" >&2; return 1; }
  grep -q 'interactive: stdin is a terminal' "$case_dir/pty.raw" ||
    { echo "no terminal output recorded" >&2; return 1; }
  [ "$(cat "$case_dir/launcher-pid")" = "$(cat "$TEST_ROOT/alive-after-8s")" ] ||
    { echo "launcher-pid does not name the observed launcher" >&2; return 1; }
  # #434: the socket lived in a private directory, removed with the case.
  [ -s "$TEST_ROOT/control-dir" ] ||
    { echo "control socket was not bound in a private directory" >&2; return 1; }
  [ ! -e "$(cat "$TEST_ROOT/control-dir")" ] ||
    { echo "control directory left behind" >&2; return 1; }
  [ -z "$CURRENT_N1_CONTROL_DIR" ] ||
    { echo "control directory still recorded" >&2; return 1; }
  # The case stopped both the launcher and the pty helper.
  ! _agmsg_pid_alive_local "$(cat "$TEST_ROOT/alive-after-8s")"
}

@test "#426: a pty helper that never reports a launcher PID is unknown" {
  ARTIFACT_DIR="$UNIT_ARTIFACT"
  GATE_REPO="$TEST_ROOT/gate-repo"
  GATE_TEAM="gate-team"
  mkdir -p "$GATE_REPO"
  cat > "$DUMMY_HELPERS/pilot-pty.py" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$DUMMY_HELPERS/pilot-pty.py"
  PTY_HELPER="$DUMMY_HELPERS/pilot-pty.py"
  export_isolated_environment() { :; }
  wait_for_binding() { printf '%s\n' called >> "$CALL_LOG"; return 1; }

  local case_status=0
  launch_n1_case fresh 1 || case_status="$?"

  [ "$case_status" -eq "$EX_GATE_UNKNOWN" ]
  [ "$(cat "$UNIT_ARTIFACT/N1/fresh/verdict")" = "unknown" ]
  [ ! -s "$CALL_LOG" ]
}

# --- #434: one marker prompt per N1 case ------------------------------------

# Carry a case past the binding and process checks (all stubbed) up to the
# marker prompt, with the real pty helper holding a tty-checking launcher.
prepare_n1_marker_case() {
  install_tty_checking_launcher
  ARTIFACT_DIR="$UNIT_ARTIFACT"
  GATE_TEAM="gate-team"
  RUN_ID="run434"
  PTY_HELPER="$SCRIPTS/lib/pilot-pty.py"
  printf '%s\n' '{}' > "$TEST_ROOT/binding.json"
  # The runner calls the isolation helper with python3; accept every check.
  printf '%s\n' 'raise SystemExit(0)' > "$ISOLATION_HELPER"

  export_isolated_environment() { :; }
  wait_for_binding() { sleep 1; printf '%s\n' "$TEST_ROOT/binding.json"; }
  json_field() { printf '%s\n' "123e4567-e89b-42d3-a456-426614174000"; }
  record_process_command() { printf '%s\n' "claude" > "$2"; }
  wait_for_transcript() {
    printf '%s|%s\n' "$1" "$2" >> "$TEST_ROOT/transcript-calls"
    return 1
  }
}

@test "#434 N1M-01: the marker prompt reaches the native PTY once and the transcript wait uses that marker" {
  prepare_n1_marker_case

  local case_status=0
  launch_n1_case fresh 1 || case_status="$?"

  local case_dir="$ARTIFACT_DIR/N1/fresh"
  local marker
  marker="$(cat "$case_dir/marker.txt")"
  [[ "$marker" == AGMSG_N1_TRANSCRIPT_MARKER_run434_fresh_* ]] ||
    { echo "marker: $marker" >&2; return 1; }
  [ "$(cat "$case_dir/input-count")" = "1" ]
  [ "$(cat "$case_dir/input-result")" = "ok" ] ||
    { cat "$case_dir/stderr.raw" >&2; return 1; }
  grep -qF "$marker" "$case_dir/pty.raw" ||
    { echo "prompt not seen on the PTY" >&2; return 1; }
  [ "$(cat "$TEST_ROOT/transcript-calls")" = "123e4567-e89b-42d3-a456-426614174000|$marker" ] ||
    { cat "$TEST_ROOT/transcript-calls" >&2; return 1; }
  # No transcript was observed: unknown, never pass.
  [ "$case_status" -eq "$EX_GATE_UNKNOWN" ]
}

@test "#434 N1M-07: a prompt that does not reach the PTY is unknown, recorded once and not retried" {
  prepare_n1_marker_case
  local real_helper="$PTY_HELPER"
  PTY_HELPER="$DUMMY_HELPERS/pilot-pty-send-fails.py"
  cat > "$PTY_HELPER" <<EOF
import os, sys
if sys.argv[1:2] == ["send"]:
    with open("$TEST_ROOT/send-calls", "a") as fh:
        fh.write("send\n")
    sys.exit(1)
os.execv(sys.executable, [sys.executable, "$real_helper", *sys.argv[1:]])
EOF
  chmod +x "$PTY_HELPER"

  local case_status=0
  launch_n1_case fresh 1 || case_status="$?"

  local case_dir="$ARTIFACT_DIR/N1/fresh"
  [ "$case_status" -eq "$EX_GATE_UNKNOWN" ]
  [ "$(cat "$case_dir/verdict")" = "unknown" ]
  [ "$(cat "$case_dir/input-count")" = "1" ]
  [ "$(cat "$case_dir/input-result")" = "error" ]
  [ "$(wc -l < "$TEST_ROOT/send-calls" | tr -d ' ')" = "1" ]
  [ ! -e "$TEST_ROOT/transcript-calls" ]
}
