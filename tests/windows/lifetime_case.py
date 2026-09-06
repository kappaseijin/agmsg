"""Assemble bats-fixture excerpts by reference closure; refuse unresolved names."""
from pathlib import Path
import re
import subprocess

from lifetime_adapter import bash_executable

ROOT=Path(__file__).resolve().parents[2]
DEF=re.compile(r'(?m)^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{')
# Helpers in this repository are always named `_[a-z][a-z0-9_]*`. Keying on that
# convention removes the need to tokenize quoting, which a prototype got wrong on
# the multi-line PowerShell embedded in `_windows_native_diag_snapshot`.
HELPER=re.compile(
    r'(?m)(?:^|&&|\|\||[;|(]|\$\(|`|\bif\b|\bthen\b|\belse\b|\bdo\b|\buntil\b|\bwhile\b)'
    r'\s*(?:!\s+)?(_[a-z][a-z0-9_]*)(?![=\w])')
EXTERNAL_SOURCES=('tests/test_helper*.bash','scripts/lib/*.sh','lib/*.sh',
                  'tests/windows/lifetime-adapter.sh')


SHADOWED=frozenset({'setup','teardown','taskkill'})
# Names that must NOT be renamed, and cannot be derived: bats calls `setup` and
# `teardown` by name, and `taskkill` deliberately shadows the external command.
CONFORMING=re.compile(r'_[a-z][a-z0-9_]*')


class UnresolvedReference(ValueError):
    """Raised before any case script is written, never at rc=127 runtime."""


def definitions(source):
    """Map function name to its text, terminated by a line containing only `}`."""
    found={}
    for match in DEF.finditer(source):
        name=match.group(1)
        if name in found: continue
        end=source.find('\n}\n',match.start())
        if end<0: continue
        found[name]=source[match.start():end+3]
    return found


def definition_names(source):
    """Names only. Single-line stubs never match the `\\n}\\n` terminator above."""
    return {match.group(1) for match in DEF.finditer(source)}


def nonconforming_definitions(source):
    """Fixture-defined names that break the `_[a-z]` convention HELPER assumes.

    HELPER only sees `_[a-z]...`, so a helper named otherwise is invisible to the
    unresolved-reference scan. Refusing the definition closes the road to that hole
    instead of trying to widen the scan (which leaves false positives; see the
    design note for the measurement).
    """
    return {name for name in definition_names(source)
            if name not in SHADOWED and not CONFORMING.fullmatch(name)}


def helper_refs(text):
    return {match.group(1) for match in HELPER.finditer(text)}


def external_helpers(root=ROOT):
    """Names resolved outside the fixture: test_helper, sourced libs, adapter hooks."""
    names=set()
    for pattern in EXTERNAL_SOURCES:
        for path in sorted(Path(root).glob(pattern)):
            names|=definition_names(path.read_text(encoding='utf8',errors='replace'))
    return names


def build_case(source, entry, stubs='', tail='', root=ROOT):
    """Return the case script text. Never writes a file; raises before that can happen."""
    bad=nonconforming_definitions(source)
    if bad: raise UnresolvedReference('fixture defines non-conforming names: '+', '.join(sorted(bad)))
    defs=definitions(source)
    if entry not in defs: raise UnresolvedReference('entry not defined: '+entry)
    stubbed=definition_names(stubs)
    kept={}
    frontier=[entry]
    while frontier:
        name=frontier.pop()
        # Stubbed names are kept too, not skipped: the stub text appended below wins
        # by position, and keeping the real definition holds its callees in the closure.
        if name in kept or name not in defs: continue
        kept[name]=defs[name]
        frontier.extend(sorted(helper_refs(defs[name])))
    # Stubs come after the closure: bash lets the later definition win, which is how
    # existing tests deliberately replace taskkill and the bridge-pid helpers.
    script='\n'.join(kept.values())+stubs+tail
    missing=helper_refs(script)-set(kept)-stubbed-external_helpers(root)
    if missing: raise UnresolvedReference('unresolved helper references: '+', '.join(sorted(missing)))
    check=subprocess.run([bash_executable(),'-n'],input=script,capture_output=True,text=True)
    if check.returncode!=0: raise UnresolvedReference('case script fails bash -n: '+check.stderr.strip())
    return script

