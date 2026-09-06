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
