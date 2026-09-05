#!/usr/bin/env node

'use strict';

const fs = require('fs');

const args = process.argv.slice(2);
const value = (name) => {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : '';
};
const heartbeat = value('--heartbeat');
const nowRaw = value('--now');
const staleRaw = value('--stale-seconds');
if (!heartbeat || !/^[0-9]+$/u.test(nowRaw) || !/^[0-9]+$/u.test(staleRaw)) process.exit(2);
let valueJson;
try { valueJson = JSON.parse(fs.readFileSync(heartbeat, 'utf8')); } catch (_) {
  process.stdout.write(JSON.stringify({status: 'alert', reason: 'heartbeat_unavailable'}) + '\n');
  process.exit(1);
}
const observed = Number(valueJson.observedAtEpoch);
const now = Number(nowRaw);
const stale = Number(staleRaw);
if (!Number.isSafeInteger(observed) || observed > now || now - observed > stale) {
  process.stdout.write(JSON.stringify({status: 'alert', reason: 'heartbeat_stale'}) + '\n');
  process.exit(1);
}
process.stdout.write(JSON.stringify({status: 'healthy', ageSeconds: now - observed}) + '\n');
