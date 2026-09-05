"""Offline schema-v2 lifetime packet evaluator; no process control.

Uses integer FILETIME throughout: no float rounding of adjacent generations.
Unrelated OS events remain in the packet but do not certify fixture coverage.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re


def tick(value):
    try:
        text = str(value)
        return int(text) if re.fullmatch(r'[0-9]+', text) and int(text) > 0 else None
    except (ValueError, TypeError):
        return None


def evaluate(records):
    reasons = set()
    by_type = defaultdict(list)
    for r in records:
        if not isinstance(r, dict):
            reasons.add('malformed-packet-record')
            continue
        by_type[r.get('record_type')].append(r)
    manifests = by_type['run-manifest']
    manifest = manifests[0] if len(manifests) == 1 else {}
    run_id = manifest.get('run_id')
    subject = by_type['subject-result']
    subject = subject[0] if len(subject) == 1 else {}
    result = dict(schema_version=2, collector_run_id=run_id,
                  subject_status=subject.get('subject_status', 'unknown'),
                  subject_exit_code=subject.get('subject_exit_code'),
                  collector_quality='unknown', reasons=[], generations=[], operations=[],
                  required_but_unmatched=[], termination_actor='not-determined',
                  event_coverage='registered-roots-and-observed-descendants', uncaptured='unknown')
    if manifest.get('schema_version') != 2 or not run_id:
        result['reasons'] = ['unknown-schema']
        return result
    if manifest.get('clock_quality') != 'known': reasons.add('clock-correspondence-unknown')
    if not any(r.get('subscription_status') == 'ready' for r in by_type['subscription-ready']):
        reasons.add('subscription-not-ready')
    if not any(r.get('subscription_end_confirmed') is True for r in by_type['subscription-ended']):
        reasons.add('subscription-end-unconfirmed')
    if len(by_type['packet-close']) != 1: reasons.add('packet-incomplete')
    if subject.get('subject_status') != 'completed' or not isinstance(subject.get('subject_exit_code'), int):
        reasons.add('subject-result-incomplete')
    for q in by_type['quality']: reasons.add(q.get('reason') or 'collector-quality-unknown')

    # Pair within each PID, bounded by the next start. A missing stop is never
    # supplied by a later generation. Conflicting evidence stays ambiguous.
    starts, stops = defaultdict(list), defaultdict(list)
    for kind, dest in [('process-start', starts), ('process-stop', stops)]:
        for r in by_type[kind]:
            pid = str(r.get('process_id', ''))
            t = tick(r.get('event_time_created_raw'))
            if t is None or r.get('event_clock_quality') != 'known':
                reasons.add('clock-correspondence-unknown')
                continue
            dest[pid].append((t, r))
    gens, pid_gens = [], defaultdict(list)
    for pid in sorted(starts):
        ordered = sorted(starts[pid], key=lambda pair: pair[0])
        for i, (t, r) in enumerate(ordered):
            next_t = ordered[i+1][0] if i+1 < len(ordered) else None
            matches = [s for s, _ in stops[pid] if s >= t and (next_t is None or s < next_t)]
            duplicates = sum(s == t for s, _ in ordered) > 1
            error = 'generation-ambiguous' if duplicates or len(matches) > 1 else ('missing-stop-event' if not matches else None)
            g = dict(generation=f'{run_id}/{pid}/{t}', process_id=pid,
                     parent_process_id=str(r.get('parent_process_id','')), start=t,
                     stop=matches[0] if len(matches)==1 and not duplicates else None,
                     next_start=next_t, scope=None, parent_generation=None, errors=[])
            if error: g['errors'].append(error)
            gens.append(g)
            pid_gens[pid].append(g)

    def possible(pid, begin, end):
        # Include a stopped candidate before the operation only if no later
        # generation exists before its end. This retains already-gone PIDs,
        # without equating tasklist-gone with an observed process stop.
        candidates = []
        for g in pid_gens.get(str(pid), []):
            if g['start'] > end: continue
            if g['next_start'] is not None and g['next_start'] <= begin: continue
            candidates.append(g)
        return candidates

    bound = set()
    for r in by_type['root-binding']:
        t = tick(r.get('actor_filetime'))
        pid = str(r.get('process_id',''))
        candidates = [] if t is None else [g for g in pid_gens[pid] if g['start'] <= t and (g['stop'] is None or t <= g['stop']) and (g['next_start'] is None or t < g['next_start'])]
        if r.get('match') is not True or not r.get('creation_date') or len(candidates)!=1:
            reasons.add('missing-start-event' if not pid_gens[pid] else 'root-generation-unknown')
            result['required_but_unmatched'].append(dict(root_key=r.get('root_key'), process_id=pid, candidates=[g['generation'] for g in candidates]))
            continue
        g=candidates[0]
        if g['scope'] is not None and g['scope'] != r.get('root_key'):
            reasons.add('root-generation-unknown')
        g['scope']=r.get('root_key')
        bound.add(g['generation'])
    if not bound: reasons.add('root-unbound')
    for key in manifest.get('required_root_keys', []):
        if not any(g['scope']==key for g in gens): reasons.add('root-unbound')

    # Parent must be alive at the child's start. Descendants remain attached
    # after their parent/root stops. Iteration supports arbitrary delivery order.
    for _ in range(len(gens)):
        changed=False
        for g in gens:
            if g['scope'] is not None: continue
            parents=[p for p in pid_gens.get(g['parent_process_id'],[]) if p['start'] < g['start'] and (p['stop'] is None or g['start'] < p['stop']) and (p['next_start'] is None or g['start'] < p['next_start'])]
            scoped=[p for p in parents if p['scope'] is not None]
            if scoped and len(parents)!=1:
                reasons.add('parent-generation-unknown')
            elif len(scoped)==1:
                p=scoped[0]
                g['scope']=p['scope']; g['parent_generation']=p['generation']
                changed=True
        if not changed: break
    for g in gens:
        if g['scope'] is None: continue
        reasons.update(g['errors'])
        result['generations'].append(dict(generation=g['generation'], process_id=g['process_id'], scope=g['scope'], parent_generation=g['parent_generation'], start_raw=str(g['start']), stop_raw=str(g['stop']) if g['stop'] is not None else None, lifetime_ms=(g['stop']-g['start'])/10000 if g['stop'] is not None else None, errors=g['errors']))
    # Stop without a corresponding start, whose reported parent belongs to our
    # scope, is evidence of an unobserved child, not harmless background noise.
    for pid, events in stops.items():
        for t, r in events:
            if not any(g['stop']==t for g in pid_gens.get(pid,[])):
                parents=pid_gens.get(str(r.get('parent_process_id','')),[])
                if any(p['scope'] is not None for p in parents):
                    reasons.add('missing-start-event')

    for required in by_type['required-process']:
        candidates=[g for g in gens if g['process_id']==str(required.get('process_id')) and g['scope']==required.get('root_key')]
        if len(candidates)!=1:
            reasons.add('missing-start-event')
            result['required_but_unmatched'].append(dict(process_id=required.get('process_id'),reason='required-child-unmatched'))

    operations=defaultdict(list)
    for r in by_type['operation']: operations[str(r.get('operation_id',''))].append(r)
    for op_id in sorted(operations):
        rows=operations[op_id]
        begins=[r for r in rows if r.get('phase')=='begin']
        ends=[r for r in rows if r.get('phase')=='end']
        if not op_id or len(begins)!=1 or len(ends)!=1:
            reasons.add('operation-incomplete')
            result['operations'].append(dict(operation_id=op_id, quality='unknown', pid_mappings=[]))
            continue
        begin,end=begins[0],ends[0]
        a,b=tick(begin.get('actor_filetime')),tick(end.get('actor_filetime'))
        op=dict(operation_id=op_id, operation=begin.get('operation'), rc=end.get('rc'), pid_mappings=[], quality='known')
        if a is None or b is None or b<a:
            reasons.add('clock-correspondence-unknown'); op['quality']='unknown'
        if not isinstance(end.get('rc'),int): reasons.add('operation-incomplete')
        if begin.get('operation') != 'taskkill':
            if begin.get('namespace')=='msys':
                native=begin.get('native_pid')
                candidates=[] if a is None or b is None else possible(native,a,b)
                if begin.get('mapping_quality')!='known' or len(candidates)!=1 or candidates[0]['errors']:
                    reasons.add('msys-native-mapping-unknown'); op['quality']='unknown'
                op['pid_mappings'].append(dict(msys_pid=begin.get('process_id'),native_pid=native,candidates=[g['generation'] for g in candidates]))
            result['operations'].append(op)
            continue
        output=str(end.get('output',''))
        pids={str(begin.get('process_id',''))}
        lines=[line.strip() for line in output.splitlines() if line.strip()]
        for line in lines:
            found=re.findall(r'\bPID\s+(\d+)\b',line,re.I)
            pids.update(found)
            if not found or not re.match(r'^(SUCCESS|ERROR):',line):
                reasons.add('taskkill-output-unparsed'); op['quality']='unknown'
        if not lines: reasons.add('taskkill-output-unparsed')
        for pid in sorted(pids):
            candidates=[] if a is None or b is None else possible(pid,a,b)
            mapping=dict(process_id=pid,candidates=[g['generation'] for g in candidates],quality='unknown',stop_relation='unknown',hold_ms=None)
            if len(candidates)==1 and candidates[0]['scope'] is not None and not candidates[0]['errors']:
                g=candidates[0]; stop=g['stop']
                mapping.update(quality='known',stop_relation='before-operation' if stop<a else ('during-operation' if stop<=b else 'after-operation'),hold_ms=(a-g['start'])/10000)
            else:
                reasons.add('required-pid-unmatched'); op['quality']='unknown'
                result['required_but_unmatched'].append(dict(operation_id=op_id,**mapping))
            op['pid_mappings'].append(mapping)
        result['operations'].append(op)
    result['generations'].sort(key=lambda g:g['generation'])
    result['reasons']=sorted(reasons)
    result['collector_quality']='unknown' if reasons else 'known'
    return result


def read_packet(path):
    rows=[]
    try:
        with open(path, encoding='utf-8-sig') as source:
            for line in source:
                if line.strip():
                    try: rows.append(json.loads(line))
                    except (ValueError,TypeError): rows.append(dict(record_type='quality',reason='malformed-packet-record'))
    except OSError:
        rows.append(dict(record_type='quality',reason='packet-unavailable'))
    return rows


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('packet')
    args=parser.parse_args()
    print(json.dumps(evaluate(read_packet(args.packet)),sort_keys=True,separators=(',',':')))

if __name__=='__main__': main()
