"""Validate downloaded bytes and re-evaluate both controls, without process access."""
import argparse
import hashlib
import json
from pathlib import Path
from lifetime_packet import evaluate_file


def replay(root):
    root=Path(root)
    runs=sorted(root.glob('preflight-*'))
    if not runs: raise ValueError('no saved preflight packets')
    outcomes=[]
    evaluations=[]
    for run in runs:
        hashes=json.loads((run/'hashes.json').read_text())
        for name,digest in hashes.items():
            path=(run/name.replace('\\','/')).resolve()
            if not path.is_relative_to(run.resolve()): raise ValueError('artifact path escape')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=digest: raise ValueError('artifact hash mismatch: '+name)
        packets=sorted(run.glob('*.jsonl'))
        if not packets or any(p.name not in {n.replace('\\','/') for n in hashes} for p in packets):
            raise ValueError('packet missing from saved hash manifest')
        for packet in packets:
            value=evaluate_file(packet)
            if value['comparison_gate']!='blocked': raise ValueError('WMI comparison unexpectedly accepted')
            evaluations.append(dict(packet=str(packet.relative_to(root)),source_packet_sha256=value['source_packet_sha256']))
            if packet.name=='packet.jsonl': outcomes.append(value['subject_exit_code'])
    return dict(artifact_persistence='verified',runs=len(runs),subject_exit_codes=outcomes,
                packet_replay='v3-derived',comparison_gate='blocked',evaluations=evaluations)

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('root'); a=p.parse_args()
    print(json.dumps(replay(a.root)))
