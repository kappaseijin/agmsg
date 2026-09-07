"""Print "<files> <hunks>" for the tests that correspond to an implementation.

The count is the design note's 44 / 211. It is computed from the ledger rather
than from the tree so that it moves with the fixed bases, not with whatever is
checked out.
"""
import collections
import importlib.util
import sys

spec = importlib.util.spec_from_file_location('hunk_ledger', sys.argv[-1] if sys.argv[1] == '--probe' else sys.argv[1])
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)

if sys.argv[1] == '--probe':
    # Which implementations a handful of names resolve to, printed one per line.
    # Names that are not test names must resolve to nothing; a real test name is
    # the control that says matching still works at all.
    sample = {'README.md', 'scripts/watch.sh', 'scripts/delivery.sh',
              'tests/fixtures/pm-broker/broker.js', 'tests/test_watch.bats'}
    for probe in ('tests/fixtures/pm-broker/broker.js',
                  'tests/fixtures/team-work-audit/closed.json',
                  'tests/test_watch.bats'):
        print(probe, len(ledger.implementations_for(probe, sample)))
    raise SystemExit(0)

paths = [line.split('\t')[1] for line in open(sys.argv[2], encoding='utf8').read().split('\n')
         if line and not line.startswith('#')]
hunks = collections.Counter(paths)
corresponding = [p for p in set(paths)
                 if ledger.is_test_path(p) and ledger.implementations_for(p, set(paths))]
print(len(corresponding), sum(hunks[p] for p in corresponding))
