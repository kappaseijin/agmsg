"""Failure attribution controls using the actual Bats test and real fixture."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

TARGET = '^launcher: reaps owned launcher and bridge but leaves foreign controls$'
TESTS = Path(__file__).resolve().parent
SOURCE = TESTS / 'test_codex_bridge_launcher.bats'

CASES = {
    'helper-failure': ('''skip_on_windows() { return 37; }''', 'helper-before', 37),
    'body-failure': ('''
eval "$(declare -f _reap_diag_phase | sed '1s/_reap_diag_phase/_original_reap_phase/')"
_reap_diag_phase() {
  _original_reap_phase "$@"
  if [ "$1" = snapshot ]; then
    false
    _reap_diag_log swallowed-failure 0
  fi
}
''', 'snapshot', 1),
    'foreign-contamination': ('''
eval "$(declare -f _launcher_snapshot_owned_pids | sed '1s/_launcher_snapshot_owned_pids/_original_snapshot/')"
_launcher_snapshot_owned_pids() {
  _original_snapshot
  printf '%s\\n' "$foreign"
}
''', 'foreign-exclusion', 1),
    'reap-failure': ('''_reap_test_owned_codex_processes() { return 39; }''', 'reap', 39),
    'wait-failure': ('''
eval "$(declare -f wait_for_pid_exit | sed '1s/wait_for_pid_exit/_original_wait_exit/')"
wait_for_pid_exit() {
  if [ "${_REAP_PHASE:-}" = exit-wait ]; then return 41; fi
  _original_wait_exit "$@"
}
''', 'exit-wait', 41),
    'positive': ('', 'body-complete', 0),
}


def controls(out, bats):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    results = []
    for name, (override, phase, expected) in CASES.items():
        directory = out / name
        directory.mkdir()
        with tempfile.NamedTemporaryFile(mode='w', prefix='.issue261-control-', suffix='.bats', dir=TESTS, delete=False) as script:
            script.write(SOURCE.read_text() + '\n' + override + '\n')
            path = Path(script.name)
        try:
            run = subprocess.run([bats, '--tap', '--filter', TARGET, str(path)],
                env=dict(os.environ, RUNNER_TEMP=str(directory)), capture_output=True, text=True, timeout=90)
        finally:
            path.unlink()
        (directory / 'bats.stdout').write_text(run.stdout)
        (directory / 'bats.stderr').write_text(run.stderr)
        packets = list(directory.glob('agmsg-reap-phase.*'))
        if len(packets) != 1:
            raise AssertionError(f'{name}: expected one diagnostic packet, found {len(packets)}')
        packet = packets[0].read_text()
        events = [dict(item.split('=', 1) for item in line.split() if '=' in item) for line in packet.splitlines()]
        terminal = [r for r in events if r['event'] == 'parent-end']
        body = [r for r in events if r['event'] == 'body-result']
        assert len(terminal) == 1 and int(terminal[0]['rc']) == expected and terminal[0]['phase'] == phase, (name, terminal)
        assert (run.returncode == 0) == (expected == 0), (name, run.returncode)
        if name == 'helper-failure':
            assert not body and terminal[0]['phase'] == phase, (name, body)
        else:
            assert len(body) == 1 and body[0]['phase'] == phase and int(body[0]['rc']) == expected, (name, body)
            assert any(r['event'] == 'cleanup-end' and int(r['primary_rc']) == expected for r in events), name
            assert terminal[0]['body_terminated'] == 'true', name
        assert 'swallowed-failure' not in packet, name
        result = dict(control=name, expected_phase=phase, primary_rc=expected,
                      bats_rc=run.returncode, parent_terminal=terminal[0], packet=packets[0].name)
        results.append(result)
        (out / 'controls.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps(result), flush=True)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--bats', default='bats')
    args = parser.parse_args()
    controls(args.out, args.bats)
