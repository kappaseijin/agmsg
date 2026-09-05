#!/usr/bin/env node

/*
 * Strict session identity query for the PM PreToolUse guard (#236/#240).
 *
 * This is the one place where the hook asks for the (team, agent, project,
 * type, session, process-generation) binding. The hook does not parse team
 * configuration or actas lock files itself. Environment variables are only
 * launcher-provided selectors; every value is checked against the binding,
 * roster, current claim, and live process before success is returned.
 */

'use strict';

const fs = require('fs');
const path = require('path');
const {execFileSync, spawnSync} = require('child_process');

const fail = (reason) => {
  process.stderr.write(JSON.stringify({status: 'unidentifiable', reason}) + '\n');
  process.exit(1);
};

const text = (value, name) => {
  if (typeof value !== 'string' || value.length === 0 || /[\u0000-\u001f\u007f]/u.test(value)) {
    fail(`${name}_invalid`);
  }
  return value;
};

const commandOutput = (command, args, reason) => {
  try {
    return execFileSync(command, args, {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 1000,
    }).trim();
  } catch (_) {
    fail(reason);
  }
};

const processStartToken = (pid) => {
  if (process.platform === 'win32') {
    return commandOutput(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-Command',
        `(Get-Process -Id ${pid}).StartTime.ToUniversalTime().ToString("o")`],
      'process_start_unavailable',
    );
  }
  return commandOutput('ps', ['-o', 'lstart=', '-p', String(pid)], 'process_start_unavailable');
};

const parentPid = (pid) => {
  if (process.platform === 'win32') {
    const value = commandOutput(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-Command',
        `(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}").ParentProcessId`],
      'process_parent_unavailable',
    );
    if (!/^[0-9]+$/u.test(value)) fail('process_parent_invalid');
    return Number(value);
  }
  const value = commandOutput('ps', ['-o', 'ppid=', '-p', String(pid)], 'process_parent_unavailable');
  if (!/^[0-9]+$/u.test(value)) fail('process_parent_invalid');
  return Number(value);
};

const isDescendantOf = (targetPid) => {
  let current = process.ppid;
  const seen = new Set();
  for (let depth = 0; depth < 32 && current > 1; depth += 1) {
    if (current === targetPid) return true;
    if (seen.has(current)) break;
    seen.add(current);
    current = parentPid(current);
  }
  return false;
};

const readJson = (file, reason) => {
  let value;
  try {
    value = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (_) {
    fail(reason);
  }
  return value;
};

const canonicalPath = (value, reason) => {
  try {
    return fs.realpathSync.native(value);
  } catch (_) {
    fail(reason);
  }
};

const encodeLockPart = (value) => {
  const bytes = Buffer.from(value, 'utf8');
  let result = '';
  for (const byte of bytes) {
    const character = String.fromCharCode(byte);
    result += /[A-Za-z0-9._-]/.test(character)
      ? character
      : `%${byte.toString(16).toUpperCase().padStart(2, '0')}`;
  }
  return result;
};

const parseInput = () => {
  let input;
  try {
    input = JSON.parse(fs.readFileSync(0, 'utf8'));
  } catch (_) {
    fail('hook_input_invalid');
  }
  if (!input || typeof input !== 'object' || Array.isArray(input)) fail('hook_input_invalid');
  const sessionId = text(input.session_id, 'session_id');
  const cwd = text(input.cwd, 'cwd');
  const toolName = text(input.tool_name, 'tool_name');
  const toolUseId = text(input.tool_use_id, 'tool_use_id');
  if (!input.tool_input || typeof input.tool_input !== 'object' || Array.isArray(input.tool_input)) {
    fail('tool_input_invalid');
  }
  return {sessionId, cwd, toolName, toolUseId};
};

if (process.argv[2] === '--process-start') {
  const processPidArg = text(process.argv[3] || '', 'process_pid');
  if (!/^[1-9][0-9]*$/u.test(processPidArg)) fail('process_pid_invalid');
  process.stdout.write(`${processStartToken(Number(processPidArg))}\n`);
  process.exit(0);
}

const input = parseInput();
const bindingFile = text(process.env.AGMSG_PM_BINDING_FILE || '', 'binding_file');
const team = text(process.env.AGMSG_PM_TEAM || '', 'launcher_team');
const agent = text(process.env.AGMSG_PM_AGENT || '', 'launcher_agent');
const type = text(process.env.AGMSG_PM_TYPE || '', 'launcher_type');
const processPid = text(process.env.AGMSG_PM_PROCESS_PID || '', 'process_pid');
const generation = text(process.env.AGMSG_PM_PROCESS_GENERATION || '', 'process_generation');
const processStart = text(process.env.AGMSG_PM_PROCESS_START || '', 'process_start');
const binding = readJson(bindingFile, 'binding_unreadable');

if (!binding || binding.schemaVersion !== 1) fail('binding_schema_invalid');
for (const field of [
  'team', 'agent', 'type', 'project', 'sessionId', 'generation', 'pid', 'pidStart',
  'profileDigest', 'policyVersion', 'guardDigest', 'brokerDigest',
]) {
  text(binding[field], `binding_${field}`);
}
if (binding.team !== team || binding.agent !== agent || binding.type !== type) fail('binding_launcher_mismatch');
if (binding.sessionId !== input.sessionId || binding.generation !== generation || binding.pidStart !== processStart) {
  fail('binding_process_mismatch');
}
if (binding.pid !== processPid || !/^[1-9][0-9]*$/u.test(processPid)) fail('binding_pid_mismatch');
if (binding.project !== canonicalPath(input.cwd, 'cwd_unreadable')) fail('binding_project_mismatch');
if (binding.project !== canonicalPath(binding.project, 'binding_project_unreadable')) fail('binding_project_mismatch');

const pid = Number(processPid);
if (!Number.isSafeInteger(pid) || pid <= 1) fail('binding_pid_invalid');
if (!/^sha256:[0-9a-f]{64}$/u.test(binding.guardDigest) || !/^sha256:[0-9a-f]{64}$/u.test(binding.brokerDigest)) {
  fail('binding_digest_invalid');
}
if (processStartToken(pid) !== processStart) fail('process_start_mismatch');
if (!isDescendantOf(pid)) fail('process_parent_mismatch');
const instanceScript = path.join(process.env.SKILL_DIR || path.join(__dirname, '..'), 'scripts', 'lib', 'instance-id.sh');
const liveness = spawnSync('bash', ['-c', 'set -e; . "$1"; agmsg_instance_alive "$2"', '--', instanceScript, `${input.sessionId}.${processPid}`], {
  encoding: 'utf8',
  timeout: 2000,
  env: process.env,
});
if (liveness.error || liveness.status !== 0) {
  fail('process_not_live');
}

const teamsDir = process.env.AGMSG_PM_TEAMS_DIR || path.join(__dirname, '..', 'teams');
let teamFiles;
try {
  teamFiles = fs.readdirSync(teamsDir).sort()
    .map((entry) => path.join(teamsDir, entry, 'config.json'))
    .filter((file) => fs.existsSync(file));
} catch (_) {
  fail('roster_unreadable');
}

let registrationCount = 0;
for (const file of teamFiles) {
  const config = readJson(file, 'roster_unreadable');
  if (!config || typeof config !== 'object' || typeof config.name !== 'string') fail('roster_schema_invalid');
  if (config.name !== team) continue;
  if (!config.agents || typeof config.agents !== 'object' || Array.isArray(config.agents)) {
    fail('roster_schema_invalid');
  }
  const record = config.agents[agent];
  if (!record || typeof record !== 'object' || Array.isArray(record)) continue;
  const registrations = Array.isArray(record.registrations)
    ? record.registrations
    : (record.type !== undefined || record.project !== undefined ? [record] : []);
  for (const registration of registrations) {
    if (!registration || typeof registration !== 'object' || Array.isArray(registration)) fail('roster_schema_invalid');
    if (registration.type !== type || typeof registration.project !== 'string') continue;
    if (canonicalPath(registration.project, 'roster_project_unreadable') === binding.project) {
      registrationCount += 1;
    }
  }
}
if (registrationCount !== 1) fail(registrationCount === 0 ? 'roster_no_match' : 'roster_multiple_match');

const claimFile = path.join(
  process.env.SKILL_DIR || path.join(__dirname, '..'),
  'run',
  `actas.${encodeLockPart(team)}__${encodeLockPart(agent)}.session`,
);
const claim = readJson(claimFile, 'claim_unreadable');
if (!claim || claim.schemaVersion !== 1) fail('claim_schema_invalid');
for (const field of ['team', 'agent', 'project', 'sessionId', 'generation', 'pid', 'pidStart']) {
  text(claim[field], `claim_${field}`);
}
if (claim.team !== team || claim.agent !== agent || claim.sessionId !== input.sessionId ||
    claim.generation !== binding.generation || claim.pid !== binding.pid ||
    claim.pidStart !== binding.pidStart ||
    canonicalPath(claim.project, 'claim_project_unreadable') !== binding.project) {
  fail('claim_mismatch');
}

process.stdout.write(JSON.stringify({
  status: 'ok',
  team,
  agent,
  type,
  project: binding.project,
  sessionId: binding.sessionId,
  generation: binding.generation,
  pid: binding.pid,
  pidStart: binding.pidStart,
  profileDigest: binding.profileDigest,
  guardDigest: binding.guardDigest,
  brokerDigest: binding.brokerDigest,
  policyVersion: binding.policyVersion,
}) + '\n');
