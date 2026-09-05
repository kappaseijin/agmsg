#!/usr/bin/env node

'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const fileFrom = (name) => process.env[name] || '';
const readLines = (file, reason, alerts) => {
  if (!file || !fs.existsSync(file)) {
    alerts.push(reason);
    return [];
  }
  const rows = [];
  try {
    for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/u)) {
      if (!line) continue;
      try { rows.push(JSON.parse(line)); } catch (_) { alerts.push(`${reason}_malformed`); }
    }
  } catch (_) {
    alerts.push(reason);
  }
  return rows;
};

const alerts = [];
const guard = fileFrom('AGMSG_PM_GUARD_PATH');
if (!guard || !fs.existsSync(guard) || !fs.statSync(guard).isFile() || (process.platform !== 'win32' && !(fs.statSync(guard).mode & 0o111))) {
  alerts.push('guard_unavailable');
}
let guardDigest = '';
try { guardDigest = `sha256:${crypto.createHash('sha256').update(fs.readFileSync(guard)).digest('hex')}`; } catch (_) {}
if (process.env.AGMSG_PM_GUARD_DIGEST && process.env.AGMSG_PM_GUARD_DIGEST !== guardDigest) alerts.push('guard_digest_mismatch');

const bindingFile = fileFrom('AGMSG_PM_BINDING_FILE');
let binding = null;
try { binding = JSON.parse(fs.readFileSync(bindingFile, 'utf8')); } catch (_) { alerts.push('binding_unavailable'); }
const decisions = readLines(fileFrom('AGMSG_PM_DECISIONS_FILE'), 'decision_log_unavailable', alerts);
const executions = readLines(fileFrom('AGMSG_PM_EXECUTIONS_FILE'), 'execution_log_unavailable', alerts);
const decisionById = new Map();
for (const decision of decisions) {
  if (!decision || typeof decision.toolUseId !== 'string' || typeof decision.decision !== 'string') {
    alerts.push('decision_schema_invalid');
    continue;
  }
  decisionById.set(decision.toolUseId, decision);
}
for (const execution of executions) {
  if (!execution || typeof execution.toolUseId !== 'string') {
    alerts.push('execution_schema_invalid');
    continue;
  }
  const decision = decisionById.get(execution.toolUseId);
  if (!decision) alerts.push('execution_without_decision');
  else if (decision.decision === 'deny') alerts.push('denied_tool_executed');
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
  policyVersion: 'pm-pretool-v1',
};
try {
  for (const file of [heartbeatFile, auditFile]) {
    if (!file) throw new Error('audit_output_unconfigured');
    fs.mkdirSync(path.dirname(file), {recursive: true});
    const temporary = `${file}.${process.pid}.${crypto.randomUUID()}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(report) + '\n');
    fs.renameSync(temporary, file);
  }
} catch (error) {
  report.status = 'alert';
  report.alerts.push(error.message || 'audit_output_failed');
}
process.stdout.write(JSON.stringify(report) + '\n');
process.exitCode = report.alerts.length === 0 ? 0 : 1;
