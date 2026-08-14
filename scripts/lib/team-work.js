#!/usr/bin/env node
"use strict";

const crypto = require("crypto");
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

function main() {
  const [command, team, packPath] = process.argv.slice(2);
  if (command !== "validate" && command !== "self-check") {
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
  validateContractPack(pack, roster, team);

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
}

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
