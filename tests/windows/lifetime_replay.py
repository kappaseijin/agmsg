"""Validate downloaded bytes and re-evaluate both controls, without process access."""
import argparse
import hashlib
import json
from pathlib import Path
from lifetime_packet import evaluate, read_packet


def replay(root):
    root=Path(root)
    runs=list(root.glob('preflight-*'))
    if len(runs)!=2: raise ValueError('expected both preflight subject outcomes')
    outcomes=[]
    for run in runs:
        hashes=json.loads((run/'hashes.json').read_text())
        for name,digest in hashes.items():
            path=(run/name).resolve()
            if not path.is_relative_to(run.resolve()): raise ValueError('artifact path escape')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=digest: raise ValueError('artifact hash mismatch: '+name)
        for name,quality in [('packet','known'),('missing-child-stop','unknown')]:
            value=evaluate(read_packet(run/(name+'.jsonl')))
            saved=json.loads((run/(name+'.summary.json')).read_text())
            if value!=saved or value['collector_quality']!=quality:
                raise ValueError('independent packet replay mismatch: '+name)
        gate=json.loads((run/'gate.json').read_text())
        outcomes.append(gate['subject_exit_code'])
    if sorted(outcomes)!=[0,7]: raise ValueError('subject rc was not preserved')
    return dict(artifact_persistence='verified',runs=2,subject_exit_codes=sorted(outcomes),packet_replay='identical')

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('root'); a=p.parse_args()
    print(json.dumps(replay(a.root)))
