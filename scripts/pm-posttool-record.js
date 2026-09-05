#!/usr/bin/env node

'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const file = process.env.AGMSG_PM_EXECUTIONS_FILE;
if (typeof file !== 'string' || file.length === 0) process.exit(1);
let input;
try {
  input = JSON.parse(fs.readFileSync(0, 'utf8'));
} catch (_) {
  process.exit(1);
}
if (!input || typeof input !== 'object' || typeof input.tool_use_id !== 'string' || typeof input.tool_name !== 'string') {
  process.exit(1);
}
const toolInput = input.tool_input && typeof input.tool_input === 'object' ? input.tool_input : {};
const record = {
  schemaVersion: 1,
  observedAt: new Date().toISOString(),
  sessionId: typeof input.session_id === 'string' ? input.session_id : '',
  toolUseId: input.tool_use_id,
  tool: input.tool_name,
  inputDigest: `sha256:${crypto.createHash('sha256').update(JSON.stringify(toolInput)).digest('hex')}`,
};
try {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.appendFileSync(file, JSON.stringify(record) + '\n', {encoding: 'utf8', flag: 'a'});
} catch (_) {
  process.exit(1);
}
