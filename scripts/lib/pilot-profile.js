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
 * With --posttool, the profile also carries exactly one PostToolUse handler:
 * the absolute path of scripts/pm-posttool-record (F3 requires one). The
 * profile is assembled only here, so nothing appends hooks afterwards and the
 * digest pinned by the binding covers the whole profile.
 *
 * Usage:
 *   node scripts/lib/pilot-profile.js --project <abs path> --guard <abs path> [--posttool <abs path>]
 *
 * Writes <project>/.claude/settings.local.json and prints its path.
 */

'use strict';

const fs = require('fs');
const path = require('path');

const GUARD_NAME = 'pm-pilot-pretool-guard';
const POSTTOOL_NAME = 'pm-posttool-record';
const GUARD_MATCHER = '*';
// Longer than the guard's worst case (2s identity helper plus digests).
const GUARD_TIMEOUT_SECONDS = 10;

const fail = (reason) => {
  process.stderr.write(`pilot-profile: ${reason}\n`);
  process.exit(1);
};

const handlerGroup = (command) => ({
  matcher: GUARD_MATCHER,
  hooks: [
    {
      type: 'command',
      command,
      timeout: GUARD_TIMEOUT_SECONDS,
    },
  ],
});

const renderProfile = (guardPath, posttoolPath = null) => {
  const hooks = {PreToolUse: [handlerGroup(guardPath)]};
  if (posttoolPath !== null) hooks.PostToolUse = [handlerGroup(posttoolPath)];
  return {hooks};
};

const checkHook = (label, file, name) => {
  if (typeof file !== 'string' || !path.isAbsolute(file) || path.basename(file) !== name) {
    fail(`${label} path must be an absolute path to ${name}`);
  }
  let stat;
  try {
    stat = fs.lstatSync(file);
  } catch (_) {
    fail(`${label} is unavailable`);
  }
  if (stat.isSymbolicLink() || !stat.isFile()) fail(`${label} must be a regular file, not a symlink`);
  if (process.platform !== 'win32' && !(stat.mode & 0o111)) fail(`${label} is not executable`);
};

const writeProfile = ({project, guard, posttool = null}) => {
  if (typeof project !== 'string' || !path.isAbsolute(project)) fail('project must be an absolute path');
  let projectStat;
  try {
    projectStat = fs.statSync(project);
  } catch (_) {
    fail('project is unavailable');
  }
  if (!projectStat.isDirectory()) fail('project is not a directory');
  checkHook('guard', guard, GUARD_NAME);
  if (posttool !== null) checkHook('posttool', posttool, POSTTOOL_NAME);
  const dir = path.join(project, '.claude');
  const file = path.join(dir, 'settings.local.json');
  fs.mkdirSync(dir, {recursive: true});
  const temp = `${file}.tmp-${process.pid}`;
  fs.writeFileSync(temp, JSON.stringify(renderProfile(guard, posttool), null, 2) + '\n', {encoding: 'utf8', mode: 0o644});
  fs.renameSync(temp, file);
  return file;
};

const USAGE = 'usage: pilot-profile.js --project <abs path> --guard <abs path> [--posttool <abs path>]';

const parseArgs = (argv) => {
  const options = {};
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i];
    const value = argv[i + 1];
    if (!['--project', '--guard', '--posttool'].includes(key) || value === undefined ||
        options[key.slice(2)] !== undefined) {
      fail(USAGE);
    }
    options[key.slice(2)] = value;
  }
  if (options.project === undefined || options.guard === undefined) fail(USAGE);
  return options;
};

if (require.main === module) {
  const file = writeProfile(parseArgs(process.argv.slice(2)));
  process.stdout.write(`${file}\n`);
}

module.exports = {GUARD_MATCHER, GUARD_TIMEOUT_SECONDS, renderProfile, writeProfile};
