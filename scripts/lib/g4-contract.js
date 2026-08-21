#!/usr/bin/env node
"use strict";

const {
  SchemaError,
  canonicalJson,
  sha256Digest,
} = require("./team-work");

const WORK_KINDS = new Set([
  "implementation",
  "writeback",
  "inventory",
  "closeout",
  "reconciliation",
]);
const SHA256 = /^sha256:[0-9a-f]{64}$/;
const REPOSITORY = /^[^/\s]+\/[^/\s]+$/;
const POSITIVE_INTEGER = /^[1-9][0-9]*$/;
const REASON_CODE = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/;
const RFC3339_JST = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+09:00$/;

const PACK_KEYS = new Set(["schemaVersion", "team", "scopes", "entries"]);
const SCOPE_KEYS = new Set(["id", "repository", "issueState", "labelsAll", "basis"]);
const ENTRY_KEYS = new Set([
  "schemaVersion",
  "source",
  "state",
  "ownerSeat",
  "workKinds",
  "basis",
  "blocker",
  "revision",
  "entryDigest",
]);
const SOURCE_KEYS = new Set(["repository", "number"]);
const BASIS_KEYS = new Set(["contentDigest", "refs"]);
const BLOCKER_KEYS = new Set(["reasonCode", "releasePredicate"]);
const PREDICATE_KEYS = {
  issue_closed: new Set(["kind", "repository", "number"]),
  pull_request_merged: new Set(["kind", "repository", "number"]),
  review_approved: new Set(["kind", "repository", "number", "headOid"]),
  not_before: new Set(["kind", "at"]),
  issue_comment_digest: new Set(["kind", "repository", "number", "commentId", "contentDigest"]),
  all_of: new Set(["kind", "predicates"]),
};

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

function requireObject(value, message) {
  if (!isObject(value)) schemaError(message);
  return value;
}

function requireOnlyKeys(value, allowed, context) {
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) schemaError(`${context} contains unsupported field: ${key}`);
  }
}

function requireRepository(value, context) {
  if (!isNonEmptyString(value) || !REPOSITORY.test(value)) {
    schemaError(`${context} must be owner/name`);
  }
}

function requirePositiveInteger(value, context) {
  if (!isPositiveInteger(value)) schemaError(`${context} must be a positive integer`);
}

function withoutKeys(value, keys) {
  const result = Object.assign({}, value);
  for (const key of keys) delete result[key];
  return result;
}

function validateReference(reference, context) {
  requireObject(reference, `${context} must be an object`);
  if (!isNonEmptyString(reference.kind)) schemaError(`${context}.kind must be a non-empty string`);

  switch (reference.kind) {
    case "git":
      requireOnlyKeys(reference, new Set(["kind", "repository", "commit"]), context);
      requireRepository(reference.repository, `${context}.repository`);
      if (!isNonEmptyString(reference.commit)) schemaError(`${context}.commit must be a non-empty string`);
      return;
    case "github_issue":
    case "issue":
      requireOnlyKeys(reference, new Set(["kind", "repository", "number"]), context);
      requireRepository(reference.repository, `${context}.repository`);
      requirePositiveInteger(reference.number, `${context}.number`);
      return;
    case "github_pull_request":
    case "pull_request":
      requireOnlyKeys(reference, new Set(["kind", "repository", "number"]), context);
      requireRepository(reference.repository, `${context}.repository`);
      requirePositiveInteger(reference.number, `${context}.number`);
      return;
    case "commit":
      requireOnlyKeys(reference, new Set(["kind", "repository", "sha"]), context);
      requireRepository(reference.repository, `${context}.repository`);
      if (!isNonEmptyString(reference.sha)) schemaError(`${context}.sha must be a non-empty string`);
      return;
    case "evidence":
      requireOnlyKeys(reference, new Set(["kind", "url", "sha256"]), context);
      if (!isNonEmptyString(reference.url) && !SHA256.test(reference.sha256 || "")) {
        schemaError(`${context} must contain a non-empty url or sha256 digest`);
      }
      return;
    default:
      schemaError(`${context}.kind is not an immutable reference kind: ${reference.kind}`);
  }
}

function validateBasis(basis, context, digestInput) {
  requireObject(basis, `${context} must be an object`);
  requireOnlyKeys(basis, BASIS_KEYS, context);
  if (!SHA256.test(basis.contentDigest || "")) {
    schemaError(`${context}.contentDigest must be sha256:<64 lowercase hex>`);
  }
  if (!Array.isArray(basis.refs) || basis.refs.length === 0) {
    schemaError(`${context}.refs must be a non-empty array`);
  }
  basis.refs.forEach((reference, index) => validateReference(reference, `${context}.refs[${index}]`));
  if (basis.contentDigest !== sha256Digest(digestInput)) {
    schemaError(`${context} contentDigest does not match canonical declaration`);
  }
}

function validateNotBefore(value, context) {
  if (!isNonEmptyString(value) || !RFC3339_JST.test(value) || Number.isNaN(Date.parse(value))) {
    schemaError(`${context} must be RFC3339 with a JST +09:00 offset`);
  }
}

function validatePredicate(predicate, context, depth) {
  if (depth > 16) schemaError(`${context} nesting is too deep`);
  requireObject(predicate, `${context} must be an object`);
  if (!isNonEmptyString(predicate.kind) || !PREDICATE_KEYS[predicate.kind]) {
    schemaError(`${context}.kind is not supported in predicate v1`);
  }
  requireOnlyKeys(predicate, PREDICATE_KEYS[predicate.kind], context);

  switch (predicate.kind) {
    case "issue_closed":
    case "pull_request_merged":
      requireRepository(predicate.repository, `${context}.repository`);
      requirePositiveInteger(predicate.number, `${context}.number`);
      return;
    case "review_approved":
      requireRepository(predicate.repository, `${context}.repository`);
      requirePositiveInteger(predicate.number, `${context}.number`);
      if (!isNonEmptyString(predicate.headOid)) schemaError(`${context}.headOid must be a non-empty string`);
      return;
    case "not_before":
      validateNotBefore(predicate.at, `${context}.at`);
      return;
    case "issue_comment_digest":
      requireRepository(predicate.repository, `${context}.repository`);
      requirePositiveInteger(predicate.number, `${context}.number`);
      requirePositiveInteger(predicate.commentId, `${context}.commentId`);
      if (!SHA256.test(predicate.contentDigest || "")) {
        schemaError(`${context}.contentDigest must be sha256:<64 lowercase hex>`);
      }
      return;
    case "all_of":
      if (!Array.isArray(predicate.predicates) || predicate.predicates.length === 0) {
        schemaError(`${context}.predicates must be a non-empty array`);
      }
      predicate.predicates.forEach((child, index) => validatePredicate(child, `${context}.predicates[${index}]`, depth + 1));
      return;
    default:
      throw new Error(`unhandled predicate kind: ${predicate.kind}`);
  }
}

function validateScope(scope, index) {
  const context = `scopes[${index}]`;
  requireObject(scope, `${context} must be an object`);
  requireOnlyKeys(scope, SCOPE_KEYS, context);
  if (!isNonEmptyString(scope.id)) schemaError(`${context}.id must be a non-empty string`);
  requireRepository(scope.repository, `${context}.repository`);
  if (scope.issueState !== "OPEN") schemaError(`${context}.issueState must be OPEN`);
  if (!Array.isArray(scope.labelsAll)) schemaError(`${context}.labelsAll must be an array`);
  const labels = new Set();
  scope.labelsAll.forEach((label, labelIndex) => {
    if (!isNonEmptyString(label)) schemaError(`${context}.labelsAll[${labelIndex}] must be a non-empty string`);
    if (labels.has(label)) schemaError(`${context}.labelsAll must be unique`);
    labels.add(label);
  });
  const declaration = withoutKeys(scope, ["basis"]);
  validateBasis(scope.basis, `${context}.basis`, declaration);
  return scope;
}

function validateBlocker(blocker, context) {
  requireObject(blocker, `${context} must be an object`);
  requireOnlyKeys(blocker, BLOCKER_KEYS, context);
  if (!isNonEmptyString(blocker.reasonCode) || !REASON_CODE.test(blocker.reasonCode)) {
    schemaError(`${context}.reasonCode must be a lower-snake-case token`);
  }
  validatePredicate(blocker.releasePredicate, `${context}.releasePredicate`, 0);
}

function validateEntry(entry, index, seats, sources) {
  const context = `entries[${index}]`;
  requireObject(entry, `${context} must be an object`);
  requireOnlyKeys(entry, ENTRY_KEYS, context);
  if (entry.schemaVersion !== 1) schemaError(`${context}.schemaVersion must be integer 1`);

  requireObject(entry.source, `${context}.source must be an object`);
  requireOnlyKeys(entry.source, SOURCE_KEYS, `${context}.source`);
  requireRepository(entry.source.repository, `${context}.source.repository`);
  requirePositiveInteger(entry.source.number, `${context}.source.number`);
  const sourceKey = `${entry.source.repository}#${entry.source.number}`;
  if (sources.has(sourceKey)) schemaError(`entries source must be unique: ${sourceKey}`);
  sources.add(sourceKey);

  if (entry.state !== "ready" && entry.state !== "blocked" && entry.state !== "unknown") {
    schemaError(`${context}.state must be ready, blocked, or unknown`);
  }
  if (!isNonEmptyString(entry.ownerSeat)) schemaError(`${context}.ownerSeat must be a non-empty string`);
  const owner = seats.get(entry.ownerSeat);
  if (!owner) schemaError(`owner seat does not exist: ${entry.ownerSeat}`);
  if (owner.kind !== "seat") schemaError(`owner must be an exact kind: seat: ${entry.ownerSeat}`);

  if (!Array.isArray(entry.workKinds) || entry.workKinds.length === 0) {
    schemaError(`${context}.workKinds must be a non-empty array`);
  }
  const workKinds = new Set();
  entry.workKinds.forEach((workKind) => {
    if (!WORK_KINDS.has(workKind)) schemaError(`unknown work kind: ${String(workKind)}`);
    if (workKinds.has(workKind)) schemaError(`${context}.workKinds must be unique`);
    workKinds.add(workKind);
  });

  const basisInput = withoutKeys(entry, ["basis", "entryDigest"]);
  validateBasis(entry.basis, `${context}.basis`, basisInput);
  if (!isPositiveInteger(entry.revision)) schemaError(`${context}.revision must be a positive integer`);

  const hasBlocker = Object.prototype.hasOwnProperty.call(entry, "blocker");
  if (entry.state === "ready" && hasBlocker) schemaError("ready entry must not have blocker");
  if (entry.state === "blocked") {
    if (!hasBlocker || !isObject(entry.blocker) || !isNonEmptyString(entry.blocker.reasonCode) || !entry.blocker.releasePredicate) {
      schemaError("blocked entry requires blocker.reasonCode and blocker.releasePredicate");
    }
    validateBlocker(entry.blocker, `${context}.blocker`);
  }
  if (entry.state === "unknown" && hasBlocker) schemaError("unknown entry must not have blocker");

  if (Object.prototype.hasOwnProperty.call(entry, "entryDigest")) {
    if (!SHA256.test(entry.entryDigest || "")) schemaError(`${context}.entryDigest must be sha256:<64 lowercase hex>`);
    if (entry.entryDigest !== sha256Digest(withoutKeys(entry, ["entryDigest"]))) {
      schemaError(`${context}.entryDigest does not match canonical entry`);
    }
  }
  return {
    entry,
    sourceKey,
    entryDigest: sha256Digest(withoutKeys(entry, ["entryDigest"])),
  };
}

function rosterSeats(roster, requestedTeam) {
  requireObject(roster, "roster contract must be an object");
  if (roster.schemaVersion !== 1) schemaError("roster contract schemaVersion must be integer 1");
  if (roster.team !== requestedTeam) schemaError("roster contract team does not match requested team");
  if (!Array.isArray(roster.members)) schemaError("roster contract members must be an array");
  const seats = new Map();
  for (const member of roster.members) {
    requireObject(member, "roster contract member must be an object");
    if (!isNonEmptyString(member.name)) schemaError("roster contract member name must be a non-empty string");
    if (seats.has(member.name)) schemaError(`roster contract member name must be unique: ${member.name}`);
    seats.set(member.name, member);
  }
  return seats;
}

function validateG4Pack(pack, roster, requestedTeam) {
  requireObject(pack, "G4 pack must be an object");
  requireOnlyKeys(pack, PACK_KEYS, "G4 pack");
  if (pack.schemaVersion !== 1) schemaError("G4 pack schemaVersion must be integer 1");
  if (pack.team !== requestedTeam) schemaError("G4 pack team does not match requested team");
  if (!Array.isArray(pack.scopes) || pack.scopes.length === 0) schemaError("G4 pack scopes must be a non-empty array");
  if (!Array.isArray(pack.entries)) schemaError("G4 pack entries must be an array");

  const seats = rosterSeats(roster, requestedTeam);
  const scopeIds = new Set();
  pack.scopes.forEach((scope, index) => {
    validateScope(scope, index);
    if (scopeIds.has(scope.id)) schemaError(`scope id must be unique: ${scope.id}`);
    scopeIds.add(scope.id);
  });

  const sources = new Set();
  const entries = pack.entries.map((entry, index) => validateEntry(entry, index, seats, sources));
  return {
    pack,
    seats,
    scopes: pack.scopes,
    entries,
    packDigest: sha256Digest(pack),
  };
}

function parseJson(text, message) {
  try {
    return JSON.parse(text);
  } catch (_) {
    schemaError(message);
  }
}

module.exports = {
  WORK_KINDS,
  SHA256,
  SchemaError,
  canonicalJson,
  sha256Digest,
  isObject,
  isNonEmptyString,
  isPositiveInteger,
  requireRepository,
  validatePredicate,
  validateG4Pack,
  parseJson,
  withoutKeys,
};
