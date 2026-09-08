"""Fail-closed evaluator controls; these are not the real F/O experiment."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("harness", Path(__file__).parents[1] / "scripts/issue253_public_path.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class Controls(unittest.TestCase):
    def setUp(self):
        self.expected = [(key, "team", "receiver", "requestId=" + key + "\nsame") for key in ("A", "B")]
        self.rows = [{"id": str(index), "team": team, "to": recipient, "body": body}
                     for index, (_, team, recipient, body) in enumerate(self.expected)]

    def test_positive_exact_ids(self):
        self.assertEqual(harness.correlate(self.rows, self.expected, True)["ids"], {"A": "0", "B": "1"})

    def test_omitted_id(self):
        self.rows[0].pop("id")
        self.assertEqual(harness.correlate(self.rows, self.expected, True)["status"], "unknown")

    def test_latest_only_and_missing_history(self):
        self.assertEqual(harness.correlate(self.rows[-1:], self.expected, True)["status"], "unknown")

    def test_partial_history_even_with_expected_rows(self):
        self.assertEqual(harness.correlate(self.rows, self.expected, False)["status"], "unknown")

    def test_ambiguous_duplicate_request(self):
        self.assertEqual(harness.correlate(self.rows + [self.rows[0]], self.expected, True)["status"], "unknown")

    def test_scope(self):
        self.rows[0]["team"] = "other"
        self.assertEqual(harness.correlate(self.rows, self.expected, True)["status"], "unknown")

    def test_rc_zero_not_ack(self):
        self.assertEqual(harness.ack_verdict(0, None), "unknown")
        self.assertEqual(harness.ack_verdict(0, "handedOff", fault=True), "unknown")
        self.assertEqual(harness.ack_verdict(0, "handedOff", interrupted=True), "unknown")
        self.assertEqual(harness.ack_verdict(0, "handedOff"), "pass")

    def test_both_receivers_required(self):
        observations = [{"stdout": "requestId=A"}] * 2
        self.assertEqual(harness.receiver_verdict(observations, 2, "requestId=A"), "incompatible")
        self.assertEqual(harness.receiver_verdict(observations[:1], 2, "requestId=A"), "unknown")

    def test_layer_a_requires_every_receipt_control(self):
        observed = {
            "receipt_after_handoff": {"status": "receipt", "count": 1, "evidence": "inbox_stdout"},
            "handoff_observed": True,
            "idempotent_count": 1,
            "legacy_without_receipt": "legacy_read",
            "record_failure": {"delivered": True, "diagnostic": True},
            "deleted_receipt_status": "legacy_read",
        }
        self.assertEqual(harness.layer_a_verdict(observed), "pass")
        mutations = (
            lambda row: row["receipt_after_handoff"].update(evidence="wrong_path"),
            lambda row: row.update(handoff_observed=False),
            lambda row: row.update(idempotent_count=2),
            lambda row: row.update(legacy_without_receipt="none"),
            lambda row: row["record_failure"].update(diagnostic=False),
            lambda row: row.update(deleted_receipt_status="receipt"),
        )
        for mutate in mutations:
            candidate = copy.deepcopy(observed)
            mutate(candidate)
            self.assertEqual(harness.layer_a_verdict(candidate), "incompatible")

    def test_interruption_points_require_opposite_read_and_replay_states(self):
        before_consume = {"receipt_count": 0, "receipt_status": "none",
                          "consumed": False, "replayed": True}
        before_receipt = {"receipt_count": 0, "receipt_status": "legacy_read",
                          "consumed": True, "replayed": False}
        self.assertEqual(harness.interruption_verdict(before_consume, before_receipt), "pass")
        before_receipt["replayed"] = True
        self.assertEqual(harness.interruption_verdict(before_consume, before_receipt), "incompatible")


if __name__ == "__main__":
    unittest.main()
