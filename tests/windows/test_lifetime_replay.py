"""Windows hash-manifest paths must replay on a POSIX download host."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from lifetime_packet import evaluate
from lifetime_replay import replay
from test_lifetime_packet import packet

class ReplayTests(unittest.TestCase):
    def test_windows_paths_and_corrupt_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for rc in (0,7):
                out=root/f'preflight-{rc}'; out.mkdir()
                rows=packet()
                next(r for r in rows if r['record_type']=='subject-result')['subject_exit_code']=rc
                negative=[r for r in rows if not (r['record_type']=='process-stop' and r['process_id']=='30')]
                for name,data in [('packet',rows),('missing-child-stop',negative)]:
                    (out/(name+'.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in data))
                    (out/(name+'.summary.json')).write_text(json.dumps(dict(collector_quality='known',lifetime_ms=1)))
                (out/'gate.json').write_text(json.dumps(dict(subject_exit_code=rc)))
                (out/'control').mkdir(); (out/'control'/'example.json').write_text('{}')
                hashes={str(p.relative_to(out)).replace('/','\\'):hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*') if p.is_file()}
                (out/'hashes.json').write_text(json.dumps(hashes))
            before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            value=replay(root)
            self.assertEqual(value['subject_exit_codes'],[0,7])
            self.assertEqual(value['comparison_gate'],'blocked')
            self.assertTrue(all(p.read_bytes()==data for p,data in before.items()))
            (root/'preflight-0/control/example.json').write_text('changed')
            with self.assertRaisesRegex(ValueError,'hash mismatch'): replay(root)

if __name__=='__main__': unittest.main()
