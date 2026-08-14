#!/usr/bin/env bash

# Atomic reservation helpers for message receiver adapters. A claim keeps a
# message unread until the owning receiver has handed it to its host and ACKed.

if ! declare -F agmsg_db_path >/dev/null 2>&1; then
  _agmsg_claims_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # shellcheck disable=SC1091
  source "$_agmsg_claims_dir/storage.sh"
fi

_agmsg_claim_sql_quote() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/''/g")"
}

_agmsg_claim_ttl() {
  case "${1:-}" in
    ''|*[!0-9]*) printf '30' ;;
    *) printf '%s' "$1" ;;
  esac
}

_agmsg_claim_initialize() {
  agmsg_storage_ensure_initialized
}

# stdout: id US from_agent US escaped_body US created_at. Empty stdout means
# there is no unclaimed unread message for this receiver.
agmsg_claim_next() {
  local team="$1" agent="$2" owner="$3" ttl db result
  ttl="$(_agmsg_claim_ttl "${4:-30}")"
  _agmsg_claim_initialize
  db="$(agmsg_db_path)"

  result="$(agmsg_sqlite "$db" <<SQL
BEGIN IMMEDIATE;
DELETE FROM message_claims
 WHERE expires_at <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now');
INSERT OR IGNORE INTO message_claims(message_id, owner, claimed_at, expires_at)
SELECT m.id,
       $(_agmsg_claim_sql_quote "$owner"),
       strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
       strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '+${ttl} seconds')
  FROM messages AS m
 WHERE m.team=$(_agmsg_claim_sql_quote "$team")
   AND m.to_agent=$(_agmsg_claim_sql_quote "$agent")
   AND m.read_at IS NULL
   AND NOT EXISTS (
     SELECT 1 FROM message_claims AS c WHERE c.message_id=m.id
   )
 ORDER BY m.created_at, m.id
 LIMIT 1;
SELECT m.id || char(31) || m.from_agent || char(31)
       || replace(replace(m.body, char(10), '\\n'), char(9), '\\t')
       || char(31) || m.created_at
 FROM messages AS m
  JOIN message_claims AS c ON c.message_id=m.id
 WHERE c.owner=$(_agmsg_claim_sql_quote "$owner")
   AND m.team=$(_agmsg_claim_sql_quote "$team")
   AND m.to_agent=$(_agmsg_claim_sql_quote "$agent")
   AND c.expires_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
   AND m.read_at IS NULL
 ORDER BY m.id
 LIMIT 1;
COMMIT;
SQL
)"
  [ -z "$result" ] || printf '%s\n' "$result"
}

# Claim a row already selected by an adapter such as watch.sh. Returns success
# only when this owner holds an unexpired claim for an unread row.
agmsg_claim_id() {
  local message_id="$1" owner="$2" ttl db held
  case "$message_id" in
    ''|*[!0-9]*) return 2 ;;
  esac
  ttl="$(_agmsg_claim_ttl "${3:-30}")"
  _agmsg_claim_initialize
  db="$(agmsg_db_path)"

  held="$(agmsg_sqlite "$db" <<SQL
BEGIN IMMEDIATE;
DELETE FROM message_claims
 WHERE expires_at <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now');
INSERT OR IGNORE INTO message_claims(message_id, owner, claimed_at, expires_at)
SELECT m.id,
       $(_agmsg_claim_sql_quote "$owner"),
       strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
       strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '+${ttl} seconds')
  FROM messages AS m
 WHERE m.id=$message_id
   AND m.read_at IS NULL
   AND NOT EXISTS (
     SELECT 1 FROM message_claims AS c WHERE c.message_id=m.id
   );
SELECT CASE WHEN EXISTS (
  SELECT 1
    FROM message_claims AS c
    JOIN messages AS m ON m.id=c.message_id
   WHERE c.message_id=$message_id
     AND c.owner=$(_agmsg_claim_sql_quote "$owner")
     AND c.expires_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
     AND m.read_at IS NULL
) THEN 1 ELSE 0 END;
COMMIT;
SQL
)"
  [ "$held" = "1" ]
}

agmsg_release_claim() {
  local message_id="$1" owner="$2" db changed
  case "$message_id" in
    ''|*[!0-9]*) return 2 ;;
  esac
  _agmsg_claim_initialize
  db="$(agmsg_db_path)"

  changed="$(agmsg_sqlite "$db" <<SQL
BEGIN IMMEDIATE;
DELETE FROM message_claims
 WHERE message_id=$message_id
   AND owner=$(_agmsg_claim_sql_quote "$owner")
   AND expires_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now');
SELECT changes();
COMMIT;
SQL
)"
  [ "$changed" = "1" ]
}

agmsg_ack_claim() {
  local message_id="$1" owner="$2" evidence="${3:-host_handoff}" db changed
  case "$message_id" in
    ''|*[!0-9]*) return 2 ;;
  esac
  _agmsg_claim_initialize
  db="$(agmsg_db_path)"

  changed="$(agmsg_sqlite "$db" <<SQL
BEGIN IMMEDIATE;
INSERT OR IGNORE INTO message_receipts(message_id, owner, handed_off_at, evidence)
SELECT c.message_id,
       c.owner,
       strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
       $(_agmsg_claim_sql_quote "$evidence")
  FROM message_claims AS c
  JOIN messages AS m ON m.id=c.message_id
 WHERE c.message_id=$message_id
   AND c.owner=$(_agmsg_claim_sql_quote "$owner")
   AND c.expires_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
   AND m.read_at IS NULL;
SELECT changes();
UPDATE messages
   SET read_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
 WHERE id=$message_id
   AND read_at IS NULL
   AND EXISTS (
     SELECT 1 FROM message_claims AS c
      WHERE c.message_id=$message_id
        AND c.owner=$(_agmsg_claim_sql_quote "$owner")
        AND c.expires_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
   );
DELETE FROM message_claims
 WHERE message_id=$message_id
   AND owner=$(_agmsg_claim_sql_quote "$owner")
   AND expires_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now');
COMMIT;
SQL
)"
  [ "$changed" = "1" ]
}
