"""Run one wait helper to its timeout and print the elapsed time in hundredths.

Separate from the bats file because bash has no sub-second clock everywhere the
suite runs, and `$SECONDS` -- the very thing under test -- cannot measure its own
truncation.

  wait_helper_elapsed.py <test_helper.bash> <helper> [args...]
"""
import subprocess
import sys
import time

helper_lib, helper = sys.argv[1], sys.argv[2]
script = 'source "$1"; shift; "$@"'
started = time.monotonic()
subprocess.run(['bash', '-c', script, '_', helper_lib, helper, *sys.argv[3:]],
               capture_output=True)
print(int((time.monotonic() - started) * 100))
