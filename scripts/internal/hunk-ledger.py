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
OWNERS = frozenset({'official', 'agguild', 'pool', 'exception'})
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
        if row['owner'] not in OWNERS:
            add('unclassified', f"{hunk_id} owner={row['owner'] or '(empty)'}")
        if not row['disposition']:
            add('no-disposition', hunk_id)
        if row['status'] not in STATUSES:
            add('invalid-status', f"{hunk_id} status={row['status'] or '(empty)'}")

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
    """Execution-gate condition 1: every row verified, on top of a passing check.

    Deliberately not part of `--check`. The check runs in CI from the moment the
    ledger exists, and `unclassified` is the state the design gives every row to
    start in -- folding this in would paint CI red for the whole classification
    period and call a planned state a defect. The other three conditions in the
    design's gate are not machine-readable at all, so this is one input to that
    decision, not the decision.
    """
    findings, notes = check(ledger_path, repo, base_merge, base_fork)
    unverified = []
    with open(ledger_path, encoding='utf8') as handle:
        for line in handle.read().split('\n'):
            if line.startswith('#') or not line.strip():
                continue
            fields = line.split('\t')
            if len(fields) == len(COLUMNS) and fields[-1] != 'verified':
                unverified.append(f'{fields[0]} status={fields[-1] or "(empty)"}')
    for detail in unverified:
        findings.setdefault('not-verified', []).append(detail)
    return findings, notes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--emit-header', action='store_true')
    parser.add_argument('--emit-rows', action='store_true')
    parser.add_argument('--check', metavar='LEDGER')
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
        if args.check:
            findings, notes = check(args.check, args.repo, args.base_merge, args.base_fork)
        elif args.gate:
            findings, notes = gate(args.gate, args.repo, args.base_merge, args.base_fork)
        else:
            parser.error('one of --emit-header, --emit-rows, --check, --gate is required')
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
