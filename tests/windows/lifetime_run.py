"""Isolated Windows collection and artifact persistence; never kills subjects.

`preflight` is the comparison gate. `condition` requires an explicit verifier
receipt and a disposable clone of the exact subject HEAD. Its exit code is the
subject exit code, independent of quality. No automatic retries.
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
from lifetime_packet import evaluate, read_packet

TOOLS=Path(__file__).resolve().parent
PS='powershell.exe'


def write_json(path,value):
    Path(path).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n',encoding='utf8')


def finalize(out):
    out=Path(out)
    for packet in sorted(out.glob('*.jsonl')):
        write_json(packet.with_suffix('.summary.json'),evaluate(read_packet(packet)))
    hashes={p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file() and p.name!='hashes.json'}
    write_json(out/'hashes.json',hashes)


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
    if os.name!='nt': raise SystemExit('Windows collector requires native Windows Python')
    if args.mode=='preflight':
        parent=Path(args.out).resolve(); parent.mkdir(parents=True,exist_ok=True)
        for rc in (0,7):
            run_id='preflight-'+str(rc)+'-'+uuid.uuid4().hex
            out=parent/run_id
            m=manifest('preflight',run_id)
            command=[PS,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(TOOLS/'lifetime-preflight-subject.ps1'),'-Directory','{CONTROL}','-RunId','{RUN_ID}','-ExitCode',str(rc)]
            actual=collect(out,command,m)
            rows=read_packet(out/'packet.jsonl'); summary=evaluate(rows)
            roots=[g for g in summary['generations'] if g['scope']=='target' and g['parent_generation'] is None]
            children=[g for g in summary['generations'] if g['parent_generation'] in [r['generation'] for r in roots]]
            required=[r for r in rows if r.get('record_type')=='required-process']
            children=[g for g in children if g['process_id'] in [r['process_id'] for r in required]]
            if actual!=rc or summary['collector_quality']!='known' or len(children)!=1:
                raise SystemExit('preflight lifecycle/subject rc gate failed: '+json.dumps(summary))
            child=children[0]
            boundaries={r['phase']:int(r['actor_filetime']) for r in rows if r.get('record_type')=='snapshot-boundary'}
            if not boundaries['before']<int(child['start_raw'])<=int(child['stop_raw'])<boundaries['after']:
                raise SystemExit('child did not start/stop between snapshots')
            negative=[r for r in rows if not (r.get('record_type')=='process-stop' and str(r.get('process_id'))==child['process_id'] and str(r.get('event_time_created_raw'))==child['stop_raw'])]
            if len(negative)!=len(rows)-1: raise SystemExit('negative control must drop exactly one child stop')
            (out/'missing-child-stop.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in negative),encoding='utf8')
            negative_summary=evaluate(negative)
            if negative_summary['collector_quality']!='unknown' or 'missing-stop-event' not in negative_summary['reasons']:
                raise SystemExit('missing child stop incorrectly accepted')
            write_json(out/'gate.json',dict(subject_exit_code=actual,positive='known',negative='unknown',child_generation=child['generation'],child_start_raw=child['start_raw'],child_stop_raw=child['stop_raw'],snapshots=boundaries))
            finalize(out)
            print(json.dumps(dict(run_id=run_id,gate='passed',subject_exit_code=actual,collector_quality='known',negative_quality='unknown')))
        return 0
    if not args.subject or not args.condition or not args.verifier_receipt:
        raise SystemExit('condition requires subject, condition and verifier receipt')
    receipt=json.loads(Path(args.verifier_receipt).read_text())
    current=subprocess.check_output(['git','-C',str(TOOLS),'rev-parse','HEAD'],text=True).strip()
    if receipt.get('collector_head')!=current or receipt.get('comparison_gate')!='accepted' or not receipt.get('artifact_url'):
        raise SystemExit('verifier receipt does not accept this fixed collector HEAD')
    run_id=args.condition+'-'+uuid.uuid4().hex
    out=Path(args.out).resolve()/run_id
    # Prepare metadata outside the collection dir, which collect creates once.
    prepared=out.parent/(run_id+'-application.json'); prepared.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run([os.sys.executable,str(TOOLS/'lifetime_adapter.py'),'prepare',args.subject,args.condition,str(prepared)],check=True)
    m=manifest(args.condition,run_id); m.update(json.loads(prepared.read_text())); m['required_root_keys']=['target','foreign']
    command=[bash_executable(),'-c','exec bats --tap --filter "$1" "$2"','_','^launcher: windows-native starts the bridge',str(Path(args.subject).resolve()/'tests/test_codex_bridge_launcher.bats')]
    rc=collect(out,command,m)
    (out/'application.json').write_bytes(prepared.read_bytes())
    (out/'verifier-receipt.json').write_bytes(Path(args.verifier_receipt).read_bytes())
    finalize(out)
    return rc if rc is not None else 124

if __name__=='__main__': raise SystemExit(main())
