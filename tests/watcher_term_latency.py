"""Measure how long the stop fixture's watcher takes to exit after TERM.

TERM lands half a second into the watcher's sleep, so a watcher that defers its
trap until the sleep returns takes about the remaining half second, while one
that waits interruptibly exits at once. Written in Python because bash has no
sub-second clock everywhere the suite runs. Prints hundredths of a second.
"""
import os
import signal
import subprocess
import sys
import time

watcher, ready = sys.argv[1], sys.argv[2]
env = dict(os.environ, MODE='stop', STOP_ROOT=os.path.dirname(ready))
if os.path.exists(ready):
    os.unlink(ready)
child = subprocess.Popen(['bash', watcher], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
deadline = time.monotonic() + 10
while not os.path.exists(ready):
    if time.monotonic() > deadline:
        child.kill()
        sys.exit('watcher never signalled ready')
    time.sleep(0.005)
time.sleep(0.5)
started = time.monotonic()
child.send_signal(signal.SIGTERM)
child.wait()
print(int((time.monotonic() - started) * 100))
