'use strict';

const fs = require('fs');
const path = require('path');

// Keep this byte encoding in lock-step with scripts/lib/actas-lock.sh.
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

const claimPath = (skillDir, team, agent) => path.join(
  skillDir,
  'run',
  `actas.${encodeLockPart(team)}__${encodeLockPart(agent)}.session`,
);

const sameClaimPath = (actual, expected) => {
  if (typeof actual !== 'string' || typeof expected !== 'string' ||
      !path.isAbsolute(actual) || !path.isAbsolute(expected)) return false;
  return path.resolve(actual) === path.resolve(expected);
};

const expectedOwner = (sessionId, pid) => `${sessionId}.${pid}`;

// The actas writer deliberately stores only one owner token and one LF. Do
// not parse this as JSON or accept a fallback representation: callers need to
// distinguish a valid claim from every malformed or unreadable state.
const readClaim = (file, owner) => {
  if (typeof file !== 'string' || file.length === 0 || typeof owner !== 'string' || owner.length === 0) {
    return {ok: false, reason: 'claim_path_invalid'};
  }

  let raw;
  try {
    raw = fs.readFileSync(file);
  } catch (_) {
    return {ok: false, reason: 'claim_unreadable'};
  }

  const text = raw.toString('utf8');
  if (Buffer.from(text, 'utf8').compare(raw) !== 0) {
    return {ok: false, reason: 'claim_format_invalid'};
  }
  if (!text.endsWith('\n')) {
    return {ok: false, reason: 'claim_format_invalid'};
  }
  const value = text.slice(0, -1);
  if (value.length === 0 || value.includes('\n') || /[\u0000-\u001f\u007f]/u.test(value)) {
    return {ok: false, reason: 'claim_format_invalid'};
  }
  if (value !== owner) {
    return {ok: false, reason: 'claim_mismatch'};
  }
  return {ok: true, owner: value};
};

module.exports = {
  claimPath,
  encodeLockPart,
  expectedOwner,
  readClaim,
  sameClaimPath,
};
