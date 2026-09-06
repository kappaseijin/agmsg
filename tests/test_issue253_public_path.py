"""Fail-closed evaluator controls; these are not the real F/O experiment."""
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


if __name__ == "__main__":
    unittest.main()
