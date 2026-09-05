#!/usr/bin/env node

/* Structured, data-only PM broker boundary used by the isolated CLI probe. */

'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const operations = new Set([
  'agmsg_send', 'agmsg_receive', 'agmsg_delegate', 'monitor', 'actas', 'drop',
  'rule_load', 'rule_record', 'git_maintenance', 'sync_origin_clone',
  'proxy_git_write', 'team_provision', 'bot_collaborator', 'apply_decision',
  'stale_runtime_cleanup', 'issue_view', 'issue_comment', 'pr_view',
  'pr_comment', 'seat_start', 'seat_stop',
]);

const die = (reason) => {
  process.stderr.write(JSON.stringify({status: 'denied', reason}) + '\n');
  process.exit(1);
};

const [operation, encoded] = process.argv.slice(2);
if (!operations.has(operation)) die('operation_not_allowed');
if (typeof encoded !== 'string' || !/^[A-Za-z0-9_-]+$/u.test(encoded)) die('payload_encoding_invalid');

let payload;
try {
  payload = JSON.parse(Buffer.from(encoded, 'base64url').toString('utf8'));
} catch (_) {
  die('payload_invalid');
}
if (!payload || typeof payload !== 'object' || Array.isArray(payload) || payload.operation !== operation) {
  die('payload_schema_invalid');
}
if (operation.startsWith('agmsg_') && typeof payload.recipient !== 'string') die('recipient_required');

const root = process.env.AGMSG_PM_BROKER_ROOT || '';
if (!path.isAbsolute(root)) die('broker_root_invalid');
try {
  fs.mkdirSync(root, {recursive: true});
  const payloadDigest = `sha256:${crypto.createHash('sha256').update(encoded).digest('hex')}`;
  const request = {
    schemaVersion: 1,
    policyVersion: 'pm-pretool-v1',
    operation,
    requestId: typeof payload.requestId === 'string' ? payload.requestId : payloadDigest,
    payloadDigest,
    pid: process.pid,
    recordedAt: new Date().toISOString(),
  };
  fs.appendFileSync(path.join(root, 'requests.jsonl'), JSON.stringify(request) + '\n', {encoding: 'utf8', flag: 'a'});
  fs.writeFileSync(path.join(root, 'executed'), '1\n');
} catch (_) {
  die('broker_record_failed');
}
process.stdout.write(JSON.stringify({status: 'accepted', operation}) + '\n');
