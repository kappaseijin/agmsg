"""Isolated Windows collection and artifact persistence; never kills subjects.

`preflight` and `condition` stay blocked by the v3 semantics contract.
`finalize` adds a versioned evaluation without replacing historical evidence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import time
import uuid
from lifetime_adapter import HEADS, publish, bash_executable
from lifetime_packet import evaluate_file

TOOLS=Path(__file__).resolve().parent
PS='powershell.exe'


def write_json(path,value):
    Path(path).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n',encoding='utf8')


def finalize(out):
    # Re-evaluation adds only v3 outputs. Existing summaries, gate and hashes
    # remain historical evidence, including any obsolete known assertions.
    out=Path(out)
    for packet in sorted(out.glob('*.jsonl')):
        evaluate_file(packet)


def collect(out,command,manifest):
    out=Path(out).resolve(); out.mkdir(parents=True,exist_ok=False)
    control=out/'control'; control.mkdir()
    write_json(out/'manifest.json',manifest)
    env=dict(os.environ,AGMSG_LIFETIME_CONTROL=control.as_posix(),AGMSG_LIFETIME_RUN_ID=manifest['run_id'],AGMSG_LIFETIME_OUTPUT=out.as_posix(),AGMSG_LIFETIME_TOOLS=TOOLS.as_posix(),AGMSG_LIFETIME_ADAPTER_SHELL=(TOOLS/'lifetime-adapter.sh').as_posix())
    collector_args=[PS,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(TOOLS/'process-lifetime-collector.ps1'),'-Mode','collect','-PacketPath',str(out/'packet.jsonl'),'-ControlDirectory',str(control),'-RunManifestPath',str(out/'manifest.json')]
    subject_rc=None
    with (out/'collector.stdout').open('wb') as stdout, (out/'collector.stderr').open('wb') as stderr:
        collector=subprocess.Popen(collector_args,stdout=stdout,stderr=stderr,env=env)
        ready_limit=time.monotonic()+30
        ready=False
        while time.monotonic()<ready_limit and collector.poll() is None:
            if (control/'ready.json').exists():
                value=json.loads((control/'ready.json').read_text(encoding='utf-8-sig'))
                ready=value.get('run_id')==manifest['run_id'] and value.get('status')=='ready'
                break
            time.sleep(.05)
        try:
            if ready:
                command=[s.replace('{CONTROL}',str(control)).replace('{RUN_ID}',manifest['run_id']) for s in command]
                with (out/'subject.stdout').open('wb') as so, (out/'subject.stderr').open('wb') as se:
                    subject=subprocess.Popen(command,stdout=so,stderr=se,env=env)
                    # Deadline records the failure but does not kill or alter
                    # fixture cleanup. The job timeout is the outer boundary.
                    try: subject_rc=subject.wait(timeout=manifest['subject_deadline_s'])
                    except subprocess.TimeoutExpired:
                        publish(control,manifest['run_id'],dict(record_type='quality',reason='subject-deadline',quality='unknown'))
                publish(control,manifest['run_id'],dict(record_type='subject-result',subject_status='completed' if subject_rc is not None else 'aborted',subject_exit_code=subject_rc,subject_process_terminated=subject.poll() is not None))
            else:
                publish(control,manifest['run_id'],dict(record_type='subject-result',subject_status='not-started',subject_exit_code=None))
        finally:
            adapter_errors=out/'adapter.stderr'
            if adapter_errors.exists() and adapter_errors.stat().st_size:
                publish(control,manifest['run_id'],dict(record_type='quality',reason='adapter-error',quality='unknown'))
            publish(control,manifest['run_id'],dict(record_type='stop-request'))
            try: collector_rc=collector.wait(timeout=20)
            except subprocess.TimeoutExpired: collector_rc=None
            write_json(out/'execution.json',dict(subject_exit_code=subject_rc,collector_exit_code=collector_rc,ready=ready))
            finalize(out)
    return subject_rc


def manifest(condition,run_id):
    def version(args):
        try: return subprocess.check_output(args,stderr=subprocess.STDOUT,text=True).strip()
        except (OSError,subprocess.CalledProcessError): return 'unknown'
    return dict(record_type='run-manifest',schema_version=2,run_id=run_id,condition_id=condition,clock_quality='known',clock_basis='same-host UTC FILETIME; event raw ticks and actor ticks stored separately',collector_deadline_s=600,subject_deadline_s=540,required_root_keys=['target'],tools_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in TOOLS.iterdir() if p.suffix in ('.py','.ps1','.sh')},collector_head=version(['git','-C',str(TOOLS),'rev-parse','HEAD']),os=platform.platform(),bash=version([bash_executable(),'--version']),powershell=version([PS,'-NoProfile','-Command','$PSVersionTable.PSVersion.ToString()']),bats=version([bash_executable(),'-c','bats --version']),job=dict(run_id=os.environ.get('GITHUB_RUN_ID'),run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),job=os.environ.get('GITHUB_JOB')),observation_perturbation='nonzero; original fixture deadlines unchanged')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['preflight','condition','finalize'])
    parser.add_argument('--out',required=True)
    parser.add_argument('--condition',choices=HEADS)
    parser.add_argument('--subject')
    parser.add_argument('--verifier-receipt')
    args=parser.parse_args()
    if args.mode=='finalize':
        for directory in Path(args.out).glob('*'):
            if directory.is_dir(): finalize(directory)
        return 0
    raise SystemExit('comparison blocked: WMI notification time/generation are unproven; offline v3 evaluation only')

if __name__=='__main__': raise SystemExit(main())
