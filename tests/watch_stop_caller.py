"""Execute the actual broad-watcher caller and a real Bats/xargs EOF control."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import sys

TESTS=Path(__file__).resolve().parent
source=(TESTS/'test_watch.bats').read_text()
a=source.index('@test "watch: a broad (non-actas) watcher')
b=source.index('\n@test ',a+1)
body=source[source.index('{\n',a)+2:b].rstrip()
assert body.endswith('}')
body=body[:-1]
with tempfile.TemporaryDirectory(prefix='watch-stop-caller-') as temp:
    root=Path(temp); skill=root/'skill'; scripts=skill/'scripts'; scripts.mkdir(parents=True); (skill/'run').mkdir()
    for name in ('join.sh','send.sh'): (scripts/name).write_text('#!/usr/bin/env bash\nexit 0\n')
    (scripts/'watch.sh').write_text('''#!/usr/bin/env bash
if [ "$CONTROL" = childrc ]; then trap 'exit 42' TERM
else trap 'rm -f "$TEST_SKILL_DIR/run/ready.team__alice"; exit 0' TERM; fi
if [ "$CONTROL" = sentinel ]; then : > "$TEST_SKILL_DIR/run/ready.team__alice"; fi
printf 'M-broad-marker\\n'
while :; do sleep 0.05; done
''')
    prefix='''source "$HELPERS/test_helper.bash"
source "$HELPERS/watch_stop_helper.bash"
if [ "$CONTROL" = stop ]; then
  eval "$(declare -f _watch_stop_owned | sed '1s/_watch_stop_owned/_real_watch_stop_owned/')"
  _watch_stop_owned() { _real_watch_stop_owned; return 1; }
fi
'''
    driver=root/'driver.sh'; driver.write_text(prefix+'\nbroad_test() {\n'+body+'\n}\nbroad_test\n')
    for mode,expected in [('normal',0),('stop',1),('sentinel',1),('childrc',1)]:
        env=dict(os.environ,CONTROL=mode,HELPERS=str(TESTS),TEST_SKILL_DIR=str(skill),SCRIPTS=str(scripts),PROJ=str(root/'project'),RUNNER_TEMP=str(root),AGMSG_TEST_WAIT_TIMEOUT_S='2',AGMSG_TEST_WAIT_POLL_S='0.05')
        result=subprocess.run(['bash',str(driver)],env=env,capture_output=True,text=True,timeout=15)
        if result.returncode!=expected:
            raise AssertionError(f'{mode}: expected {expected}, got {result.returncode}: {result.stdout} {result.stderr}')
        if mode=='childrc':
            # #268: the caller's failure must come from the child-status branch
            # (ownership, signal, and wait succeeded), not from another branch.
            lines=result.stderr.splitlines()
            if not any('phase=child-status' in l and 'result=42' in l for l in lines):
                raise AssertionError(f'childrc: child-status result=42 not reached: {result.stderr}')
            if not any('phase=test-stop-end' in l and 'result=1' in l for l in lines):
                raise AssertionError(f'childrc: stop helper did not fail: {result.stderr}')
            if not any('phase=wait-exit' in l and 'result=0' in l for l in lines):
                raise AssertionError(f'childrc: child was not reaped within the bound: {result.stderr}')
            if any('phase=ownership-unknown' in l or 'phase=recovery' in l for l in lines):
                raise AssertionError(f'childrc: failed through another branch: {result.stderr}')
            print(f'caller-control={mode} rc={result.returncode} child-status=42')
        else:
            print(f'caller-control={mode} rc={result.returncode}')
    # The test body reaches its final successful command, but another child
    # still owns fd3. A marker/individual assertion is not a suite EOF proof.
    fixture=root/'pipe.bats'
    fixture.write_text('''#!/usr/bin/env bats
@test "separate child keeps the TAP descriptor open" {
  bash -c 'for i in {1..200}; do [ -f "$RELEASE" ] && exit 0; sleep 0.05; done' &
  : > "$BODY_DONE"
}
''')
    env=dict(os.environ,RELEASE=str(root/'release'),BODY_DONE=str(root/'body-done'))
    runner=subprocess.Popen(['bash','-c','printf "%s\\n" "$1" | xargs bats --tap; rc=$?; printf "xargs-end rc=%s\\n" "$rc"; exit "$rc"','_',str(fixture)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+5
        while not (root/'body-done').exists() and time.monotonic()<deadline: time.sleep(.02)
        if not (root/'body-done').exists(): raise AssertionError('test body did not reach its end')
        try:
            runner.communicate(timeout=.2)
            raise AssertionError('pipe-holding control unexpectedly reached suite EOF')
        except subprocess.TimeoutExpired: pass
    finally:
        (root/'release').touch()
        stdout,stderr=runner.communicate(timeout=12)
    if runner.returncode or 'ok 1 separate child' not in stdout or 'xargs-end rc=0' not in stdout:
        raise AssertionError(f'Bats/xargs did not terminate: {stdout} {stderr}')
    print('pipe-control: body-end precedes suite EOF; release -> Bats/xargs/driver rc=0')
