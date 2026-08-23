setup_g4_fixture() {
  export G4_FIXTURES="$BATS_TEST_DIRNAME/fixtures/g4-audit"
  export G4_FAKE_GH_BIN="$BATS_TEST_TMPDIR/g4-fake-gh-bin"
  export G4_GH_LOG="$BATS_TEST_TMPDIR/g4-gh-requests.jsonl"
  mkdir -p "$G4_FAKE_GH_BIN"
  cp "$G4_FIXTURES/gh" "$G4_FAKE_GH_BIN/gh"
  chmod +x "$G4_FAKE_GH_BIN/gh"
  : > "$G4_GH_LOG"
}

write_g4_pack() {
  local path="$1"
  local shape="$2"
  [ -n "$shape" ] || shape=two
  G4_PACK_PATH="$path" G4_PACK_SHAPE="$shape" G4_OWNER_SEAT="$G4_OWNER_SEAT" node <<'NODE'
const crypto = require("crypto");
const fs = require("fs");

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
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
function digest(value) {
  return "sha256:" + crypto.createHash("sha256")
    .update(JSON.stringify(canonicalize(value)), "utf8").digest("hex");
}
function makeScope() {
  const value = {
    id: "example-open-issues",
    repository: "kappaseijin/example",
    issueState: "OPEN",
    labelsAll: [],
  };
  value.basis = {
    contentDigest: digest(value),
    refs: [{kind: "git", repository: "kappaseijin/example",
      commit: "0123456789abcdef0123456789abcdef01234567"}],
  };
  return value;
}
function makeEntry(number, state) {
  const value = {
    schemaVersion: 1,
    source: {repository: "kappaseijin/example", number},
    state: state || "ready",
    ownerSeat: process.env.G4_OWNER_SEAT || "owner",
    workKinds: ["implementation"],
    basis: {
      contentDigest: "",
      refs: [{kind: "github_issue", repository: "kappaseijin/example", number}],
    },
    revision: 1,
  };
  const basisInput = Object.assign({}, value);
  delete basisInput.basis;
  value.basis.contentDigest = digest(basisInput);
  value.entryDigest = digest(value);
  return value;
}
const entries = [makeEntry(42), makeEntry(43)];
if (process.env.G4_PACK_SHAPE === "one") entries.splice(1);
if (process.env.G4_PACK_SHAPE === "empty") entries.splice(0);
fs.writeFileSync(process.env.G4_PACK_PATH, JSON.stringify({
  schemaVersion: 1,
  team: "demo",
  scopes: [makeScope()],
  entries,
}));
NODE
}

write_predicate_pack() {
  local path="$1"
  local kind="$2"
  write_g4_pack "$path" two
  G4_PACK_PATH="$path" G4_PREDICATE_KIND="$kind" node <<'NODE'
const crypto = require("crypto");
const fs = require("fs");
const pack = JSON.parse(fs.readFileSync(process.env.G4_PACK_PATH, "utf8"));
function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
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
function digest(value) {
  return "sha256:" + crypto.createHash("sha256")
    .update(JSON.stringify(canonicalize(value)), "utf8").digest("hex");
}
function refreshEntry(entry) {
  const basisInput = Object.assign({}, entry);
  delete basisInput.basis;
  delete basisInput.entryDigest;
  entry.basis.contentDigest = digest(basisInput);
  const entryInput = Object.assign({}, entry);
  delete entryInput.entryDigest;
  entry.entryDigest = digest(entryInput);
}
const predicates = {
  issue_closed: {kind: "issue_closed", repository: "kappaseijin/example", number: 42},
  pull_request_merged: {kind: "pull_request_merged", repository: "kappaseijin/example", number: 42},
  review_approved: {kind: "review_approved", repository: "kappaseijin/example",
    number: 42, headOid: "head-42"},
  issue_comment_digest: {kind: "issue_comment_digest", repository: "kappaseijin/example",
    number: 42, commentId: 7, contentDigest: digest("release comment")},
};
const predicate = predicates[process.env.G4_PREDICATE_KIND];
if (!predicate) throw new Error("unknown G4 predicate kind");
pack.entries[0].state = "blocked";
pack.entries[0].blocker = {
  reasonCode: process.env.G4_PREDICATE_KIND + "_gate",
  releasePredicate: predicate,
};
refreshEntry(pack.entries[0]);
fs.writeFileSync(process.env.G4_PACK_PATH, JSON.stringify(pack));
NODE
}

mutate_g4_pack() {
  local path="$1"
  local mutation="$2"
  G4_PACK_PATH="$path" G4_MUTATION="$mutation" node <<'NODE'
const crypto = require("crypto");
const fs = require("fs");
const pack = JSON.parse(fs.readFileSync(process.env.G4_PACK_PATH, "utf8"));
function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
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
function digest(value) {
  return "sha256:" + crypto.createHash("sha256")
    .update(JSON.stringify(canonicalize(value)), "utf8").digest("hex");
}
function refreshEntry(entry) {
  const basisInput = Object.assign({}, entry);
  delete basisInput.basis;
  delete basisInput.entryDigest;
  entry.basis.contentDigest = digest(basisInput);
  const entryInput = Object.assign({}, entry);
  delete entryInput.entryDigest;
  entry.entryDigest = digest(entryInput);
}
switch (process.env.G4_MUTATION) {
  case "ready-blocker":
    pack.entries[0].blocker = {reasonCode: "review",
      releasePredicate: {kind: "not_before", at: "2026-08-21T00:00:00+09:00"}};
    refreshEntry(pack.entries[0]);
    break;
  case "blocked-no-predicate":
    pack.entries[0].state = "blocked";
    pack.entries[0].blocker = {reasonCode: "upstream_issue"};
    refreshEntry(pack.entries[0]);
    break;
  case "blocked-predicate":
    pack.entries[0].state = "blocked";
    pack.entries[0].blocker = {
      reasonCode: "not_before_gate",
      releasePredicate: {kind: "not_before",
        at: process.env.G4_PREDICATE_AT || "2099-01-01T00:00:00+09:00"},
    };
    refreshEntry(pack.entries[0]);
    break;
  case "unknown-blocker":
    pack.entries[0].state = "unknown";
    pack.entries[0].blocker = {reasonCode: "observation",
      releasePredicate: {kind: "not_before", at: "2026-08-21T00:00:00+09:00"}};
    refreshEntry(pack.entries[0]);
    break;
  case "bad-scope-digest":
    pack.scopes[0].basis.contentDigest =
      "sha256:0000000000000000000000000000000000000000000000000000000000000000";
    break;
  case "quiescent-entry":
    pack.entries[0].state = "quiescent";
    refreshEntry(pack.entries[0]);
    break;
  case "invalid-ref":
    pack.scopes[0].basis.refs = [];
    break;
  default:
    throw new Error("unknown G4 mutation: " + process.env.G4_MUTATION);
}
fs.writeFileSync(process.env.G4_PACK_PATH, JSON.stringify(pack));
NODE
}

run_g4_audit() {
  local fixture="$1"
  local pack="$2"
  run env PATH="$G4_FAKE_GH_BIN:$PATH" G4_GH_FIXTURE="$fixture" G4_GH_LOG="$G4_GH_LOG" \
    bash "$SCRIPTS/team-work.sh" g4-audit demo "$pack"
}

json_value() {
  JSON_INPUT="$1" JSON_SELECTOR="$2" node -e '
let value = JSON.parse(process.env.JSON_INPUT);
for (const part of process.env.JSON_SELECTOR.split(".")) {
  if (!part) continue;
  const match = /^(.+)\[([0-9]+)\]$/.exec(part);
  value = match ? value[match[1]][Number(match[2])] : value[part];
}
process.stdout.write(typeof value === "string" ? value : JSON.stringify(value));
'
}

assert_canonical_json() {
  JSON_INPUT="$1" node -e '
const input = process.env.JSON_INPUT;
const value = JSON.parse(input);
function sort(item) {
  if (Array.isArray(item)) return item.map(sort);
  if (item && typeof item === "object") {
    const result = {};
    for (const key of Object.keys(item).sort()) result[key] = sort(item[key]);
    return result;
  }
  return item;
}
if (input !== JSON.stringify(sort(value))) process.exit(1);
'
}
