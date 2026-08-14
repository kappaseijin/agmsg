#!/bin/sh
# agmsg gh owner guard launcher
#
# install.sh replaces both placeholders with absolute paths. Keeping this
# launcher POSIX-only lets us clear shell startup hooks before Bash reads the
# authorization code.

set -eu

unset BASH_ENV ENV SHELLOPTS BASHOPTS CDPATH GLOBIGNORE 2>/dev/null || :

# Bash imports exported functions through BASH_FUNC_* environment entries.
# Remove those entries without relying on their names being known in advance.
for name in $(env | sed -n 's/=.*//p'); do
  case "$name" in
    BASH_FUNC_*) unset "$name" 2>/dev/null || : ;;
  esac
done

GUARD_SCRIPT='__AGMSG_GH_GUARD_SCRIPT__'
REAL_GH='__AGMSG_REAL_GH__'

case "$GUARD_SCRIPT:$REAL_GH" in
  *AGMSG_GH_GUARD_SCRIPT*|*AGMSG_REAL_GH*)
    echo 'error: agmsg gh owner guard launcher is not installed' >&2
    exit 1
    ;;
esac

case "$GUARD_SCRIPT:$REAL_GH" in
  /*:/*) ;;
  *)
    echo 'error: agmsg gh owner guard launcher has non-absolute paths' >&2
    exit 1
    ;;
esac

exec /bin/bash "$GUARD_SCRIPT" "$REAL_GH" "$@"
