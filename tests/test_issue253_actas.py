import copy
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import issue253_actas
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

    def test_evaluate_cannot_be_made_callable_without_the_observations(self):
        # Detects a change that makes the observations optional, which is the fail-open
        # default this design exists to remove. Reads the live function object rather than
        # the source text. Deliberate circumvention, such as rewriting this test, is out
        # of scope; the tests above already cover a caller passing the wrong thing.
        parameters = inspect.signature(evaluate).parameters
        self.assertEqual(list(parameters), ["instances", "correlation", "polled", "observed"])
        for name in ("polled", "observed"):
            self.assertIs(parameters[name].default, inspect.Parameter.empty,
                          f"{name} became optional; a caller can now omit the observation")

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

class ObserveWiring(unittest.TestCase):
    """observe() is otherwise only reached from the harness, so nothing in CI runs it."""

    def run_observe(self):
        with tempfile.TemporaryDirectory() as temp:
            return issue253_actas.observe(Path(__file__).resolve().parents[1], Path(temp) / "run", True)

    def test_observe_passes_real_observations_to_the_verdict(self):
        report = self.run_observe()
        # The window may or may not be reached on a loaded machine, so assert the wiring
        # rather than the outcome: both observations are recorded, and the verdict is one
        # of the values evaluate() can return once require() has accepted them.
        for key in ("watch_poll_reached", "handoff_observation_reached"):
            self.assertIsInstance(report[key], bool, key)
        self.assertIn(report["role_path"], ("pass", "unknown", "incompatible"))
        json.dumps(report)

    def test_observe_reports_each_observation_under_its_own_name(self):
        # Pin the two outcomes to different values so reporting one as the other, or
        # hard-coding either, changes the report. Left to a real run the two often agree
        # and the difference stays invisible.
        outcomes = iter((False, True))
        original = issue253_actas.wait_for
        issue253_actas.wait_for = lambda predicate, seconds=8: next(outcomes)
        try:
            report = self.run_observe()
        finally:
            issue253_actas.wait_for = original
        self.assertEqual((report["watch_poll_reached"], report["handoff_observation_reached"]), (False, True))
        self.assertEqual(report["role_path"], "unknown")

    def test_observe_refuses_a_bare_bool_reaching_the_verdict(self):
        original = issue253_actas.observe_until
        issue253_actas.observe_until = lambda label, predicate, seconds: original(label, predicate, seconds).reached
        try:
            with self.assertRaises(TypeError) as raised:
                self.run_observe()
        finally:
            issue253_actas.observe_until = original
        self.assertIn("expected Observation from observe_until", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
