"""Versioned offline notification evaluation; never infer process lifetimes.

Raw records and legacy summaries are evidence, not trusted evaluation inputs.
Both PowerShell and Python consumers use this evaluator. No process control.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess

# Last FILETIME representable by DateTime/UTC through 9999-12-31.
MAX_FILETIME = 2650467743999999999


def tick(value):
    text = str(value)
    if not re.fullmatch(r'[0-9]+', text):
        return None
    try:
        number = int(text)
    except ValueError:
        return None
    return number if 0 <= number <= MAX_FILETIME else None


def revision():
    value = subprocess.check_output(
        ['git', '-C', str(Path(__file__).parent), 'rev-parse', 'HEAD'], text=True).strip()
    if not re.fullmatch(r'[0-9a-f]{40}', value):
        raise ValueError('fixed evaluator commit unavailable')
    return value


def evaluate(records, source_hash=None, evaluator_revision=None, source_lines=None):
    # Direct in-memory callers get an explicitly serialized source identity.
    # File entry points always supply the SHA of the original bytes instead.
    source_hash = source_hash or hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    rows, by_type = [], defaultdict(list)
    raw_rows = []
    details = []

    def reason(code, ids=(), target='packet'):
        detail = dict(reason=str(code), record_ids=sorted(set(ids)), target=target)
        if detail not in details:
            details.append(detail)

    for index, record in enumerate(records):
        line = source_lines[index] if source_lines is not None else index + 1
        rid = str(record.get('record_id', f'{source_hash}:{line}')) if isinstance(record, dict) else f'{source_hash}:{line}'
        raw_rows.append((rid, record))
        if not isinstance(record, dict) or not isinstance(record.get('record_type'), str):
            reason('malformed-packet-record', [rid])
            rows.append((rid, {}))
            continue
        rows.append((rid, record))
        by_type[record['record_type']].append((rid, record))
    ids = [rid for rid, _ in rows]
    if len(set(ids)) != len(ids):
        reason('duplicate-record-id', ids)
    manifests = by_type['run-manifest']
    manifest = manifests[0][1] if len(manifests) == 1 else {}
    run_id = manifest.get('run_id')
    # Legacy is recognized positively by its target-only trace schema, not
    # merely by the absence of a v2 manifest.
    legacy = not manifests and not any('schema_version' in r for _, r in rows) and any(r.get('scope') == 'fixture-owned-target' and r.get('record_type') in ('process-start', 'process-stop') for _, r in rows)
    schema = 2 if len(manifests) == 1 and manifest.get('schema_version') == 2 and run_id else ('legacy' if legacy else 'unknown')
    if schema == 'unknown':
        reason('unknown-schema', [i for i, _ in manifests])
    run_ids = {str(r['run_id']) for _, r in rows if r.get('run_id')}
    subscription_sources = {str(r['start_source']) for _, r in by_type['subscription-ready'] if r.get('start_source')}
    if len(manifests) > 1 or len(run_ids) > 1 or len(subscription_sources) > 1:
        reason('multiple-runs', [i for i, r in rows if r.get('run_id')])
    if not any(r.get('subscription_status') == 'ready' for _, r in by_type['subscription-ready']):
        reason('subscription-not-ready', [i for i, _ in by_type['subscription-ready']])
    if not any(r.get('subscription_end_confirmed') is True for _, r in by_type['subscription-ended']):
        reason('subscription-end-unconfirmed', [i for i, _ in by_type['subscription-ended']])
    if schema != 'legacy' and len(by_type['packet-close']) != 1:
        reason('packet-incomplete', [i for i, _ in by_type['packet-close']])
    # Legacy writer closes its packet with a confirmed subscription-ended row.
    for i, r in by_type['quality']:
        reason(r.get('reason') or 'collector-quality-unknown', [i])
    capture_faults = bool(details)

    subjects = by_type['subject-result']
    subject = subjects[0][1] if len(subjects) == 1 else {}
    if schema == 2 and (len(subjects) != 1 or subject.get('subject_status') != 'completed' or type(subject.get('subject_exit_code')) is not int):
        reason('subject-result-incomplete', [i for i, _ in subjects])

    process = defaultdict(lambda: {'start': [], 'stop': [], 'references': []})
    invalid_clock_ids = []
    for i, r in rows:
        kind = r.get('record_type')
        if kind in ('process-start', 'process-stop'):
            pid = str(r.get('process_id', ''))
            process[pid]['start' if kind == 'process-start' else 'stop'].append((i, r))
            if not pid:
                reason('process-id-missing', [i])
            if tick(r.get('event_time_created_raw')) is None:
                invalid_clock_ids.append(i)
                reason('clock-format-invalid', [i], 'pid:' + pid)
        if 'actor_filetime' in r and tick(r['actor_filetime']) is None:
            invalid_clock_ids.append(i)
            reason('clock-format-invalid', [i])
        if kind in ('required-process', 'root-binding'):
            process[str(r.get('process_id', ''))]['references'].append((i, r))
    root_keys = manifest.get('required_root_keys', [])
    if not isinstance(root_keys, list) or any(not isinstance(key, str) for key in root_keys):
        reason('malformed-required-roots', [i for i, _ in manifests])
        capture_faults = True
        root_keys = []
    for key in root_keys:
        if not any(r.get('root_key') == key for _, r in by_type['root-binding']):
            reason('root-binding-missing', [i for i, _ in manifests], 'root:' + str(key))

    operations = defaultdict(list)
    for i, r in by_type['operation']:
        operations[str(r.get('operation_id', ''))].append((i, r))
    for i, r in by_type['taskkill']:
        operations['legacy:' + i].append((i, r))
    operation_results = []
    for op_id, entries in sorted(operations.items()):
        opids = sorted(i for i, _ in entries)
        begins = [r for _, r in entries if r.get('phase') == 'begin']
        ends = [r for _, r in entries if r.get('phase') == 'end']
        begin = begins[0] if len(begins) == 1 else entries[0][1]
        end = ends[0] if len(ends) == 1 else {}
        a, b = tick(begin.get('actor_filetime')), tick(end.get('actor_filetime'))
        complete = bool(op_id) and len(begins) == len(ends) == 1 and type(end.get('rc')) is int and begin.get('operation') == end.get('operation')
        if not complete:
            reason('operation-incomplete', opids, 'operation:' + op_id)
        clock = a is not None and b is not None and b >= a
        if not clock:
            reason('operation-clock-format-unproven', opids, 'operation:' + op_id)
        pids = {str(r['process_id']) for _, r in entries if r.get('process_id') is not None and r.get('namespace') != 'msys'}
        pids.update(str(r['native_pid']) for _, r in entries if r.get('native_pid') is not None)
        if begin.get('namespace') == 'msys':
            reason('msys-native-mapping-unproven', opids, 'operation:' + op_id)
        output = str(end.get('output', begin.get('output', '')))
        if begin.get('operation') == 'taskkill' or begin.get('record_type') == 'taskkill':
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            for line in lines:
                found = re.findall(r'\bPID\s+(\d+)\b', line, re.I)
                pids.update(found)
                if not found or not re.match(r'^(SUCCESS|ERROR):', line):
                    reason('taskkill-output-unparsed', opids, 'operation:' + op_id)
            if not lines:
                reason('taskkill-output-unparsed', opids, 'operation:' + op_id)
        for pid in pids:
            process[pid]['references'].extend(entries)
        operation_results.append(dict(operation_id=op_id, record_ids=opids,
            operation=begin.get('operation', begin.get('record_type')), rc=end.get('rc', begin.get('rc')),
            output=output, operation_record_quality='known' if complete and clock else 'unknown',
            clock_format_quality='known' if clock else 'unknown', quality='unknown',
            process_time_quality='unknown', process_generation_quality='unknown',
            pid_mappings=[], _pids=sorted(pids)))

    generations = []
    for pid, groups in sorted(process.items()):
        start_ids = sorted(i for i, _ in groups['start'])
        stop_ids = sorted(i for i, _ in groups['stop'])
        record_ids = sorted(set(start_ids + stop_ids + [i for i, _ in groups['references']]))
        target = 'pid:' + pid
        for code in ('process-time-unproven', 'process-generation-unproven', 'scope-unproven'):
            reason(code, record_ids, target)
        if not start_ids:
            reason('missing-start-notification', record_ids, target)
        if not stop_ids:
            reason('missing-stop-notification', record_ids, target)
        if len(start_ids) > 1 or len(stop_ids) > 1:
            reason('notification-association-ambiguous', start_ids + stop_ids, target)
        local_reasons = sorted({d['reason'] for d in details if d['target'] == target})
        generations.append(dict(process_id=pid, record_ids=record_ids,
            required=bool(groups['references']) or (schema == 'legacy' and any(r.get('scope') == 'fixture-owned-target' for _, r in groups['start'] + groups['stop'])),
            start_candidates=start_ids, stop_candidates=stop_ids,
            reported_parent_pids=sorted({str(r.get('parent_process_id')) for _, r in groups['start'] + groups['stop'] if r.get('parent_process_id') is not None}),
            generation=None, parent_generation=None, scope='unknown', lifetime_ms=None, hold_ms=None,
            stop_relation='unknown', reaper_judgment='unknown',
            notification_capture_quality='known' if start_ids and stop_ids and not capture_faults and pid else 'unknown',
            clock_format_quality='known' if start_ids + stop_ids and not set(start_ids + stop_ids).intersection(invalid_clock_ids) else 'unknown',
            process_time_quality='unknown', process_generation_quality='unknown', reasons=local_reasons))

    by_pid = {g['process_id']: g for g in generations}
    for g in generations:
        g['parent_start_candidates'] = sorted({candidate for pid in g['reported_parent_pids'] for candidate in by_pid.get(pid, {}).get('start_candidates', [])})
    for op in operation_results:
        target = 'operation:' + op['operation_id']
        for code in ('process-time-unproven', 'process-generation-unproven', 'scope-unproven'):
            reason(code, op['record_ids'], target)
        pids = op.pop('_pids')
        for pid in pids:
            g = by_pid[pid]
            op['pid_mappings'].append(dict(process_id=pid, start_candidates=g['start_candidates'], stop_candidates=g['stop_candidates'], quality='unknown', stop_relation='unknown', hold_ms=None))
        op['notification_capture_quality'] = 'known' if pids and all(by_pid[p]['notification_capture_quality'] == 'known' for p in pids) else 'unknown'
        op['reasons'] = sorted({d['reason'] for d in details if d['target'] == target})
    if not generations:
        reason('required-process-unreported', ids)
    for code in ('process-time-unproven', 'process-generation-unproven', 'scope-unproven'):
        reason(code, ids)
    required = [g for g in generations if g['required']]
    if not required:
        reason('required-process-unreported', ids)
    capture = bool(required) and not capture_faults and all(g['notification_capture_quality'] == 'known' for g in required)
    clocks = bool(generations) and not invalid_clock_ids and all(g['clock_format_quality'] == 'known' for g in generations) and all(o['clock_format_quality'] == 'known' for o in operation_results)
    details.sort(key=lambda d: (d['target'], d['reason'], d['record_ids']))
    return dict(evaluation_schema_version=3, evaluator_revision=evaluator_revision or revision(),
        source_packet_sha256=source_hash, source_packet_schema=schema, collector_run_id=run_id,
        record_ids=sorted(ids), observations=[dict(record_id=i, raw=r) for i, r in sorted(raw_rows, key=lambda pair: pair[0])],
        subject_status=subject.get('subject_status', 'unknown'), subject_exit_code=subject.get('subject_exit_code'),
        subject_records=[dict(record_id=i, raw=r) for i, r in sorted(subjects)],
        comparison='unknown', collector_quality='unknown', comparison_gate='blocked',
        notification_capture_quality='known' if capture else 'unknown', clock_format_quality='known' if clocks else 'unknown',
        process_time_quality='unknown', process_generation_quality='unknown',
        lifetime_ms=None, hold_ms=None, reaper_judgment='unknown', termination_actor='not-determined', uncaptured='unknown',
        generations=generations, operations=operation_results,
        reasons=sorted({d['reason'] for d in details}), reason_details=details)


def decode_packet(data):
    rows, lines = [], []
    try:
        text = data.decode('utf-8-sig')
    except UnicodeError:
        return [dict(record_type='quality', reason='packet-invalid-encoding')], [1]
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip():
            lines.append(number)
            try:
                rows.append(json.loads(line))
            except (ValueError, TypeError):
                rows.append(dict(record_type='quality', reason='malformed-packet-record'))
    return rows, lines


def read_packet(path):
    try:
        return decode_packet(Path(path).read_bytes())[0]
    except OSError:
        return [dict(record_type='quality', reason='packet-unavailable')]


def evaluate_file(path, output=None):
    path = Path(path)
    data = path.read_bytes()
    records, lines = decode_packet(data)
    value = evaluate(records, hashlib.sha256(data).hexdigest(), source_lines=lines)
    destination = Path(output) if output else path.with_suffix('.evaluation-v3.json')
    if destination.resolve() == path.resolve():
        raise ValueError('evaluation must not overwrite source packet')
    encoded = json.dumps(value, sort_keys=True, indent=2) + '\n'
    # A previously evaluated artifact remains immutable, including its revision.
    if destination.exists():
        if destination.read_text(encoding='utf8') != encoded:
            raise ValueError('existing evaluation differs; use a separate output directory')
    else:
        destination.write_text(encoded, encoding='utf8')
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('packet')
    parser.add_argument('--output')
    args = parser.parse_args()
    print(json.dumps(evaluate_file(args.packet, args.output), sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
