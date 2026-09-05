#!/usr/bin/env bats

# Offline semantics replace the withdrawn WMI lifetime acceptance contract.
load test_helper

setup() {
  :
}

@test "windows collector evaluator keeps notification and process evidence separate" {
  skip_unless_windows "the legacy evaluation entry point uses Windows PowerShell"
  run python -m unittest discover -s "$BATS_TEST_DIRNAME/windows" -p test_lifetime_semantics.py -v
  if [ "$status" -ne 0 ]; then
    printf '%s\n' "$output" >&2
  fi
  [ "$status" -eq 0 ]
}
