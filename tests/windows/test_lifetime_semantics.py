"""Same byte-preserving contract through Python and PowerShell entry points."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from lifetime_packet import evaluate_file
from test_lifetime_packet import packet

TOOLS=Path(__file__).parent


def legacy():
    rows=[r for r in packet() if r['record_type'] in ('process-start','process-stop','subscription-ready','subscription-ended','subject-result')]
    for r in rows:
        if r['record_type'].startswith('process-'):
            r.update(scope='fixture-owned-target',generation='old-known',generation_quality='known')
    rows.append(dict(record_type='taskkill',process_id='20',actor_time_utc='2026-09-06T00:00:00+00:00',rc=0))
    return rows


class SharedEntryTests(unittest.TestCase):
    def test_shared_entrypoints_preserve_raw_and_old_summaries(self):
        shell=shutil.which('powershell.exe') or shutil.which('pwsh')
        self.assertIsNotNone(shell,'PowerShell is required to verify the legacy entry point')
        for schema,base in [('legacy',legacy()),('v2',packet())]:
            for rc in (0,7):
                for mutation in ('complete','missing-stop','corrupt-clock-boundary','reuse-other-stop','reordered'):
                    with self.subTest(schema=schema,rc=rc,mutation=mutation), tempfile.TemporaryDirectory() as directory:
                        root=Path(directory); rows=json.loads(json.dumps(base))
                        next(r for r in rows if r['record_type']=='subject-result')['subject_exit_code']=rc
                        for i,r in enumerate(rows): r['record_id']=str(i)
                        if mutation=='missing-stop': rows=[r for r in rows if not (r['record_type']=='process-stop' and r['process_id']=='30')]
                        if mutation=='reuse-other-stop': rows=[r for r in rows if not (r['record_type']=='process-stop' and r['process_id']=='20' and r['event_time_created_raw']=='180')]
                        if mutation=='reordered': rows.reverse()
                        if mutation=='corrupt-clock-boundary':
                            next(r for r in rows if r['record_type']=='process-start')['event_time_created_raw']='broken'
                            rows=[r for r in rows if r.get('phase')!='end']
                        source=root/'packet.jsonl'
                        data=''.join(json.dumps(r)+'\n' for r in rows)
                        if mutation=='corrupt-clock-boundary': data+='broken-json\n'
                        source.write_text(data,encoding='utf8'); before=source.read_bytes()
                        old=root/'packet.summary.json'; old.write_text('{"comparison":"known","lifetime_ms":1}')
                        old_before=old.read_bytes()
                        expected=evaluate_file(source)
                        actual=subprocess.run([shell,'-NoProfile','-NonInteractive','-File',str(TOOLS/'process-lifetime-collector.ps1'),'-Mode','evaluate','-PacketPath',str(source)],text=True,capture_output=True,timeout=20)
                        self.assertEqual(actual.returncode,0,actual.stderr)
                        self.assertEqual(json.loads(actual.stdout),expected)
                        self.assertEqual(source.read_bytes(),before)
                        self.assertEqual(old.read_bytes(),old_before)
                        self.assertEqual(expected['source_packet_sha256'],hashlib.sha256(before).hexdigest())
                        self.assertEqual(expected['subject_exit_code'],rc)
                        self.assertEqual(expected['comparison_gate'],'blocked')
                        self.assertRegex(expected['evaluator_revision'],r'^[0-9a-f]{40}$')
                        if mutation=='missing-stop': self.assertIn('missing-stop-notification',expected['reasons'])
                        if mutation=='corrupt-clock-boundary':
                            for reason in ('clock-format-invalid','malformed-packet-record','operation-incomplete'):
                                self.assertIn(reason,expected['reasons'])
                        self.assertFalse((root/'gate.json').exists())

    def test_comparison_commands_remain_blocked_without_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'absent'
            for mode in ('preflight','condition'):
                run=subprocess.run([sys.executable,str(TOOLS/'lifetime_run.py'),mode,'--out',str(out)],capture_output=True,text=True,timeout=5)
                self.assertNotEqual(run.returncode,0)
                self.assertIn('comparison blocked',run.stderr)
                self.assertFalse(out.exists())

    def test_source_ids_use_original_lines_including_blanks(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'packet.jsonl'
            source.write_bytes(b'\n{}\n\nbroken-json\n')
            result=evaluate_file(source)
            digest=result['source_packet_sha256']
            self.assertEqual(result['record_ids'],[digest+':2',digest+':4'])
            self.assertIn('malformed-packet-record',result['reasons'])

    def test_conflicting_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'packet.jsonl'; source.write_text('{}\n')
            destination=source.with_suffix('.evaluation-v3.json'); destination.write_text('old')
            with self.assertRaisesRegex(ValueError,'existing evaluation differs'): evaluate_file(source)
            self.assertEqual(destination.read_text(),'old')
            with self.assertRaisesRegex(ValueError,'overwrite source'): evaluate_file(source,source)


if __name__=='__main__': unittest.main()
