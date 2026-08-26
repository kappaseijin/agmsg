#!/usr/bin/env bats

load test_helper

setup() {
  setup_test_env
  export PROJ="$TEST_SKILL_DIR/proj"
  mkdir -p "$PROJ"
  bash "$SCRIPTS/join.sh" team alice codex "$PROJ" >/dev/null
  bash "$SCRIPTS/join.sh" team bob codex "$PROJ" >/dev/null
}

teardown() {
  teardown_test_env
}

install_unread_query_failure_stub() {
  cat >>"$SCRIPTS/drivers/storage/sqlite.sh" <<'EOF'
storage_list_unread() {
  printf '%s\n' 'stub unread storage failure' >&2
  return 13
}
EOF
}

install_unread_id_failure_stub() {
  export AGMSG_WATCH_ID_FAILURE_MARKER="$TEST_SKILL_DIR/id-query-started"
  cat >>"$SCRIPTS/drivers/storage/sqlite.sh" <<'EOF'
storage_list_unread() {
  printf '%s\n' '{"type":"message_sent","id":"stub-id","team":"team","from":"bob","to":"alice","body":"stub","at":"2026-08-26T00:00:00Z"}'
  : >"$AGMSG_WATCH_ID_FAILURE_MARKER"
}
EOF
  cat >>"$SCRIPTS/lib/storage.sh" <<'EOF'

agmsg_sqlite() {
  if [ -f "${AGMSG_WATCH_ID_FAILURE_MARKER:-}" ] && [ "${1-}" = ":memory:" ]; then
    printf '%s\n' 'stub unread id parse failure' >&2
    return 13
  fi
  sqlite3 "$@"
}
EOF
}

assert_output_absent() {
  local needle="$1"
  if printf '%s\n' "$output" | grep -Fq "$needle"; then
    printf 'unexpected output: %s\n' "$needle" >&2
    false
  fi
}

@test "watch-once: unread query failure is a concrete non-timeout error" {
  bash "$SCRIPTS/send.sh" team bob alice "pending for unread query" >/dev/null
  install_unread_query_failure_stub

  run bash "$TYPES/codex/watch-once.sh" "$PROJ" codex \
    --name alice --team team --timeout 1 --interval 1

  [ "$status" -eq 13 ]
  printf '%s\n' "$output" | grep -Fq 'watch-once: unread query failed team=team agent=alice exit=13'
  printf '%s\n' "$output" | grep -Fq 'stub unread storage failure'
  assert_output_absent 'status=timeout'
}

@test "watch-once: unread id query failure is a concrete non-timeout error" {
  bash "$SCRIPTS/send.sh" team bob alice "pending for id query" >/dev/null
  install_unread_id_failure_stub

  run bash "$TYPES/codex/watch-once.sh" "$PROJ" codex \
    --name alice --team team --timeout 1 --interval 1

  [ "$status" -eq 13 ]
  printf '%s\n' "$output" | grep -Fq 'watch-once: unread id query failed team=team agent=alice exit=13'
  printf '%s\n' "$output" | grep -Fq 'stub unread id parse failure'
  assert_output_absent 'status=timeout'
}

write_fake_bridge_driver_failure() {
  local fake="$TEST_SKILL_DIR/fake-app-server-watch-driver-failure.js"
  cat >"$fake" <<'EOF'
const readline = require("readline");
const rl = readline.createInterface({ input: process.stdin });

function send(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

rl.on("line", (line) => {
  const message = JSON.parse(line);
  if (message.method === "initialize") {
    send({ jsonrpc: "2.0", id: message.id, result: {} });
  } else if (message.method === "thread/start") {
    send({ jsonrpc: "2.0", id: message.id, result: { thread: { id: "thread-1", status: { type: "idle" } } } });
  } else if (message.method === "process/spawn") {
    send({ jsonrpc: "2.0", id: message.id, result: {} });
    setTimeout(() => {
      send({
        jsonrpc: "2.0",
        method: "process/exited",
        params: {
          processHandle: message.params.processHandle,
          exitCode: 13,
          stdout: "",
          stderr: "watch-once: unread query failed team=team agent=alice exit=13: stub unread storage failure",
        },
      });
    }, 10);
  } else if (message.method === "process/kill") {
    send({ jsonrpc: "2.0", id: message.id, result: {} });
  }
});
EOF
  printf '%s\n' "$fake"
}

@test "codex-bridge: driver error selects failure path instead of timeout rearm" {
  local fake
  fake="$(write_fake_bridge_driver_failure)"

  AGMSG_CODEX_APP_SERVER_CMD="node $fake" run node "$TYPES/codex/codex-bridge.js" \
    --project "$PROJ" --team team --name alice --timeout 1 --interval 1 \
    --request-timeout-ms 1000 --watch-failure-limit 1

  [ "$status" -eq 1 ]
  printf '%s\n' "$output" | grep -Fq 'watch-once failed with exit 13: watch-once: unread query failed team=team agent=alice exit=13: stub unread storage failure'
  printf '%s\n' "$output" | grep -Fq 'stopping after 1 consecutive watch-once failure'
  assert_output_absent 'status=timeout'
}
