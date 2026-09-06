"""Executable adapter boundaries: original and observed rc/output must agree."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from lifetime_adapter import instrument, bash_executable
from lifetime_case import build_case

SOURCE=Path(__file__).resolve().parents[1]/'test_codex_bridge_launcher.bats'

def case_env(**extra):
    # The closure now carries the real diagnostic helpers, which append to
    # AGMSG_WINDOWS_DIAG_* targets when set. Drop them so cases stay side-effect free.
    env={k:v for k,v in __import__('os').environ.items() if not k.startswith('AGMSG_WINDOWS_DIAG_')}
    env.update(extra); return env

class AdapterTests(unittest.TestCase):
    def test_taskkill_failures_preserve_original_rc_and_output(self):
        source=SOURCE.read_text(); adapted=instrument(source)
        prefix='''
_lifetime_bind_root() { :; }
_lifetime_begin() { :; }
_lifetime_end() { printf 'observed rc=%s output=%s\\n' "$1" "$2" >> "$TRACE"; }
_windows_native_bridge_pids() { printf '11\\n22\\n'; }
taskkill() { printf 'ERROR: The process with PID %s could not be terminated.\\n' "$2"; return 5; }
_windows_native_wait_tasklist_gone() { return 0; }
_windows_native_assert_no_bridge_processes() { return 0; }
'''
        with tempfile.TemporaryDirectory() as temp:
            trace=Path(temp)/'trace'
            env=case_env(TRACE=trace.as_posix())
            results=[]
            for text in (source,adapted):
                code=build_case(text,'_windows_native_reap_bridge_root',prefix,'\n_windows_native_reap_bridge_root root\n')
                script=Path(temp)/'case.sh'
                script.write_text(code,encoding='utf8',newline='\n')
                results.append(subprocess.run([bash_executable(),script.as_posix()],env=env,capture_output=True,text=True))
            self.assertEqual(results[0].returncode,1,results[0].stderr)
            self.assertEqual(results[1].returncode,1,results[1].stderr)
            self.assertEqual(results[0].stdout,results[1].stdout)
            self.assertEqual(results[0].stderr,results[1].stderr)
            self.assertEqual(trace.read_text().count('observed rc=5'),2)

    def test_signal_wait_failure_suppression_is_preserved(self):
        source=SOURCE.read_text(); adapted=instrument(source)
        prefix='''
kill() { return 7; }
wait() { return 8; }
_lifetime_begin() { :; }
_lifetime_end() { printf 'observed=%s\\n' "$1"; }
'''
        results=[]
        with tempfile.TemporaryDirectory() as temp:
            for text in (source,adapted):
                script=Path(temp)/'case.sh'
                script.write_text(build_case(text,'cleanup_windows_native_processes',prefix,'\ncleanup_windows_native_processes 11 22'),encoding='utf8',newline='\n')
                results.append(subprocess.run([bash_executable(),'-e',script.as_posix()],env=case_env(),capture_output=True,text=True))
        original,observed=results
        self.assertEqual(original.returncode,0,original.stderr)
        self.assertEqual(observed.returncode,0,observed.stderr)
        self.assertEqual(observed.stdout.splitlines(),['observed=7','observed=7','observed=8','observed=8'])

    def test_unknown_source_rejected_before_adaptation(self):
        with self.assertRaises(ValueError): instrument(SOURCE.read_text().replace('load test_helper','load changed_helper'))

if __name__=='__main__': unittest.main()
