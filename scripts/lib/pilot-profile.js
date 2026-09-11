#!/usr/bin/env node

/*
 * pilot-profile.js — writes the G4 pilot Claude profile (#404).
 *
 * Contract: docs/decisions/2026-09-11T133016_issue-404-pilot-pretool-guard-contract.md §9
 *
 * The profile carries exactly one PreToolUse handler: the absolute path of
 * scripts/pm-pilot-pretool-guard, no args, and a matcher covering every tool.
 * A "Bash" matcher would let Write/Edit reach the tool without the guard.
 *
 * Usage:
 *   node scripts/lib/pilot-profile.js --project <abs path> --guard <abs path>
 *
 * Writes <project>/.claude/settings.local.json and prints its path.
 */

'use strict';

const fs = require('fs');
const path = require('path');

const GUARD_NAME = 'pm-pilot-pretool-guard';
const GUARD_MATCHER = '*';
// Longer than the guard's worst case (2s identity helper plus digests).
const GUARD_TIMEOUT_SECONDS = 10;

const fail = (reason) => {
  process.stderr.write(`pilot-profile: ${reason}\n`);
  process.exit(1);
};

const renderProfile = (guardPath) => ({
  hooks: {
    PreToolUse: [
      {
        matcher: GUARD_MATCHER,
        hooks: [
          {
            type: 'command',
            command: guardPath,
            timeout: GUARD_TIMEOUT_SECONDS,
          },
        ],
      },
    ],
  },
});

const checkGuard = (guardPath) => {
  if (typeof guardPath !== 'string' || !path.isAbsolute(guardPath) || path.basename(guardPath) !== GUARD_NAME) {
    fail('guard path must be an absolute path to pm-pilot-pretool-guard');
  }
  let stat;
  try {
    stat = fs.lstatSync(guardPath);
  } catch (_) {
    fail('guard is unavailable');
  }
  if (stat.isSymbolicLink() || !stat.isFile()) fail('guard must be a regular file, not a symlink');
  if (process.platform !== 'win32' && !(stat.mode & 0o111)) fail('guard is not executable');
};

const writeProfile = ({project, guard}) => {
  if (typeof project !== 'string' || !path.isAbsolute(project)) fail('project must be an absolute path');
  let projectStat;
  try {
    projectStat = fs.statSync(project);
  } catch (_) {
    fail('project is unavailable');
  }
  if (!projectStat.isDirectory()) fail('project is not a directory');
  checkGuard(guard);
  const dir = path.join(project, '.claude');
  const file = path.join(dir, 'settings.local.json');
  fs.mkdirSync(dir, {recursive: true});
  const temp = `${file}.tmp-${process.pid}`;
  fs.writeFileSync(temp, JSON.stringify(renderProfile(guard), null, 2) + '\n', {encoding: 'utf8', mode: 0o644});
  fs.renameSync(temp, file);
  return file;
};

const parseArgs = (argv) => {
  const options = {};
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i];
    const value = argv[i + 1];
    if ((key !== '--project' && key !== '--guard') || value === undefined || options[key.slice(2)] !== undefined) {
      fail('usage: pilot-profile.js --project <abs path> --guard <abs path>');
    }
    options[key.slice(2)] = value;
  }
  if (options.project === undefined || options.guard === undefined) {
    fail('usage: pilot-profile.js --project <abs path> --guard <abs path>');
  }
  return options;
};

if (require.main === module) {
  const file = writeProfile(parseArgs(process.argv.slice(2)));
  process.stdout.write(`${file}\n`);
}

module.exports = {GUARD_MATCHER, GUARD_TIMEOUT_SECONDS, renderProfile, writeProfile};
