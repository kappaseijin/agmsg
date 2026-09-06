"""Run one wait_for_file to its timeout and print the elapsed time in hundredths.

Separate from the bats file because bash has no sub-second clock everywhere the
suite runs, and `$SECONDS` -- the very thing under test -- cannot measure its own
truncation.
"""
import subprocess
import sys
import time

helper, missing = sys.argv[1], sys.argv[2]
started = time.monotonic()
subprocess.run(['bash', '-c', 'source "$1"; wait_for_file "$2"', '_', helper, missing],
               capture_output=True)
print(int((time.monotonic() - started) * 100))
