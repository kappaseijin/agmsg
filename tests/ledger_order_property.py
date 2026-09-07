"""How many different classifications a ledger produces when its rows are reordered.

Prints one integer. Anything but 1 means the answer depends on the order the
rows happen to sit in, which is the shape of defect this exists to catch: the
first version read one owner per path and kept whichever hunk came last, so
sorting the same facts differently classified them differently.

  ledger_order_property.py <hunk-ledger.py> <ledger.tsv>
"""
import importlib.util
import itertools
import os
import sys
import tempfile

spec = importlib.util.spec_from_file_location('hunk_ledger', sys.argv[1])
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)

lines = open(sys.argv[2], encoding='utf8').read().split('\n')
comments = [l for l in lines if l.startswith('#')]
rows = [l for l in lines if l and not l.startswith('#')]

outcomes = set()
for order in itertools.permutations(rows):
    with tempfile.NamedTemporaryFile('w', suffix='.tsv', delete=False, encoding='utf8') as handle:
        handle.write('\n'.join(comments + list(order)))
        path = handle.name
    try:
        out, _, _ = ledger.classify_mechanical(path)
    finally:
        os.unlink(path)
    # Compare what was decided, not the order it was printed in.
    decided = frozenset((l.split('\t')[0], l.split('\t')[2]) for l in out if not l.startswith('#'))
    outcomes.add(decided)
print(len(outcomes))
