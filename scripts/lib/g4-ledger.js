#!/usr/bin/env node
"use strict";

const childProcess = require("child_process");
const {
  SchemaError,
  canonicalJson,
  sha256Digest,
} = require("./team-work");

const REPOSITORY = /^[^/\s]+\/[^/\s]+$/;

function schemaError(message) {
  throw new SchemaError(message);
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function requireNonEmptyString(value, context) {
  if (typeof value !== "string" || value.length === 0) {
    schemaError(context + " must be a non-empty string");
  }
  return value;
}

function requireSource(source) {
  if (!isObject(source)) schemaError("source must be an object");
  requireNonEmptyString(source.repository, "source.repository");
  if (!REPOSITORY.test(source.repository)) schemaError("source.repository must be owner/name");
  if (!Number.isInteger(source.number) || source.number < 1) {
    schemaError("source.number must be a positive integer");
  }
  return source;
}

function sqlLiteral(value) {
  return "'" + String(value).replace(/'/g, "''") + "'";
}

function g4CurrentResultSql(team, source) {
  requireNonEmptyString(team, "team");
  requireSource(source);
  return [
    "SELECT json_object(",
    "  'auditDigest', audit_digest,",
    "  'basis', json(basis_json),",
    "  'blocker', CASE WHEN blocker_json IS NULL THEN NULL ELSE json(blocker_json) END,",
    "  'coverageDigest', coverage_digest,",
    "  'createdAt', created_at,",
    "  'entryDigest', entry_digest,",
    "  'evidence', evidence,",
    "  'lastAction', last_action,",
    "  'lastActor', last_actor,",
    "  'ownerSeat', owner_seat,",
    "  'packDigest', pack_digest,",
    "  'revision', revision,",
    "  'source', json_object(",
    "    'number', source_number,",
    "    'repository', source_repository",
    "  ),",
    "  'state', state,",
    "  'team', team,",
    "  'updatedAt', updated_at,",
    "  'workKinds', json(work_kinds_json)",
    ")",
    "FROM team_work_g4_current",
    "WHERE team = " + sqlLiteral(team),
    "  AND source_repository = " + sqlLiteral(source.repository),
    "  AND source_number = " + source.number,
    "ORDER BY source_repository, source_number;",
  ].join("\n");
}

function parseSingleJsonRow(stdout, context) {
  const lines = String(stdout || "").trim().split(/\r?\n/).filter(Boolean);
  if (lines.length === 0) return null;
  if (lines.length !== 1) throw new Error(context + " returned multiple rows");
  try {
    return JSON.parse(lines[0]);
  } catch (_) {
    throw new Error(context + " returned malformed JSON");
  }
}

function readG4Current(dbPath, team, source) {
  requireNonEmptyString(dbPath, "dbPath");
  const sql = g4CurrentResultSql(team, source);
  const result = childProcess.spawnSync("sqlite3", ["-readonly", dbPath, sql], {
    encoding: "utf8",
    maxBuffer: 4 * 1024 * 1024,
  });
  if (result.error) {
    if (result.error.code === "ENOENT") throw new Error("g4-ledger requires sqlite3 on PATH");
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(String(result.stderr || "").trim() || "sqlite3 G4 current read failed");
  }
  return parseSingleJsonRow(result.stdout, "G4 current read");
}

function snapshotG4Row(row) {
  if (!isObject(row)) schemaError("G4 current row must be an object");
  requireNonEmptyString(row.team, "row.team");
  requireSource(row.source);
  requireNonEmptyString(row.state, "row.state");
  requireNonEmptyString(row.ownerSeat, "row.ownerSeat");
  if (!Array.isArray(row.workKinds)) schemaError("row.workKinds must be an array");
  if (!Number.isInteger(row.revision) || row.revision < 1) {
    schemaError("row.revision must be a positive integer");
  }
  requireNonEmptyString(row.packDigest, "row.packDigest");
  requireNonEmptyString(row.entryDigest, "row.entryDigest");
  requireNonEmptyString(row.coverageDigest, "row.coverageDigest");
  requireNonEmptyString(row.auditDigest, "row.auditDigest");
  if (!isObject(row.basis)) schemaError("row.basis must be an object");
  if (row.blocker !== null && row.blocker !== undefined && !isObject(row.blocker)) {
    schemaError("row.blocker must be an object or null");
  }
  requireNonEmptyString(row.evidence, "row.evidence");
  requireNonEmptyString(row.lastAction, "row.lastAction");
  requireNonEmptyString(row.lastActor, "row.lastActor");
  if (!Number.isInteger(row.createdAt) || !Number.isInteger(row.updatedAt)) {
    schemaError("row timestamps must be integers");
  }
  return {
    action: row.lastAction,
    actor: row.lastActor,
    auditDigest: row.auditDigest,
    basis: row.basis,
    blocker: row.blocker === undefined ? null : row.blocker,
    coverageDigest: row.coverageDigest,
    createdAt: row.createdAt,
    entryDigest: row.entryDigest,
    evidence: row.evidence,
    ownerSeat: row.ownerSeat,
    packDigest: row.packDigest,
    revision: row.revision,
    schemaVersion: 1,
    source: {
      number: row.source.number,
      repository: row.source.repository,
    },
    state: row.state,
    updatedAt: row.updatedAt,
    workKinds: row.workKinds,
  };
}

function runG4Transaction(dbPath, statements) {
  requireNonEmptyString(dbPath, "dbPath");
  const sql = Array.isArray(statements) ? statements.join("\n") : statements;
  requireNonEmptyString(sql, "statements");
  const script = [
    ".bail on",
    ".timeout 5000",
    "BEGIN IMMEDIATE;",
    sql,
    "COMMIT;",
  ].join("\n");
  const result = childProcess.spawnSync("sqlite3", [dbPath], {
    encoding: "utf8",
    input: script,
    maxBuffer: 4 * 1024 * 1024,
  });
  if (result.error) {
    if (result.error.code === "ENOENT") throw new Error("g4-ledger requires sqlite3 on PATH");
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(String(result.stderr || "").trim() || "sqlite3 G4 transaction failed");
  }
  return String(result.stdout || "").trim();
}

module.exports = {
  readG4Current,
  snapshotG4Row,
  runG4Transaction,
  g4CurrentResultSql,
  canonicalJson,
  sha256Digest,
};
