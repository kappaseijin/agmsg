#!/usr/bin/env bash
set -euo pipefail

# agmsg git push owner guard.
#
# The launcher supplies an absolute path to the Git executable that was
# inspected at install time. Every resolver call and the final exec use that
# same path; PATH is never consulted again.

if [ "$#" -lt 1 ]; then
  echo 'error: git-push-owner-guard: real git path is missing' >&2
  exit 1
fi

readonly REAL_GIT="$1"
shift
readonly -a ALLOWED_OWNERS=(kappaseijin kappaseijinjp)
readonly -a ALLOWED_HOSTS=(
  github.com
  github.com-kappaseijinsub
  github.com-kappaseijin4claude
  github.com-kappaseijin4codex
)

if [[ "$REAL_GIT" != /* || ! -x "$REAL_GIT" || -d "$REAL_GIT" ]]; then
  echo "error: git-push-owner-guard: invalid fixed git path: $REAL_GIT" >&2
  exit 1
fi

die() {
  echo "error: git-push-owner-guard: $*" >&2
  exit 1
}

ascii_lower() {
  LC_ALL=C tr '[:upper:]' '[:lower:]' <<< "$1"
}

allowed_owner() {
  local owner
  owner="$(ascii_lower "$1")"
  local candidate
  for candidate in "${ALLOWED_OWNERS[@]}"; do
    [ "$owner" = "$candidate" ] && return 0
  done
  return 1
}

allowed_host() {
  local host
  host="$(ascii_lower "$1")"
  local candidate
  for candidate in "${ALLOWED_HOSTS[@]}"; do
    [ "$host" = "$candidate" ] && return 0
  done
  return 1
}

valid_segment() {
  [ -n "$1" ] || return 1
  case "$1" in
    *[!A-Za-z0-9_.-]*) return 1 ;;
    *) return 0 ;;
  esac
}

GLOBAL_ARGS=()
CONFIG_ARGS=()
COMMAND=''
PUSH_ARGS=()

append_global_value() {
  local option="$1" value="$2"
  GLOBAL_ARGS+=("$option" "$value")
  case "$option" in
    -c|--config-env) CONFIG_ARGS+=("$option" "$value") ;;
  esac
}

append_global_equals() {
  local token="$1"
  GLOBAL_ARGS+=("$token")
  case "$token" in
    -c*|--config-env=*) CONFIG_ARGS+=("$token") ;;
  esac
}

real_git_global() {
  if [ "${#GLOBAL_ARGS[@]}" -gt 0 ]; then
    "$REAL_GIT" "${GLOBAL_ARGS[@]}" "$@"
  else
    "$REAL_GIT" "$@"
  fi
}

synthetic_git() {
  if [ "${#CONFIG_ARGS[@]}" -gt 0 ]; then
    env -u GIT_DIR -u GIT_WORK_TREE "$REAL_GIT" "${CONFIG_ARGS[@]}" "$@"
  else
    env -u GIT_DIR -u GIT_WORK_TREE "$REAL_GIT" "$@"
  fi
}

# Parse only options that Git accepts before its subcommand. Unknown options
# fail closed instead of being allowed to change the context the resolver sees.
parse_global_options() {
  local -a args=("$@")
  local i=0 tok
  while [ "$i" -lt "${#args[@]}" ]; do
    tok="${args[$i]}"
    case "$tok" in
      -C|--git-dir|--work-tree|--namespace|--config-env|--exec-path|--super-prefix)
        i=$((i + 1))
        [ "$i" -lt "${#args[@]}" ] || die "missing value for global option $tok"
        append_global_value "$tok" "${args[$i]}"
        ;;
      -c)
        i=$((i + 1))
        [ "$i" -lt "${#args[@]}" ] || die 'missing value for global option -c'
        append_global_value -c "${args[$i]}"
        ;;
      -C?*|-c?*|--git-dir=*|--work-tree=*|--namespace=*|--config-env=*|--exec-path=*|--super-prefix=*)
        append_global_equals "$tok"
        ;;
      --no-pager|--paginate|--no-replace-objects|--no-lazy-fetch|--no-optional-locks|\
      --literal-pathspecs|--glob-pathspecs|--noglob-pathspecs|--icase-pathspecs|\
      --no-advice)
        GLOBAL_ARGS+=("$tok")
        ;;
      --list-cmds=*|--help|--version|-h)
        COMMAND="$tok"
        i=$((i + 1))
        PUSH_ARGS=("${args[@]:$i}")
        return 0
        ;;
      --)
        die 'unsupported -- before git subcommand'
        ;;
      -*)
        die "unsupported global option: $tok"
        ;;
      *)
        COMMAND="$tok"
        i=$((i + 1))
        PUSH_ARGS=("${args[@]:$i}")
        return 0
        ;;
    esac
    i=$((i + 1))
  done
}

PUSH_DEST=''
PUSH_DEST_COUNT=0

set_push_destination() {
  [ "$PUSH_DEST_COUNT" -eq 0 ] || die 'multiple push destinations are ambiguous'
  PUSH_DEST="$1"
  PUSH_DEST_COUNT=1
}

push_bool_option() {
  case "$1" in
    -u|-q|-f|-d|--all|--atomic|--delete|--dry-run|--follow-tags|--force|\
    --force-with-lease|--mirror|--no-verify|--no-thin|--no-tags|--no-signed|\
    --porcelain|--prune|--progress|--quiet|--set-upstream|--signed|--stdin|\
    --tags|--thin|--verbose|--ipv4|--ipv6|--no-force|--no-progress|\
    --signed=*|--force-with-lease=*|--recurse-submodules=*) return 0 ;;
    *) return 1 ;;
  esac
}

push_value_option() {
  case "$1" in
    -o|--push-option|--receive-pack|--upload-pack|--exec|--output|--repo|\
    --recurse-submodules) return 0 ;;
    *) return 1 ;;
  esac
}

parse_push_options() {
  local -a args=("$@")
  local i=0 tok after_double_dash=0
  while [ "$i" -lt "${#args[@]}" ]; do
    tok="${args[$i]}"
    if [ "$after_double_dash" -eq 1 ]; then
      [ "$PUSH_DEST_COUNT" -eq 0 ] && set_push_destination "$tok"
      i=$((i + 1))
      continue
    fi
    case "$tok" in
      --)
        after_double_dash=1
        ;;
      --repo=*)
        set_push_destination "${tok#*=}"
        [ -n "$PUSH_DEST" ] || die 'empty --repo value'
        ;;
      -R|--repo)
        i=$((i + 1))
        [ "$i" -lt "${#args[@]}" ] || die 'missing --repo value'
        set_push_destination "${args[$i]}"
        [ -n "$PUSH_DEST" ] || die 'empty --repo value'
        ;;
      --push-option=*|--receive-pack=*|--upload-pack=*|--exec=*|--output=*)
        ;;
      -o?*)
        ;;
      --*=*)
        push_bool_option "$tok" || die "unsupported push option: $tok"
        ;;
      *)
        if push_bool_option "$tok"; then
          :
        elif push_value_option "$tok"; then
          i=$((i + 1))
          [ "$i" -lt "${#args[@]}" ] || die "missing value for push option $tok"
        elif [[ "$tok" == -* ]]; then
          die "unsupported push option: $tok"
        elif [ "$PUSH_DEST_COUNT" -eq 0 ]; then
          set_push_destination "$tok"
        fi
        ;;
    esac
    i=$((i + 1))
  done
}

parse_push_url() {
  local url="$1" authority_path authority host path user
  local -a parts=()
  PARSED_HOST=''
  PARSED_OWNER=''

  [ -n "$url" ] || return 1
  case "$url" in
    *$'\n'*|*$'\r'*|*$'\t'*) return 1 ;;
  esac
  case "$url" in
    https://*)
      authority_path="${url#https://}"
      authority="${authority_path%%/*}"
      [ "$authority_path" != "$authority" ] || return 1
      [[ "$authority" != *@* && "$authority" != *:* ]] || return 1
      host="$authority"
      path="${authority_path#*/}"
      ;;
    ssh://*)
      authority_path="${url#ssh://}"
      authority="${authority_path%%/*}"
      [ "$authority_path" != "$authority" ] || return 1
      [[ "$authority" != *:* ]] || return 1
      case "$authority" in
        git@*) user="${authority%%@*}"; host="${authority#*@}"; [ "$user" = git ] || return 1 ;;
        *@*) return 1 ;;
        *) host="$authority" ;;
      esac
      path="${authority_path#*/}"
      ;;
    git@*:*/*)
      user="${url%%@*}"
      [ "$user" = git ] || return 1
      host="${url#*@}"
      host="${host%%:*}"
      path="${url#*:}"
      ;;
    *)
      return 1
      ;;
  esac

  [ -n "$host" ] || return 1
  [[ "$host" != *'@'* && "$host" != *:* && "$host" != *'.' ]] || return 1
  host="$(ascii_lower "$host")"
  IFS='/' read -r -a parts <<< "$path"
  [ "${#parts[@]}" -eq 2 ] || return 1
  valid_segment "${parts[0]}" || return 1
  case "${parts[1]}" in
    *.git) parts[1]="${parts[1]%.git}" ;;
  esac
  valid_segment "${parts[1]}" || return 1
  PARSED_HOST="$host"
  PARSED_OWNER="$(ascii_lower "${parts[0]}")"
}

authorize_url() {
  parse_push_url "$1" || return 1
  allowed_host "$PARSED_HOST" || return 1
  allowed_owner "$PARSED_OWNER"
}

resolve_remote_urls() {
  local remote="$1" output status
  set +e
  output="$(real_git_global remote get-url --push --all "$remote" 2>/dev/null)"
  status=$?
  set -e
  [ "$status" -eq 0 ] || return 1
  [ -n "$output" ] || return 1
  [[ "$output" != *$'\r'* ]] || return 1
  printf '%s\n' "$output"
}

resolve_original_git_dir() {
  local output status
  set +e
  output="$(real_git_global rev-parse --absolute-git-dir 2>/dev/null)"
  status=$?
  set -e
  [ "$status" -eq 0 ] || return 1
  [[ "$output" != *$'\n'* && "$output" != *$'\r'* ]] || return 1
  [ -d "$output" ] || return 1
  printf '%s\n' "$output"
}

resolve_direct_url() {
  local destination="$1" temp repo_dir remote_name original_git_dir output status
  temp="$(mktemp -d "${TMPDIR:-/tmp}/agmsg-git-owner-guard.XXXXXX")" || return 1
  repo_dir="$temp/repo.git"
  remote_name="agmsg-owner-guard-$PPID"

  if ! synthetic_git init --bare -q "$repo_dir" >/dev/null 2>&1; then
    rm -rf "$temp"
    return 1
  fi

  original_git_dir="$(resolve_original_git_dir 2>/dev/null || true)"
  if [ -n "$original_git_dir" ]; then
    case "$original_git_dir" in *$'\n'*|*$'\r'*) rm -rf "$temp"; return 1 ;; esac
    if [ -f "$original_git_dir/config" ]; then
      printf '\n[include]\n\tpath = %s\n' "$original_git_dir/config" >> "$repo_dir/config"
    fi
    if [ -f "$original_git_dir/config.worktree" ]; then
      printf '[include]\n\tpath = %s\n' "$original_git_dir/config.worktree" >> "$repo_dir/config"
    fi
  fi

  if ! synthetic_git --git-dir="$repo_dir" remote add "$remote_name" "$destination" >/dev/null 2>&1; then
    rm -rf "$temp"
    return 1
  fi
  set +e
  output="$(synthetic_git --git-dir="$repo_dir" remote get-url --push --all "$remote_name" 2>/dev/null)"
  status=$?
  set -e
  rm -rf "$temp"
  [ "$status" -eq 0 ] || return 1
  [ -n "$output" ] || return 1
  [[ "$output" != *$'\r'* ]] || return 1
  printf '%s\n' "$output"
}

is_direct_destination() {
  case "$1" in
    */*|*:*) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_destination_urls() {
  local destination="$1"
  if is_direct_destination "$destination"; then
    resolve_direct_url "$destination"
  else
    case "$destination" in
      ''|*[!A-Za-z0-9_.-]*) return 1 ;;
    esac
    resolve_remote_urls "$destination"
  fi
}

resolve_default_remote() {
  local output status branch
  set +e
  output="$(real_git_global config --get remote.pushDefault 2>/dev/null)"
  status=$?
  set -e
  [ "$status" -le 1 ] || return 1
  if [ "$status" -eq 0 ] && [ -n "$output" ]; then
    [[ "$output" != *$'\n'* ]] || return 1
    printf '%s\n' "$output"
    return 0
  fi

  set +e
  branch="$(real_git_global symbolic-ref --quiet --short HEAD 2>/dev/null)"
  set -e
  if [ -n "$branch" ]; then
    set +e
    output="$(real_git_global config --get "branch.$branch.remote" 2>/dev/null)"
    status=$?
    set -e
    [ "$status" -le 1 ] || return 1
    if [ "$status" -eq 0 ] && [ -n "$output" ]; then
      [[ "$output" != *$'\n'* ]] || return 1
      printf '%s\n' "$output"
      return 0
    fi
  fi
  printf '%s\n' origin
}

authorize_push() {
  local destination="$1" urls url
  urls="$(resolve_destination_urls "$destination")" || die "could not resolve push destination: $destination"
  [ -n "$urls" ] || die 'push destination resolved to no URLs'
  while IFS= read -r url || [ -n "$url" ]; do
    [ -n "$url" ] || die 'push destination contains an empty URL'
    authorize_url "$url" || die "push destination is not an allowed GitHub owner: $url"
  done <<< "$urls"
}

check_git_alias() {
  local value status
  set +e
  value="$(real_git_global config --get "alias.$COMMAND" 2>/dev/null)"
  status=$?
  set -e
  case "$status" in
    0) die "git alias '$COMMAND' is not allowed" ;;
    1) return 0 ;;
    *) die "could not resolve whether git '$COMMAND' is an alias" ;;
  esac
}

parse_global_options "$@"

case "$COMMAND" in
  push)
    if [ "${#PUSH_ARGS[@]}" -gt 0 ]; then
      parse_push_options "${PUSH_ARGS[@]}"
    else
      parse_push_options
    fi
    if [ "$PUSH_DEST_COUNT" -eq 0 ]; then
      PUSH_DEST="$(resolve_default_remote)" || die 'could not resolve the default push remote'
    fi
    authorize_push "$PUSH_DEST"
    exec "$REAL_GIT" "$@"
    ;;
  send-pack|receive-pack)
    die "git $COMMAND is not allowed through the owner guard"
    ;;
  --help|--version|-h|version)
    exec "$REAL_GIT" "$@"
    ;;
  *)
    check_git_alias
    exec "$REAL_GIT" "$@"
    ;;
esac
