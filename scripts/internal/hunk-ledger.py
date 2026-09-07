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
import re
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


# Dependency reading, used only by the official-depends-on-agguild verdict.
#
# Restricted to code: a name in prose is a word, not a reference. Without that
# restriction README.ja.md alone reports hundreds of hits on words like `name`
# and `state` (measured: 1044 findings across the ledger, against 8 with the
# restrictions below).
CODE_SUFFIXES = ('.sh', '.bash', '.bats', '.js', '.mjs', '.py', '.awk')
FUNCTION = re.compile(r'(?m)^\+\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{')
# Upper case only. A lower-case assignment is almost always a local, and reading
# one file's local as another hunk's definition produced most of the noise
# (measured: 75 findings, against 26 when limited to these).
GLOBAL = re.compile(r'(?m)^\+\s*(?:export\s+|readonly\s+)?([A-Z][A-Z0-9_]{2,})=')
# A use, not an occurrence: an expansion, or a name in command position. The
# same shape the HELPER scan in lifetime_case.py uses, for the same reason -- a
# bare word anywhere in a line is usually prose or an argument.
USES = re.compile(r"""\$\{?([A-Za-z_][A-Za-z0-9_]*)
                    | (?:^|&&|\|\||[;|(`]|\$\(|\bif\b|\bthen\b|\belse\b|\bdo\b|\buntil\b|\bwhile\b)
                      \s*(?:!\s+)?([A-Za-z_][A-Za-z0-9_]*)(?![=\w])""", re.X | re.M)


# Names that a test fixture defines in order to stand in for an external
# command. A hunk calling one of these is calling the command, not the stub, so
# the stub is not something it depends on.
#
# A list, because there is nothing to derive it from: whether a definition
# shadows a command is a fact about the machine, and reading it from the machine
# would make this check answer differently on different runners. Only honoured
# when the definition lives under tests/ -- if product code ever defines
# `mktemp`, that is a real dependency and this must not hide it.
SHADOWED_COMMANDS = {
    'mktemp': 'test fixtures replace mktemp to control where temp dirs land',
    'rm': 'test fixtures replace rm to assert what a teardown removes',
}


def is_code(path):
    """Whether names in this file are names rather than words."""
    return path.endswith(CODE_SUFFIXES) or '.' not in path.rsplit('/', 1)[-1]


def used_names(body):
    """Names a hunk's lines expand or call.

    The `+`/`-` marker is stripped first. Leaving it on makes every call that
    starts a line invisible, because the name is no longer in command position:
    measured, `agmsg_validate_utf8` -- the design note's own example of a real
    dependency -- was found nowhere at all.
    """
    stripped = '\n'.join(line[1:] if line[:1] in '+- ' else line
                         for line in body.split('\n'))
    return {name for match in USES.finditer(stripped) for name in match.groups() if name}


# `source x`, `. x`, and bats' `load x`. The argument is taken as written: a
# name built from a variable cannot be followed, and that case is handled by
# reporting rather than by guessing (see `visible_files`).
SOURCES = re.compile(r'(?m)^\s*(?:source|\.|load)\s+(\S+)')


def _file_at(path, repo, revision, cache):
    key = (revision, path)
    if key not in cache:
        done = subprocess.run(['git', '-C', repo, 'show', f'{revision}:{path}'],
                              capture_output=True, text=True)
        cache[key] = done.stdout if done.returncode == 0 else ''
    return cache[key]


def visible_files(path, known_paths, repo, revision, cache):
    """(files reachable from `path` by sourcing, whether anything was unresolved).

    Transitive: a library that sources another library brings the second one's
    names along, and stopping at one level would call the second invisible.

    An argument that cannot be resolved statically -- `source "$LIB_DIR/x.sh"`,
    or a name matching no file here -- makes the answer unknown for that file.
    Unknown is reported as visible: a dependency that is real and hidden costs a
    patch that does not build, while one that is visible and false costs a look.
    Which of the two happened is recorded rather than folded into the result.
    """
    by_name = {}
    for candidate in known_paths:
        by_name.setdefault(candidate.rsplit('/', 1)[-1], set()).add(candidate)
    seen, frontier, unresolved = {path}, [path], False
    while frontier:
        current = frontier.pop()
        for argument in SOURCES.findall(_file_at(current, repo, revision, cache)):
            argument = argument.strip('\'"')
            name = argument.rsplit('/', 1)[-1]
            if '$' in argument or name not in by_name:
                unresolved = True
                continue
            for target in by_name[name] - seen:
                seen.add(target)
                frontier.append(target)
    return seen, unresolved


def _defined_at_base(name, paths, repo, base_merge, cache):
    """Whether the name already exists at the merge base, so nobody added it.

    Across every file the caller can reach, not just its own. A test stubbing
    `storage_init` defines a name that upstream already provides in
    drivers/storage/*.sh; looking only at the stub's own file makes the stub
    look like the origin of the name and the caller look dependent on it.

    This is the general form of the shadowed-command list: there, the thing
    being covered is an external command and cannot be read from the tree; here
    it is an upstream function and can, so it is read rather than listed.
    """
    quoted = re.escape(name)
    pattern = re.compile(r'(?m)^\s*(?:function\s+)?' + quoted + r'\s*\(\)'
                         r'|^\s*(?:export\s+|readonly\s+)?' + quoted + '=')
    return any(pattern.search(_file_at(candidate, repo, base_merge, cache))
               for candidate in paths)


def depends_on_agguild(hunks, owners_by_id, repo='.', base_merge=BASE_MERGE,
                       base_fork=BASE_FORK):
    """official hunks that use a name only agguild hunks introduce.

    NOT a decision procedure for the invariant it serves. The invariant is that
    an official hunk must not depend on an agguild one; what this finds is the
    subset of those dependencies that show up as a name. A hunk can carry a
    fork-only subject without borrowing a single name -- a 475-line hunk whose
    body is a fork feature's tests reads as independent here -- so a clean run
    means "no dependency of this shape", never "no dependency". Reading the
    official hunks by hand stays necessary.

    Four narrowings, each measured against the ledger rather than reasoned about:

      code files only            1044 -> 287
      every definer is agguild    287 ->  75   (SCRIPT_DIR is assigned in a
                                                dozen places; one of them being
                                                agguild does not make it theirs)
      functions and globals only   75 ->  26
      globals within one file,     26 ->   8   (a local in another script is not
      names absent from the base                a definition of anything here)

    Still an approximation: no scoping, no ordering, and a definition that
    shadows an external command reads as a definition. A false positive costs
    one person one look; a false negative ships a patch that does not build.
    """
    functions, globals_, files, cache = {}, {}, {}, {}
    for hunk_id, path, _, body in hunks:
        files[hunk_id] = path
        if not is_code(path):
            continue
        for name in FUNCTION.findall(body):
            functions.setdefault(name, set()).add(hunk_id)
        for name in GLOBAL.findall(body):
            globals_.setdefault((path, name), set()).add(hunk_id)

    known = {path for path in files.values() if is_code(path)}
    reach, unresolved = {}, set()
    found = []
    for hunk_id, path, _, body in hunks:
        if owners_by_id.get(hunk_id) != 'official' or not is_code(path):
            continue
        if path not in reach:
            reach[path], blind = visible_files(path, known, repo, base_fork, cache)
            if blind:
                # Unknown, not empty. Filtering this file by a set that is known
                # to be incomplete would read "could not tell" as "not visible",
                # which is the reading that hides real dependencies.
                unresolved.add(path)
                reach[path] = known
        for name in sorted(used_names(body)):
            # Only names this file could see. Environment variables are a gap:
            # `env FOO=bar cmd` reaches a child that sourced nothing, so a
            # cross-file variable dependency is invisible here. Modelling it was
            # measured rather than assumed -- matching variables across files
            # adds two false positives on the current ledger (BATS_TEST_DIRNAME,
            # which bats itself sets) and finds nothing real, because the case
            # that prompted it is an agguild hunk and outside this verdict.
            # Left unmodelled, and recorded as a gap rather than closed badly.
            for definers in (functions.get(name, set()), globals_.get((path, name), set())):
                others = definers - {hunk_id}
                if not others or {owners_by_id.get(o) for o in others} != {'agguild'}:
                    continue
                # A definition in this same file wins over anything sourced, so
                # the name is this file's own. `repair-invalid-utf8.sh` defines
                # and calls its own `usage`; another script's `usage` is a
                # coincidence of naming, not a dependency.
                if any(files[o] == path for o in definers):
                    continue
                # A function defined in a file this one never sources is not the
                # function being called; two scripts each defining `usage` are
                # not a dependency.
                if path in reach and not (reach[path] & {files[o] for o in others}):
                    continue
                if name in SHADOWED_COMMANDS and all(
                        files[o].startswith('tests/') for o in others):
                    continue
                if _defined_at_base(name, reach.get(path, {path}), repo, base_merge, cache):
                    continue
                found.append((hunk_id, path, name, sorted(others)))
                break
    return found, sorted(unresolved)


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

    dependent, unresolved = depends_on_agguild(
        hunks, {row['hunk_id']: row['owner'] for row in rows.values()},
        repo, base_merge, base_fork)
    for hunk_id, path, name, sources in dependent:
        add('official-depends-on-agguild',
            f'{hunk_id} {path} uses {name}, added by ' + ', '.join(sources))
    if unresolved:
        notes.append('diagnostic: sources not statically resolvable, so everything '
                     'counts as visible from: ' + ', '.join(unresolved))

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
