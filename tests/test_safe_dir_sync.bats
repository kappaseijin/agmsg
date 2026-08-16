#!/usr/bin/env bats

# scripts/lib/safe-dir-sync.sh — replaces a directory's files without
# truncating one a running process still has open (#16, #17, ADR-0005).

load test_helper

setup() {
  export WORK="$(mktemp -d)"
  export LIB="$BATS_TEST_DIRNAME/../scripts/lib/safe-dir-sync.sh"
  WATCHER_PID=""
  source "$LIB"
}

teardown() {
  if [ -n "${WATCHER_PID:-}" ]; then
    kill "$WATCHER_PID" 2>/dev/null || true
    wait "$WATCHER_PID" 2>/dev/null || true
  fi
  rm -rf "$WORK"
}

@test "safe_dir_sync: copies new files into an empty destination" {
  mkdir -p "$WORK/src"
  echo "content-a" > "$WORK/src/a.txt"
  mkdir -p "$WORK/src/sub"
  echo "content-b" > "$WORK/src/sub/b.txt"

  safe_dir_sync "$WORK/src" "$WORK/dest"

  [ "$(cat "$WORK/dest/a.txt")" = "content-a" ]
  [ "$(cat "$WORK/dest/sub/b.txt")" = "content-b" ]
}

@test "safe_dir_sync: replaces existing file content" {
  mkdir -p "$WORK/src" "$WORK/dest"
  echo "old" > "$WORK/dest/a.txt"
  echo "new" > "$WORK/src/a.txt"

  safe_dir_sync "$WORK/src" "$WORK/dest"

  [ "$(cat "$WORK/dest/a.txt")" = "new" ]
}

@test "safe_dir_sync: preserves a destination file absent from source (orphan survives)" {
  mkdir -p "$WORK/src" "$WORK/dest"
  echo "keep-me" > "$WORK/dest/only-in-dest.txt"
  echo "new" > "$WORK/src/a.txt"

  safe_dir_sync "$WORK/src" "$WORK/dest"

  [ -f "$WORK/dest/a.txt" ]
  [ "$(cat "$WORK/dest/only-in-dest.txt")" = "keep-me" ]
}

@test "safe_dir_sync: no staging directory left behind after a successful sync" {
  mkdir -p "$WORK/src"
  echo "x" > "$WORK/src/a.txt"

  safe_dir_sync "$WORK/src" "$WORK/dest"

  run find "$WORK/dest" -maxdepth 1 -name '.safe-dir-sync.*'
  [ -z "$output" ]
}

@test "safe_dir_sync: fails loudly when source is not a directory" {
  run safe_dir_sync "$WORK/does-not-exist" "$WORK/dest"
  [ "$status" -ne 0 ]
  [[ "$output" == *"source not a directory"* ]]
}

@test "negative control: cp -R truncates a running script's inode mid-execution (reproduces #16)" {
  # Establishes that the failure this fix targets is real, using the same
  # mechanism the fix avoids (a fresh watcher, then an in-place overwrite).
  mkdir -p "$WORK/dest" "$WORK/newsrc"
  {
    echo '#!/usr/bin/env bash'
    for i in $(seq 1 20); do echo "echo tick-$i"; echo "sleep 0.2"; done
    echo "echo ALL-TICKS-COMPLETE-V1"
  } > "$WORK/dest/watcher.sh"
  chmod +x "$WORK/dest/watcher.sh"

  bash "$WORK/dest/watcher.sh" > "$WORK/out.log" 2>&1 &
  local pid=$!
  sleep 1.4

  {
    echo '#!/usr/bin/env bash'
    for i in $(seq 1 8); do echo "echo tick-$i"; echo "sleep 0.2"; done
    echo "echo ALL-TICKS-COMPLETE-V2-SHORT"
  } > "$WORK/newsrc/watcher.sh"
  cp -R "$WORK/newsrc/." "$WORK/dest/"

  wait "$pid" || true

  run grep -c "ALL-TICKS-COMPLETE-V1" "$WORK/out.log"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "safe_dir_sync leaves a running script completely undisturbed (#16/#17 fix)" {
  mkdir -p "$WORK/dest" "$WORK/newsrc"
  {
    echo '#!/usr/bin/env bash'
    for i in $(seq 1 20); do echo "echo tick-$i"; echo "sleep 0.2"; done
    echo "echo ALL-TICKS-COMPLETE-V1"
  } > "$WORK/dest/watcher.sh"
  chmod +x "$WORK/dest/watcher.sh"

  bash "$WORK/dest/watcher.sh" > "$WORK/out.log" 2>&1 &
  local pid=$!
  sleep 1.4

  {
    echo '#!/usr/bin/env bash'
    for i in $(seq 1 8); do echo "echo tick-$i"; echo "sleep 0.2"; done
    echo "echo ALL-TICKS-COMPLETE-V2-SHORT"
  } > "$WORK/newsrc/watcher.sh"
  safe_dir_sync "$WORK/newsrc" "$WORK/dest"

  wait "$pid"
  local rc=$?

  [ "$rc" -eq 0 ]
  run grep -c "^tick-20$" "$WORK/out.log"
  [ "$output" -eq 1 ]
  run grep -c "ALL-TICKS-COMPLETE-V1" "$WORK/out.log"
  [ "$output" -eq 1 ]

  # The destination now serves the NEW content for the next invocation —
  # undisturbed for the running process, but genuinely updated on disk.
  run grep -c "ALL-TICKS-COMPLETE-V2-SHORT" "$WORK/dest/watcher.sh"
  [ "$output" -eq 1 ]
}

@test "install --update stops a live installed watcher after sync" {
  local project="$BATS_TEST_DIRNAME/.."
  local e2e_home="$WORK/e2e-home"
  local skill="$e2e_home/.agents/skills/agmsg"
  local ready="$skill/run/ready.e2e__receiver"
  local log="$WORK/watch.log"

  run env HOME="$e2e_home" bash "$project/install.sh" --cmd agmsg
  [ "$status" -eq 0 ]

  run env HOME="$e2e_home" bash "$skill/scripts/join.sh" e2e sender codex "$project"
  [ "$status" -eq 0 ]
  run env HOME="$e2e_home" bash "$skill/scripts/join.sh" e2e receiver codex "$project"
  [ "$status" -eq 0 ]

  env HOME="$e2e_home" AGMSG_WATCH_INTERVAL=1 \
    bash "$skill/scripts/watch.sh" e2e-session "$project" codex receiver >"$log" 2>&1 &
  WATCHER_PID=$!

  local ready_found=false
  for _ in $(seq 1 50); do
    if [ -f "$ready" ]; then
      ready_found=true
      break
    fi
    kill -0 "$WATCHER_PID" 2>/dev/null || break
    sleep 0.1
  done
  [ "$ready_found" = true ]

  run env HOME="$e2e_home" bash "$project/install.sh" --cmd agmsg --update
  [ "$status" -eq 0 ]
  local stopped=false
  for _ in $(seq 1 50); do
    if ! kill -0 "$WATCHER_PID" 2>/dev/null; then
      stopped=true
      break
    fi
    sleep 0.1
  done
  [ "$stopped" = true ]
  grep -q "installation was updated" "$log"
}
