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
        dict(record_type='required-process', process_id='30', root_key='target'),
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

    def test_notifications_do_not_certify_lifetime_or_generation(self):
        value=self.evaluate(packet())
        self.assertEqual(value['notification_capture_quality'],'known')
        self.assertEqual(value['clock_format_quality'],'known')
        self.assertEqual(value['collector_quality'],'unknown')
        for g in value['generations']:
            self.assertIsNone(g['lifetime_ms'])
            self.assertIsNone(g['generation'])
            self.assertEqual(g['process_time_quality'],'unknown')
            self.assertEqual(g['process_generation_quality'],'unknown')
        op=value['operations'][0]
        self.assertEqual(op['operation_record_quality'],'known')
        self.assertEqual(op['rc'],128)
        self.assertEqual(op['pid_mappings'][0]['stop_relation'],'unknown')
        self.assertEqual(len(op['pid_mappings'][0]['start_candidates']),2)

    def test_order_and_delayed_notifications_keep_candidates(self):
        rows=packet()
        for i,r in enumerate(rows): r['record_id']=str(i)
        first=self.evaluate(rows)
        reordered=self.evaluate(list(reversed(rows)))
        self.assertNotEqual(first['source_packet_sha256'],reordered['source_packet_sha256'])
        self.assertEqual({k:v for k,v in first.items() if k!='source_packet_sha256'}, {k:v for k,v in reordered.items() if k!='source_packet_sha256'})
        rows[3]['event_time_created_raw']='999999'
        delayed=self.evaluate(rows)
        self.assertEqual(first['record_ids'],delayed['record_ids'])
        self.assertEqual(first['operations'],delayed['operations'])

    def test_missing_stop_and_other_generation_stop_are_distinct(self):
        rows=[r for r in packet() if not (r['record_type']=='process-stop' and r['process_id']=='30')]
        value=self.evaluate(rows)
        self.assertEqual(value['notification_capture_quality'],'unknown')
        child=next(g for g in value['generations'] if g['process_id']=='30')
        self.assertIn('missing-stop-notification',child['reasons'])
        rows=[r for r in packet() if not (r['record_type']=='process-stop' and r['process_id']=='20' and r['event_time_created_raw']=='180')]
        child=next(g for g in self.evaluate(rows)['generations'] if g['process_id']=='20')
        self.assertNotIn('missing-stop-notification',child['reasons'])
        self.assertIn('notification-association-ambiguous',child['reasons'])
        self.assertIn('process-generation-unproven',child['reasons'])

    def test_faults_accumulate_without_overwriting_subject_rc(self):
        for rc in (0,7):
            rows=packet()
            next(r for r in rows if r['record_type']=='subject-result')['subject_exit_code']=rc
            rows[3]['event_time_created_raw']='99999999999999999999999999'
            rows=[r for r in rows if r.get('phase')!='end']+[42]
            value=self.evaluate(rows)
            self.assertEqual(value['subject_exit_code'],rc)
            for reason in ('clock-format-invalid','operation-incomplete','malformed-packet-record','process-time-unproven'):
                self.assertIn(reason,value['reasons'])
            self.assertEqual(value['comparison'],'unknown')
        rows=packet(); rows[0]['schema_version']=99
        self.assertIn('unknown-schema',self.evaluate(rows)['reasons'])
        rows=packet(); rows[-1]['run_id']='foreign'
        self.assertIn('multiple-runs',self.evaluate(rows)['reasons'])

    def test_malformed_field_shapes_do_not_abort_evaluation(self):
        rows=packet()
        rows[0]['required_root_keys']=None
        rows[3]['event_time_created_raw']='9'*5000
        rows.append(dict(record_type='quality',reason=['corrupt']))
        value=self.evaluate(rows)
        self.assertEqual(value['comparison_gate'],'blocked')
        self.assertEqual(value['notification_capture_quality'],'unknown')
        self.assertIn('malformed-required-roots',value['reasons'])
        self.assertIn('clock-format-invalid',value['reasons'])

    def test_legacy_known_is_not_inherited(self):
        rows=[dict(record_type='subscription-ready',subscription_status='ready'),
              dict(record_type='subscription-ended',subscription_end_confirmed=True)]
        for kind,raw in [('process-start','100'),('process-stop','200')]:
            rows.append(dict(record_type=kind,process_id='20',scope='fixture-owned-target',event_time_created_raw=raw,generation='old-known',generation_quality='known'))
        rows.append(dict(record_type='taskkill',process_id='20',actor_time_utc='2026-09-06T00:00:00+00:00',rc=0))
        value=self.evaluate(rows)
        self.assertEqual(value['source_packet_schema'],'legacy')
        self.assertEqual(value['notification_capture_quality'],'known')
        self.assertEqual(value['comparison'],'unknown')
        self.assertIsNone(value['generations'][0]['lifetime_ms'])
        self.assertEqual(value['operations'][0]['operation_record_quality'],'unknown')
        self.assertIn('operation-incomplete',value['reasons'])

if __name__ == '__main__': unittest.main()
