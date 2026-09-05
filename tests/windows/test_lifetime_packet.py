"""Behavior controls for v2 offline generation/operation evaluation (#255)."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

MODULE = Path(__file__).with_name('lifetime_packet.py')

def packet():
    # Delivery order deliberately differs from event time. PID 20 is reused;
    # child 30 stops after its parent. Foreign root 90 stays separate.
    records = [
        dict(record_type='run-manifest', schema_version=2, run_id='control', clock_quality='known'),
        dict(record_type='subscription-ready', subscription_status='ready'),
        dict(record_type='process-stop', process_id='20', event_time_created_raw='180'),
        dict(record_type='process-start', process_id='10', parent_process_id='1', event_time_created_raw='100'),
        dict(record_type='process-start', process_id='20', parent_process_id='10', event_time_created_raw='150'),
        dict(record_type='process-start', process_id='30', parent_process_id='20', event_time_created_raw='160'),
        dict(record_type='process-start', process_id='20', parent_process_id='10', event_time_created_raw='200'),
        dict(record_type='process-stop', process_id='30', event_time_created_raw='190'),
        dict(record_type='process-stop', process_id='20', event_time_created_raw='250'),
        dict(record_type='process-stop', process_id='10', event_time_created_raw='300'),
        dict(record_type='process-start', process_id='90', parent_process_id='1', event_time_created_raw='101'),
        dict(record_type='process-stop', process_id='90', event_time_created_raw='301'),
        dict(record_type='root-binding', root_key='target', process_id='10', actor_filetime='120', creation_date='snapshot-creation', match=True),
        dict(record_type='root-binding', root_key='foreign', process_id='90', actor_filetime='120', creation_date='another-creation', match=True),
        dict(record_type='operation', operation_id='kill1', phase='begin', operation='taskkill', namespace='native', process_id='20', actor_filetime='210'),
        dict(record_type='operation', operation_id='kill1', phase='end', operation='taskkill', namespace='native', process_id='20', actor_filetime='230', rc=128, output='ERROR: The process with PID 20 could not be terminated.'),
        dict(record_type='subject-result', subject_status='completed', subject_exit_code=7),
        dict(record_type='subscription-ended', subscription_end_confirmed=True),
        dict(record_type='packet-close')]
    for r in records:
        if r['record_type'].startswith('process-'):
            r['event_clock_quality'] = 'known'
    return records

class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('lifetime_packet', MODULE)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def evaluate(self, rows):
        return self.module.evaluate(rows)

    def test_delayed_delivery_pid_reuse_and_foreign_are_separate(self):
        value = self.evaluate(packet())
        self.assertEqual(value['collector_quality'], 'known', value)
        self.assertEqual(value['subject_exit_code'], 7)
        generations = {g['generation']: g for g in value['generations']}
        self.assertEqual(generations['control/20/150']['scope'], 'target')
        self.assertEqual(generations['control/20/200']['scope'], 'target')
        self.assertEqual(generations['control/30/160']['parent_generation'], 'control/20/150')
        self.assertEqual(generations['control/90/101']['scope'], 'foreign')
        self.assertEqual(value['operations'][0]['pid_mappings'][0]['candidates'], ['control/20/200'])
        self.assertEqual(value['operations'][0]['pid_mappings'][0]['stop_relation'], 'after-operation')

    def test_missing_short_child_stop_cannot_borrow_other_stop(self):
        rows = [r for r in packet() if not (r['record_type']=='process-stop' and r['process_id']=='30')]
        result = self.evaluate(rows)
        self.assertEqual(result['collector_quality'], 'unknown')
        self.assertIn('missing-stop-event', result['reasons'])

    def test_gate_failures_remain_unknown(self):
        for missing, reason in [('subscription-ready','subscription-not-ready'), ('packet-close','packet-incomplete')]:
            with self.subTest(missing=missing):
                result = self.evaluate([r for r in packet() if r['record_type'] != missing])
                self.assertIn(reason, result['reasons'])
        for mutation, reason in [('clock','clock-correspondence-unknown'), ('end','operation-incomplete'), ('error','required-pid-unmatched'), ('start','missing-start-event')]:
            with self.subTest(mutation=mutation):
                rows = packet()
                if mutation == 'clock': rows[0]['clock_quality']='unknown'
                if mutation == 'end': rows=[r for r in rows if r.get('phase') != 'end']
                if mutation == 'error':
                    next(r for r in rows if r.get('phase')=='end')['output']='ERROR: The process with PID 777 could not be terminated.'
                if mutation == 'start': rows=[r for r in rows if not (r['record_type']=='process-start' and r['process_id']=='10')]
                result = self.evaluate(rows)
                self.assertEqual(result['collector_quality'], 'unknown')
                self.assertIn(reason, result['reasons'])

    def test_zero_subject_rc_does_not_hide_unknown_and_schema_is_closed(self):
        rows=packet()
        next(r for r in rows if r['record_type']=='subject-result')['subject_exit_code']=0
        rows[0]['schema_version']=99
        result=self.evaluate(rows)
        self.assertEqual(result['collector_quality'],'unknown')
        self.assertIn('unknown-schema', result['reasons'])
        self.assertEqual(result['subject_exit_code'],0)

    def test_ambiguous_generation_and_unparsed_output_are_unknown(self):
        rows=packet()
        rows.append(copy.deepcopy(rows[4]))
        self.assertIn('generation-ambiguous',self.evaluate(rows)['reasons'])
        rows=packet()
        next(r for r in rows if r.get('phase')=='end')['output']='unrecognized locale output 20'
        self.assertIn('taskkill-output-unparsed',self.evaluate(rows)['reasons'])

    def test_serialization_replay_keeps_collection_id(self):
        first=self.evaluate(packet())
        replay=self.evaluate([json.loads(json.dumps(r)) for r in reversed(packet())])
        self.assertEqual(first, replay)
        self.assertEqual(replay['collector_run_id'],'control')

if __name__ == '__main__': unittest.main()
