import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from issue253_actas import claim_state, evaluate, ownership


class ActasControls(unittest.TestCase):
    def setUp(self):
        self.instances = [dict(host_pid=10, instance="sid.10", boundary="ready", handoffs=1, held_owner=None),
                          dict(host_pid=20, instance="sid.20", boundary="held", handoffs=0, held_owner="sid.10")]
        self.correlation = {"status": "pass", "ids": {"request": "opaque-id"}}

    def test_limited_positive(self):
        self.assertEqual(evaluate(self.instances, self.correlation), "pass")

    def test_absent_receiver_is_unknown(self):
        self.assertEqual(evaluate(self.instances[:1], self.correlation), "unknown")

    def test_start_failure_and_missing_readiness_are_not_held(self):
        for boundary in ("start_failure", "ready_missing"):
            data = copy.deepcopy(self.instances)
            data[1]["boundary"] = boundary
            self.assertEqual(evaluate(data, self.correlation), "unknown")

    def test_wrong_owner_or_pid_is_unknown(self):
        self.instances[1]["held_owner"] = "unobserved-owner"
        self.assertEqual(evaluate(self.instances, self.correlation), "unknown")
        self.instances[1]["host_pid"] = 10
        self.assertEqual(evaluate(self.instances, self.correlation), "unknown")

    def test_duplicate_handoff_is_incompatible(self):
        self.instances[1]["handoffs"] = 1
        self.assertEqual(evaluate(self.instances, self.correlation), "incompatible")

    def test_ownership_does_not_prove_delivery(self):
        self.instances[0]["handoffs"] = 0
        self.assertEqual(ownership(self.instances), "pass")
        self.assertEqual(evaluate(self.instances, self.correlation), "unknown")

    def test_incomplete_public_id_is_unknown(self):
        self.assertEqual(evaluate(self.instances, {"status": "unknown"}), "unknown")
        self.assertEqual(evaluate(self.instances, {"status": "pass"}), "unknown")

    def test_claim_exit_and_fields_are_both_required(self):
        self.assertEqual(claim_state({"rc": 1, "stdout": "status=held team=team owner=sid.10"}), ("held", "sid.10"))
        for command in ({"rc": 1, "stdout": ""}, {"rc": 2, "stdout": "status=not_registered"},
                        {"rc": 0, "stdout": "status=held team=team owner=sid.10"}):
            self.assertEqual(claim_state(command)[0], "start_failure")


if __name__ == "__main__":
    unittest.main()
