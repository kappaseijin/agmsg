#!/usr/bin/env python3
"""Run real evaluator mutations in temporary copies; a surviving mutant fails."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / "scripts/issue253_public_path.py").read_text()
mutations = {
    "omit_id": ('return {"status": "pass", "ids": mapping}',
                'return {"status": "pass", "ids": {}}'),
    "latest_only": ('for request, team, recipient, body in expected:',
                    'expected = expected[-1:]\n    for request, team, recipient, body in expected:'),
    "rc_zero_is_ack": ('if interrupted or fault or rc != 0 or evidence != "handedOff":',
                       'if rc != 0:'),
    "hide_receiver": ('if len(observations) != expected_receivers:', 'if False:'),
    "ignore_handoff": ('and observed["handoff_observed"] is True', 'and True'),
    "ignore_receipt_evidence": (
        'handoff == {"status": "receipt", "count": 1, "evidence": "inbox_stdout"}',
        'handoff.get("status") == "receipt"'),
    "ignore_idempotency": ('and observed["idempotent_count"] == 1', 'and True'),
    "ignore_legacy_read": ('and observed["legacy_without_receipt"] == "legacy_read"', 'and True'),
    "ignore_record_failure_diagnostic": ('and failure.get("diagnostic") is True', 'and True'),
    "ignore_deleted_receipt": ('and observed["deleted_receipt_status"] == "legacy_read"', 'and True'),
    "collapse_interruption_replay": (
        '"consumed": True, "replayed": False}',
        '"consumed": True, "replayed": True}'),
    "ignore_delivery_success": ('and failure.get("delivered") is True', 'and True'),
    "ignore_nonzero_ack_rc": (
        'if interrupted or fault or rc != 0 or evidence != "handedOff":',
        'if interrupted or fault or evidence != "handedOff":'),
    "allow_reused_message_id": (
        'if len(set(mapping.values())) != len(expected):',
        'if False:'),
}
results = {}
for name, (before, after) in mutations.items():
    assert source.count(before) == 1, name
    with tempfile.TemporaryDirectory(prefix="issue253-mutation-") as directory:
        temp = Path(directory)
        (temp / "scripts").mkdir()
        (temp / "tests").mkdir()
        (temp / "scripts/issue253_public_path.py").write_text(source.replace(before, after))
        shutil.copy(root / "tests/test_issue253_public_path.py", temp / "tests")
        run = subprocess.run(["python3", "-m", "unittest", "discover", "-s", "tests",
                              "-p", "test_issue253_public_path.py", "-v"], cwd=temp,
                             capture_output=True, text=True)
        results[name] = {"killed": run.returncode != 0, "rc": run.returncode,
                         "stdout": run.stdout, "stderr": run.stderr}
print(json.dumps(results, indent=2))
raise SystemExit(0 if all(value["killed"] for value in results.values()) else 1)
