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
const {spawnSync} = require('child_process');

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
for (const field of ['team', 'agent', 'type', 'project', 'sessionId', 'generation', 'pid', 'pidStart', 'profileDigest', 'policyVersion']) {
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
if (!Number.isSafeInteger(pid) || pid <= 0) fail('binding_pid_invalid');
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
let claim;
try {
  const raw = fs.readFileSync(claimFile, 'utf8').replace(/\n$/u, '');
  if (!raw || /\r?\n/u.test(raw)) fail('claim_schema_invalid');
  claim = raw;
} catch (_) {
  fail('claim_unreadable');
}
if (claim !== `${input.sessionId}.${processPid}` || claim !== `${binding.sessionId}.${binding.pid}`) {
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
  profileDigest: binding.profileDigest,
  policyVersion: binding.policyVersion,
}) + '\n');
