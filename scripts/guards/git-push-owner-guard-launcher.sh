#!/bin/sh
# agmsg git push owner guard launcher
#
# install.sh replaces both placeholders with absolute paths. The launcher is
# intentionally POSIX-only so shell startup hooks are removed before Bash
# reads the authorization code.

set -eu

unset BASH_ENV ENV SHELLOPTS BASHOPTS CDPATH GLOBIGNORE 2>/dev/null || :

for name in $(env | sed -n 's/=.*//p'); do
  case "$name" in
    BASH_FUNC_*) unset "$name" 2>/dev/null || : ;;
  esac
done

GUARD_SCRIPT='__AGMSG_GIT_GUARD_SCRIPT__'
REAL_GIT='__AGMSG_REAL_GIT__'

case "$GUARD_SCRIPT:$REAL_GIT" in
  *AGMSG_GIT_GUARD_SCRIPT*|*AGMSG_REAL_GIT*)
    echo 'error: agmsg git push owner guard launcher is not installed' >&2
    exit 1
    ;;
esac

case "$GUARD_SCRIPT:$REAL_GIT" in
  /*:/*) ;;
  *)
    echo 'error: agmsg git push owner guard launcher has non-absolute paths' >&2
    exit 1
    ;;
esac

exec /bin/bash "$GUARD_SCRIPT" "$REAL_GIT" "$@"
