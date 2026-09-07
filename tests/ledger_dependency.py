"""Counts for the official-depends-on-agguild verdict, under stated conditions.

  ledger_dependency.py <hunk-ledger.py> <ledger.tsv> <case>

Cases, each printing "<findings> <names>":

  as-is             the ledger as it stands
  no-shadow-list    with SHADOWED_COMMANDS emptied
  flipped           with the definer of agmsg_validate_utf8 marked agguild
  product-shadow    with a scripts/ hunk defining mktemp
  no-visibility     with the sourcing check disabled
  no-local-wins     with both the sourcing check and same-file precedence off

Run against the real ledger rather than a fixture: the verdict reads the whole
diff, and a fixture would only say what the fixture was built to say.
"""
import importlib.util
import sys

TOOL, LEDGER, CASE = sys.argv[1], sys.argv[2], sys.argv[3]
source = open(TOOL, encoding='utf8').read()
VISIBILITY = """                if path in reach and not (reach[path] & {files[o] for o in others}):
                    continue"""
LOCAL_WINS = """                if any(files[o] == path for o in definers):
                    continue"""
if CASE in ('no-visibility', 'no-local-wins'):
    source = source.replace(VISIBILITY, '                pass', 1)
if CASE == 'no-local-wins':
    source = source.replace(LOCAL_WINS, '                pass', 1)

path = '/tmp/ledger_dependency_variant.py'
open(path, 'w', encoding='utf8').write(source)
spec = importlib.util.spec_from_file_location('variant', path)
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)

hunks, _ = ledger.enumerate_hunks(ledger.run_diff())
owners = {}
for line in open(LEDGER, encoding='utf8').read().split('\n'):
    if line and not line.startswith('#'):
        fields = line.split('\t')
        owners[fields[0]] = fields[2]

if CASE == 'no-shadow-list':
    ledger.SHADOWED_COMMANDS.clear()
if CASE == 'flipped':
    for hunk_id, file_path, _, body in hunks:
        if file_path == 'scripts/lib/validate.sh' and 'agmsg_validate_utf8()' in body:
            owners[hunk_id] = 'agguild'
if CASE == 'product-shadow':
    hunks = list(hunks) + [('f' * 16, 'scripts/fake-lib.sh', ' -0,0 +1,1 ',
                            '+mktemp() {\n+  :\n+}')]
    owners['f' * 16] = 'agguild'

found, _ = ledger.depends_on_agguild(hunks, owners)
print(len(found), ','.join(sorted({name for _, _, name, _ in found})) or '-')
