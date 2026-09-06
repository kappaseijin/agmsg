import ast
import copy
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
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

    def test_missed_observation_window_is_unknown(self):
        for polled, observed in ((False, True), (True, False), (False, False)):
            self.assertEqual(evaluate(self.instances, self.correlation, polled, observed), "unknown")
        self.assertEqual(evaluate(self.instances, self.correlation, True, True), "pass")

    def test_no_wait_for_result_is_discarded(self):
        # ast, not grep: a text match reads `wait_for(` inside comments and strings and
        # misses a call wrapped across lines. ast.Expr is exactly a discarded value.
        for path in (SCRIPTS / "issue253_actas.py", SCRIPTS / "issue253_public_path.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) \
                        and getattr(node.value.func, "id", None) == "wait_for":
                    self.fail(f"{path.name}:{node.lineno}: wait_for result discarded")

    def test_no_wait_for_result_is_forced_true(self):
        # `x or wait_for(...)` short-circuits the wait away and pins the result true.
        # `x and wait_for(...)` only narrows it, so conjunction stays allowed.
        for path in (SCRIPTS / "issue253_actas.py", SCRIPTS / "issue253_public_path.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) \
                        and any(isinstance(inner, ast.Call) and getattr(inner.func, "id", None) == "wait_for"
                                for value in node.values for inner in ast.walk(value)):
                    self.fail(f"{path.name}:{node.lineno}: wait_for result disjoined to true")

    def test_observation_results_reach_the_verdict(self):
        # The default True on evaluate() is fail-open by design, so the guard has to be
        # that the caller passes the observations. Checking the wait_for call shape alone
        # does not: dropping the arguments, or passing literals, leaves that shape intact.
        source = ast.parse((SCRIPTS / "issue253_actas.py").read_text())
        observe = next(n for n in ast.walk(source)
                       if isinstance(n, ast.FunctionDef) and n.name == "observe")
        waited = {target.id for node in ast.walk(observe)
                  if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                  and getattr(node.value.func, "id", None) == "wait_for"
                  for target in node.targets if isinstance(target, ast.Name)}
        calls = [n for n in ast.walk(observe)
                 if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "evaluate"]
        self.assertEqual(len(calls), 1, "expected exactly one evaluate() call in observe()")
        self.assertEqual(len(calls[0].args), 4, f"evaluate() line {calls[0].lineno}: observations not passed")
        for argument in calls[0].args[2:]:
            self.assertIsInstance(argument, ast.Name, f"evaluate() line {calls[0].lineno}: literal observation")
            self.assertIn(argument.id, waited,
                          f"evaluate() line {calls[0].lineno}: {argument.id} is not bound from wait_for")
        # The report fields are the evidence for that verdict, so they carry the same
        # requirement: a literal there would claim an observation that never happened.
        reported = {key.value: value for node in ast.walk(observe) if isinstance(node, ast.Dict)
                    for key, value in zip(node.keys, node.values)
                    if isinstance(key, ast.Constant) and key.value in
                    ("watch_poll_reached", "handoff_observation_reached")}
        self.assertEqual(set(reported), {"watch_poll_reached", "handoff_observation_reached"})
        for key, value in sorted(reported.items()):
            self.assertIsInstance(value, ast.Name, f"{key}: literal evidence")
            self.assertIn(value.id, waited, f"{key}: {value.id} is not bound from wait_for")
        # Pin the pairing to evaluate()'s argument positions rather than to the variable
        # names, so a rename stays legal but reporting one observation as the other does not.
        self.assertEqual([reported["watch_poll_reached"].id, reported["handoff_observation_reached"].id],
                         [argument.id for argument in calls[0].args[2:]],
                         "evidence fields do not match the observations passed to evaluate()")


if __name__ == "__main__":
    unittest.main()
