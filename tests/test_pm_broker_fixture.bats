#!/usr/bin/env bats

# Issue #236 is intentionally tested as an isolated fixture. Nothing in this
# file installs a launcher, edits a hook, or changes a real PM session.

bats_require_minimum_version 1.5.0

FIXTURE="$BATS_TEST_DIRNAME/fixtures/pm-broker/broker.js"

setup() {
  export CASE_COUNTER=0
  export PROJECT_HEAD
  PROJECT_HEAD="$(git -C "$BATS_TEST_DIRNAME/.." rev-parse HEAD)"
  make_fixture
}

json_get() {
  JSON_VALUE="$1" JSON_PATH="$2" node <<'NODE'
const value = JSON.parse(process.env.JSON_VALUE);
let current = value;
for (const part of process.env.JSON_PATH.split('.').filter(Boolean)) {
  current = current[part];
}
if (typeof current === 'string' || typeof current === 'number' || typeof current === 'boolean') {
  process.stdout.write(String(current));
} else {
  process.stdout.write(JSON.stringify(current));
}
NODE
}

make_fixture() {
  CASE_COUNTER=$((CASE_COUNTER + 1))
  export ROOT="$BATS_TEST_TMPDIR/pm-broker-fixture-$BATS_TEST_NUMBER-$CASE_COUNTER"
  run node "$FIXTURE" init "$ROOT" "$PROJECT_HEAD"
  [ "$status" -eq 0 ] || { printf '%s\n' "$output" >&2; return 1; }
}

launch_pm() {
  run node "$FIXTURE" launch "$ROOT" pm "$@"
  [ "$status" -eq 0 ] || { printf '%s\n' "$output" >&2; return 1; }
  export PM_SESSION
  PM_SESSION="$(json_get "$output" sessionId)"
  export PM_HOOK_STATUS
  PM_HOOK_STATUS="$(json_get "$output" hookStatus)"
}

launch_actor() {
  local actor="$1"
  shift
  run node "$FIXTURE" launch "$ROOT" "$actor" "$@"
  [ "$status" -eq 0 ] || { printf '%s\n' "$output" >&2; return 1; }
  json_get "$output" sessionId
}

invoke_pm() {
  local operation="$1"
  local input=''
  if [ "$#" -ge 2 ]; then
    input="$2"
  fi
  run node "$FIXTURE" invoke "$ROOT" "$PM_SESSION" "$operation" "$input"
}

write_json_with_node() {
  local file="$1" script="$2"
  JSON_FILE="$file" node -e "$script"
}

marker_lines() {
  local file="$1"
  if [ -f "$file" ]; then
    wc -l < "$file" | tr -d '[:space:]'
  else
    printf '0\n'
  fi
}

@test "PM broker blocks the original three operations while a delegated owner can run them" {
  launch_pm

  for kind in fetch remote_branches pr_list; do
    invoke_pm work "{\"kind\":\"$kind\"}"
    [ "$status" -ne 0 ]
  done
  [ ! -e "$ROOT/markers/direct-work.jsonl" ]

  owner_session="$(launch_actor owner)"
  for kind in fetch remote_branches pr_list; do
    run node "$FIXTURE" invoke "$ROOT" "$owner_session" work "{\"kind\":\"$kind\"}"
    [ "$status" -eq 0 ]
  done
  [ "$(marker_lines "$ROOT/markers/direct-work.jsonl")" -eq 3 ]
}

@test "an executable stub is a positive execution control, not an absent-file refusal" {
  stub="$ROOT/harmless-stub"
  stub_marker="$ROOT/stub-marker"
  printf '#!/bin/sh\nprintf stub-ran > "$1"\n' > "$stub"
  chmod +x "$stub"

  run "$stub" "$stub_marker"
  [ "$status" -eq 0 ]
  [ "$(cat "$stub_marker")" = stub-ran ]

  run -127 "$ROOT/does-not-exist"
  [ "$status" -ne 0 ]

  launch_pm
  invoke_pm work '{"kind":"fetch","command":"harmless-stub"}'
  [ "$status" -ne 0 ]
  [ ! -e "$ROOT/markers/direct-work.jsonl" ]
}

@test "same cwd keeps owner and programmer capable while target PM is restricted" {
  launch_pm
  invoke_pm work '{"kind":"fetch"}'
  [ "$status" -ne 0 ]

  owner_session="$(launch_actor owner)"
  run node "$FIXTURE" invoke "$ROOT" "$owner_session" work '{"kind":"fetch"}'
  [ "$status" -eq 0 ]
  programmer_session="$(launch_actor programmer)"
  run node "$FIXTURE" invoke "$ROOT" "$programmer_session" work '{"kind":"pr_list"}'
  [ "$status" -eq 0 ]
  [ "$(marker_lines "$ROOT/markers/direct-work.jsonl")" -eq 2 ]
}

@test "identity anomalies fail closed: unclaimed, missing, multiple, stale, and fake agent" {
  for anomaly in unclaimed missing multiple stale; do
    make_fixture
    launch_pm
    case "$anomaly" in
      unclaimed)
        write_json_with_node "$ROOT/claims.json" \
          'require("fs").writeFileSync(process.env.JSON_FILE, "[]\n")'
        ;;
      missing)
        write_json_with_node "$ROOT/roster.json" \
          'const fs=require("fs"); const p=process.env.JSON_FILE; const r=JSON.parse(fs.readFileSync(p)); fs.writeFileSync(p, JSON.stringify(r.filter(x=>x.agent!=="pm"))+"\n")'
        ;;
      multiple)
        write_json_with_node "$ROOT/claims.json" \
          'const fs=require("fs"); const p=process.env.JSON_FILE; const a=JSON.parse(fs.readFileSync(p)); a.push({...a[0]}); fs.writeFileSync(p, JSON.stringify(a)+"\n")'
        ;;
      stale)
        write_json_with_node "$ROOT/claims.json" \
          'const fs=require("fs"); const p=process.env.JSON_FILE; const a=JSON.parse(fs.readFileSync(p)); a[0].generation="stale-generation"; fs.writeFileSync(p, JSON.stringify(a)+"\n")'
        ;;
    esac
    invoke_pm message '{"recipient":"owner","body":"identity probe"}'
    [ "$status" -ne 0 ]
  done

  make_fixture
  launch_pm
  run node "$FIXTURE" launch "$ROOT" pm --resume "$PM_SESSION"
  [ "$status" -ne 0 ]

  run node "$FIXTURE" invoke "$ROOT" "$PM_SESSION" work '{"kind":"fetch"}' --agent-arg owner
  [ "$status" -ne 0 ]
}

@test "alternate notation and alternate tools cannot reach a PM marker" {
  launch_pm
  for operation in absolute-path rtk shell-wrapper interpreter gui mcp child-agent; do
    invoke_pm "$operation" '{"command":"/bin/sh -c \"touch marker\"","path":"/bin/echo","tool":"'"$operation"'"}'
    [ "$status" -ne 0 ]
  done
  [ ! -e "$ROOT/markers/direct-work.jsonl" ]
}

@test "hook success and hook failure never change the PM capability boundary" {
  hook_ok="$ROOT/hook-ok"
  printf '#!/bin/sh\nprintf fired > "$AGMSG_PM_BROKER_ROOT/hook-fired"\n' > "$hook_ok"
  chmod +x "$hook_ok"

  launch_pm --hook "$hook_ok"
  [ "$PM_HOOK_STATUS" = completed ]
  [ "$(cat "$ROOT/hook-fired")" = fired ]
  run node "$FIXTURE" invoke "$ROOT" "$PM_SESSION" work '{"kind":"fetch"}'
  [ "$status" -ne 0 ]

  for mode in fail noexec timeout missing; do
    make_fixture
    hook="$ROOT/hook-$mode"
    case "$mode" in
      fail)
        printf '#!/bin/sh\nexit 17\n' > "$hook"
        chmod +x "$hook"
        expected_hook_status=failed-17
        ;;
      noexec)
        printf '#!/bin/sh\nexit 0\n' > "$hook"
        expected_hook_status=not-executable
        ;;
      timeout)
        printf '#!/bin/sh\nsleep 2\n' > "$hook"
        chmod +x "$hook"
        expected_hook_status=timeout
        ;;
      missing)
        hook="$ROOT/missing-hook"
        expected_hook_status=unavailable
        ;;
    esac
    launch_pm --hook "$hook"
    [ "$PM_HOOK_STATUS" = "$expected_hook_status" ]
    run node "$FIXTURE" invoke "$ROOT" "$PM_SESSION" work '{"kind":"fetch"}'
    [ "$status" -ne 0 ]
    [ ! -e "$ROOT/markers/direct-work.jsonl" ]
  done
}

@test "plugin, MCP, resume, broken config, unknown tools, and unknown load refuse capability growth" {
  write_json_with_node "$ROOT/config.json" \
    'const fs=require("fs"); const p=process.env.JSON_FILE; const c=JSON.parse(fs.readFileSync(p)); c.plugins=["fixture-plugin"]; c.mcp=["fixture-mcp"]; fs.writeFileSync(p, JSON.stringify(c)+"\n")'
  launch_pm
  invoke_pm work '{"kind":"fetch"}'
  [ "$status" -ne 0 ]

  run node "$FIXTURE" close "$ROOT" "$PM_SESSION"
  [ "$status" -eq 0 ]
  run node "$FIXTURE" launch "$ROOT" pm --resume "$PM_SESSION"
  [ "$status" -eq 0 ]
  PM_SESSION="$(json_get "$output" sessionId)"
  invoke_pm work '{"kind":"fetch"}'
  [ "$status" -ne 0 ]

  for mode in broken unknown-tool unknown-load; do
    make_fixture
    case "$mode" in
      broken)
        printf '{not-json\n' > "$ROOT/config.json"
        ;;
      unknown-tool)
        write_json_with_node "$ROOT/config.json" \
          'const fs=require("fs"); const p=process.env.JSON_FILE; const c=JSON.parse(fs.readFileSync(p)); c.tools=["broker","shell"]; fs.writeFileSync(p, JSON.stringify(c)+"\n")'
        ;;
      unknown-load)
        write_json_with_node "$ROOT/config.json" \
          'const fs=require("fs"); const p=process.env.JSON_FILE; const c=JSON.parse(fs.readFileSync(p)); c.loadResult="unknown"; fs.writeFileSync(p, JSON.stringify(c)+"\n")'
        ;;
    esac
    run node "$FIXTURE" launch "$ROOT" pm
    [ "$status" -ne 0 ]
    [ "$(find "$ROOT/bindings" -type f | wc -l | tr -d '[:space:]')" -eq 0 ]
  done
}

@test "PM normal broker operations pass and shell-like message text remains data" {
  launch_pm
  invoke_pm message '{"recipient":"owner","body":"git fetch; touch SHOULD_NOT_RUN"}'
  [ "$status" -eq 0 ]
  invoke_pm record '{"target":"notes","text":"recorded git fetch; touch SHOULD_NOT_RUN"}'
  [ "$status" -eq 0 ]
  invoke_pm issue_view '{"kind":"issue","number":236}'
  [ "$status" -eq 0 ]
  invoke_pm pr_view '{"kind":"pull_request","number":237}'
  [ "$status" -eq 0 ]
  invoke_pm seat_status '{"seat":"owner"}'
  [ "$status" -eq 0 ]
  invoke_pm seat_start '{"seat":"programmer","profile":"codex-worker"}'
  [ "$status" -eq 0 ]
  [ ! -e "$ROOT/SHOULD_NOT_RUN" ]
  grep -Fq 'git fetch; touch SHOULD_NOT_RUN' "$ROOT/records/messages.jsonl"
  grep -Fq 'recorded git fetch; touch SHOULD_NOT_RUN' "$ROOT/records/notes.md"

  outside="$BATS_TEST_TMPDIR/outside-notes"
  printf unchanged > "$outside"
  rm "$ROOT/records/notes.md"
  ln -s "$outside" "$ROOT/records/notes.md"
  invoke_pm record '{"target":"notes","text":"symlink escape"}'
  [ "$status" -ne 0 ]
  [ "$(cat "$outside")" = unchanged ]
}

@test "git maintenance accepts registered operations and rejects unsafe boundaries" {
  repo="$ROOT/repo"
  note="$repo/untracked-maintenance-note"
  printf keep > "$note"

  run git -C "$repo" checkout -b merged-feature
  [ "$status" -eq 0 ]
  printf merged > "$repo/merged.txt"
  run git -C "$repo" add merged.txt
  [ "$status" -eq 0 ]
  run git -C "$repo" -c user.email=fixture@example.invalid -c user.name=fixture commit -m merged
  [ "$status" -eq 0 ]
  run git -C "$repo" checkout main
  [ "$status" -eq 0 ]
  run git -C "$repo" merge --no-ff merged-feature -m merge-feature
  [ "$status" -eq 0 ]
  # Simulate the already-merged remote state without invoking the shared
  # push-owner guard: fetch the local commit into the isolated bare origin.
  run git --git-dir="$ROOT/origin.git" fetch "$repo" main:refs/heads/main
  [ "$status" -eq 0 ]

  launch_pm
  invoke_pm git_maintenance '{"repoId":"fixture-origin-clone","operation":"delete_merged_branch","branch":"merged-feature"}'
  [ "$status" -eq 0 ]
  run git -C "$repo" show-ref --verify --quiet refs/heads/merged-feature
  [ "$status" -ne 0 ]
  [ "$(cat "$note")" = keep ]

  run git -C "$repo" checkout -b unmerged-feature
  [ "$status" -eq 0 ]
  printf unmerged > "$repo/unmerged.txt"
  run git -C "$repo" add unmerged.txt
  [ "$status" -eq 0 ]
  run git -C "$repo" -c user.email=fixture@example.invalid -c user.name=fixture commit -m unmerged
  [ "$status" -eq 0 ]
  run git -C "$repo" checkout main
  [ "$status" -eq 0 ]
  invoke_pm git_maintenance '{"repoId":"fixture-origin-clone","operation":"delete_merged_branch","branch":"unmerged-feature"}'
  [ "$status" -ne 0 ]
  run git -C "$repo" show-ref --verify --quiet refs/heads/unmerged-feature
  [ "$status" -eq 0 ]

  printf dirty >> "$repo/README.fixture"
  invoke_pm git_maintenance '{"repoId":"fixture-origin-clone","operation":"sync_main"}'
  [ "$status" -ne 0 ]
  run git -C "$repo" checkout -- README.fixture
  [ "$status" -eq 0 ]
  invoke_pm git_maintenance '{"repoId":"fixture-origin-clone","operation":"sync_main","repoPath":"/tmp/replaced-repo"}'
  [ "$status" -ne 0 ]
  [ "$(cat "$note")" = keep ]

  invoke_pm git_maintenance '{"repoId":"fixture-origin-clone","operation":"fetch_prune"}'
  [ "$status" -eq 0 ]
  invoke_pm sync_origin_clone '{"repoId":"fixture-origin-clone"}'
  [ "$status" -eq 0 ]
  expected_head="$(git -C "$repo" rev-parse HEAD)"
  invoke_pm git_maintenance "{\"repoId\":\"fixture-origin-clone\",\"operation\":\"post_merge_verify\",\"expectedHead\":\"$expected_head\"}"
  [ "$status" -eq 0 ]
  invoke_pm git_maintenance '{"repoId":"fixture-origin-clone","operation":"force_push"}'
  [ "$status" -ne 0 ]

  worktree="$BATS_TEST_TMPDIR/pm-broker-worktree-$BATS_TEST_NUMBER"
  run git -C "$repo" worktree add -b review-worktree "$worktree"
  [ "$status" -eq 0 ]
  run git -C "$repo" worktree remove --force "$worktree"
  [ "$status" -eq 0 ]
  invoke_pm git_maintenance '{"repoId":"fixture-origin-clone","operation":"worktree_prune"}'
  [ "$status" -eq 0 ]
}

@test "proxy git writes require confirmed sandbox, digest, HEAD, and producer account" {
  launch_pm
  expected_head="$(git -C "$ROOT/repo" rev-parse HEAD)"
  valid='{"sandboxDigest":"sha256:fixture-sandbox-v1","diffDigest":"sha256:fixture-diff-v1","expectedHead":"'"$expected_head"'","account":"kappaseijin4codex","operation":"commit"}'
  invoke_pm proxy_git_write "$valid"
  [ "$status" -eq 0 ]
  for operation in push pull_request; do
    case "$operation" in
      push)
        input='{"sandboxDigest":"sha256:fixture-sandbox-v1","diffDigest":"sha256:fixture-diff-v1","expectedHead":"'"$expected_head"'","account":"kappaseijin4codex","operation":"push"}'
        ;;
      pull_request)
        input='{"sandboxDigest":"sha256:fixture-sandbox-v1","diffDigest":"sha256:fixture-diff-v1","expectedHead":"'"$expected_head"'","account":"kappaseijin4codex","operation":"pull_request"}'
        ;;
    esac
    invoke_pm proxy_git_write "$input"
    [ "$status" -eq 0 ]
  done
  [ "$(marker_lines "$ROOT/proxy/effects.jsonl")" -eq 3 ]

  for field in sandbox diff account head; do
    case "$field" in
      sandbox) input='{"sandboxDigest":"sha256:unknown","diffDigest":"sha256:fixture-diff-v1","expectedHead":"'"$expected_head"'","account":"kappaseijin4codex","operation":"commit"}' ;;
      diff) input='{"sandboxDigest":"sha256:fixture-sandbox-v1","diffDigest":"sha256:changed","expectedHead":"'"$expected_head"'","account":"kappaseijin4codex","operation":"commit"}' ;;
      account) input='{"sandboxDigest":"sha256:fixture-sandbox-v1","diffDigest":"sha256:fixture-diff-v1","expectedHead":"'"$expected_head"'","account":"thirdparty","operation":"commit"}' ;;
      head) input='{"sandboxDigest":"sha256:fixture-sandbox-v1","diffDigest":"sha256:fixture-diff-v1","expectedHead":"0000000","account":"kappaseijin4codex","operation":"commit"}' ;;
    esac
    invoke_pm proxy_git_write "$input"
    [ "$status" -ne 0 ]
  done
  [ "$(marker_lines "$ROOT/proxy/effects.jsonl")" -eq 3 ]
}

@test "team provision, decision application, and stale cleanup keep fixed targets" {
  launch_pm
  invoke_pm team_provision '{"decisionId":"decision-236","role":"worker","template":"codex-worker","pathId":"worker-clone"}'
  [ "$status" -eq 0 ]
  for file in AGENT.md config.toml registration.json layout.json; do
    [ -f "$ROOT/provision/worker/$file" ]
  done
  invoke_pm team_provision '{"decisionId":"decision-236","role":"worker","template":"codex-worker","pathId":"/tmp/arbitrary","patch":"chmod 777"}'
  [ "$status" -ne 0 ]
  [ ! -e "$ROOT/tmp/arbitrary" ]

  invoke_pm apply_decision '{"decisionId":"decision-236","patchDigest":"sha256:fixture-patch-v1","expectedPreimage":"sha256:empty","target":"rule","content":"approved rule"}'
  [ "$status" -eq 0 ]
  invoke_pm apply_decision '{"decisionId":"wrong","patchDigest":"sha256:fixture-patch-v1","expectedPreimage":"sha256:empty","target":"rule","content":"bad"}'
  [ "$status" -ne 0 ]
  invoke_pm apply_decision '{"decisionId":"decision-236","patchDigest":"sha256:fixture-patch-v1","expectedPreimage":"sha256:empty","target":"arbitrary","content":"bad"}'
  [ "$status" -ne 0 ]

  write_json_with_node "$ROOT/runtime.json" \
    'const fs=require("fs"); const p=process.env.JSON_FILE; const r=JSON.parse(fs.readFileSync(p)); r.active=true; fs.writeFileSync(p, JSON.stringify(r)+"\n")'
  invoke_pm stale_runtime_cleanup '{"seat":"worker","generation":"stale-generation","owner":"worker"}'
  [ "$status" -ne 0 ]
  [ -f "$ROOT/runtime/worker.pid" ]
  write_json_with_node "$ROOT/runtime.json" \
    'const fs=require("fs"); const p=process.env.JSON_FILE; const r=JSON.parse(fs.readFileSync(p)); r.active=false; fs.writeFileSync(p, JSON.stringify(r)+"\n")'
  invoke_pm stale_runtime_cleanup '{"seat":"worker","generation":"stale-generation","owner":"worker"}'
  [ "$status" -eq 0 ]
  [ ! -e "$ROOT/runtime/worker.pid" ]
  [ ! -e "$ROOT/runtime/worker.lock" ]
}

@test "bot collaborator fixture permits only owned private-repository operations" {
  launch_pm
  valid='{"botId":"kappaseijin4codex","owner":"kappaseijin","repo":"kappaseijin/private-fixture","visibility":"private","permission":"write","invitationId":"invite-236","operation":"add"}'
  invoke_pm bot_collaborator "$valid"
  [ "$status" -eq 0 ]
  accept='{"botId":"kappaseijin4codex","owner":"kappaseijin","repo":"kappaseijin/private-fixture","visibility":"private","permission":"write","invitationId":"invite-236","operation":"accept_invite"}'
  invoke_pm bot_collaborator "$accept"
  [ "$status" -eq 0 ]
  [ "$(node -pe 'JSON.parse(require("fs").readFileSync(process.argv[1])).writes' "$ROOT/api/fixture.json")" -eq 2 ]

  for bad in public thirdparty delete mismatched-invite; do
    case "$bad" in
      public) invalid='{"botId":"kappaseijin4codex","owner":"kappaseijin","repo":"kappaseijin/private-fixture","visibility":"public","permission":"write","invitationId":"invite-236","operation":"add"}' ;;
      thirdparty) invalid='{"botId":"thirdparty-bot","owner":"kappaseijin","repo":"kappaseijin/private-fixture","visibility":"private","permission":"write","invitationId":"invite-236","operation":"add"}' ;;
      delete) invalid='{"botId":"kappaseijin4codex","owner":"kappaseijin","repo":"kappaseijin/private-fixture","visibility":"private","permission":"write","invitationId":"invite-236","operation":"delete"}' ;;
      mismatched-invite) invalid='{"botId":"kappaseijin4codex","owner":"kappaseijin","repo":"kappaseijin/private-fixture","visibility":"private","permission":"write","invitationId":"invite-other","operation":"add"}' ;;
    esac
    invoke_pm bot_collaborator "$invalid"
    [ "$status" -ne 0 ]
  done
  [ "$(node -pe 'JSON.parse(require("fs").readFileSync(process.argv[1])).writes' "$ROOT/api/fixture.json")" -eq 2 ]
}

@test "maintenance fetch is distinct from general investigation fetch" {
  launch_pm
  invoke_pm git_maintenance '{"repoId":"fixture-origin-clone","operation":"fetch_prune"}'
  [ "$status" -eq 0 ]
  grep -Fq '"event":"maintenance.fetch_prune"' "$ROOT/markers/operations.jsonl"

  owner_session="$(launch_actor owner)"
  run node "$FIXTURE" invoke "$ROOT" "$owner_session" work '{"kind":"fetch"}'
  [ "$status" -eq 0 ]
  grep -Fq '"kind":"fetch"' "$ROOT/markers/direct-work.jsonl"
}

@test "non-target team PM keeps the ordinary launch contract" {
  launch_pm
  invoke_pm work '{"kind":"fetch"}'
  [ "$status" -ne 0 ]

  other_session="$(launch_actor other-pm)"
  run node "$FIXTURE" invoke "$ROOT" "$other_session" work '{"kind":"fetch"}'
  [ "$status" -eq 0 ]
  [ "$(marker_lines "$ROOT/markers/direct-work.jsonl")" -eq 1 ]
  run node "$FIXTURE" invoke "$ROOT" "$other_session" shell-wrapper '{"command":"touch marker"}'
  [ "$status" -ne 0 ]
}

@test "a timeout after one allowed operation is outcome_unknown and is not retried" {
  launch_pm
  request='{"requestId":"timeout-request","recipient":"owner","body":"one message","simulateTimeoutAfter":true}'
  invoke_pm message "$request"
  [ "$status" -eq 3 ]
  grep -Fq '"status":"outcome_unknown"' <<<"$output"
  [ "$(marker_lines "$ROOT/records/messages.jsonl")" -eq 1 ]

  invoke_pm message "$request"
  [ "$status" -eq 3 ]
  grep -Fq '"duplicate":true' <<<"$output"
  [ "$(marker_lines "$ROOT/records/messages.jsonl")" -eq 1 ]
}

@test "fixture packet records evidence fields and raw marker output" {
  launch_pm
  invoke_pm message '{"recipient":"owner","body":"packet probe"}'
  [ "$status" -eq 0 ]
  run node "$FIXTURE" packet "$ROOT"
  [ "$status" -eq 0 ]
  [ -n "$(json_get "$output" value.pmDirectMarkerCount)" ]
  [ "$(json_get "$output" value.pmDirectMarkerCount)" -eq 0 ]
  [ "$(json_get "$output" value.activeBinding)" = pm ]
  [ "$(json_get "$output" cutoff)" = "fixed-head:$PROJECT_HEAD" ]
  [ "$(json_get "$output" source)" = tests/fixtures/pm-broker/broker.js ]
  [ -n "$(json_get "$output" command)" ]
  [ -n "$(json_get "$output" cliVersion)" ]
  case "$(json_get "$output" launchProfileDigest)" in
    sha256:*) ;;
    *) return 1 ;;
  esac
  [ "$(json_get "$output" fixedHead)" = "$PROJECT_HEAD" ]
  [ "$(json_get "$output" effectiveTools)" = '["broker"]' ]
  [ "$(json_get "$output" rawMarkerOutput)" = '<empty>' ]
  [ "$(json_get "$output" hook.status)" = not-configured ]
}
