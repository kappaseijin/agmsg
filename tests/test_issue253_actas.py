import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from issue253_actas import Observation, claim_state, evaluate, observe_until, ownership


def reached(label, value=True):
    """Build the observation the only legal way. A false one has to run out its window,
    so keep that window short; a true one returns on the first check."""
    return observe_until(label, lambda: value, 1 if value else 0.01)


class ActasControls(unittest.TestCase):
    def setUp(self):
        self.instances = [dict(host_pid=10, instance="sid.10", boundary="ready", handoffs=1, held_owner=None),
                          dict(host_pid=20, instance="sid.20", boundary="held", handoffs=0, held_owner="sid.10")]
        self.correlation = {"status": "pass", "ids": {"request": "opaque-id"}}

    def test_limited_positive(self):
        self.assertEqual(evaluate(self.instances, self.correlation, reached("watch_poll"), reached("handoff")), "pass")

    def test_absent_receiver_is_unknown(self):
        self.assertEqual(evaluate(self.instances[:1], self.correlation, reached("watch_poll"), reached("handoff")), "unknown")

    def test_start_failure_and_missing_readiness_are_not_held(self):
        for boundary in ("start_failure", "ready_missing"):
            data = copy.deepcopy(self.instances)
            data[1]["boundary"] = boundary
            self.assertEqual(evaluate(data, self.correlation, reached("watch_poll"), reached("handoff")), "unknown")

    def test_wrong_owner_or_pid_is_unknown(self):
        self.instances[1]["held_owner"] = "unobserved-owner"
        self.assertEqual(evaluate(self.instances, self.correlation, reached("watch_poll"), reached("handoff")), "unknown")
        self.instances[1]["host_pid"] = 10
        self.assertEqual(evaluate(self.instances, self.correlation, reached("watch_poll"), reached("handoff")), "unknown")

    def test_duplicate_handoff_is_incompatible(self):
        self.instances[1]["handoffs"] = 1
        self.assertEqual(evaluate(self.instances, self.correlation, reached("watch_poll"), reached("handoff")), "incompatible")

    def test_ownership_does_not_prove_delivery(self):
        self.instances[0]["handoffs"] = 0
        self.assertEqual(ownership(self.instances), "pass")
        self.assertEqual(evaluate(self.instances, self.correlation, reached("watch_poll"), reached("handoff")), "unknown")

    def test_incomplete_public_id_is_unknown(self):
        self.assertEqual(evaluate(self.instances, {"status": "unknown"}, reached("watch_poll"), reached("handoff")), "unknown")
        self.assertEqual(evaluate(self.instances, {"status": "pass"}, reached("watch_poll"), reached("handoff")), "unknown")

    def test_claim_exit_and_fields_are_both_required(self):
        self.assertEqual(claim_state({"rc": 1, "stdout": "status=held team=team owner=sid.10"}), ("held", "sid.10"))
        for command in ({"rc": 1, "stdout": ""}, {"rc": 2, "stdout": "status=not_registered"},
                        {"rc": 0, "stdout": "status=held team=team owner=sid.10"}):
            self.assertEqual(claim_state(command)[0], "start_failure")

    def test_missed_observation_window_is_unknown(self):
        for polled, observed in ((False, True), (True, False), (False, False)):
            self.assertEqual(evaluate(self.instances, self.correlation,
                                      reached("watch_poll", polled), reached("handoff", observed)), "unknown")

    def test_observations_are_required_arguments(self):
        with self.assertRaises(TypeError):
            evaluate(self.instances, self.correlation)

    def test_a_bare_bool_is_not_an_observation(self):
        for polled, observed in ((True, reached("handoff")), (reached("watch_poll"), True)):
            with self.assertRaises(TypeError) as raised:
                evaluate(self.instances, self.correlation, polled, observed)
            self.assertIn("expected Observation from observe_until", str(raised.exception))

    def test_swapped_observations_are_refused(self):
        with self.assertRaises(TypeError) as raised:
            evaluate(self.instances, self.correlation, reached("handoff"), reached("watch_poll"))
        self.assertIn("expected observation 'watch_poll'", str(raised.exception))

    def test_observation_cannot_be_constructed_directly(self):
        with self.assertRaises(TypeError):
            Observation("watch_poll", True)

    def test_narrowing_an_observation_stays_legal(self):
        # `x and observe_until(...)` only narrows the result, so it must keep working.
        # The previous static-scan design rejected this shape; nothing here can.
        self.assertEqual(evaluate(self.instances, self.correlation,
                                  True and reached("watch_poll"), reached("handoff")), "pass")
        self.assertIs(bool(False and reached("watch_poll")), False)

    def test_observation_is_not_json_serialisable(self):
        # The report must carry .reached; forgetting it fails loudly rather than silently.
        with self.assertRaises(TypeError):
            json.dumps({"watch_poll_reached": reached("watch_poll")})
        self.assertEqual(json.dumps({"watch_poll_reached": reached("watch_poll").reached}),
                         '{"watch_poll_reached": true}')


if __name__ == "__main__":
    unittest.main()
