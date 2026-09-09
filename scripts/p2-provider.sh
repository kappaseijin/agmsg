#!/usr/bin/env bash
# Fixed, fail-closed P2 provider façade. No generic resource dispatch.
set -euo pipefail

ACTION="${1:-}"
shift || true

DIR="$(cd "$(dirname "$0")" && pwd)"

# shellcheck disable=SC1091
source "$DIR/lib/storage.sh"

literal() {
  printf '%s' "${1//\'/\'\'}"
}

claim_json() {
  local state="$1"
  local message_id="$2"
  local owner="$3"

  printf \
    '{"schemaVersion":1,"state":"%s","messageId":"%s","owner":"%s"}\n' \
    "$state" \
    "$message_id" \
    "$owner"
}

ack_json() {
  local message_id="$1"
  local owner="$2"
  local receipt_id="$3"

  printf \
    '{"schemaVersion":1,"state":"acked","messageId":"%s","owner":"%s","receiptId":"%s"}\n' \
    "$message_id" \
    "$owner" \
    "$receipt_id"
}

receipt_table() {
  local db="$1"

  agmsg_sqlite "$db" '
    CREATE TABLE IF NOT EXISTS p2_handoff_receipts (
      receipt_id TEXT PRIMARY KEY,
      team TEXT NOT NULL,
      input_id TEXT NOT NULL,
      request_id TEXT NOT NULL,
      delegate_id TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
  ' >/dev/null
}

legacy_id() {
  local team="$1"
  local id="$2"
  local db
  local result

  printf '%s\n' "$id" | grep -Eq '^[0-9a-f-]{36}$' || return 1

  agmsg_storage_load
  db="$(agmsg_db_path "$team")"

  result="$(
    agmsg_sqlite "$db" "
      SELECT legacy_id
      FROM events
      WHERE type='message_sent'
        AND team='$(literal "$team")'
        AND id='$(literal "$id")'
        AND legacy_id IS NOT NULL
      LIMIT 1;
    "
  )" || return 1

  [ -n "$result" ] || return 1
  printf '%s\n' "$result"
}

case "$ACTION" in
  message-send)
    [ "$#" -eq 5 ] || exit 2

    team="$1"
    from="$2"
    to="$3"
    request="$4"
    body="$5"

    out="$(
      bash "$DIR/send.sh" \
        "$team" \
        "$from" \
        "$to" \
        "$body"
    )" || exit 1

    id="$(
      printf '%s\n' "$out" |
        sed -n 's/^Queued message #\([^ ]*\).*/\1/p'
    )"

    [ -n "$id" ] || exit 1

    printf \
      '{"schemaVersion":1,"state":"queued","messageId":"%s","requestId":"%s","team":"%s","from":"%s","to":"%s"}\n' \
      "$id" \
      "$request" \
      "$team" \
      "$from" \
      "$to"
    ;;

  message-peek)
    [ "$#" -eq 2 ] || exit 2

    team="$1"
    recipient="$2"

    agmsg_storage_load
    db="$(agmsg_db_path "$team")"

    row="$(
      agmsg_sqlite "$db" "
        SELECT json_object(
          'schemaVersion', 1,
          'state', 'ok',
          'messageId', e.id,
          'from', e.from_agent,
          'to', e.to_agent,
          'body', e.body,
          'createdAt', e.at
        )
        FROM events AS e
        JOIN messages AS m
          ON m.id = e.legacy_id
        WHERE e.type = 'message_sent'
          AND e.team = '$(literal "$team")'
          AND e.to_agent = '$(literal "$recipient")'
          AND e.legacy_id IS NOT NULL
          AND m.read_at IS NULL
        ORDER BY e.seq
        LIMIT 1;
      "
    )" || exit 1

    if [ -n "$row" ]; then
      printf '%s\n' "$row"
    else
      printf '{"schemaVersion":1,"state":"absent"}\n'
    fi
    ;;

  message-claim)
    [ "$#" -eq 3 ] || exit 2

    team="$1"
    id="$2"
    owner="$3"

    legacy="$(legacy_id "$team" "$id")" || exit 1
    [ -n "$legacy" ] || exit 1

    AGMSG_CLAIM_TEAM="$team" \
      bash "$DIR/claim.sh" claim "$legacy" "$owner" || exit 1

    claim_json "claimed" "$id" "$owner"
    ;;

  message-release)
    [ "$#" -eq 3 ] || exit 2

    team="$1"
    id="$2"
    owner="$3"

    legacy="$(legacy_id "$team" "$id")" || exit 1
    [ -n "$legacy" ] || exit 1

    AGMSG_CLAIM_TEAM="$team" \
      bash "$DIR/claim.sh" release "$legacy" "$owner" || exit 1

    claim_json "released" "$id" "$owner"
    ;;

  handoff-receipt)
    [ "$#" -eq 4 ] || exit 2

    team="$1"
    input="$2"
    request="$3"
    delegate="$4"

    input_legacy="$(legacy_id "$team" "$input")" || exit 1
    delegate_legacy="$(legacy_id "$team" "$delegate")" || exit 1

    [ -n "$input_legacy" ] || exit 1
    [ -n "$delegate_legacy" ] || exit 1

    agmsg_storage_load
    db="$(agmsg_db_path "$team")"

    receipt_table "$db"

    receipt="$(compat_uuid7)"
    [ -n "$receipt" ] || exit 1

    agmsg_sqlite "$db" "
      INSERT INTO p2_handoff_receipts(
        receipt_id,
        team,
        input_id,
        request_id,
        delegate_id,
        created_at
      )
      VALUES (
        '$(literal "$receipt")',
        '$(literal "$team")',
        '$(literal "$input")',
        '$(literal "$request")',
        '$(literal "$delegate")',
        strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
      );
    " >/dev/null || exit 1

    printf \
      '{"schemaVersion":1,"state":"recorded","inputMessageId":"%s","requestId":"%s","delegateMessageId":"%s","receiptId":"%s","team":"%s"}\n' \
      "$input" \
      "$request" \
      "$delegate" \
      "$receipt" \
      "$team"
    ;;

  message-ack)
    [ "$#" -eq 4 ] || exit 2

    team="$1"
    id="$2"
    owner="$3"
    receipt="$4"

    legacy="$(legacy_id "$team" "$id")" || exit 1
    [ -n "$legacy" ] || exit 1

    agmsg_storage_load
    db="$(agmsg_db_path "$team")"

    receipt_table "$db"

    found="$(
      agmsg_sqlite "$db" "
        SELECT 1
        FROM p2_handoff_receipts
        WHERE receipt_id = '$(literal "$receipt")'
          AND team = '$(literal "$team")'
          AND input_id = '$(literal "$id")'
        LIMIT 1;
      "
    )" || exit 1

    [ "$found" = "1" ] || exit 1

    AGMSG_CLAIM_TEAM="$team" \
      bash "$DIR/claim.sh" ack "$legacy" "$owner" "$receipt" || exit 1

    ack_json "$id" "$owner" "$receipt"
    ;;

  *)
    exit 2
    ;;
esac
