#!/usr/bin/env node
"use strict";

const childProcess = require("child_process");
const fs = require("fs");
const {
  SchemaError,
  canonicalJson,
  sha256Digest,
} = require("./team-work");
const {
  parseJson,
  validateG4Pack,
} = require("./g4-contract");
const {runAudit} = require("./g4-audit");

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

function requireEvidence(value) {
  if (typeof value !== "string" || value.trim().length === 0) {
    schemaError("evidence must be a non-empty string");
  }
  return value;
}

function requireManager(roster, managerSeat) {
  requireNonEmptyString(managerSeat, "manager-seat");
  if (!isObject(roster) || !Array.isArray(roster.members)) {
    schemaError("roster contract members must be an array");
  }
  const manager = roster.members.find((member) => member && member.name === managerSeat);
  if (!manager || manager.kind !== "seat" || manager.role !== "manager") {
    schemaError("manager-seat must be an exact kind: seat manager");
  }
  return manager;
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

function sourceKey(source) {
  return `${source.repository}#${source.number}`;
}

function sourcePredicate(sources) {
  return sources.map((source) => `(
    source_repository = ${sqlLiteral(source.repository)}
    AND source_number = ${source.number}
  )`).join(" OR ");
}

function bootstrapSql(team, entries, packDigest, coverageDigest, auditDigest, managerSeat, evidence) {
  const now = "CAST(strftime('%s', 'now') AS INTEGER)";
  const sources = entries.map((info) => info.entry.source);
  const where = `team = ${sqlLiteral(team)} AND (${sourcePredicate(sources)})`;
  const statements = [
    "CREATE TEMP TABLE g4_bootstrap_no_existing(value INTEGER NOT NULL, CONSTRAINT g4_bootstrap_no_existing_guard CHECK(value = 0));",
    `INSERT INTO g4_bootstrap_no_existing(value)
SELECT COUNT(*) FROM team_work_g4_current WHERE ${where};`,
    "DROP TABLE g4_bootstrap_no_existing;",
  ];

  for (const info of entries) {
    const entry = info.entry;
    statements.push(`
INSERT INTO team_work_g4_current(
  team, source_repository, source_number, state, owner_seat,
  work_kinds_json, revision, pack_digest, entry_digest, coverage_digest,
  audit_digest, basis_json, blocker_json, evidence, last_action, last_actor,
  created_at, updated_at
) VALUES (
  ${sqlLiteral(team)},
  ${sqlLiteral(entry.source.repository)},
  ${entry.source.number},
  ${sqlLiteral(entry.state)},
  ${sqlLiteral(entry.ownerSeat)},
  json(${sqlLiteral(canonicalJson(entry.workKinds))}),
  1,
  ${sqlLiteral(packDigest)},
  ${sqlLiteral(info.entryDigest)},
  ${sqlLiteral(coverageDigest)},
  ${sqlLiteral(auditDigest)},
  json(${sqlLiteral(canonicalJson(entry.basis))}),
  ${entry.blocker ? `json(${sqlLiteral(canonicalJson(entry.blocker))})` : "NULL"},
  ${sqlLiteral(evidence)},
  'g4-bootstrap',
  ${sqlLiteral(managerSeat)},
  ${now},
  ${now}
);`);
  }

  statements.push(
    `CREATE TEMP TABLE g4_bootstrap_complete(value INTEGER NOT NULL, CONSTRAINT g4_bootstrap_complete_guard CHECK(value = ${entries.length}));`,
    `INSERT INTO g4_bootstrap_complete(value)
SELECT COUNT(*) FROM team_work_g4_current WHERE ${where};`,
    "DROP TABLE g4_bootstrap_complete;",
    `SELECT COUNT(*) FROM team_work_g4_current WHERE ${where};`,
  );
  return statements;
}

function bootstrapOutput(team, managerSeat, evidence, details) {
  return Object.assign({
    schemaVersion: 1,
    command: "g4-bootstrap",
    team,
    managerSeat: managerSeat === undefined ? null : managerSeat,
    evidence: evidence === undefined ? null : evidence,
    packDigest: null,
    coverageDigest: null,
    auditDigest: null,
    sources: [],
    revision: 1,
    bootstrapped: false,
    remediation: [],
  }, details || {});
}

function bootstrapRejected(team, managerSeat, evidence, code, details) {
  return bootstrapOutput(team, managerSeat, evidence, Object.assign({
    bootstrapped: false,
    sources: [],
    remediation: [{code}],
  }, details || {}));
}

function bootstrapG4(team, pack, roster, managerSeat, evidence) {
  let contract;
  try {
    contract = validateG4Pack(pack, roster, team);
  } catch (_) {
    return bootstrapRejected(team, managerSeat, evidence, "invalid_contract");
  }

  try {
    requireManager(roster, managerSeat);
  } catch (_) {
    return bootstrapRejected(team, managerSeat, evidence, "invalid_manager", {
      packDigest: contract.packDigest,
    });
  }
  try {
    requireEvidence(evidence);
    for (const info of contract.entries) {
      if (info.entry.revision !== 1) schemaError("g4-bootstrap requires entry.revision 1");
    }
  } catch (_) {
    return bootstrapRejected(team, managerSeat, evidence, "invalid_bootstrap_input", {
      packDigest: contract.packDigest,
    });
  }

  let audit;
  try {
    audit = runAudit("g4-bootstrap", team, pack, roster);
  } catch (_) {
    return bootstrapRejected(team, managerSeat, evidence, "audit_incomplete", {
      packDigest: contract.packDigest,
    });
  }
  const auditDetails = {
    packDigest: audit.packDigest,
    coverageDigest: audit.coverageDigest,
    auditDigest: audit.auditDigest,
  };
  if (!audit.classificationBasis || audit.classificationBasis.status !== "complete") {
    return bootstrapRejected(team, managerSeat, evidence, "audit_incomplete", auditDetails);
  }

  const entriesBySource = new Map(contract.entries.map((info) => [info.sourceKey, info]));
  const sortedCoverage = audit.coverage.slice().sort((left, right) => sourceKey(left).localeCompare(sourceKey(right)));
  if (sortedCoverage.length !== contract.entries.length ||
      sortedCoverage.some((source) => !entriesBySource.has(sourceKey(source)))) {
    return bootstrapRejected(team, managerSeat, evidence, "audit_incomplete", auditDetails);
  }
  const entries = sortedCoverage.map((source) => entriesBySource.get(sourceKey(source)));
  const dbPath = process.env.AGMSG_TEAM_WORK_DB;
  if (typeof dbPath !== "string" || dbPath.length === 0) {
    return bootstrapRejected(team, managerSeat, evidence, "storage_unavailable", auditDetails);
  }

  try {
    const transactionResult = runG4Transaction(
      dbPath,
      bootstrapSql(team, entries, audit.packDigest, audit.coverageDigest, audit.auditDigest, managerSeat, evidence),
    );
    const lines = transactionResult.replace(/\r/g, "").split("\n").filter(Boolean);
    const insertedCount = Number(lines[lines.length - 1]);
    if (insertedCount !== entries.length) throw new Error("g4-bootstrap transaction returned an incomplete row count");
  } catch (error) {
    const errorMessage = String(error.message || "");
    const code = /g4_bootstrap_no_existing|value = 0/.test(errorMessage)
      ? "already_bootstrapped"
      : "transaction_failed";
    return bootstrapRejected(team, managerSeat, evidence, code, auditDetails);
  }

  return bootstrapOutput(team, managerSeat, evidence, {
    packDigest: audit.packDigest,
    coverageDigest: audit.coverageDigest,
    auditDigest: audit.auditDigest,
    sources: sortedCoverage,
    revision: 1,
    bootstrapped: true,
    remediation: [],
  });
}

function parsePackFile(packPath) {
  try {
    return parseJson(fs.readFileSync(packPath, "utf8"), "G4 state pack is not valid JSON");
  } catch (_) {
    return null;
  }
}

module.exports = {
  readG4Current,
  snapshotG4Row,
  runG4Transaction,
  g4CurrentResultSql,
  bootstrapG4,
  canonicalJson,
  sha256Digest,
};

function main() {
  const [command, team, packPath, managerSeat, evidence] = process.argv.slice(2);
  if (command !== "g4-bootstrap") {
    throw new SchemaError(`unknown G4 ledger command: ${command || ""}`);
  }
  if (process.argv.slice(2).length !== 5) {
    throw new SchemaError("invalid arguments for g4-bootstrap");
  }
  const pack = parsePackFile(packPath);
  if (!pack) {
    process.stdout.write(`${canonicalJson(bootstrapRejected(team, managerSeat, evidence, "invalid_contract"))}\n`);
    return;
  }
  let roster;
  try {
    roster = parseJson(fs.readFileSync(0, "utf8"), "roster contract is not valid JSON");
  } catch (_) {
    process.stdout.write(`${canonicalJson(bootstrapRejected(team, managerSeat, evidence, "invalid_contract"))}\n`);
    return;
  }
  process.stdout.write(`${canonicalJson(bootstrapG4(team, pack, roster, managerSeat, evidence))}\n`);
}

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
