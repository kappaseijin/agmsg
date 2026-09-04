#!/usr/bin/env node
"use strict";

const childProcess = require("child_process");
const fs = require("fs");
const {
  SchemaError,
  canonicalJson,
  sha256Digest,
  isObject,
  isNonEmptyString,
  isPositiveInteger,
  validateG4Pack,
  parseJson,
} = require("./g4-contract");

const AUDIT_COMMANDS = new Set(["g4-audit", "g4-reconcile"]);
const MAX_PAGES = 10000;

class LiveSourceError extends Error {
  constructor(code, message, details) {
    super(message);
    this.name = "LiveSourceError";
    this.code = code;
    this.details = details || {};
  }
}

function sourceError(code, message, details) {
  throw new LiveSourceError(code, message, details);
}

function splitRepository(repository) {
  if (!isNonEmptyString(repository)) sourceError("coverage_source_unavailable", "repository is unavailable");
  const parts = repository.split("/");
  if (parts.length !== 2 || !isNonEmptyString(parts[0]) || !isNonEmptyString(parts[1])) {
    sourceError("coverage_source_unavailable", "repository must be owner/name");
  }
  return {owner: parts[0], name: parts[1]};
}

function referenceKey(repository, number) {
  return `${repository}#${number}`;
}

function scopeQuery() {
  return [
    "query G4ScopeIssues($owner: String!, $name: String!, $after: String) {",
    "  repository(owner: $owner, name: $name) {",
    "    issues(first: 100, after: $after, states: OPEN) {",
    "      totalCount",
    "      nodes {",
    "        number",
    "        state",
    "        repository { nameWithOwner }",
    "        labels(first: 100) {",
    "          totalCount",
    "          nodes { name }",
    "          pageInfo { hasNextPage endCursor }",
    "        }",
    "      }",
    "      pageInfo { hasNextPage endCursor }",
    "    }",
    "  }",
    "}",
  ].join("\n");
}

function issueStateQuery() {
  return [
    "query G4IssueState($owner: String!, $name: String!, $number: Int!) {",
    "  repository(owner: $owner, name: $name) { issue(number: $number) { number state } }",
    "}",
  ].join("\n");
}

function pullRequestStateQuery() {
  return [
    "query G4PullRequestState($owner: String!, $name: String!, $number: Int!) {",
    "  repository(owner: $owner, name: $name) { pullRequest(number: $number) { number merged } }",
    "}",
  ].join("\n");
}

function reviewQuery() {
  return [
    "query G4PullRequestReviews($owner: String!, $name: String!, $number: Int!, $after: String) {",
    "  repository(owner: $owner, name: $name) {",
    "    pullRequest(number: $number) {",
    "      number",
    "      headRefOid",
    "      reviews(first: 100, after: $after) {",
    "        totalCount",
    "        nodes { state author { login } }",
    "        pageInfo { hasNextPage endCursor }",
    "      }",
    "    }",
    "  }",
    "}",
  ].join("\n");
}

function commentQuery() {
  return [
    "query G4IssueComments($owner: String!, $name: String!, $number: Int!, $after: String) {",
    "  repository(owner: $owner, name: $name) {",
    "    issue(number: $number) {",
    "      number",
    "      comments(first: 100, after: $after) {",
    "        totalCount",
    "        nodes { databaseId body }",
    "        pageInfo { hasNextPage endCursor }",
    "      }",
    "    }",
    "  }",
    "}",
  ].join("\n");
}

function parseGraphqlJson(stdout) {
  let response;
  try {
    response = JSON.parse(String(stdout || ""));
  } catch (_) {
    sourceError("coverage_source_unavailable", "GitHub GraphQL returned invalid JSON");
  }
  if (!isObject(response) || !isObject(response.data) || (Array.isArray(response.errors) && response.errors.length > 0)) {
    sourceError("coverage_source_unavailable", "GitHub GraphQL response contains errors");
  }
  return response.data;
}

function runGraphql(query, variables) {
  const repository = variables.repository;
  const parsedRepository = repository ? splitRepository(repository) : null;
  const args = [
    "api",
    "graphql",
    "-f", `query=${query}`,
  ];
  if (parsedRepository) {
    args.push("-f", `owner=${parsedRepository.owner}`);
    args.push("-f", `name=${parsedRepository.name}`);
  }
  if (variables.number !== undefined) args.push("-F", `number=${variables.number}`);
  if (variables.after !== null && variables.after !== undefined) args.push("-f", `after=${variables.after}`);

  const result = childProcess.spawnSync("gh", args, {
    encoding: "utf8",
    maxBuffer: 4 * 1024 * 1024,
  });
  if (result.error) sourceError("coverage_source_unavailable", "GitHub GraphQL command is unavailable");
  if (result.status !== 0) sourceError("coverage_source_unavailable", "GitHub GraphQL request failed");
  return parseGraphqlJson(result.stdout);
}

function requireConnection(value, name) {
  if (!isObject(value)) sourceError("coverage_source_unavailable", `${name} connection is unavailable`);
  if (!Number.isInteger(value.totalCount) || value.totalCount < 0) {
    sourceError("coverage_source_unavailable", `${name} totalCount is invalid`);
  }
  if (!Array.isArray(value.nodes) || !isObject(value.pageInfo) || typeof value.pageInfo.hasNextPage !== "boolean") {
    sourceError("coverage_source_unavailable", `${name} page info is invalid`);
  }
  return value;
}

function normalizeLabels(labels, name) {
  const connection = requireConnection(labels, `${name} labels`);
  const names = [];
  const seen = new Set();
  for (const label of connection.nodes) {
    if (!isObject(label) || !isNonEmptyString(label.name)) {
      sourceError("coverage_source_unavailable", `${name} label is invalid`);
    }
    if (seen.has(label.name)) sourceError("coverage_source_unavailable", `${name} label repeated`);
    seen.add(label.name);
    names.push(label.name);
  }
  if (connection.nodes.length !== connection.totalCount || connection.pageInfo.hasNextPage) {
    sourceError("coverage_source_unavailable", `${name} labels are not fully paginated`);
  }
  return names;
}

function normalizeIssue(node, scope) {
  const name = `scope ${scope.id}`;
  if (!isObject(node) || !isPositiveInteger(node.number) || node.state !== "OPEN" || !isObject(node.repository)) {
    sourceError("coverage_source_unavailable", `${name} returned an invalid issue`);
  }
  if (node.repository.nameWithOwner !== scope.repository) {
    sourceError("coverage_source_unavailable", `${name} returned an issue from another repository`);
  }
  return {
    repository: node.repository.nameWithOwner,
    number: node.number,
    labels: normalizeLabels(node.labels, `${name}#${node.number}`),
  };
}

function fetchScopeIssues(scope) {
  const parsedRepository = splitRepository(scope.repository);
  const nodes = [];
  const seenNodes = new Set();
  const seenCursors = new Set();
  let expectedCount = null;
  let after = null;
  let pages = 0;

  while (true) {
    pages += 1;
    if (pages > MAX_PAGES) sourceError("coverage_source_unavailable", `scope ${scope.id} exceeded pagination bound`);
    if (after !== null) {
      if (seenCursors.has(after)) sourceError("coverage_source_unavailable", `scope ${scope.id} cursor repeated`);
      seenCursors.add(after);
    }
    const data = runGraphql(scopeQuery(), {
      repository: scope.repository,
      after,
    });
    const repository = data.repository;
    const connection = requireConnection(repository && repository.issues, `scope ${scope.id}`);
    if (expectedCount === null) expectedCount = connection.totalCount;
    if (expectedCount !== connection.totalCount) {
      sourceError("coverage_source_unavailable", `scope ${scope.id} totalCount changed`);
    }

    for (const rawNode of connection.nodes) {
      const node = normalizeIssue(rawNode, scope);
      const key = referenceKey(node.repository, node.number);
      if (seenNodes.has(key)) sourceError("coverage_source_unavailable", `scope ${scope.id} issue repeated`, {source: key});
      seenNodes.add(key);
      if (scope.labelsAll.every((label) => node.labels.includes(label))) {
        nodes.push({repository: node.repository, number: node.number});
      }
    }

    if (!connection.pageInfo.hasNextPage) {
      if (seenNodes.size !== expectedCount) {
        sourceError("coverage_source_unavailable", `scope ${scope.id} page count is incomplete`);
      }
      break;
    }
    if (!isNonEmptyString(connection.pageInfo.endCursor)) {
      sourceError("coverage_source_unavailable", `scope ${scope.id} next-page cursor is missing`);
    }
    after = connection.pageInfo.endCursor;
  }

  nodes.sort((left, right) => referenceKey(left.repository, left.number).localeCompare(referenceKey(right.repository, right.number)));
  return {
    scope: {
      id: scope.id,
      repository: scope.repository,
      issueState: scope.issueState,
      labelsAll: scope.labelsAll.slice(),
    },
    coverage: nodes,
  };
}

function parseNow() {
  const raw = process.env.G4_AUDIT_NOW;
  if (raw === undefined || raw === "") return Date.now();
  if (!isNonEmptyString(raw) || Number.isNaN(Date.parse(raw))) {
    throw new SchemaError("G4_AUDIT_NOW must be an RFC3339 timestamp");
  }
  return Date.parse(raw);
}

function evaluateIssueClosed(predicate) {
  const data = runGraphql(issueStateQuery(), predicate);
  const issue = data.repository && data.repository.issue;
  if (!isObject(issue) || issue.number !== predicate.number || !isNonEmptyString(issue.state)) {
    sourceError("predicate_source_unavailable", "issue_closed predicate source is unavailable");
  }
  return issue.state === "CLOSED";
}

function evaluatePullRequestMerged(predicate) {
  const data = runGraphql(pullRequestStateQuery(), predicate);
  const pullRequest = data.repository && data.repository.pullRequest;
  if (!isObject(pullRequest) || pullRequest.number !== predicate.number || typeof pullRequest.merged !== "boolean") {
    sourceError("predicate_source_unavailable", "pull_request_merged predicate source is unavailable");
  }
  return pullRequest.merged;
}

function evaluateReviews(predicate) {
  let after = null;
  let expectedCount = null;
  const seenCursors = new Set();
  const approved = [];
  let headOid = null;
  let observedCount = 0;
  let pages = 0;
  while (true) {
    pages += 1;
    if (pages > MAX_PAGES) sourceError("predicate_source_unavailable", "review predicate exceeded pagination bound");
    if (after !== null) {
      if (seenCursors.has(after)) sourceError("predicate_source_unavailable", "review predicate cursor repeated");
      seenCursors.add(after);
    }
    const data = runGraphql(reviewQuery(), Object.assign({}, predicate, {after}));
    const pullRequest = data.repository && data.repository.pullRequest;
    if (!isObject(pullRequest) || pullRequest.number !== predicate.number || !isNonEmptyString(pullRequest.headRefOid)) {
      sourceError("predicate_source_unavailable", "review predicate pull request is unavailable");
    }
    if (headOid === null) headOid = pullRequest.headRefOid;
    if (headOid !== pullRequest.headRefOid) sourceError("predicate_source_unavailable", "review predicate head changed");
    const reviews = requireConnection(pullRequest.reviews, "review predicate");
    if (expectedCount === null) expectedCount = reviews.totalCount;
    if (expectedCount !== reviews.totalCount) sourceError("predicate_source_unavailable", "review predicate totalCount changed");
    observedCount += reviews.nodes.length;
    for (const review of reviews.nodes) {
      if (!isObject(review) || !isNonEmptyString(review.state)) sourceError("predicate_source_unavailable", "review predicate returned invalid review");
      if (review.state === "APPROVED" && isObject(review.author) && isNonEmptyString(review.author.login)) approved.push(review.author.login);
    }
    if (!reviews.pageInfo.hasNextPage) {
      if (observedCount !== expectedCount) sourceError("predicate_source_unavailable", "review predicate page count is incomplete");
      break;
    }
    if (!isNonEmptyString(reviews.pageInfo.endCursor)) sourceError("predicate_source_unavailable", "review predicate next cursor is missing");
    after = reviews.pageInfo.endCursor;
  }
  return headOid === predicate.headOid && approved.length > 0;
}

function evaluateCommentDigest(predicate) {
  let after = null;
  let expectedCount = null;
  const seenCursors = new Set();
  let pages = 0;
  while (true) {
    pages += 1;
    if (pages > MAX_PAGES) sourceError("predicate_source_unavailable", "comment predicate exceeded pagination bound");
    if (after !== null) {
      if (seenCursors.has(after)) sourceError("predicate_source_unavailable", "comment predicate cursor repeated");
      seenCursors.add(after);
    }
    const data = runGraphql(commentQuery(), Object.assign({}, predicate, {after}));
    const issue = data.repository && data.repository.issue;
    if (!isObject(issue) || issue.number !== predicate.number) sourceError("predicate_source_unavailable", "comment predicate Issue is unavailable");
    const comments = requireConnection(issue.comments, "comment predicate");
    if (expectedCount === null) expectedCount = comments.totalCount;
    if (expectedCount !== comments.totalCount) sourceError("predicate_source_unavailable", "comment predicate totalCount changed");
    for (const comment of comments.nodes) {
      if (!isObject(comment) || !isPositiveInteger(comment.databaseId) || typeof comment.body !== "string") {
        sourceError("predicate_source_unavailable", "comment predicate returned invalid comment");
      }
      if (comment.databaseId === predicate.commentId && sha256Digest(comment.body) === predicate.contentDigest) return true;
    }
    if (!comments.pageInfo.hasNextPage) {
      if (comments.nodes.length !== expectedCount) sourceError("predicate_source_unavailable", "comment predicate page count is incomplete");
      break;
    }
    if (!isNonEmptyString(comments.pageInfo.endCursor)) sourceError("predicate_source_unavailable", "comment predicate next cursor is missing");
    after = comments.pageInfo.endCursor;
  }
  return false;
}

function evaluatePredicate(predicate, now) {
  try {
    switch (predicate.kind) {
      case "issue_closed":
        return {status: evaluateIssueClosed(predicate) ? "true" : "false"};
      case "pull_request_merged":
        return {status: evaluatePullRequestMerged(predicate) ? "true" : "false"};
      case "review_approved":
        return {status: evaluateReviews(predicate) ? "true" : "false"};
      case "not_before":
        return {status: Date.parse(predicate.at) <= now ? "true" : "false"};
      case "issue_comment_digest":
        return {status: evaluateCommentDigest(predicate) ? "true" : "false"};
      case "all_of": {
        const children = predicate.predicates.map((child) => evaluatePredicate(child, now));
        if (children.some((child) => child.status === "unknown")) return {status: "unknown", children};
        return {status: children.every((child) => child.status === "true") ? "true" : "false", children};
      }
      default:
        return {status: "unknown"};
    }
  } catch (error) {
    if (error instanceof LiveSourceError) return {status: "unknown", reason: error.code};
    throw error;
  }
}

function sourceListDigest(list) {
  return sha256Digest(list.slice().sort((left, right) => referenceKey(left.repository, left.number).localeCompare(referenceKey(right.repository, right.number))));
}

function sortReasons(reasons) {
  const unique = [];
  const seen = new Set();
  for (const reason of reasons) {
    const fingerprint = canonicalJson(reason);
    if (!seen.has(fingerprint)) {
      seen.add(fingerprint);
      unique.push(reason);
    }
  }
  return unique.sort((left, right) => canonicalJson(left).localeCompare(canonicalJson(right)));
}

function reconcileFindings(contract) {
  const owners = new Set(contract.entries.map((info) => info.entry.ownerSeat));
  return Array.from(contract.seats.values())
    .filter((member) => member.kind === "seat" && member.role !== "manager" && member.role !== "pm")
    .map((member) => member.name)
    .filter((seat) => !owners.has(seat))
    .sort((left, right) => left.localeCompare(right))
    .map((seat) => ({code: "unassigned_seat", seat}));
}

function outputEntry(info, predicateObservation) {
  const entry = info.entry;
  const result = {
    source: {
      repository: entry.source.repository,
      number: entry.source.number,
    },
    state: predicateObservation && predicateObservation.status === "unknown" ? "unknown" : entry.state,
    ownerSeat: entry.ownerSeat,
    workKinds: entry.workKinds.slice(),
    revision: entry.revision,
    entryDigest: info.entryDigest,
  };
  if (entry.blocker) result.blocker = entry.blocker;
  if (predicateObservation) result.releasePredicate = predicateObservation;
  return result;
}

function runAudit(command, team, pack, roster) {
  const contract = validateG4Pack(pack, roster, team);
  const now = parseNow();
  const scopeAudits = [];
  const coverageMap = new Map();
  const fatalReasons = [];
  const stateSignals = [];

  for (const scope of contract.scopes) {
    try {
      const audit = fetchScopeIssues(scope);
      scopeAudits.push(Object.assign({}, audit.scope, {
        coverage: audit.coverage,
        coverageDigest: sourceListDigest(audit.coverage),
      }));
      for (const source of audit.coverage) coverageMap.set(referenceKey(source.repository, source.number), source);
    } catch (error) {
      if (error instanceof LiveSourceError) {
        fatalReasons.push(Object.assign({code: error.code, scopeId: scope.id}, error.details));
      } else {
        throw error;
      }
      scopeAudits.push({
        id: scope.id,
        repository: scope.repository,
        issueState: scope.issueState,
        labelsAll: scope.labelsAll.slice(),
        coverage: [],
        coverageDigest: sourceListDigest([]),
      });
    }
  }

  const coverage = Array.from(coverageMap.values()).sort((left, right) => referenceKey(left.repository, left.number).localeCompare(referenceKey(right.repository, right.number)));
  const coverageKeys = new Set(coverage.map((source) => referenceKey(source.repository, source.number)));
  const entryKeys = new Set(contract.entries.map((info) => info.sourceKey));
  const missing = coverage.filter((source) => !entryKeys.has(referenceKey(source.repository, source.number)));
  const extra = contract.entries
    .filter((info) => !coverageKeys.has(info.sourceKey))
    .map((info) => ({repository: info.entry.source.repository, number: info.entry.source.number}));
  if (missing.length > 0 || extra.length > 0) {
    fatalReasons.push({code: "coverage_mismatch", missing, extra});
  }

  const entries = [];
  let readyCount = 0;
  let blockedCount = 0;
  let unknownCount = 0;
  for (const info of contract.entries.slice().sort((left, right) => left.sourceKey.localeCompare(right.sourceKey))) {
    let observation = null;
    if (info.entry.state === "blocked") observation = evaluatePredicate(info.entry.blocker.releasePredicate, now);
    const output = outputEntry(info, observation);
    entries.push(output);
    if (output.state === "ready") readyCount += 1;
    if (output.state === "blocked") blockedCount += 1;
    if (output.state === "unknown") unknownCount += 1;
    if (info.entry.state === "unknown") fatalReasons.push({code: "unknown_entry", source: info.sourceKey});
    if (observation && observation.status === "false") stateSignals.push({code: "blocked_predicate_false", source: info.sourceKey});
    if (observation && observation.status === "true") stateSignals.push({code: "transition_required", source: info.sourceKey});
    if (observation && observation.status === "unknown") fatalReasons.push({code: "blocked_predicate_unknown", source: info.sourceKey, reason: observation.reason || "predicate_source_unavailable"});
  }

  const coverageDigest = sourceListDigest(coverage);
  const sortedReasons = sortReasons(fatalReasons.concat(stateSignals));
  let status = "complete";
  if (fatalReasons.length > 0) status = "unknown";
  else if (stateSignals.some((reason) => reason.code === "transition_required")) status = "transition_required";
  else if (readyCount === 0) status = "quiescent";
  const ready = status === "unknown" ? [] : entries.filter((entry) => entry.state === "ready").map((entry) => ({
    source: entry.source,
    ownerSeat: entry.ownerSeat,
    workKinds: entry.workKinds,
    revision: entry.revision,
    entryDigest: entry.entryDigest,
  }));
  const classificationBasis = {
    status,
    scopeCount: contract.scopes.length,
    coverageCount: coverage.length,
    entryCount: entries.length,
    readyCount: ready.length,
    blockedCount,
    unknownCount,
    reasons: sortedReasons,
    coverageDigest,
  };
  const sourceEvidence = {
    team,
    packDigest: contract.packDigest,
    scopeAudits,
    coverage,
  };
  const sourceDigest = sha256Digest(sourceEvidence);
  const output = {
    schemaVersion: 1,
    command,
    team,
    packDigest: contract.packDigest,
    coverageDigest,
    scopeAudits,
    coverage,
    entries,
    classificationBasis,
    sourceDigest,
    ready,
  };
  if (command === "g4-reconcile") output.findings = reconcileFindings(contract);
  output.auditDigest = sha256Digest(output);
  return output;
}

function main() {
  const [command, team, packPath, ...args] = process.argv.slice(2);
  if (!AUDIT_COMMANDS.has(command)) {
    process.stderr.write(`Error: unknown G4 audit command: ${command || ""}\n`);
    process.exitCode = 1;
    return;
  }
  if (args.length !== 0) throw new SchemaError(`invalid arguments for ${command}`);
  let packText;
  try {
    packText = fs.readFileSync(packPath, "utf8");
  } catch (_) {
    throw new SchemaError(`cannot read G4 state pack: ${packPath}`);
  }
  const pack = parseJson(packText, "G4 state pack is not valid JSON");
  const roster = parseJson(fs.readFileSync(0, "utf8"), "roster contract is not valid JSON");
  process.stdout.write(`${canonicalJson(runAudit(command, team, pack, roster))}\n`);
}

module.exports = {
  LiveSourceError,
  fetchScopeIssues,
  evaluatePredicate,
  runAudit,
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
