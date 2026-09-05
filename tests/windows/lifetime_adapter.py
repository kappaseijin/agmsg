"""Publish atomic observer controls and instrument only pinned fixture sources."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

HEADS = {'original':'32f6411e4e7a13d42e0811fc023c2d8d51138cb1',
         'polling-hold':'28b2b78f4e48297b62a970921a0cc9f6e932fdd7',
         'stable-hold':'b0156754dd706bf3ffc178c654f6ccbf23f340d8'}


def bash_executable():
    if os.name!='nt': return 'bash'
    candidate=Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'Git/bin/bash.exe'
    if not candidate.is_file(): raise RuntimeError('Git Bash executable unavailable')
    return str(candidate)


def publish(directory, run_id, record):
    record=dict(record, run_id=run_id, actor_filetime=str(time.time_ns()//100+116444736000000000),
                actor_time=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat())
    directory=Path(directory)
    name=uuid.uuid4().hex
    temporary=directory/(name+'.tmp')
    temporary.write_text(json.dumps(record,separators=(',',':')),encoding='utf8')
    temporary.rename(directory/(name+'.control.json'))


def instrument(source):
    # Exact call-site edits: do not wrap a function in `if`, change errexit,
    # suppress rc, alter the kill set, or modify hold/deadline constants.
    def replace(old,new,count=1):
        nonlocal source
        if source.count(old)!=count:
            raise ValueError('fixture adapter anchor mismatch: '+repr(old))
        source=source.replace(old,new)
    replace('load test_helper','load test_helper\nsource "$AGMSG_LIFETIME_ADAPTER_SHELL"')
    replace('setup() {\n','setup() {\n  _lifetime_controller\n')
    task='    if taskkill_output="$(MSYS_NO_PATHCONV=1 taskkill /PID "$pid" /T /F 2>&1)"; then'
    replace(task,'    _lifetime_begin taskkill native "$pid"\n'+task)
    # Use rc at the same call site, before it can be overwritten by diagnostics.
    end='''    fi
    if _windows_native_wait_tasklist_gone "$pid"; then'''
    diagnostic='''    _windows_native_diag_record_taskkill "taskkill-$pid" "$root" "$pid"'''
    if diagnostic in source:
        replace(diagnostic,'    _lifetime_end "$taskkill_rc" "$taskkill_output"\n'+diagnostic)
    else:
        replace(end,'''    fi
    _lifetime_end "$taskkill_rc" "$taskkill_output"
    if _windows_native_wait_tasklist_gone "$pid"; then''')
    # Restrict built-in instrumentation to the cleanup function, preserving
    # original protection/suppression and every original signal/wait command.
    start=source.index('cleanup_windows_native_processes() {')
    finish=source.index('\n}\n',start)+3
    block=source[start:finish]
    for variable in ('dispatcher_pid','parent_pid'):
        original=f'  if kill "${variable}" 2>/dev/null; then kill_rc=0; else kill_rc="$?"; fi'
        if original in block:
            block=block.replace(original,f'  _lifetime_begin signal msys "${variable}"\n'+original+'\n  _lifetime_end "$kill_rc" ""')
        else:
            original=f'  kill "${variable}" 2>/dev/null || true'
            if original not in block: raise ValueError('signal anchor missing')
            block=block.replace(original,f'  _lifetime_begin signal msys "${variable}"\n  _lifetime_rc=0\n  kill "${variable}" 2>/dev/null || _lifetime_rc=$?\n  _lifetime_end "$_lifetime_rc" ""')
        original=f'  wait "${variable}" 2>/dev/null || true'
        if original not in block: raise ValueError('wait anchor missing')
        block=block.replace(original,f'  _lifetime_begin wait msys "${variable}"\n  _lifetime_rc=0\n  wait "${variable}" 2>/dev/null || _lifetime_rc=$?\n  _lifetime_end "$_lifetime_rc" ""')
    source=source[:start]+block+source[finish:]
    # Bind from native metadata when each existing root is observed. The
    # predicate stays separate from (and cannot change) reaper selection.
    anchor='  local root="$1" pids pid taskkill_output taskkill_rc wait_rc reap_status=0'
    replace(anchor,anchor+'\n  _lifetime_bind_root "$root"')
    anchor='      printf \'%s\\n\' "$pids"\n      return 0'
    replace(anchor,'      _lifetime_bind_root "$root"\n'+anchor)
    replace('  if ! teardown_test_env; then','  _lifetime_save_fixture\n  if ! teardown_test_env; then')
    replace('  return "$cleanup_rc"','  _lifetime_emit cleanup-result "rc=$cleanup_rc"\n  return "$cleanup_rc"')
    anchor='  local foreign_root="$BATS_TEST_TMPDIR/foreign-bridge-root"'
    replace(anchor,'  _lifetime_bind_root "$TEST_SKILL_DIR"\n'+anchor)
    for anchor in ('  _windows_native_diag_snapshot short-child-before-release "$AGMSG_WINDOWS_DIAG_TARGET_ROOT"',
                   '  _windows_native_diag_snapshot already-ended-before-release "$ended_root"'):
        if anchor in source:
            root='"$ended_root"' if 'already-ended' in anchor else '"$TEST_SKILL_DIR"'
            replace(anchor,'  _lifetime_bind_root '+root+'\n'+anchor)
    return source


def main():
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest='command',required=True)
    emit=sub.add_parser('emit'); emit.add_argument('record_type'); emit.add_argument('fields',nargs='*')
    prepare=sub.add_parser('prepare'); prepare.add_argument('subject'); prepare.add_argument('condition',choices=HEADS); prepare.add_argument('out')
    controller=sub.add_parser('controller'); controller.add_argument('msys_pid')
    a=p.parse_args()
    if a.command=='prepare':
        root=Path(a.subject); head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
        if head!=HEADS[a.condition]: raise SystemExit('subject HEAD mismatch')
        path=root/'tests/test_codex_bridge_launcher.bats'
        original=subprocess.check_output(['git','-C',str(root),'show',head+':tests/test_codex_bridge_launcher.bats'])
        if path.read_bytes()!=original: raise SystemExit('subject fixture is dirty')
        modified=instrument(original.decode())
        path.write_text(modified,encoding='utf8')
        subprocess.run(['git','-C',str(root),'add','tests/test_codex_bridge_launcher.bats'],check=True)
        applied_tree=subprocess.check_output(['git','-C',str(root),'write-tree'],text=True).strip()
        Path(a.out).write_text(json.dumps(dict(applied_tree=applied_tree,condition_id=a.condition,subject_head=head,subject_tree=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD^{tree}'],text=True).strip(),original_sha256=hashlib.sha256(original).hexdigest(),applied_sha256=hashlib.sha256(path.read_bytes()).hexdigest()),indent=2)+'\n')
    elif a.command=='controller':
        subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(Path(__file__).with_name('lifetime-bind.ps1')),'-Directory',os.environ['AGMSG_LIFETIME_CONTROL'],'-RunId',os.environ['AGMSG_LIFETIME_RUN_ID'],'-ControllerPid',str(os.getppid()),'-MsysPid',a.msys_pid],check=True)
    else:
        record=dict(record_type=a.record_type)
        for item in a.fields:
            key,value=item.split('=',1)
            if key=='rc': value=int(value)
            record[key]=value
        if a.record_type=='operation' and record.get('phase')=='begin' and record.get('namespace')=='msys':
            record['mapping_quality']='unknown'
            try:
                lines=subprocess.check_output(['ps','-p',record['process_id']],text=True).splitlines()
                header=lines[0].split(); native_index=header.index('WINPID'); pid_index=header.index('PID')
                matches=[line.split() for line in lines[1:] if len(line.split())>native_index and line.split()[pid_index]==record['process_id']]
                if len(matches)==1:
                    record['native_pid']=matches[0][native_index]
                    record['mapping_quality']='known'
            except (OSError,ValueError,IndexError,subprocess.CalledProcessError): pass
        publish(os.environ['AGMSG_LIFETIME_CONTROL'],os.environ['AGMSG_LIFETIME_RUN_ID'],record)

if __name__=='__main__': main()
