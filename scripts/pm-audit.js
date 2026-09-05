#!/usr/bin/env node

'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const {
  claimPath,
  expectedOwner,
  readClaim,
  sameClaimPath,
} = require('./lib/pm-claim');

const POLICY_VERSION = 'pm-pretool-v1';
const HASH = /^sha256:[0-9a-f]{64}$/u;
const ALLOWED_TOOLS = new Set(['Bash', 'Monitor', 'AskUserQuestion', 'EnterPlanMode', 'ExitPlanMode']);
const NATIVE_TOOLS = new Set(['Monitor', 'AskUserQuestion', 'EnterPlanMode', 'ExitPlanMode']);

const fileFrom = (name) => process.env[name] || '';
const isObject = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const isText = (value) => typeof value === 'string' && value.length > 0 && !/[\u0000-\u001f\u007f]/u.test(value);
const isDigest = (value) => typeof value === 'string' && HASH.test(value);
const addAlert = (alerts, value) => alerts.push(value);

const readJson = (file, reason, alerts) => {
  if (!file || !fs.existsSync(file)) {
    addAlert(alerts, reason);
    return null;
  }
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (_) {
    addAlert(alerts, reason);
    return null;
  }
};

const readLines = (file, reason, alerts) => {
  if (!file || !fs.existsSync(file)) {
    addAlert(alerts, reason);
    return [];
  }
  const rows = [];
  try {
    for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/u)) {
      if (!line) continue;
      try { rows.push(JSON.parse(line)); } catch (_) { addAlert(alerts, `${reason}_malformed`); }
    }
  } catch (_) {
    addAlert(alerts, reason);
  }
  return rows;
};

const alerts = [];
const guard = fileFrom('AGMSG_PM_GUARD_PATH');
let guardDigest = '';
try {
  const stat = fs.statSync(guard);
  if (!stat.isFile() || (process.platform !== 'win32' && !(stat.mode & 0o111))) addAlert(alerts, 'guard_unavailable');
  guardDigest = `sha256:${crypto.createHash('sha256').update(fs.readFileSync(guard)).digest('hex')}`;
} catch (_) {
  addAlert(alerts, 'guard_unavailable');
}
const expectedGuardDigest = process.env.AGMSG_PM_GUARD_DIGEST || '';
if (!isDigest(expectedGuardDigest)) addAlert(alerts, 'guard_digest_unconfigured');
else if (expectedGuardDigest !== guardDigest) addAlert(alerts, 'guard_digest_mismatch');

const bindingFile = fileFrom('AGMSG_PM_BINDING_FILE');
const binding = readJson(bindingFile, 'binding_unavailable', alerts);
let bindingValid = true;
if (!isObject(binding) || binding.schemaVersion !== 1) {
  addAlert(alerts, 'binding_schema_invalid');
  bindingValid = false;
} else {
  for (const field of [
    'team', 'agent', 'type', 'project', 'sessionId', 'generation', 'pid', 'pidStart',
    'profileDigest', 'policyVersion', 'guardDigest', 'brokerDigest',
  ]) {
    if (!isText(binding[field])) {
      addAlert(alerts, `binding_${field}_invalid`);
      bindingValid = false;
    }
  }
  if (binding.policyVersion !== POLICY_VERSION) { addAlert(alerts, 'binding_policy_invalid'); bindingValid = false; }
  if (!isDigest(binding.profileDigest) || !isDigest(binding.guardDigest) || !isDigest(binding.brokerDigest)) {
    addAlert(alerts, 'binding_digest_invalid');
    bindingValid = false;
  }
  if (!/^[1-9][0-9]*$/u.test(binding.pid || '')) { addAlert(alerts, 'binding_pid_invalid'); bindingValid = false; }
  if (!path.isAbsolute(binding.project || '')) { addAlert(alerts, 'binding_project_invalid'); bindingValid = false; }
  else {
    try {
      if (fs.realpathSync.native(binding.project) !== binding.project) {
        addAlert(alerts, 'binding_project_noncanonical');
        bindingValid = false;
      }
    } catch (_) {
      addAlert(alerts, 'binding_project_unreadable');
      bindingValid = false;
    }
  }
}

if (bindingValid) {
  const skillDir = process.env.SKILL_DIR || path.join(__dirname, '..');
  const derivedClaimFile = claimPath(skillDir, binding.team, binding.agent);
  const configuredClaimFile = fileFrom('AGMSG_PM_CLAIM_FILE');
  if (!configuredClaimFile) {
    addAlert(alerts, 'claim_path_unavailable');
  } else if (!sameClaimPath(configuredClaimFile, derivedClaimFile)) {
    addAlert(alerts, 'claim_path_mismatch');
  } else {
    const result = readClaim(derivedClaimFile, expectedOwner(binding.sessionId, binding.pid));
    if (!result.ok) addAlert(alerts, result.reason);
  }
}

const decisions = readLines(fileFrom('AGMSG_PM_DECISIONS_FILE'), 'decision_log_unavailable', alerts);
const executions = readLines(fileFrom('AGMSG_PM_EXECUTIONS_FILE'), 'execution_log_unavailable', alerts);
const decisionById = new Map();
const decisionIds = new Set();
for (const decision of decisions) {
  if (!isObject(decision) || decision.schemaVersion !== 1 || !isText(decision.toolUseId) ||
      !isText(decision.tool) || !isText(decision.sessionId) || !isText(decision.generation) ||
      !isDigest(decision.inputDigest) || !['allow', 'deny', 'native'].includes(decision.decision) ||
      decision.policyVersion !== POLICY_VERSION || (decision.reason !== null && !isText(decision.reason))) {
    addAlert(alerts, 'decision_schema_invalid');
    continue;
  }
  if (decisionIds.has(decision.toolUseId)) addAlert(alerts, 'decision_duplicate_id');
  decisionIds.add(decision.toolUseId);
  if (bindingValid && (decision.sessionId !== binding.sessionId || decision.generation !== binding.generation)) {
    addAlert(alerts, 'decision_binding_mismatch');
  }
  decisionById.set(decision.toolUseId, decision);
}

const executionIds = new Set();
for (const execution of executions) {
  if (!isObject(execution) || execution.schemaVersion !== 1 || !isText(execution.toolUseId) ||
      !isText(execution.tool) || !isText(execution.sessionId) || !isText(execution.generation) ||
      !isDigest(execution.inputDigest) || execution.policyVersion !== POLICY_VERSION) {
    addAlert(alerts, 'execution_schema_invalid');
    continue;
  }
  if (!ALLOWED_TOOLS.has(execution.tool)) addAlert(alerts, 'execution_unknown_tool');
  if (executionIds.has(execution.toolUseId)) addAlert(alerts, 'execution_duplicate_id');
  executionIds.add(execution.toolUseId);
  if (bindingValid && (execution.sessionId !== binding.sessionId || execution.generation !== binding.generation)) {
    addAlert(alerts, 'execution_binding_mismatch');
  }
  const decision = decisionById.get(execution.toolUseId);
  if (!decision) {
    addAlert(alerts, 'execution_without_decision');
    continue;
  }
  if (decision.decision === 'deny') addAlert(alerts, 'denied_tool_executed');
  if (decision.tool !== execution.tool) addAlert(alerts, 'decision_execution_tool_mismatch');
  if (decision.inputDigest !== execution.inputDigest) addAlert(alerts, 'decision_execution_input_mismatch');
  if (decision.decision === 'native' && !NATIVE_TOOLS.has(execution.tool)) addAlert(alerts, 'native_tool_mismatch');
}

const now = Math.floor(Date.now() / 1000);
const heartbeatFile = fileFrom('AGMSG_PM_HEARTBEAT_FILE');
const auditFile = fileFrom('AGMSG_PM_AUDIT_FILE');
const report = {
  schemaVersion: 1,
  status: alerts.length === 0 ? 'healthy' : 'alert',
  observedAt: new Date().toISOString(),
  observedAtEpoch: now,
  alerts: [...new Set(alerts)],
  sessionId: binding && typeof binding.sessionId === 'string' ? binding.sessionId : '',
  generation: binding && typeof binding.generation === 'string' ? binding.generation : '',
  guardDigest,
  decisionCount: decisions.length,
  executionCount: executions.length,
  policyVersion: POLICY_VERSION,
};

const writeAtomic = (file, value) => {
  if (!file) throw new Error('audit_output_unconfigured');
  fs.mkdirSync(path.dirname(file), {recursive: true});
  const temporary = `${file}.${process.pid}.${crypto.randomUUID()}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(value) + '\n');
  fs.renameSync(temporary, file);
};

let auditCommitted = false;
try {
  // The audit is the source record. Heartbeat is committed only after it is
  // durable, so a failed audit cannot leave a fresh healthy heartbeat behind.
  writeAtomic(auditFile, report);
  auditCommitted = true;
  writeAtomic(heartbeatFile, report);
} catch (error) {
  report.status = 'alert';
  report.alerts = [...new Set([...report.alerts, error.message || 'audit_output_failed'])];
  try {
    if (auditCommitted || auditFile) writeAtomic(auditFile, report);
  } catch (_) {}
}
process.stdout.write(JSON.stringify(report) + '\n');
process.exitCode = report.alerts.length === 0 ? 0 : 1;
