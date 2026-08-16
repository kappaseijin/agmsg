#!/usr/bin/env bash
# delivery-capability.sh — machine-readable, fail-closed delivery capability.
#
# This is sourced by delivery.sh after its shared helpers are loaded. It does
# not scrape human `delivery.sh status` output: the JSON contract observes the
# type-specific runtime artifacts directly and reuses message-status.sh's JSON
# receipt contract.

[ -n "${_AGMSG_DELIVERY_CAPABILITY_SH:-}" ] && return 0
_AGMSG_DELIVERY_CAPABILITY_SH=1

: "${SKILL_DIR:?delivery-capability.sh requires SKILL_DIR}"
: "${SCRIPT_DIR:?delivery-capability.sh requires SCRIPT_DIR}"
: "${RUN_DIR:?delivery-capability.sh requires RUN_DIR}"

# readiness paths and their owner-liveness check are shared with watch.sh.
# shellcheck disable=SC1091
. "$SKILL_DIR/scripts/lib/actas-lock.sh"
# Codex's role -> session binding is advisory state maintained by the bridge
# launcher / record-session helper. A bridge alone is intentionally insufficient.
# shellcheck disable=SC1091
. "$SKILL_DIR/scripts/lib/role-session.sh"

agmsg_delivery_capability_sql_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

agmsg_delivery_capability_json_quote() {
  local value="$1" sql_value
  sql_value="$(agmsg_delivery_capability_sql_escape "$value")"
  agmsg_sqlite_mem "SELECT json_quote('$sql_value');" 2>/dev/null
}

agmsg_delivery_capability_json_or_null() {
  if [ -n "$1" ]; then
    agmsg_delivery_capability_json_quote "$1"
  else
    printf 'null'
  fi
}

agmsg_delivery_capability_json_delivery_value() {
  case "$1" in
    true)  printf 'true' ;;
    false) printf 'false' ;;
    *)     printf '"unknown"' ;;
  esac
}

agmsg_delivery_capability_number_or_zero() {
  case "${1:-}" in
    ''|*[!0-9]*) printf '0' ;;
    *) printf '%s' "$1" ;;
  esac
}

agmsg_delivery_capability_evidence() {
  local source="$1" state="$2" reason="${3:-}" session="${4:-}"
  printf '{"source":%s,"state":%s' \
    "$(agmsg_delivery_capability_json_quote "$source")" \
    "$(agmsg_delivery_capability_json_quote "$state")"
  if [ -n "$reason" ]; then
    printf ',"reason":%s' "$(agmsg_delivery_capability_json_quote "$reason")"
  fi
  if [ -n "$session" ]; then
    printf ',"sessionId":%s' "$(agmsg_delivery_capability_json_quote "$session")"
  fi
  printf '}'
}

agmsg_delivery_capability_file_field() {
  local file="$1" key="$2" line
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      "$key"=*) printf '%s' "${line#"$key"=}"; return 0 ;;
    esac
  done < "$file" 2>/dev/null
  return 0
}

agmsg_delivery_capability_same_project() {
  local expected="$1" actual="$2" want have
  [ -n "$expected" ] && [ -n "$actual" ] || return 1
  want="$(agmsg_normalize_project_path "$(agmsg_canonical_path "$expected")")"
  have="$(agmsg_normalize_project_path "$(agmsg_canonical_path "$actual")")"
  [ "$want" = "$have" ]
}

# Returns the configuration evidence without parsing human status output.
# JSON event hook types retain their exact monitor/turn/both/off distinction;
# rule-file and other types only promise that a project-local integration file
# exists, because that is not a runtime liveness contract.
agmsg_delivery_capability_config_mode() {
  local type="$1" project="$2" hf has_ss=0 has_st=0 sql_hf
  hf="$(resolve_hooks_file "$type" "$project" 2>/dev/null || true)"
  [ -n "$hf" ] || { printf 'unknown'; return 0; }

  case "$type" in
    claude-code|codex)
      if [ -f "$hf" ]; then
        sql_hf="$(agmsg_sql_readfile_path "$hf")"
        has_ss="$(agmsg_sqlite_mem "
          SELECT EXISTS(
            SELECT 1 FROM json_each(json_extract(readfile('$sql_hf'), '\$.hooks.SessionStart')) AS s,
              json_each(json_extract(s.value, '\$.hooks')) AS h
            WHERE instr(json_extract(h.value, '\$.command'), '$SKILL_NAME') > 0
          );" 2>/dev/null || echo 0)"
        has_st="$(agmsg_sqlite_mem "
          SELECT EXISTS(
            SELECT 1 FROM json_each(json_extract(readfile('$sql_hf'), '\$.hooks.Stop')) AS s,
              json_each(json_extract(s.value, '\$.hooks')) AS h
            WHERE instr(json_extract(h.value, '\$.command'), '$SKILL_NAME') > 0
          );" 2>/dev/null || echo 0)"
      fi
      if [ "$has_ss" = "1" ] && [ "$has_st" = "1" ]; then
        printf 'both'
      elif [ "$has_ss" = "1" ]; then
        printf 'monitor'
      elif [ "$has_st" = "1" ]; then
        printf 'turn'
      else
        printf 'off'
      fi
      ;;
    hermes)
      printf 'off'
      ;;
    *)
      if [ -f "$hf" ]; then printf 'configured'; else printf 'off'; fi
      ;;
  esac
}

agmsg_delivery_capability_receipt_state() {
  local queued="$1" claimed="$2" handed="$3" unknown="$4" active=0 state="none"
  [ "$queued" -gt 0 ] && { active=$((active + 1)); state="queued"; }
  [ "$claimed" -gt 0 ] && { active=$((active + 1)); state="claimed"; }
  [ "$handed" -gt 0 ] && { active=$((active + 1)); state="handedOff"; }
  [ "$unknown" -gt 0 ] && { active=$((active + 1)); state="unknown"; }
  [ "$active" -le 1 ] && { printf '%s' "$state"; return 0; }
  printf 'mixed'
}

# Sets the AGMSG_CAP_RECEIPT_* globals and AGMSG_CAP_RECEIPT JSON object for a
# (team, agent). message-status.sh remains the single source of truth for the
# receipt database query and its ACK semantics.
agmsg_delivery_capability_receipt() {
  local team="$1" name="$2" payload sql_payload metrics state
  AGMSG_CAP_RECEIPT_QUEUED=0
  AGMSG_CAP_RECEIPT_CLAIMED=0
  AGMSG_CAP_RECEIPT_HANDED=0
  AGMSG_CAP_RECEIPT_UNKNOWN=0

  payload="$("$SCRIPT_DIR/message-status.sh" "$team" "$name" --format json 2>/dev/null || true)"
  if [ -n "$payload" ]; then
    sql_payload="$(agmsg_delivery_capability_sql_escape "$payload")"
    metrics="$(agmsg_sqlite_mem "
      SELECT COALESCE(json_extract('$sql_payload', '\$.queued'), 0)
          || char(9) || COALESCE(json_extract('$sql_payload', '\$.claimed'), 0)
          || char(9) || COALESCE(json_extract('$sql_payload', '\$.handedOff'), 0)
          || char(9) || COALESCE(json_extract('$sql_payload', '\$.unknown'), 0)
      WHERE json_valid('$sql_payload');" 2>/dev/null || true)"
    if [ -n "$metrics" ]; then
      # SQLite's CLI renders control character 31 as the two printable bytes
      # "^_", so it cannot be used as a shell record delimiter here. A tab is
      # emitted literally and cannot occur in the numeric metrics.
      IFS=$'\t' read -r AGMSG_CAP_RECEIPT_QUEUED AGMSG_CAP_RECEIPT_CLAIMED AGMSG_CAP_RECEIPT_HANDED AGMSG_CAP_RECEIPT_UNKNOWN <<< "$metrics"
    fi
  fi

  AGMSG_CAP_RECEIPT_QUEUED="$(agmsg_delivery_capability_number_or_zero "$AGMSG_CAP_RECEIPT_QUEUED")"
  AGMSG_CAP_RECEIPT_CLAIMED="$(agmsg_delivery_capability_number_or_zero "$AGMSG_CAP_RECEIPT_CLAIMED")"
  AGMSG_CAP_RECEIPT_HANDED="$(agmsg_delivery_capability_number_or_zero "$AGMSG_CAP_RECEIPT_HANDED")"
  AGMSG_CAP_RECEIPT_UNKNOWN="$(agmsg_delivery_capability_number_or_zero "$AGMSG_CAP_RECEIPT_UNKNOWN")"
  state="$(agmsg_delivery_capability_receipt_state "$AGMSG_CAP_RECEIPT_QUEUED" "$AGMSG_CAP_RECEIPT_CLAIMED" "$AGMSG_CAP_RECEIPT_HANDED" "$AGMSG_CAP_RECEIPT_UNKNOWN")"
  AGMSG_CAP_RECEIPT="{\"state\":$(agmsg_delivery_capability_json_quote "$state"),\"queued\":$AGMSG_CAP_RECEIPT_QUEUED,\"claimed\":$AGMSG_CAP_RECEIPT_CLAIMED,\"handedOff\":$AGMSG_CAP_RECEIPT_HANDED,\"unknown\":$AGMSG_CAP_RECEIPT_UNKNOWN,\"ackSemantics\":\"receiver_handoff_not_task_completion\"}"
}

# Sets AGMSG_CAP_WATCH_* for one Claude Code seat. A ready sentinel by itself
# is not enough: it must name a live owner AND the corresponding live watch.sh
# process. A broad watcher without a role-scoped sentinel is intentionally
# unknown rather than guessed deliverable.
agmsg_delivery_capability_claude_watcher() {
  local team="$1" name="$2" project="$3" ready sid pidfile pid cmd f found_live=0
  AGMSG_CAP_WATCH_RUNTIME="missing"
  AGMSG_CAP_WATCH_LIVENESS="missing"
  AGMSG_CAP_WATCH_DELIVERABLE="false"
  AGMSG_CAP_WATCH_SESSION=""
  AGMSG_CAP_WATCH_EVIDENCE=""

  ready="$(agmsg_ready_path "$team" "$name")"
  if [ -f "$ready" ]; then
    sid="$(head -1 "$ready" 2>/dev/null || true)"
    AGMSG_CAP_WATCH_SESSION="$sid"
    if [ -z "$sid" ]; then
      AGMSG_CAP_WATCH_RUNTIME="stale"
      AGMSG_CAP_WATCH_LIVENESS="stale"
      AGMSG_CAP_WATCH_EVIDENCE="$(agmsg_delivery_capability_evidence watcher stale empty_readiness_sentinel)"
      return 0
    fi
    if ! actas_lock_sid_alive "$sid"; then
      AGMSG_CAP_WATCH_RUNTIME="stale"
      AGMSG_CAP_WATCH_LIVENESS="stale"
      AGMSG_CAP_WATCH_EVIDENCE="$(agmsg_delivery_capability_evidence watcher stale readiness_owner_not_alive "$sid")"
      return 0
    fi
    pidfile="$RUN_DIR/watch.$sid.pid"
    if [ ! -f "$pidfile" ]; then
      AGMSG_CAP_WATCH_RUNTIME="stale"
      AGMSG_CAP_WATCH_LIVENESS="stale"
      AGMSG_CAP_WATCH_EVIDENCE="$(agmsg_delivery_capability_evidence watcher stale watcher_pidfile_missing "$sid")"
      return 0
    fi
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if ! _agmsg_pid_alive_local "$pid"; then
      AGMSG_CAP_WATCH_RUNTIME="stale"
      AGMSG_CAP_WATCH_LIVENESS="stale"
      AGMSG_CAP_WATCH_EVIDENCE="$(agmsg_delivery_capability_evidence watcher stale watcher_pid_not_alive "$sid")"
      return 0
    fi
    cmd="$(compat_get_cmdline "$pid" 2>/dev/null || true)"
    if [ -z "$cmd" ]; then
      AGMSG_CAP_WATCH_RUNTIME="unknown"
      AGMSG_CAP_WATCH_LIVENESS="unknown"
      AGMSG_CAP_WATCH_DELIVERABLE="unknown"
      AGMSG_CAP_WATCH_EVIDENCE="$(agmsg_delivery_capability_evidence watcher unknown watcher_command_unobservable "$sid")"
      return 0
    fi
    case "$cmd" in
      *"$SKILL_DIR/scripts/watch.sh"*"$project"*claude-code*"$name"*)
        AGMSG_CAP_WATCH_RUNTIME="alive"
        AGMSG_CAP_WATCH_LIVENESS="alive"
        AGMSG_CAP_WATCH_DELIVERABLE="true"
        AGMSG_CAP_WATCH_EVIDENCE="$(agmsg_delivery_capability_evidence watcher alive '' "$sid")"
        return 0
        ;;
      *)
        AGMSG_CAP_WATCH_RUNTIME="stale"
        AGMSG_CAP_WATCH_LIVENESS="stale"
        AGMSG_CAP_WATCH_EVIDENCE="$(agmsg_delivery_capability_evidence watcher stale watcher_command_mismatch "$sid")"
        return 0
        ;;
    esac
  fi

  # A live broad watcher can receive this role, but does not prove that it did
  # not defer to another exclusive owner. Keep the safe answer unknown.
  for f in "$RUN_DIR"/watch.*.pid; do
    [ -f "$f" ] || continue
    pid="$(cat "$f" 2>/dev/null || true)"
    _agmsg_pid_alive_local "$pid" || continue
    cmd="$(compat_get_cmdline "$pid" 2>/dev/null || true)"
    case "$cmd" in
      *"$SKILL_DIR/scripts/watch.sh"*"$project"*claude-code*) found_live=1 ;;
    esac
  done
  if [ "$found_live" -eq 1 ]; then
    AGMSG_CAP_WATCH_RUNTIME="unknown"
    AGMSG_CAP_WATCH_LIVENESS="unknown"
    AGMSG_CAP_WATCH_DELIVERABLE="unknown"
    AGMSG_CAP_WATCH_EVIDENCE="$(agmsg_delivery_capability_evidence watcher unknown role_binding_unobservable)"
  else
    AGMSG_CAP_WATCH_EVIDENCE="$(agmsg_delivery_capability_evidence watcher missing no_role_scoped_watcher)"
  fi
}

# Sets AGMSG_CAP_CODEX_* for one Codex seat. The bridge's pid/meta prove that
# a process is alive for this role; role-session proves which conversational
# session it belongs to. Neither half can establish delivery by itself.
agmsg_delivery_capability_codex_bridge() {
  local team="$1" name="$2" project="$3" base pidfile metafile pid
  local meta_pid meta_project meta_type meta_team meta_name record_type binding=0
  AGMSG_CAP_CODEX_RUNTIME="unknown"
  AGMSG_CAP_CODEX_LIVENESS="unknown"
  AGMSG_CAP_CODEX_DELIVERABLE="unknown"
  AGMSG_CAP_CODEX_SESSION=""
  AGMSG_CAP_CODEX_EVIDENCE=""

  agmsg_role_session_load "$team" "$name"
  AGMSG_CAP_CODEX_SESSION="$AGMSG_ROLE_SESSION_UUID"
  record_type="$(agmsg_role_session_get "$team" "$name" type 2>/dev/null || true)"
  if [ -n "$AGMSG_ROLE_SESSION_UUID" ] \
      && [ "$record_type" = "codex" ] \
      && agmsg_delivery_capability_same_project "$project" "$AGMSG_ROLE_SESSION_PROJECT"; then
    binding=1
  else
    # A stale / cross-project record is evidence of neither the current
    # session nor delivery. Do not expose it as a confirmed sessionId.
    AGMSG_CAP_CODEX_SESSION=""
  fi

  base="$RUN_DIR/codex-bridge.$team.$name"
  pidfile="$base.pid"
  metafile="$base.meta"
  if [ ! -f "$pidfile" ]; then
    if [ "$binding" -eq 1 ]; then
      AGMSG_CAP_CODEX_RUNTIME="missing"
      AGMSG_CAP_CODEX_LIVENESS="missing"
      AGMSG_CAP_CODEX_DELIVERABLE="false"
      AGMSG_CAP_CODEX_EVIDENCE="$(agmsg_delivery_capability_evidence role_session alive '' "$AGMSG_CAP_CODEX_SESSION"),$(agmsg_delivery_capability_evidence bridge missing bridge_pidfile_missing)"
    else
      AGMSG_CAP_CODEX_EVIDENCE="$(agmsg_delivery_capability_evidence role_session unknown session_binding_missing),$(agmsg_delivery_capability_evidence bridge unknown bridge_unobservable_without_session)"
    fi
    return 0
  fi

  pid="$(cat "$pidfile" 2>/dev/null || true)"
  if [ -z "$pid" ]; then
    AGMSG_CAP_CODEX_RUNTIME="stale"
    AGMSG_CAP_CODEX_LIVENESS="stale"
    AGMSG_CAP_CODEX_DELIVERABLE="false"
    AGMSG_CAP_CODEX_EVIDENCE="$(agmsg_delivery_capability_evidence bridge stale empty_pidfile)"
    return 0
  fi
  if [ ! -f "$metafile" ]; then
    AGMSG_CAP_CODEX_RUNTIME="stale"
    AGMSG_CAP_CODEX_LIVENESS="stale"
    AGMSG_CAP_CODEX_DELIVERABLE="false"
    AGMSG_CAP_CODEX_EVIDENCE="$(agmsg_delivery_capability_evidence bridge stale bridge_metadata_missing)"
    return 0
  fi

  meta_pid="$(agmsg_delivery_capability_file_field "$metafile" pid)"
  meta_project="$(agmsg_delivery_capability_file_field "$metafile" project)"
  meta_type="$(agmsg_delivery_capability_file_field "$metafile" type)"
  meta_team="$(agmsg_delivery_capability_file_field "$metafile" team)"
  meta_name="$(agmsg_delivery_capability_file_field "$metafile" name)"
  if [ "$meta_pid" != "$pid" ] \
      || ! agmsg_delivery_capability_same_project "$project" "$meta_project" \
      || [ "$meta_type" != "codex" ] \
      || [ "$meta_team" != "$team" ] \
      || [ "$meta_name" != "$name" ]; then
    AGMSG_CAP_CODEX_RUNTIME="stale"
    AGMSG_CAP_CODEX_LIVENESS="stale"
    AGMSG_CAP_CODEX_DELIVERABLE="false"
    AGMSG_CAP_CODEX_EVIDENCE="$(agmsg_delivery_capability_evidence bridge stale metadata_mismatch)"
    return 0
  fi
  if ! _agmsg_pid_alive "$pid"; then
    AGMSG_CAP_CODEX_RUNTIME="stale"
    AGMSG_CAP_CODEX_LIVENESS="stale"
    AGMSG_CAP_CODEX_DELIVERABLE="false"
    AGMSG_CAP_CODEX_EVIDENCE="$(agmsg_delivery_capability_evidence bridge stale bridge_pid_not_alive)"
    return 0
  fi
  if [ "$binding" -eq 1 ]; then
    AGMSG_CAP_CODEX_RUNTIME="alive"
    AGMSG_CAP_CODEX_LIVENESS="alive"
    AGMSG_CAP_CODEX_DELIVERABLE="true"
    AGMSG_CAP_CODEX_EVIDENCE="$(agmsg_delivery_capability_evidence role_session alive '' "$AGMSG_CAP_CODEX_SESSION"),$(agmsg_delivery_capability_evidence bridge alive '' "$AGMSG_CAP_CODEX_SESSION")"
  else
    AGMSG_CAP_CODEX_EVIDENCE="$(agmsg_delivery_capability_evidence role_session unknown session_binding_missing),$(agmsg_delivery_capability_evidence bridge alive bridge_not_bound_to_role)"
  fi
}

# Builds one seat and sets AGMSG_CAP_SEAT_* globals. The caller supplies the
# already-derived configuration mode so all seats share the same configuration
# evidence but get independent runtime/receipt evidence.
agmsg_delivery_capability_seat() {
  local type="$1" project="$2" mode="$3" team="$4" name="$5"
  local runtime liveness deliverable session evidence receipt
  agmsg_delivery_capability_receipt "$team" "$name"

  case "$type" in
    claude-code)
      if [ "$mode" != "monitor" ] && [ "$mode" != "both" ]; then
        runtime="missing"; liveness="missing"; deliverable="false"; session=""
        evidence="$(agmsg_delivery_capability_evidence configuration "$mode" no_live_monitor_runtime)"
      else
        agmsg_delivery_capability_claude_watcher "$team" "$name" "$project"
        runtime="$AGMSG_CAP_WATCH_RUNTIME"
        liveness="$AGMSG_CAP_WATCH_LIVENESS"
        deliverable="$AGMSG_CAP_WATCH_DELIVERABLE"
        session="$AGMSG_CAP_WATCH_SESSION"
        evidence="$(agmsg_delivery_capability_evidence configuration "$mode"),$AGMSG_CAP_WATCH_EVIDENCE"
      fi
      ;;
    codex)
      if [ "$mode" != "monitor" ]; then
        runtime="missing"; liveness="missing"; deliverable="false"; session=""
        evidence="$(agmsg_delivery_capability_evidence configuration "$mode" no_live_monitor_runtime)"
      else
        agmsg_delivery_capability_codex_bridge "$team" "$name" "$project"
        runtime="$AGMSG_CAP_CODEX_RUNTIME"
        liveness="$AGMSG_CAP_CODEX_LIVENESS"
        deliverable="$AGMSG_CAP_CODEX_DELIVERABLE"
        session="$AGMSG_CAP_CODEX_SESSION"
        evidence="$(agmsg_delivery_capability_evidence configuration "$mode"),$AGMSG_CAP_CODEX_EVIDENCE"
      fi
      ;;
    *)
      if [ "$mode" = "off" ]; then
        runtime="missing"; liveness="missing"; deliverable="false"; session=""
        evidence="$(agmsg_delivery_capability_evidence configuration off runtime_disabled)"
      else
        runtime="unknown"; liveness="unknown"; deliverable="unknown"; session=""
        evidence="$(agmsg_delivery_capability_evidence runtime unknown runtime_unobservable),$(agmsg_delivery_capability_evidence configuration "$mode")"
      fi
      ;;
  esac

  receipt="$AGMSG_CAP_RECEIPT"
  AGMSG_CAP_SEAT_RUNTIME="$runtime"
  AGMSG_CAP_SEAT_LIVENESS="$liveness"
  AGMSG_CAP_SEAT_DELIVERABLE="$deliverable"
  AGMSG_CAP_SEAT_SESSION="$session"
  AGMSG_CAP_SEAT_EVIDENCE="[$evidence]"
  AGMSG_CAP_SEAT_JSON="{\"team\":$(agmsg_delivery_capability_json_quote "$team"),\"name\":$(agmsg_delivery_capability_json_quote "$name"),\"runtime\":$(agmsg_delivery_capability_json_quote "$runtime"),\"sessionId\":$(agmsg_delivery_capability_json_or_null "$session"),\"deliverable\":$(agmsg_delivery_capability_json_delivery_value "$deliverable"),\"liveness\":$(agmsg_delivery_capability_json_quote "$liveness"),\"receipt\":$receipt,\"evidence\":$AGMSG_CAP_SEAT_EVIDENCE}"
}

agmsg_delivery_capability_aggregate_receipt() {
  local queued="$1" claimed="$2" handed="$3" unknown="$4" state
  state="$(agmsg_delivery_capability_receipt_state "$queued" "$claimed" "$handed" "$unknown")"
  printf '{"state":%s,"queued":%s,"claimed":%s,"handedOff":%s,"unknown":%s,"ackSemantics":"receiver_handoff_not_task_completion"}' \
    "$(agmsg_delivery_capability_json_quote "$state")" "$queued" "$claimed" "$handed" "$unknown"
}

agmsg_delivery_capability_same_or_mixed() {
  local first="$1"; shift
  local value
  for value in "$@"; do
    [ "$value" = "$first" ] || { printf 'mixed'; return 0; }
  done
  printf '%s' "$first"
}

agmsg_delivery_capability_aggregate_delivery() {
  local first="$1"; shift
  local value all_true=1 all_false=1
  for value in "$first" "$@"; do
    [ "$value" = "true" ] || all_true=0
    [ "$value" = "false" ] || all_false=0
  done
  [ "$all_true" -eq 1 ] && { printf 'true'; return 0; }
  [ "$all_false" -eq 1 ] && { printf 'false'; return 0; }
  printf 'unknown'
}

# Public entry point. Emits exactly one JSON document on stdout on success.
agmsg_delivery_capability_json() {
  local type="$1" project="$2" tdir pairs mode team name
  local seat_count=0 sep="" seats_json="["
  local runtimes=() livenesses=() deliverables=() sessions=() evidences=()
  local queued=0 claimed=0 handed=0 unknown=0 runtime liveness deliverable session evidence receipt

  tdir="$(agmsg_type_dir "$type" 2>/dev/null || true)"
  if [ -z "$tdir" ]; then
    echo "delivery.sh: unknown agent type '$type'." >&2
    return 2
  fi

  mode="$(agmsg_delivery_capability_config_mode "$type" "$project")"
  # identities.sh orders within a team config, but the config-file enumeration
  # is not itself a public ordering contract. Stabilize the JSON seats here.
  pairs="$("$SCRIPT_DIR/identities.sh" "$project" "$type" 2>/dev/null | LC_ALL=C sort -t $'\t' -k1,1 -k2,2 || true)"
  if [ -n "$pairs" ]; then
    while IFS=$'\t' read -r team name _rest; do
      [ -n "$team" ] && [ -n "$name" ] || continue
      agmsg_delivery_capability_seat "$type" "$project" "$mode" "$team" "$name"
      seats_json="${seats_json}${sep}${AGMSG_CAP_SEAT_JSON}"
      sep=","
      runtimes[seat_count]="$AGMSG_CAP_SEAT_RUNTIME"
      livenesses[seat_count]="$AGMSG_CAP_SEAT_LIVENESS"
      deliverables[seat_count]="$AGMSG_CAP_SEAT_DELIVERABLE"
      sessions[seat_count]="$AGMSG_CAP_SEAT_SESSION"
      evidences[seat_count]="$AGMSG_CAP_SEAT_EVIDENCE"
      queued=$((queued + AGMSG_CAP_RECEIPT_QUEUED))
      claimed=$((claimed + AGMSG_CAP_RECEIPT_CLAIMED))
      handed=$((handed + AGMSG_CAP_RECEIPT_HANDED))
      unknown=$((unknown + AGMSG_CAP_RECEIPT_UNKNOWN))
      seat_count=$((seat_count + 1))
    done <<< "$pairs"
  fi
  seats_json="${seats_json}]"

  if [ "$seat_count" -eq 0 ]; then
    runtime="missing"
    liveness="missing"
    deliverable="false"
    session=""
    evidence="[$(agmsg_delivery_capability_evidence registration missing no_registered_seat)]"
  elif [ "$seat_count" -eq 1 ]; then
    runtime="${runtimes[0]}"
    liveness="${livenesses[0]}"
    deliverable="${deliverables[0]}"
    session="${sessions[0]}"
    evidence="${evidences[0]}"
  else
    runtime="$(agmsg_delivery_capability_same_or_mixed "${runtimes[0]}" "${runtimes[@]:1}")"
    liveness="$(agmsg_delivery_capability_same_or_mixed "${livenesses[0]}" "${livenesses[@]:1}")"
    deliverable="$(agmsg_delivery_capability_aggregate_delivery "${deliverables[0]}" "${deliverables[@]:1}")"
    session=""
    evidence="[$(agmsg_delivery_capability_evidence aggregate "$runtime" multiple_seats)]"
  fi
  receipt="$(agmsg_delivery_capability_aggregate_receipt "$queued" "$claimed" "$handed" "$unknown")"

  printf '{"schemaVersion":1,"type":%s,"project":%s,"runtime":%s,"sessionId":%s,"deliverable":%s,"liveness":%s,"receipt":%s,"evidence":%s,"seats":%s}\n' \
    "$(agmsg_delivery_capability_json_quote "$type")" \
    "$(agmsg_delivery_capability_json_quote "$project")" \
    "$(agmsg_delivery_capability_json_quote "$runtime")" \
    "$(agmsg_delivery_capability_json_or_null "$session")" \
    "$(agmsg_delivery_capability_json_delivery_value "$deliverable")" \
    "$(agmsg_delivery_capability_json_quote "$liveness")" \
    "$receipt" "$evidence" "$seats_json"
}
