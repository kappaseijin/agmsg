#!/usr/bin/env python3
"""Enumerate the fork's hunks, emit the ledger header, and check the ledger.

The single definition point for the canonical diff command (Issue #254). The
design note quotes this file; the ledger header is produced by `--emit-header`
rather than typed, because a hand-copied header records what someone's shell
did rather than what the command specifies.

  hunk-ledger.py --emit-header
  hunk-ledger.py --emit-rows          # hunk_id<TAB>path, one per hunk
  hunk-ledger.py --check <ledger.tsv>

Counting is not stable across git configurations: -U3 gives 484 hunks where
-U0 gives 734, diff.interHunkContext=10 gives 450, and core.quotePath=true
rewrites one non-ASCII path. The command below pins every axis that is known to
move the number and blanks the global and system config on top, so the only
remaining exposure is an unknown key in the repository's own .git/config. That
residue is not detected; it is absorbed, because any config that changes the
partition also changes the digest, and the check compares the digest.
"""
import argparse
import hashlib
import os
import subprocess
import sys

BASE_MERGE = '3d06318'
BASE_FORK = '7ea795e93683b0e7ede0fabf017fe503fcfe6aea'
BASE_UPSTREAM = 'e58dbafad5a84be625f070385bb0c076c3daa4db'
LEDGER_PATH = 'docs/migration/222-hunk-ledger.tsv'
COLUMNS = ('hunk_id', 'path', 'owner', 'disposition', 'rationale', 'public_dep',
           'regression', 'conflict_risk', 'assignee', 'status')
# Empty means "nobody has classified this yet", which is progress and belongs to
# the gate. A non-empty value outside these sets is a typo or a stale vocabulary,
# which is data and belongs to the check. Keeping that line straight is what lets
# the check stay green while 734 rows are still being worked through.
OWNERS = frozenset({'official', 'agguild', 'pool', 'exception'})
DISPOSITIONS = frozenset({'adopt', 'port', 'drop'})
STATUSES = ('unclassified', 'classified', 'verified')
# What `--emit-rows` writes into the columns a person still has to fill in.
# `unclassified` is the default state the design gives them, so the freshly
# generated ledger is an unclassified ledger rather than a malformed one.
BLANK_ROW = ('',) * (len(COLUMNS) - 3) + (STATUSES[0],)

# -U0 decides the context INSIDE a hunk; --inter-hunk-context decides how far
# apart two hunks may be before they merge. Different axes: setting only one
# leaves the count exposed to the other.
DIFF_ARGS = ('-c', 'diff.algorithm=myers', '-c', 'core.quotePath=false',
             'diff', '-U0', '--inter-hunk-context=0', '--no-color',
             '--no-ext-diff', '--no-textconv')


# Fork-owned records: the decision notes, plans and ADRs the fork wrote about
# itself. `docs/actas.md` and `docs/building-on-agmsg.md` are deliberately NOT
# here -- they describe behaviour, so they follow whatever that behaviour turns
# out to be, which is a judgement.
FORK_RECORD_PREFIXES = ('docs/decisions/', 'docs/superpowers/', 'docs/plan/', 'docs/adr/')
FORK_RECORD_RATIONALE = 'fork-owned record of its own decisions; not an upstream concern'
TEST_INHERIT_RATIONALE = 'test follows the implementation it exercises: '
# `rationale` shorter than this is treated as not written rather than written.
# The number has no derivation; it exists to reject "port" as an explanation.
MIN_RATIONALE = 10


class LedgerError(RuntimeError):
    """A precondition the enumeration refuses to work around."""


def canonical_command(base_merge=BASE_MERGE, base_fork=BASE_FORK):
    """The command as a display string, for the header and the design note."""
    return ('GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null git '
            + ' '.join(DIFF_ARGS) + f' {base_merge} {base_fork}')


def run_diff(repo='.', base_merge=BASE_MERGE, base_fork=BASE_FORK):
    """The canonical command's stdout, decoded with replacement."""
    env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')
    done = subprocess.run(['git', '-C', repo, *DIFF_ARGS, base_merge, base_fork],
                          capture_output=True, env=env)
    if done.returncode != 0:
        raise LedgerError('diff failed: ' + done.stderr.decode('utf8', 'replace').strip())
    return done.stdout.decode('utf8', 'replace')


def declared_lines(hunk_range):
    """Lines the `@@` header claims: the removed count plus the added count.

    `-m,a +n,b` with either count omitted meaning 1, which is how git writes a
    single-line side.
    """
    total = 0
    for token in hunk_range.split():
        if token[:1] not in ('-', '+'):
            continue
        _, _, count = token[1:].partition(',')
        total += int(count) if count else 1
    return total


def enumerate_hunks(diff_text):
    """Hunks as (hunk_id, path, range, body), per the design note's rules 1-6.

    Fail-closed at rules 1 and 4.5: a `diff --git` line without ` b/`, or a body
    whose length disagrees with what its own `@@` header declares, stops the
    enumeration instead of producing a different id. Both are cases where a
    plausible-looking guess would silently yield a second valid-looking answer.
    """
    hunks, unexercised = [], []
    path, hunk_range, body, in_hunk = None, None, [], False

    def close():
        if not in_hunk:
            return
        text = '\n'.join(body)
        declared = declared_lines(hunk_range)
        if declared != len(body):
            raise LedgerError(
                f'rule 4.5: {path} @@{hunk_range}@@ declares {declared} lines, body has {len(body)}')
        payload = (path + '\0' + hunk_range + '\0' + text).encode('utf8', 'replace')
        hunks.append((hashlib.sha256(payload).hexdigest()[:16], path, hunk_range, text))

    for line in diff_text.split('\n'):
        if line.startswith('diff --git '):
            close()
            in_hunk = False
            marker = line.find(' b/')
            if marker < 0:
                raise LedgerError('rule 1: no " b/" in: ' + line)
            path = line[marker + len(' b/'):]
        elif line.startswith('@@'):
            close()
            parts = line.split('@@')
            if len(parts) < 3:
                raise LedgerError('rule 2: malformed hunk header: ' + line)
            hunk_range, body, in_hunk = parts[1], [], True
        elif line.startswith('rename ') or line.startswith('similarity '):
            unexercised.append(line)
        elif in_hunk:
            if line.startswith('\\'):
                unexercised.append(line)
            elif line and line[0] in '+- ':
                body.append(line)
    close()
    return hunks, unexercised


def normalized_stem(path):
    """Basename without its extension, `-` folded to `_`, lowercased.

    `codex-bridge.js` and `test_codex_bridge.bats` differ only in punctuation;
    without this they do not match and 42 of the 44 correspondences disappear.
    """
    name = path.rsplit('/', 1)[-1]
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    return stem.replace('-', '_').lower()


def is_test_path(path):
    """Anywhere under tests/, any extension, as long as the basename says test_.

    Not `tests/test_*.bats`: read that way, `tests/isolated/**` and
    `tests/test_helper.bash` fall outside the rule entirely rather than being
    matched or left for judgement, which is not what the rule means.
    """
    return path.startswith('tests/') and path.rsplit('/', 1)[-1].startswith('test_')


def implementations_for(test_path, paths):
    """Every changed implementation whose name the test's name points at.

    A match is equality or one stem being a prefix of the other. Candidates are
    every changed path outside tests/ and docs/ -- restricting the search to
    scripts/ loses install.sh and uninstall.sh.

    Often more than one: `test_api.bats` points at `scripts/api.sh`,
    `scripts/lib/api-actas-owner.sh` and `scripts/lib/api-registrations.sh`
    alike, because prefix matching cannot tell a file from its neighbours. 22 of
    the 44 corresponding tests are in that position, covering 166 of the 211
    hunks, so the plural is the normal case rather than an edge one.
    """
    # Check the prefix before removing it. Slicing five characters off any name
    # turns `broker.js` into `r`, which then prefix-matches README.md and ten
    # other files. The old `if not stem` only caught names of five characters or
    # fewer, because it was looking at what survived the slice rather than at
    # whether the slice was warranted.
    #
    # Every caller happens to ask is_test_path first, so no classification was
    # ever wrong. The condition belongs here anyway: the guarantee should not
    # depend on each caller remembering it.
    stem = normalized_stem(test_path)
    if not stem.startswith('test_'):
        return []
    stem = stem[len('test_'):]
    if not stem:
        return []
    found = set()
    for path in paths:
        if path.startswith('tests/') or path.startswith('docs/'):
            continue
        other = normalized_stem(path)
        if stem == other or stem.startswith(other) or other.startswith(stem):
            found.add(path)
    return sorted(found)


UNCLASSIFIED = ''


def owners_by_path(rows):
    """path -> the set of owner values its hunks carry, blanks included.

    A set, not a value. `owner` is decided per hunk, and a file's hunks
    routinely disagree: `scripts/lib/actas-lock.sh` carries 19 agguild and 2
    official. Collapsing that to one value per path -- which a `{path: owner}`
    comprehension does silently, keeping whichever hunk came last -- makes a
    file look uniformly owned by its final hunk, and any test following it
    inherits that. Nothing catches it afterwards: the rule and the check read
    the same collapsed value, agree with each other, and report no mismatch.

    Blanks are kept rather than dropped. A file with seven agguild hunks and
    three nobody has looked at is not an agguild file yet: the three are unknown,
    not agreeing, and deciding on the seven means the answer can be overturned
    later by the other three.
    """
    found = {}
    for row in rows:
        found.setdefault(row['path'], set()).add(row['owner'])
    return found


def mechanical_rule(path, owners_by_path):
    """(owner, disposition, rationale) where a rule decides it, else None.

    The test rule reads the ledger's own owners rather than deciding one: which
    owner a test inherits is not knowable until the implementation it follows
    has been classified, and that is a judgement. So this is re-run as the
    judged rows land, and fills in behind them.
    """
    if path.startswith(FORK_RECORD_PREFIXES):
        return 'agguild', 'port', FORK_RECORD_RATIONALE
    if is_test_path(path):
        implementations = implementations_for(path, owners_by_path)
        owners = set()
        for implementation in implementations:
            owners |= owners_by_path.get(implementation, {UNCLASSIFIED})
        # One owner across every hunk of every implementation this test points
        # at, and every one of those hunks classified. Two owners is not a tie
        # to break, and an unclassified hunk is not a vote for the majority: in
        # both cases which owner to follow has stopped being mechanical.
        if len(owners) == 1 and owners <= OWNERS:
            owner = owners.pop()
            disposition = 'adopt' if owner == 'official' else 'port'
            return owner, disposition, TEST_INHERIT_RATIONALE + ', '.join(implementations)
    return None


def digest_of(hunk_ids):
    """sha256 of the ids, C-sorted, LF-separated AND LF-terminated."""
    joined = ''.join(i + '\n' for i in sorted(hunk_ids))
    return hashlib.sha256(joined.encode('utf8')).hexdigest()


def diff_config_keys(repo='.'):
    """Layer 3: how many `diff.*` keys survive blanking global and system.

    Diagnostic only, never a verdict. It sees `diff.*` and nothing else --
    core.bigFileThreshold, GIT_CONFIG_COUNT and .gitattributes all pass it. The
    guarantee is the digest; this only helps guess why a mismatch happened.
    """
    env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')
    done = subprocess.run(['git', '-C', repo, 'config', '--list', '--show-origin'],
                          capture_output=True, text=True, env=env)
    return [l for l in done.stdout.splitlines() if '\tdiff.' in l or l.startswith('diff.')]


def build_header(repo='.', base_merge=BASE_MERGE, base_fork=BASE_FORK):
    hunks, _ = enumerate_hunks(run_diff(repo, base_merge, base_fork))
    version = subprocess.run(['git', '-C', repo, '--version'],
                             capture_output=True, text=True).stdout.strip()
    files = sorted({path for _, path, _, _ in hunks})
    return '\n'.join([
        '# agmsg fork hunk ledger (Issue #254 / #222)',
        f'# base-merge: {base_merge}',
        f'# base-fork: {base_fork}',
        f'# base-upstream: {BASE_UPSTREAM}',
        f'# command: {canonical_command(base_merge, base_fork)}',
        f'# git-version: {version.replace("git version ", "")}',
        "# precondition: `git config --list --show-origin | grep -c '\\bdiff\\.'` must be 0",
        f'# expected-hunks: {len(hunks)}',
        f'# expected-files: {len(files)}',
        f'# expected-digest: {digest_of([h for h, _, _, _ in hunks])}',
        "#                  grep -v '^#' <ledger> | cut -f1 | LC_ALL=C sort | shasum -a 256",
        '#                  (LF-separated AND LF-terminated; see the design doc for the byte-level spec)',
        '# NOTE: the count depends on this exact command AND on git config. -U3:484, -U10:404,',
        '#       -w:742, diff.interHunkContext=10:450, core.quotePath=true changes one non-ASCII path.',
        '#       Trust expected-hunks/expected-digest, not your own diff.',
        '#',
        '# columns: ' + '  '.join(COLUMNS),
    ])


def read_rows(ledger_path):
    """(header lines, rows as dicts, malformed raw lines) from a ledger file."""
    with open(ledger_path, encoding='utf8') as handle:
        lines = handle.read().split('\n')
    rows, malformed = [], []
    for line in lines:
        if line.startswith('#') or not line.strip():
            continue
        fields = line.split('\t')
        if len(fields) != len(COLUMNS):
            malformed.append(line)
            continue
        rows.append(dict(zip(COLUMNS, fields)))
    return lines, rows, malformed


def classify_mechanical(ledger_path):
    """The ledger with the mechanical rules applied. Judged rows are untouched.

    Rows a rule does not reach keep whatever they have, including nothing: not
    reaching a row is "this needs a person", not "this is official".
    """
    lines, rows, malformed = read_rows(ledger_path)
    if malformed:
        raise LedgerError(f'{len(malformed)} malformed row(s); run --check first')
    owners = owners_by_path(rows)
    filled = 0
    by_id = {}
    for row in rows:
        rule = mechanical_rule(row['path'], owners)
        if rule is None:
            by_id[row['hunk_id']] = row
            continue
        row['owner'], row['disposition'], row['rationale'] = rule
        # `classified`, never `verified`: a rule having fired is not a person
        # having checked, and the execution gate asks for the latter.
        row['status'] = 'classified'
        by_id[row['hunk_id']] = row
        filled += 1
    # Why each test that could have inherited did not. Three different things
    # look identical in the ledger -- one implementation internally split, two
    # implementations disagreeing, hunks nobody has judged yet -- and the row is
    # blank in all three. The counts alone do not say what to do next.
    blocked = {}
    for path in sorted({row['path'] for row in rows if is_test_path(row['path'])}):
        if mechanical_rule(path, owners) is not None:
            continue
        seen = {i: sorted(v or '(unclassified)' for v in owners.get(i, {UNCLASSIFIED}))
                for i in implementations_for(path, owners)}
        if seen:
            blocked[path] = seen
    out = []
    for line in lines:
        if line.startswith('#'):
            out.append(line)
            continue
        if not line.strip():
            continue
        row = by_id.get(line.split('\t')[0])
        out.append('\t'.join(row[column] for column in COLUMNS) if row else line)
    return out, filled, blocked


def parse_header(lines):
    """`# key: value` pairs from the ledger's comment block."""
    header = {}
    for line in lines:
        if not line.startswith('#'):
            continue
        key, sep, value = line[1:].partition(':')
        if sep and not key.strip().startswith(' '):
            header.setdefault(key.strip(), value.strip())
    return header


def check(ledger_path, repo='.', base_merge=BASE_MERGE, base_fork=BASE_FORK):
    """Recount from the bases and compare with the ledger. Returns (findings, notes).

    Meaningful only when this runs separately from whatever wrote the header: a
    generator that writes and verifies in one pass compares its own output with
    itself and can never disagree.
    """
    findings = {}
    notes = []

    def add(kind, detail):
        findings.setdefault(kind, []).append(detail)

    hunks, unexercised = enumerate_hunks(run_diff(repo, base_merge, base_fork))
    for line in unexercised:
        add('unexercised-rule', line.strip())

    with open(ledger_path, encoding='utf8') as handle:
        lines = handle.read().split('\n')
    header = parse_header(lines)

    rows, seen = {}, set()
    for line in lines:
        if line.startswith('#') or not line.strip():
            continue
        fields = line.split('\t')
        if len(fields) != len(COLUMNS):
            add('malformed', f'{len(fields)} of {len(COLUMNS)} columns: {fields[0] if fields else line}')
            continue
        row = dict(zip(COLUMNS, fields))
        if row['hunk_id'] in seen:
            add('duplicate', row['hunk_id'])
            continue
        seen.add(row['hunk_id'])
        rows[row['hunk_id']] = row

    actual = {h: path for h, path, _, _ in hunks}
    for hunk_id, path in sorted(actual.items()):
        row = rows.get(hunk_id)
        if row is None:
            add('missing', f'{hunk_id} {path}')
            continue
        if row['path'] != path:
            add('path-mismatch', f"{hunk_id} ledger={row['path']} actual={path}")
    for hunk_id, row in sorted(rows.items()):
        if hunk_id not in actual:
            add('stale', f"{hunk_id} {row['path']}")
            continue
        if row['owner'] and row['owner'] not in OWNERS:
            add('invalid-owner', f"{hunk_id} owner={row['owner']}")
        if row['disposition'] and row['disposition'] not in DISPOSITIONS:
            add('invalid-disposition', f"{hunk_id} disposition={row['disposition']}")
        if row['status'] and row['status'] not in STATUSES:
            add('invalid-status', f"{hunk_id} status={row['status']}")
        # Design 1.2: disposition follows owner, except for `exception`, where
        # the choice between port and drop is the judgement. Every one of these
        # is a contradiction between two values somebody wrote, never a blank.
        pairing = {'official': ('adopt',), 'agguild': ('port',), 'exception': ('port', 'drop')}
        allowed = pairing.get(row['owner'])
        if allowed and row['disposition'] and row['disposition'] not in allowed:
            add('owner-disposition-mismatch',
                f"{hunk_id} owner={row['owner']} disposition={row['disposition']}")
        if row['owner'] == 'pool':
            # The pool holds agent personas and state, which live in another
            # repository entirely. One here means the classification is wrong or
            # the premise is; either way it stops rather than being absorbed.
            add('unexpected-pool', f"{hunk_id} {row['path']}")
        if row['rationale'] and len(row['rationale']) < MIN_RATIONALE:
            add('rationale-too-short', f"{hunk_id} rationale={row['rationale']!r}")

    owners = owners_by_path(rows.values())
    for hunk_id, row in sorted(rows.items()):
        if hunk_id not in actual:
            continue
        rule = mechanical_rule(row['path'], owners)
        if rule and row['owner'] and row['owner'] != rule[0]:
            add('rule-mismatch',
                f"{hunk_id} {row['path']} owner={row['owner']} rule={rule[0]}")

    expected = {
        'base-merge': base_merge,
        'base-fork': base_fork,
        'command': canonical_command(base_merge, base_fork),
        'expected-hunks': str(len(hunks)),
        'expected-files': str(len({p for p in actual.values()})),
        'expected-digest': digest_of(actual),
    }
    for key, want in expected.items():
        got = header.get(key)
        if got != want:
            add('header-mismatch', f'{key}: ledger={got!r} recounted={want!r}')

    stray = diff_config_keys(repo)
    notes.append(f'diagnostic: {len(stray)} diff.* config keys survive blanking global/system'
                 + (' -> ' + '; '.join(stray) if stray else ''))
    return findings, notes


def gate(ledger_path, repo='.', base_merge=BASE_MERGE, base_fork=BASE_FORK):
    """Everything the check asks, plus: is the classification actually finished.

    Deliberately not part of `--check`. The check runs in CI from the moment the
    ledger exists, and an unclassified row is the state the design gives every
    row to start in -- folding this in would paint CI red for the whole
    classification period and call a planned state a defect. The design's gate
    has three further conditions that are not machine-readable at all, so this
    is one input to that decision, not the decision.
    """
    findings, notes = check(ledger_path, repo, base_merge, base_fork)

    def add(kind, detail):
        findings.setdefault(kind, []).append(detail)

    with open(ledger_path, encoding='utf8') as handle:
        for line in handle.read().split('\n'):
            if line.startswith('#') or not line.strip():
                continue
            fields = line.split('\t')
            if len(fields) != len(COLUMNS):
                continue  # already reported as malformed by the check
            row = dict(zip(COLUMNS, fields))
            for column in ('owner', 'disposition', 'rationale'):
                if not row[column]:
                    add('unclassified', f"{row['hunk_id']} {column} is empty")
            if row['status'] != 'verified':
                add('not-verified', f"{row['hunk_id']} status={row['status'] or '(empty)'}")
    return findings, notes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--emit-header', action='store_true')
    parser.add_argument('--emit-rows', action='store_true')
    parser.add_argument('--check', metavar='LEDGER')
    parser.add_argument('--classify-mechanical', metavar='LEDGER',
                        help='apply the mechanical owner rules and print the ledger')
    parser.add_argument('--gate', metavar='LEDGER',
                        help='--check, plus the design\'s execution-gate condition that '
                             'every row is verified. Not run by CI; see gate().')
    parser.add_argument('--repo', default='.')
    parser.add_argument('--base-merge', default=BASE_MERGE)
    parser.add_argument('--base-fork', default=BASE_FORK)
    args = parser.parse_args(argv)

    try:
        if args.emit_header:
            print(build_header(args.repo, args.base_merge, args.base_fork))
            return 0
        if args.emit_rows:
            hunks, _ = enumerate_hunks(run_diff(args.repo, args.base_merge, args.base_fork))
            for hunk_id, path, _, _ in hunks:
                print('\t'.join((hunk_id, path) + BLANK_ROW))
            return 0
        if args.classify_mechanical:
            out, filled, split = classify_mechanical(args.classify_mechanical)
            print('\n'.join(out))
            print(f'classified {filled} row(s) by rule', file=sys.stderr)
            for path, seen in sorted(split.items()):
                # Named, not counted: a test that did not inherit is indis-
                # tinguishable from one no rule reached unless the reason is
                # readable somewhere.
                detail = '; '.join(f'{i} carries {"/".join(v)}' for i, v in sorted(seen.items()))
                print(f'  not inherited: {path} <- {detail}', file=sys.stderr)
            return 0
        if args.check:
            findings, notes = check(args.check, args.repo, args.base_merge, args.base_fork)
        elif args.gate:
            findings, notes = gate(args.gate, args.repo, args.base_merge, args.base_fork)
        else:
            parser.error('one of --emit-header, --emit-rows, --classify-mechanical, '
                         '--check, --gate is required')
    except LedgerError as error:
        print(f'FAIL {error}', file=sys.stderr)
        return 1

    for note in notes:
        print(note, file=sys.stderr)
    if findings:
        print('FAIL ' + repr({k: len(v) for k, v in sorted(findings.items())}), file=sys.stderr)
        for kind, details in sorted(findings.items()):
            for detail in details:
                print(f'   {kind}: {detail}', file=sys.stderr)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
