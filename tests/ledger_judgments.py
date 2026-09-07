"""Judgment names the implementation reports, and the ones the design lists.

Prints one `impl <name>` or `docs <name>` line each, so a shell test can take
the symmetric difference without parsing Python or Markdown itself.

The design note's table is read by structure -- pipe-separated cells, `/`
expanded -- not by eye. That makes the reading fragile in one specific way: if
the table's format changes, this returns nothing and the difference is empty,
which reads as agreement. The caller must assert the docs side is non-empty.
"""
import glob
import re
import sys

source = open(sys.argv[1], encoding='utf8').read()
for name in sorted(set(re.findall(r"add\('([a-z-]+)'", source))):
    print('impl', name)

names = set()
for path in glob.glob(sys.argv[2]):
    text = open(path, encoding='utf8').read()
    section = re.search(r'#### 5\.4\.1.*?\n(?=####|###|\Z)', text, re.S)
    if not section:
        continue
    for row in section.group(0).splitlines():
        if not row.startswith('|'):
            continue
        cells = [c.strip() for c in row.strip('|').split('|')]
        if len(cells) < 2 or '--check' not in cells[1] and '--gate' not in cells[1]:
            continue
        for part in cells[0].split('/'):
            found = re.search(r'`([a-z-]+)`', part)
            if found:
                names.add(found.group(1))
for name in sorted(names):
    print('docs', name)
