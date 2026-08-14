#!/usr/bin/env node
"use strict";

const crypto = require("crypto");
const childProcess = require("child_process");
const fs = require("fs");

const WORK_KINDS = new Set([
  "implementation",
  "writeback",
  "inventory",
  "closeout",
  "reconciliation",
]);
const REFERENCE_KINDS = new Set(["issue", "pull_request", "commit", "evidence"]);
const SHA256 = /^sha256:[0-9a-f]{64}$/;
const READ_ONLY_COMMANDS = new Set(["validate", "self-check"]);
const MUTATION_COMMANDS = new Set([
  "claim",
  "ack",
  "renew",
  "release",
  "set-state",
  "link-pr",
  "writeback",
]);
const MUTABLE_STATES = new Set(["acknowledged", "in_progress", "blocked", "completed"]);
const NON_NEGATIVE_INTEGER = /^(0|[1-9][0-9]*)$/;
const POSITIVE_INTEGER = /^[1-9][0-9]*$/;

class SchemaError extends Error {}

function schemaError(message) {
  throw new SchemaError(message);
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isNonEmptyString(value) {
  return typeof value === "string" && value.length > 0;
}

function isPositiveInteger(value) {
  return Number.isInteger(value) && value > 0;
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (isObject(value)) {
    const result = {};
    for (const key of Object.keys(value).sort()) result[key] = canonicalize(value[key]);
    return result;
  }
  return value;
}

function canonicalJson(value) {
  return JSON.stringify(canonicalize(value));
}

function sha256Digest(value) {
  return `sha256:${crypto.createHash("sha256").update(canonicalJson(value), "utf8").digest("hex")}`;
}

function envelopeDigest(envelope) {
  const digestInput = Object.assign({}, envelope);
  delete digestInput.envelopeDigest;
  return sha256Digest(digestInput);
}

function requireObject(value, message) {
  if (!isObject(value)) schemaError(message);
  return value;
}

function requireReference(reference, messagePrefix) {
  requireObject(reference, `${messagePrefix} must be an object`);
  if (!REFERENCE_KINDS.has(reference.kind)) {
    schemaError(`${messagePrefix}.kind must be issue, pull_request, commit, or evidence`);
  }

  switch (reference.kind) {
    case "issue":
    case "pull_request":
      if (!isNonEmptyString(reference.repository)) schemaError(`${messagePrefix}.repository must be a non-empty string`);
      if (!isPositiveInteger(reference.number)) schemaError(`${messagePrefix}.number must be a positive integer`);
      break;
    case "commit":
      if (!isNonEmptyString(reference.repository)) schemaError(`${messagePrefix}.repository must be a non-empty string`);
      if (!isNonEmptyString(reference.sha)) schemaError(`${messagePrefix}.sha must be a non-empty string`);
      break;
    case "evidence":
      if (!isNonEmptyString(reference.url) && !SHA256.test(reference.sha256 || "")) {
        schemaError(`${messagePrefix} must contain a non-empty url or sha256 digest`);
      }
      break;
  }
}

function rosterSeats(roster, requestedTeam) {
  requireObject(roster, "roster contract must be an object");
  if (roster.schemaVersion !== 1) schemaError("roster contract schemaVersion must be integer 1");
  if (roster.team !== requestedTeam) schemaError("roster contract team does not match requested team");
  if (!Array.isArray(roster.members)) schemaError("roster contract members must be an array");

  const members = new Map();
  for (const member of roster.members) {
    requireObject(member, "roster contract member must be an object");
    if (!isNonEmptyString(member.name)) schemaError("roster contract member name must be a non-empty string");
    members.set(member.name, member);
  }
  return members;
}

function validateWorkItem(item, seats, ids) {
  requireObject(item, "work item must be an object");
  if (item.schemaVersion !== 1) schemaError("work item schemaVersion must be integer 1");

  const workItem = requireObject(item.workItem, "workItem must be an object");
  if (!isNonEmptyString(workItem.id)) schemaError("workItem.id must be a non-empty string");
  if (ids.has(workItem.id)) schemaError(`workItem.id must be unique: ${workItem.id}`);
  ids.add(workItem.id);

  const source = requireObject(workItem.source, "workItem.source must be an object");
  if (source.kind !== "issue") schemaError("workItem.source.kind must be issue");
  if (!isNonEmptyString(source.repository)) schemaError("workItem.source.repository must be a non-empty string");
  if (!isPositiveInteger(source.number)) schemaError("workItem.source.number must be a positive integer");

  if (!isNonEmptyString(item.ownerSeat)) schemaError("ownerSeat must be a non-empty string");
  const owner = seats.get(item.ownerSeat);
  if (!owner) schemaError(`owner seat does not exist: ${item.ownerSeat}`);
  if (owner.kind !== "seat") schemaError(`owner must be a seat: ${item.ownerSeat}`);

  if (!Array.isArray(item.workKinds) || item.workKinds.length === 0) {
    schemaError("workKinds must be a non-empty array");
  }
  const workKinds = new Set();
  for (const workKind of item.workKinds) {
    if (!WORK_KINDS.has(workKind)) schemaError(`unknown work kind: ${String(workKind)}`);
    if (workKinds.has(workKind)) schemaError("workKinds must be unique");
    workKinds.add(workKind);
  }

  if (!Array.isArray(item.relations)) schemaError("relations must be an array");
  for (const relation of item.relations) {
    requireObject(relation, "relation must be an object");
    if (relation.kind !== "pull_request") schemaError("relation.kind must be pull_request");
    if (!isNonEmptyString(relation.repository)) schemaError("relation.repository must be a non-empty string");
    if (!isPositiveInteger(relation.number)) schemaError("relation.number must be a positive integer");
    if (relation.relation !== "contributes" && relation.relation !== "closes") {
      schemaError("relation.relation must be contributes or closes");
    }
    if (relation.relation === "closes") {
      const closingIssue = relation.closingIssue;
      if (!isObject(closingIssue)) schemaError("closes relation requires closingIssue");
      if (closingIssue.repository !== source.repository || closingIssue.number !== source.number) {
        schemaError("closes relation closingIssue must match workItem.source");
      }
    }
  }

  if (!isPositiveInteger(item.revision)) schemaError("revision must be a positive integer");

  const basis = requireObject(item.classificationBasis, "classificationBasis must be an object");
  if (!SHA256.test(basis.contentDigest || "")) {
    schemaError("classificationBasis.contentDigest must be sha256:<64 lowercase hex>");
  }
  if (!Array.isArray(basis.refs) || basis.refs.length === 0) {
    schemaError("classificationBasis.refs must be a non-empty array");
  }
  basis.refs.forEach((reference, index) => requireReference(reference, `classificationBasis.refs[${index}]`));

  if (typeof item.writebackRequired !== "boolean") schemaError("writebackRequired must be boolean");

  if (Object.prototype.hasOwnProperty.call(item, "envelopeDigest") && item.envelopeDigest !== envelopeDigest(item)) {
    schemaError("envelopeDigest does not match canonical envelope");
  }
}

function validateContractPack(pack, roster, requestedTeam) {
  requireObject(pack, "contract pack must be an object");
  if (pack.schemaVersion !== 1) schemaError("schemaVersion must be integer 1");
  if (pack.team !== requestedTeam) schemaError("contract pack team does not match requested team");
  if (!Array.isArray(pack.workItems) || pack.workItems.length === 0) {
    schemaError("workItems must be a non-empty array");
  }

  const seats = rosterSeats(roster, requestedTeam);
  const ids = new Set();
  pack.workItems.forEach((item) => validateWorkItem(item, seats, ids));
  return seats;
}

function emit(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

function parseJson(text, message) {
  try {
    return JSON.parse(text);
  } catch (_) {
    schemaError(message);
  }
}

function requireArgumentCount(command, args, counts) {
  if (!counts.includes(args.length)) {
    schemaError(`invalid arguments for ${command}`);
  }
}

function requireCliString(value, name) {
  if (!isNonEmptyString(value)) schemaError(`${name} must be a non-empty string`);
  return value;
}

function parseNonNegativeInteger(value, name, defaultValue) {
  const raw = value === undefined ? String(defaultValue) : value;
  if (typeof raw !== "string" || !NON_NEGATIVE_INTEGER.test(raw)) {
    schemaError(`${name} must be a non-negative integer`);
  }
  const parsed = Number(raw);
  if (!Number.isSafeInteger(parsed) || parsed > 2147483647) {
    schemaError(`${name} must be a non-negative integer`);
  }
  return parsed;
}

function parsePositiveIntegerArgument(value, name) {
  if (typeof value !== "string" || !POSITIVE_INTEGER.test(value)) {
    schemaError(`${name} must be a positive integer`);
  }
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed > 2147483647) {
    schemaError(`${name} must be a positive integer`);
  }
  return parsed;
}

function parseMutationArguments(command, args) {
  switch (command) {
    case "claim":
    case "renew": {
      requireArgumentCount(command, args, [2, 3]);
      return {
        workItemId: requireCliString(args[0], "work-item-id"),
        actor: requireCliString(args[1], "actor-seat"),
        ttlSeconds: parseNonNegativeInteger(args[2], "ttl-seconds", 300),
      };
    }
    case "ack":
      requireArgumentCount(command, args, [2, 3]);
      return {
        workItemId: requireCliString(args[0], "work-item-id"),
        actor: requireCliString(args[1], "actor-seat"),
        evidence: args[2] === undefined ? "owner_ack" : requireCliString(args[2], "evidence"),
      };
    case "release":
      requireArgumentCount(command, args, [2]);
      return {
        workItemId: requireCliString(args[0], "work-item-id"),
        actor: requireCliString(args[1], "actor-seat"),
      };
    case "set-state": {
      requireArgumentCount(command, args, [3]);
      const state = requireCliString(args[2], "state");
      if (!MUTABLE_STATES.has(state)) schemaError(`unsupported state: ${state}`);
      return {
        workItemId: requireCliString(args[0], "work-item-id"),
        actor: requireCliString(args[1], "actor-seat"),
        state,
      };
    }
    case "link-pr": {
      requireArgumentCount(command, args, [5]);
      const relation = requireCliString(args[4], "relation");
      if (relation !== "contributes" && relation !== "closes") {
        schemaError("relation must be contributes or closes");
      }
      return {
        workItemId: requireCliString(args[0], "work-item-id"),
        actor: requireCliString(args[1], "actor-seat"),
        repository: requireCliString(args[2], "repository"),
        number: parsePositiveIntegerArgument(args[3], "PR number"),
        relation,
      };
    }
    case "writeback":
      requireArgumentCount(command, args, [3]);
      return {
        workItemId: requireCliString(args[0], "work-item-id"),
        actor: requireCliString(args[1], "actor-seat"),
        evidence: requireCliString(args[2], "evidence"),
      };
    default:
      throw new Error(`unsupported mutation command: ${command}`);
  }
}

function findWorkItem(pack, workItemId) {
  const item = pack.workItems.find((candidate) => candidate.workItem.id === workItemId);
  if (!item) schemaError(`work item does not exist: ${workItemId}`);
  return item;
}

function requireSeatActor(seats, actor) {
  const member = seats.get(actor);
  if (!member) schemaError(`actor seat does not exist: ${actor}`);
  if (member.kind !== "seat") schemaError(`actor must be a seat: ${actor}`);
  return {
    member,
    isManager: member.role === "manager",
  };
}

function requireClaimAuthority(input, item, actorInfo) {
  if (input.actor !== item.ownerSeat && !actorInfo.isManager) {
    schemaError(`actor is not the declared owner or manager: ${input.actor}`);
  }
}

function requireStateAuthority(input, item, actorInfo) {
  if (input.actor !== item.ownerSeat && !actorInfo.isManager) {
    schemaError(`actor is not the declared owner or manager: ${input.actor}`);
  }
}

function sqlLiteral(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

function currentResultSql(team, workItemId) {
  return `
SELECT json_object(
  'schemaVersion', 1,
  'team', team,
  'workItemId', work_item_id,
  'revision', revision,
  'state', state,
  'leaseOwner', lease_owner,
  'leaseExpiresAt', lease_expires_at,
  'envelopeDigest', envelope_digest,
  'lastAction', last_action
)
FROM team_work_current
WHERE team = ${sqlLiteral(team)} AND work_item_id = ${sqlLiteral(workItemId)};
`;
}

function runSqliteMutation(dbPath, mutationSql, resultSql) {
  const script = [
    ".bail on",
    ".timeout 5000",
    "BEGIN IMMEDIATE;",
    mutationSql,
    "SELECT changes();",
    resultSql,
    "COMMIT;",
  ].join("\n");
  const result = childProcess.spawnSync("sqlite3", [dbPath], {
    encoding: "utf8",
    input: script,
    maxBuffer: 1024 * 1024,
  });
  if (result.error) {
    if (result.error.code === "ENOENT") throw new Error("team-work requires sqlite3 on PATH");
    throw result.error;
  }
  if (result.status !== 0) {
    const detail = String(result.stderr || "").trim() || "sqlite3 mutation failed";
    throw new Error(detail);
  }

  const lines = String(result.stdout || "")
    .replace(/\r/g, "")
    .trim()
    .split("\n")
    .filter((line) => line.length > 0);
  const changes = Number(lines[0]);
  if (changes !== 1) schemaError("mutation rejected");
  if (lines.length < 2) throw new Error("sqlite3 mutation did not return current state");
  return parseJson(lines[1], "sqlite3 mutation returned invalid JSON");
}

function buildMutationSql(context, input) {
  const now = "CAST(strftime('%s', 'now') AS INTEGER)";
  const team = sqlLiteral(context.team);
  const workItemId = sqlLiteral(input.workItemId);
  const actor = sqlLiteral(input.actor);
  const contractDigest = sqlLiteral(context.contractDigest);
  const itemDigest = sqlLiteral(context.itemDigest);
  const activeHolder = `lease_owner = ${actor} AND lease_expires_at > ${now}`;
  const stateAuthority = context.isManager ? "1 = 1" : activeHolder;
  const currentMatch = `
team = ${team}
  AND work_item_id = ${workItemId}
  AND contract_digest = ${contractDigest}
  AND envelope_digest = ${itemDigest}`;

  switch (context.command) {
    case "claim": {
      const source = context.item.workItem.source;
      const leaseExpiresAt = `(${now} + ${input.ttlSeconds})`;
      return `
INSERT INTO team_work_current(
  team, work_item_id, contract_digest, envelope_digest, owner_seat,
  source_repository, source_number, revision, state, lease_owner,
  lease_expires_at, ack_evidence, pr_links_json, writebacks_json,
  last_action, last_actor, created_at, updated_at
) VALUES (
  ${team}, ${workItemId}, ${contractDigest}, ${itemDigest}, ${sqlLiteral(context.item.ownerSeat)},
  ${sqlLiteral(source.repository)}, ${source.number}, ${context.item.revision}, 'claimed', ${actor},
  ${leaseExpiresAt}, NULL, '[]', '[]', 'claim', ${actor}, ${now}, ${now}
)
ON CONFLICT(team, work_item_id) DO UPDATE SET
  revision = revision + 1,
  state = 'claimed',
  lease_owner = ${actor},
  lease_expires_at = ${leaseExpiresAt},
  ack_evidence = NULL,
  last_action = 'claim',
  last_actor = ${actor},
  updated_at = ${now}
WHERE contract_digest = ${contractDigest}
  AND envelope_digest = ${itemDigest}
  AND (lease_expires_at IS NULL OR lease_expires_at <= ${now});
`;
    }
    case "ack":
      return `
UPDATE team_work_current SET
  revision = revision + 1,
  state = 'acknowledged',
  ack_evidence = ${sqlLiteral(input.evidence)},
  last_action = 'ack',
  last_actor = ${actor},
  updated_at = ${now}
WHERE ${currentMatch}
  AND ${activeHolder};
`;
    case "renew":
      return `
UPDATE team_work_current SET
  revision = revision + 1,
  lease_expires_at = (${now} + ${input.ttlSeconds}),
  last_action = 'renew',
  last_actor = ${actor},
  updated_at = ${now}
WHERE ${currentMatch}
  AND ${activeHolder};
`;
    case "release":
      return `
UPDATE team_work_current SET
  revision = revision + 1,
  lease_owner = NULL,
  lease_expires_at = NULL,
  last_action = 'release',
  last_actor = ${actor},
  updated_at = ${now}
WHERE ${currentMatch}
  AND ${activeHolder};
`;
    case "set-state":
      return `
UPDATE team_work_current SET
  revision = revision + 1,
  state = ${sqlLiteral(input.state)},
  last_action = 'set-state',
  last_actor = ${actor},
  updated_at = ${now}
WHERE ${currentMatch}
  AND (${stateAuthority});
`;
    case "link-pr":
      return `
UPDATE team_work_current SET
  revision = revision + 1,
  pr_links_json = json_insert(
    pr_links_json,
    '$[#]',
    json_object('repository', ${sqlLiteral(input.repository)}, 'number', ${input.number}, 'relation', ${sqlLiteral(input.relation)})
  ),
  last_action = 'link-pr',
  last_actor = ${actor},
  updated_at = ${now}
WHERE ${currentMatch}
  AND (${stateAuthority})
  AND NOT EXISTS (
    SELECT 1 FROM json_each(pr_links_json) AS link
    WHERE json_extract(link.value, '$.repository') = ${sqlLiteral(input.repository)}
      AND json_extract(link.value, '$.number') = ${input.number}
      AND json_extract(link.value, '$.relation') = ${sqlLiteral(input.relation)}
  );
`;
    case "writeback":
      return `
UPDATE team_work_current SET
  revision = revision + 1,
  writebacks_json = json_insert(
    writebacks_json,
    '$[#]',
    json_object('evidence', ${sqlLiteral(input.evidence)}, 'recordedAt', ${now})
  ),
  last_action = 'writeback',
  last_actor = ${actor},
  updated_at = ${now}
WHERE ${currentMatch}
  AND (${stateAuthority});
`;
    default:
      throw new Error(`unsupported mutation command: ${context.command}`);
  }
}

function executeMutation(command, team, pack, seats, args) {
  const input = parseMutationArguments(command, args);
  const item = findWorkItem(pack, input.workItemId);
  const actorInfo = requireSeatActor(seats, input.actor);

  if (command === "claim") requireClaimAuthority(input, item, actorInfo);
  if (command === "set-state" || command === "link-pr" || command === "writeback") {
    requireStateAuthority(input, item, actorInfo);
  }
  if (command === "link-pr" && input.relation === "closes" && input.repository !== item.workItem.source.repository) {
    schemaError("closes relation repository must match workItem.source");
  }

  const dbPath = process.env.AGMSG_TEAM_WORK_DB;
  if (!isNonEmptyString(dbPath)) throw new Error("team-work mutation database is unavailable");
  const context = {
    command,
    team,
    item,
    itemDigest: envelopeDigest(item),
    contractDigest: sha256Digest(pack),
    isManager: actorInfo.isManager,
  };
  const mutationSql = buildMutationSql(context, input);
  return runSqliteMutation(dbPath, mutationSql, currentResultSql(team, input.workItemId));
}

function main() {
  const [command, team, packPath, ...args] = process.argv.slice(2);
  if (!READ_ONLY_COMMANDS.has(command) && !MUTATION_COMMANDS.has(command)) {
    process.stderr.write(`Error: unknown team-work command: ${command || ""}\n`);
    process.exitCode = 1;
    return;
  }

  let packText;
  try {
    packText = fs.readFileSync(packPath, "utf8");
  } catch (_) {
    schemaError(`cannot read contract pack: ${packPath}`);
  }

  const pack = parseJson(packText, "contract pack is not valid JSON");
  const roster = parseJson(fs.readFileSync(0, "utf8"), "roster contract is not valid JSON");
  const seats = validateContractPack(pack, roster, team);

  const validation = {
    schemaVersion: 1,
    valid: true,
    team,
    workItemCount: pack.workItems.length,
  };
  if (command === "validate") {
    emit(validation);
    return;
  }

  if (command === "self-check") {
    emit(Object.assign(validation, {
      contractDigest: sha256Digest(pack),
      items: pack.workItems.map((item) => {
        const canonicalEnvelope = Object.assign({}, item);
        delete canonicalEnvelope.envelopeDigest;
        return {
          id: item.workItem.id,
          envelopeDigest: envelopeDigest(item),
          canonicalJson: canonicalJson(canonicalEnvelope),
        };
      }),
    }));
    return;
  }

  emit(executeMutation(command, team, pack, seats, args));
}

module.exports = {
  SchemaError,
  canonicalJson,
  sha256Digest,
  envelopeDigest,
  validateContractPack,
};

if (require.main === module) {
  try {
    main();
  } catch (error) {
    if (error instanceof SchemaError) {
      process.stderr.write(`schema error: ${error.message}\n`);
      process.exitCode = 2;
    } else {
      process.stderr.write(`Error: ${error.message}\n`);
      process.exitCode = 1;
    }
  }
}
