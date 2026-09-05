"""Executable adapter boundaries: original and observed rc/output must agree."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from lifetime_adapter import instrument

SOURCE=Path(__file__).resolve().parents[1]/'test_codex_bridge_launcher.bats'

def function(source,name):
    start=source.index(name+'() {')
    return source[start:source.index('\n}\n',start)+3]

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
            env=dict(__import__('os').environ,TRACE=str(trace))
            results=[]
            for text in (source,adapted):
                code=prefix+function(text,'_windows_native_reap_bridge_root')+'\n_windows_native_reap_bridge_root root\n'
                results.append(subprocess.run(['bash','-c',code],env=env,capture_output=True,text=True))
            self.assertEqual(results[0].returncode,1)
            self.assertEqual(results[1].returncode,1)
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
        original=subprocess.run(['bash','-ec',prefix+function(source,'cleanup_windows_native_processes')+'\ncleanup_windows_native_processes 11 22'],capture_output=True,text=True)
        observed=subprocess.run(['bash','-ec',prefix+function(adapted,'cleanup_windows_native_processes')+'\ncleanup_windows_native_processes 11 22'],capture_output=True,text=True)
        self.assertEqual(original.returncode,0)
        self.assertEqual(observed.returncode,0)
        self.assertEqual(observed.stdout.splitlines(),['observed=7','observed=7','observed=8','observed=8'])

    def test_unknown_source_rejected_before_adaptation(self):
        with self.assertRaises(ValueError): instrument(SOURCE.read_text().replace('load test_helper','load changed_helper'))

if __name__=='__main__': unittest.main()
