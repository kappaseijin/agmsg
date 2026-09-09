#!/usr/bin/env bash
# Fixed, fail-closed P2 provider façade.  No generic resource dispatch.
set -euo pipefail

ACTION="${1:-}"; shift || true
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/lib/storage.sh"

json() { printf '{"schemaVersion":1,"state":"%s","messageId":"%s"}\n' "$1" "$2"; }
literal() { printf '%s' "${1//\'/\'\'}"; }
receipt_table() {
  local db="$1"
  agmsg_sqlite "$db" 'CREATE TABLE IF NOT EXISTS p2_handoff_receipts (receipt_id TEXT PRIMARY KEY, team TEXT NOT NULL, input_id TEXT NOT NULL, request_id TEXT NOT NULL, delegate_id TEXT NOT NULL, created_at TEXT NOT NULL);' >/dev/null
}
legacy_id() {
  local team="$1" id="$2" db
  agmsg_storage_load; db="$(agmsg_db_path "$team")"
  [[ "$id" =~ ^[0-9a-f-]{36}$ ]] || return 1
  agmsg_sqlite "$db" "SELECT legacy_id FROM events WHERE type='message_sent' AND team='${team//\'/\'\'}' AND id='$id' AND legacy_id IS NOT NULL;" | head -1
}

case "$ACTION" in
  message-send)
    [ "$#" -eq 5 ] || exit 2
    team="$1"; from="$2"; to="$3"; request="$4"; body="$5"
    out="$(bash "$DIR/send.sh" "$team" "$from" "$to" "$body")" || exit 1
    id="$(printf '%s\n' "$out" | sed -n 's/^Queued message #\([^ ]*\).*/\1/p')"
    [ -n "$id" ] || exit 1
    printf '{"schemaVersion":1,"state":"queued","messageId":"%s","requestId":"%s"}\n' "$id" "$request"
    ;;
  message-peek)
    [ "$#" -eq 2 ] || exit 2
    team="$1"; recipient="$2"
    agmsg_storage_load; db="$(agmsg_db_path "$team")"
    rows="$(agmsg_sqlite "$db" "SELECT json_object('schemaVersion',1,'state','ok','messageId',id,'from',from_agent,'to',to_agent,'body',body,'createdAt',at) FROM events WHERE type='message_sent' AND team='$(literal "$team")' AND to_agent='$(literal "$recipient")' ORDER BY seq;")" || exit 1
    if [ -n "$rows" ]; then printf '%s\n' "$rows"; else printf '{"schemaVersion":1,"state":"absent"}\n'; fi
    ;;
  message-claim|message-release)
    [ "$#" -eq 3 ] || exit 2
    team="$1"; id="$2"; owner="$3"; legacy="$(legacy_id "$team" "$id")" || exit 1
    [ -n "$legacy" ] || exit 1
    op="claim"; [ "$ACTION" = message-release ] && op="release"
    bash "$DIR/claim.sh" "$op" "$legacy" "$owner" || exit 1
    json "${op}ed" "$id"
    ;;
  handoff-receipt)
    [ "$#" -eq 4 ] || exit 2
    team="$1"; input="$2"; request="$3"; delegate="$4"
    input_legacy="$(legacy_id "$team" "$input")" || exit 1
    delegate_legacy="$(legacy_id "$team" "$delegate")" || exit 1
    [ -n "$input_legacy" ] && [ -n "$delegate_legacy" ] || exit 1
    agmsg_storage_load; db="$(agmsg_db_path "$team")"; receipt_table "$db"
    receipt="$(compat_uuid7)"
    agmsg_sqlite "$db" "INSERT INTO p2_handoff_receipts(receipt_id,team,input_id,request_id,delegate_id,created_at) VALUES ('$(literal "$receipt")','$(literal "$team")','$(literal "$input")','$(literal "$request")','$(literal "$delegate")',strftime('%Y-%m-%dT%H:%M:%SZ','now'));" >/dev/null || exit 1
    printf '{"schemaVersion":1,"state":"recorded","inputMessageId":"%s","requestId":"%s","delegateMessageId":"%s","receiptId":"%s"}\n' "$input" "$request" "$delegate" "$receipt"
    ;;
  message-ack)
    [ "$#" -eq 4 ] || exit 2
    team="$1"; id="$2"; owner="$3"; receipt="$4"
    legacy="$(legacy_id "$team" "$id")" || exit 1
    agmsg_storage_load; db="$(agmsg_db_path "$team")"; receipt_table "$db"
    found="$(agmsg_sqlite "$db" "SELECT 1 FROM p2_handoff_receipts WHERE receipt_id='$(literal "$receipt")' AND team='$(literal "$team")' AND input_id='$(literal "$id")';")"
    [ "$found" = 1 ] || exit 1
    bash "$DIR/claim.sh" ack "$legacy" "$owner" "$receipt" || exit 1
    json acked "$id"
    ;;
  *) exit 2 ;;
esac
