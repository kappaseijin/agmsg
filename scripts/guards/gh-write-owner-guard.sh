#!/usr/bin/env bash
set -euo pipefail

# agmsg gh owner guard.
#
# The launcher passes the absolute path of the real gh as argv[1]. Keeping that
# path outside PATH is deliberate: a later PATH entry must not replace the
# executable that was inspected or the executable that receives the write.

if [ "$#" -lt 1 ]; then
  echo 'error: gh-write-owner-guard: real gh path is missing' >&2
  exit 1
fi

readonly REAL_GH="$1"
shift
readonly ALLOWED_HOST='github.com'
readonly ALLOWED_OWNER_1='kappaseijin'
readonly ALLOWED_OWNER_2='kappaseijinjp'

if [[ "$REAL_GH" != /* || ! -x "$REAL_GH" || -d "$REAL_GH" ]]; then
  echo "error: gh-write-owner-guard: invalid fixed gh path: $REAL_GH" >&2
  exit 1
fi

TOP_COMMAND=''
SUBCOMMAND=''
EXPLICIT_REPO=''
EXPLICIT_REPO_COUNT=0
REQUEST_HOST="${GH_HOST:-$ALLOWED_HOST}"
REQUEST_HOST_COUNT=0
PARSED_HOST=''
PARSED_OWNER=''
PARSED_REPO=''
API_METHOD=''
API_HAS_PARAMS=0
API_PARSE_ERROR=0
API_ENDPOINT=''
API_GRAPHQL_QUERY=''
API_GRAPHQL_QUERY_COUNT=0
API_HAS_INPUT=0
RERUN_RUN_ID=''
RERUN_JOB_ID=''
RERUN_REPO=''

die() {
  echo "error: gh-write-owner-guard: $*" >&2
  exit 1
}

CURRENT_CWD="$(pwd -P)" || die 'cannot resolve current directory'
readonly CURRENT_CWD

ascii_lower() {
  LC_ALL=C tr '[:upper:]' '[:lower:]' <<< "$1"
}

allowed_owner() {
  local owner
  owner="$(ascii_lower "$1")"
  [ "$owner" = "$ALLOWED_OWNER_1" ] || [ "$owner" = "$ALLOWED_OWNER_2" ]
}

valid_segment() {
  [ -n "$1" ] || return 1
  case "$1" in
    *[!A-Za-z0-9_.-]*) return 1 ;;
    *) return 0 ;;
  esac
}

# Parse [HOST/]OWNER/REPO and the URL/scp forms accepted by gh. The result is
# written to PARSED_HOST, PARSED_OWNER, and PARSED_REPO.
parse_repo_spec() {
  local spec="$1" default_host="$2" authority_path authority path host
  local -a parts=()
  PARSED_HOST=''
  PARSED_OWNER=''
  PARSED_REPO=''

  [ -n "$spec" ] || return 1
  case "$spec" in
    *$'\n'*|*$'\r'*|*$'\t'*) return 1 ;;
  esac

  case "$spec" in
    *.git) spec="${spec%.git}" ;;
  esac
  case "$spec" in
    http://*|https://*)
      authority_path="${spec#*://}"
      authority="${authority_path%%/*}"
      [ "$authority_path" != "$authority" ] || return 1
      [[ "$authority" != *@* && "$authority" != *:* ]] || return 1
      host="$authority"
      path="${authority_path#*/}"
      ;;
    ssh://*)
      authority_path="${spec#ssh://}"
      authority="${authority_path%%/*}"
      [ "$authority_path" != "$authority" ] || return 1
      [[ "$authority" != *@* && "$authority" != *:* ]] || return 1
      host="$authority"
      path="${authority_path#*/}"
      ;;
    git@*:*/*)
      host="${spec%%:*}"
      [ "${host#git@}" != "$host" ] || return 1
      host="${host#git@}"
      path="${spec#*:}"
      ;;
    */*/*)
      host="${spec%%/*}"
      path="${spec#*/}"
      ;;
    */*)
      host="$default_host"
      path="$spec"
      ;;
    *)
      return 1
      ;;
  esac

  [[ "$host" != *'@'* && "$host" != *:* && "$host" != *'.' ]] || return 1
  host="$(ascii_lower "$host")"
  IFS='/' read -r -a parts <<< "$path"
  [ "${#parts[@]}" -eq 2 ] || return 1
  valid_segment "${parts[0]}" || return 1
  valid_segment "${parts[1]}" || return 1
  PARSED_HOST="$host"
  PARSED_OWNER="$(ascii_lower "${parts[0]}")"
  PARSED_REPO="${parts[1]}"
}

authorize_repo_spec() {
  local spec="$1" default_host="$2"
  parse_repo_spec "$spec" "$default_host" || return 1
  [ "$PARSED_HOST" = "$ALLOWED_HOST" ] || return 1
  allowed_owner "$PARSED_OWNER"
}

resolve_explicit_repository() {
  local spec="$1" output status name url extra
  local requested_owner requested_repo name_owner name_repo url_owner url_repo

  parse_repo_spec "$spec" "$REQUEST_HOST" || return 2
  requested_owner="$PARSED_OWNER"
  requested_repo="$(ascii_lower "$PARSED_REPO")"

  if output="$(GH_PROMPT_DISABLED=true "$REAL_GH" repo view "$spec" \
    --json nameWithOwner,url \
    --template '{{.nameWithOwner}}{{"\t"}}{{.url}}' 2>/dev/null)"; then
    status=0
  else
    status=$?
  fi
  [ "$status" -eq 0 ] || return 1
  [[ "$output" != *$'\n'* ]] || return 2
  IFS=$'\t' read -r name url extra <<< "$output"
  [ -n "$name" ] && [ -n "$url" ] && [ -z "${extra:-}" ] || return 2

  parse_repo_spec "$name" "$REQUEST_HOST" || return 2
  name_owner="$PARSED_OWNER"
  name_repo="$(ascii_lower "$PARSED_REPO")"
  authorize_repo_spec "$url" "$REQUEST_HOST" || return 2
  url_owner="$PARSED_OWNER"
  url_repo="$(ascii_lower "$PARSED_REPO")"
  [ "$requested_owner" = "$name_owner" ] && \
    [ "$requested_repo" = "$name_repo" ] && \
    [ "$name_owner" = "$url_owner" ] && \
    [ "$name_repo" = "$url_repo" ]
}

is_value_flag() {
  case "$1" in
    -R|--repo|--hostname|--config-dir|--title|-t|--body|-b|--body-file|--comment|--subject|--notes|--notes-file|--team|--visibility|--source|--target|--project|--assignee|--milestone|--label|--reviewer|--base|--head|--fill-verbose|--jq|--template|--cache|--header|-H|--field|-f|--raw-field|-F|--input|--method|-X)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

# Find the first two command words without treating a value of a known option
# as a command. This parser deliberately stops at --; an apparent --repo in a
# body or positional tail is never treated as a destination flag.
resolve_operation() {
  local tok skip_next=0 after_double_dash=0
  local -a words=()
  for tok in "$@"; do
    if [ "$after_double_dash" -eq 1 ]; then
      continue
    fi
    if [ "$skip_next" -eq 1 ]; then
      skip_next=0
      continue
    fi
    case "$tok" in
      --)
        after_double_dash=1
        continue
        ;;
      -R|--repo|--hostname|--config-dir|--title|-t|--body|-b|--body-file|--comment|--subject|--notes|--notes-file|--team|--visibility|--source|--target|--project|--assignee|--milestone|--label|--reviewer|--base|--head|--jq|--template|--cache|--header|-H|--field|-f|--raw-field|-F|--input|--method|-X)
        skip_next=1
        continue
        ;;
      --*=*|-R?*|-X?*|-f?*|-F?*)
        continue
        ;;
      -*|+*)
        continue
        ;;
      *)
        words+=("$tok")
        [ "${#words[@]}" -ge 2 ] && break
        ;;
    esac
  done
  TOP_COMMAND="${words[0]:-}"
  if [ "${#words[@]}" -ge 2 ]; then
    SUBCOMMAND="$TOP_COMMAND ${words[1]}"
  else
    SUBCOMMAND="$TOP_COMMAND"
  fi
}

collect_destination_flags() {
  local tok skip_next=0 skip_kind='' after_double_dash=0
  for tok in "$@"; do
    if [ "$after_double_dash" -eq 1 ]; then
      continue
    fi
    if [ "$skip_next" -eq 1 ]; then
      case "$skip_kind" in
        repo)
          [ "$EXPLICIT_REPO" = '__MISSING__' ] && EXPLICIT_REPO="$tok"
          ;;
        hostname)
          [ "$REQUEST_HOST" = '__PENDING_HOST__' ] && REQUEST_HOST="$tok"
          ;;
      esac
      skip_next=0
      skip_kind=''
      continue
    fi
    case "$tok" in
      --)
        after_double_dash=1
        continue
        ;;
      -R|--repo)
        skip_next=1
        skip_kind=repo
        if [ -n "$EXPLICIT_REPO" ]; then
          EXPLICIT_REPO_COUNT=$((EXPLICIT_REPO_COUNT + 1))
        else
          EXPLICIT_REPO='__MISSING__'
          EXPLICIT_REPO_COUNT=1
        fi
        continue
        ;;
      --repo=*)
        EXPLICIT_REPO_COUNT=$((EXPLICIT_REPO_COUNT + 1))
        [ "$EXPLICIT_REPO" = '' ] && EXPLICIT_REPO="${tok#*=}"
        continue
        ;;
      -R?*)
        EXPLICIT_REPO_COUNT=$((EXPLICIT_REPO_COUNT + 1))
        EXPLICIT_REPO='__UNSUPPORTED_SHORT_FORM__'
        continue
        ;;
      --hostname)
        skip_next=1
        skip_kind=hostname
        REQUEST_HOST_COUNT=$((REQUEST_HOST_COUNT + 1))
        [ "$REQUEST_HOST_COUNT" -eq 1 ] && REQUEST_HOST='__PENDING_HOST__'
        continue
        ;;
      --hostname=*)
        REQUEST_HOST_COUNT=$((REQUEST_HOST_COUNT + 1))
        [ "$REQUEST_HOST_COUNT" -eq 1 ] && REQUEST_HOST="${tok#*=}"
        continue
        ;;
      --config-dir)
        skip_next=1
        skip_kind=config
        continue
        ;;
      --config-dir=*)
        continue
        ;;
      *)
        if is_value_flag "$tok"; then
          skip_next=1
        fi
        ;;
    esac
  done

  [ "$REQUEST_HOST_COUNT" -le 1 ] || die 'multiple --hostname values are ambiguous'
  if [ "$REQUEST_HOST" = '__PENDING_HOST__' ]; then
    die 'missing --hostname value'
  fi
  REQUEST_HOST="$(ascii_lower "$REQUEST_HOST")"
}

record_api_field() {
  local field="$1"
  API_HAS_PARAMS=1
  case "$field" in
    query=*)
      API_GRAPHQL_QUERY_COUNT=$((API_GRAPHQL_QUERY_COUNT + 1))
      [ "$API_GRAPHQL_QUERY_COUNT" -eq 1 ] && API_GRAPHQL_QUERY="${field#query=}"
      ;;
  esac
}

parse_api_args() {
  local tok skip_next=0 skip_kind='' after_double_dash=0 seen_api=0
  API_METHOD=''
  API_HAS_PARAMS=0
  API_PARSE_ERROR=0
  API_ENDPOINT=''
  API_GRAPHQL_QUERY=''
  API_GRAPHQL_QUERY_COUNT=0
  API_HAS_INPUT=0
  for tok in "$@"; do
    if [ "$seen_api" -eq 0 ]; then
      [ "$tok" = api ] && seen_api=1
      continue
    fi
    [ "$after_double_dash" -eq 1 ] && continue
    if [ "$skip_next" -eq 1 ]; then
      case "$skip_kind" in
        method) API_METHOD="$tok" ;;
        field) record_api_field "$tok" ;;
        input) API_HAS_PARAMS=1; API_HAS_INPUT=1 ;;
      esac
      skip_next=0
      skip_kind=''
      continue
    fi
    case "$tok" in
      --)
        after_double_dash=1
        ;;
      --method|-X)
        skip_next=1
        skip_kind=method
        ;;
      --method=*) API_METHOD="${tok#*=}" ;;
      -X?*) API_METHOD="${tok#-X}"; API_METHOD="${API_METHOD#=}" ;;
      --field|-f|--raw-field|-F)
        skip_next=1
        skip_kind=field
        ;;
      --input)
        API_HAS_PARAMS=1
        API_HAS_INPUT=1
        skip_next=1
        skip_kind=input
        ;;
      --field=*) record_api_field "${tok#*=}" ;;
      --raw-field=*) record_api_field "${tok#*=}" ;;
      -f?*) record_api_field "${tok#-f}" ;;
      -F?*) record_api_field "${tok#-F}" ;;
      --input=*)
        API_HAS_PARAMS=1
        API_HAS_INPUT=1
        ;;
      --header|-H|--hostname|--cache|--jq|--template|--config-dir)
        skip_next=1
        skip_kind=option
        ;;
      --header=*|--hostname=*|--cache=*|--jq=*|--template=*|--config-dir=*) ;;
      -*) ;;
      *)
        if [ -z "$API_ENDPOINT" ]; then
          API_ENDPOINT="$tok"
        else
          API_PARSE_ERROR=1
        fi
        ;;
    esac
  done
  [ -z "$skip_kind" ] || API_PARSE_ERROR=1
}

is_safe_graphql_query() {
  [ "$API_ENDPOINT" = graphql ] || return 1
  [ -z "$API_METHOD" ] || return 1
  [ "$API_HAS_INPUT" -eq 0 ] || return 1
  [ "$API_GRAPHQL_QUERY_COUNT" -eq 1 ] || return 1
  printf '%s' "$API_GRAPHQL_QUERY" | LC_ALL=C grep -Eq '^[[:space:]]*query[[:space:]]' || return 1
  if printf '%s' "$API_GRAPHQL_QUERY" | LC_ALL=C grep -Eq '(^|[^A-Za-z0-9_])(mutation|subscription)([^A-Za-z0-9_]|$)'; then
    return 1
  fi
  return 0
}

is_read_only_operation() {
  case "$TOP_COMMAND" in
    ''|help|version|completion|status|search|browse|config|auth)
      return 0
      ;;
    api)
      parse_api_args "$@"
      [ "$API_PARSE_ERROR" -eq 0 ] || return 1
      is_safe_graphql_query && return 0
      if [ "$API_ENDPOINT" = graphql ] && [ "$API_HAS_PARAMS" -eq 1 ]; then
        return 1
      fi
      local method
      method="$(ascii_lower "$API_METHOD")"
      if [ -n "$method" ]; then
        [ "$method" = get ] && return 0
        return 1
      fi
      [ "$API_HAS_PARAMS" -eq 0 ]
      return
      ;;
    alias)
      case "$SUBCOMMAND" in
        'alias list'|'alias set'|'alias delete') return 0 ;;
      esac
      ;;
    extension)
      [ "$SUBCOMMAND" = 'extension list' ] && return 0
      ;;
    issue)
      case "$SUBCOMMAND" in
        'issue list'|'issue view'|'issue status') return 0 ;;
      esac
      ;;
    pr)
      case "$SUBCOMMAND" in
        'pr list'|'pr view'|'pr status'|'pr diff'|'pr checks') return 0 ;;
      esac
      ;;
    repo)
      case "$SUBCOMMAND" in
        'repo list'|'repo view'|'repo clone'|'repo sync'|'repo set-default') return 0 ;;
      esac
      ;;
    run)
      case "$SUBCOMMAND" in
        'run list'|'run view'|'run watch') return 0 ;;
      esac
      ;;
    workflow)
      case "$SUBCOMMAND" in
        'workflow list'|'workflow view') return 0 ;
      esac
      ;;
    release)
      case "$SUBCOMMAND" in
        'release list'|'release view'|'release download') return 0 ;
      esac
      ;;
    gist)
      case "$SUBCOMMAND" in
        'gist list'|'gist view') return 0 ;
      esac
      ;;
    project|label)
      case "$SUBCOMMAND" in
        'project list'|'project view'|'label list') return 0 ;
      esac
      ;;
  esac
  return 1
}

is_destination_checked_write() {
  case "$SUBCOMMAND" in
    'issue create'|'issue comment'|'issue close'|'issue reopen'|'issue edit'|'issue lock'|'issue unlock'|\
    'pr create'|'pr comment'|'pr review'|'pr ready'|'pr merge'|'pr close'|'pr reopen'|'pr edit'|'pr lock'|'pr unlock'|\
    'run rerun'|'label create')
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

valid_positive_decimal() {
  case "$1" in
    ''|0|0*|*[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

# Keep the rerun write intentionally narrower than `gh run rerun` itself. The
# guard only permits one already-completed failed job, never a whole run or the
# broader `--failed` selection. The destination resolver below still performs
# the immutable owner/host check before this target is read.
validate_rerun_arguments() {
  local tok run_id='' job_id='' repo='' run_count=0 job_count=0 repo_count=0
  local repo_owner repo_name extra

  [ "$#" -ge 2 ] && [ "$1" = run ] && [ "$2" = rerun ] || \
    die 'rerun command shape is invalid'
  shift 2

  while [ "$#" -gt 0 ]; do
    tok="$1"
    case "$tok" in
      --repo)
        [ "$#" -ge 2 ] || die 'rerun --repo value is missing'
        repo_count=$((repo_count + 1))
        [ "$repo_count" -eq 1 ] || die 'rerun --repo is ambiguous'
        repo="$2"
        shift 2
        ;;
      --repo=*)
        repo_count=$((repo_count + 1))
        [ "$repo_count" -eq 1 ] || die 'rerun --repo is ambiguous'
        repo="${tok#*=}"
        shift
        ;;
      --job)
        [ "$#" -ge 2 ] || die 'rerun --job value is missing'
        job_count=$((job_count + 1))
        [ "$job_count" -eq 1 ] || die 'rerun --job is ambiguous'
        job_id="$2"
        shift 2
        ;;
      --job=*)
        job_count=$((job_count + 1))
        [ "$job_count" -eq 1 ] || die 'rerun --job is ambiguous'
        job_id="${tok#*=}"
        shift
        ;;
      --failed|--debug|-R|-R?*|-j|-j?*)
        die 'only one explicit job rerun is permitted'
        ;;
      --*)
        die "unsupported rerun option: $tok"
        ;;
      *)
        run_count=$((run_count + 1))
        [ "$run_count" -eq 1 ] || die 'rerun run target is ambiguous'
        run_id="$tok"
        shift
        ;;
    esac
  done

  [ "$repo_count" -eq 1 ] || die 'rerun requires one explicit --repo'
  [ "$job_count" -eq 1 ] || die 'rerun requires one explicit --job'
  valid_positive_decimal "$run_id" || die 'rerun run id is not a positive decimal'
  valid_positive_decimal "$job_id" || die 'rerun job id is not a positive decimal'

  IFS='/' read -r repo_owner repo_name extra <<< "$repo"
  [ -n "$repo_owner" ] && [ -n "$repo_name" ] && [ -z "${extra:-}" ] || \
    die 'rerun --repo must be OWNER/REPO'
  valid_segment "$repo_owner" || die 'rerun repository owner is invalid'
  valid_segment "$repo_name" || die 'rerun repository name is invalid'

  RERUN_RUN_ID="$run_id"
  RERUN_JOB_ID="$job_id"
  RERUN_REPO="$repo"
}

verify_rerun_target() {
  local output status first_line remainder candidate job_status job_conclusion extra
  local found=0

  set +e
  output="$(GH_PROMPT_DISABLED=true "$REAL_GH" run view "$RERUN_RUN_ID" \
    --repo "$RERUN_REPO" \
    --json databaseId,jobs \
    --jq '[((.databaseId | tostring)), (.jobs[] | [(.databaseId | tostring), .status, (.conclusion // "")] | @tsv)] | .[]' \
    2>/dev/null)"
  status=$?
  set -e
  [ "$status" -eq 0 ] || die 'rerun target run cannot be read'
  [[ "$output" == *$'\n'* ]] || die 'rerun target output is invalid'

  first_line="${output%%$'\n'*}"
  [ "$first_line" = "$RERUN_RUN_ID" ] || die 'rerun target run id does not match'
  remainder="${output#*$'\n'}"
  while IFS=$'\t' read -r candidate job_status job_conclusion extra; do
    [ -n "$candidate" ] || continue
    [ -n "$job_status" ] && [ -n "$job_conclusion" ] && [ -z "${extra:-}" ] || \
      die 'rerun target job output is invalid'
    [ "$candidate" = "$RERUN_JOB_ID" ] || continue
    [ "$found" -eq 0 ] || die 'rerun target job is ambiguous'
    case "$job_status:$job_conclusion" in
      completed:failure|completed:cancelled|completed:timed_out)
        found=1
        ;;
      *)
        die 'rerun target job is not a completed failure'
        ;;
    esac
  done <<< "$remainder"
  [ "$found" -eq 1 ] || die 'rerun target job does not belong to the run'
}

resolve_default_repository() {
  local output status
  if output="$(GH_PROMPT_DISABLED=true "$REAL_GH" repo set-default --view 2>/dev/null)"; then
    status=0
  else
    status=$?
  fi
  [ "$status" -eq 0 ] || return 1
  # A project that never ran `gh repo set-default` gets exit 0 with empty
  # stdout here -- the "No default remote repository has been set" notice
  # goes to stderr, which is discarded above. That reads the same as "gh
  # itself failed" (return 1, caller falls through to resolve_cwd_repository)
  # rather than "a default IS set, but to something we can't use" (return 2,
  # caller dies). Empty/multi-line output means there is no single repo name
  # to evaluate at all, so there is nothing here to authorize or reject yet --
  # only a default that resolved cleanly and then failed authorization (below)
  # represents an actual answer worth dying on.
  [ -n "$output" ] || return 1
  [[ "$output" != *$'\n'* ]] || return 1
  authorize_repo_spec "$output" "$REQUEST_HOST" || return 2
}

resolve_cwd_repository() {
  local output status name url extra
  if output="$(GH_PROMPT_DISABLED=true "$REAL_GH" repo view --json nameWithOwner,url --template '{{.nameWithOwner}}{{"\t"}}{{.url}}' 2>/dev/null)"; then
    status=0
  else
    status=$?
  fi
  [ "$status" -eq 0 ] || return 1
  [[ "$output" != *$'\n'* ]] || return 2
  IFS=$'\t' read -r name url extra <<< "$output"
  [ -n "$name" ] && [ -n "$url" ] && [ -z "${extra:-}" ] || return 2
  parse_repo_spec "$name" "$REQUEST_HOST" || return 2
  local name_owner="$PARSED_OWNER" name_repo="$PARSED_REPO"
  authorize_repo_spec "$url" "$REQUEST_HOST" || return 2
  [ "$name_owner" = "$PARSED_OWNER" ] && [ "$name_repo" = "$PARSED_REPO" ] || return 2
}

resolve_destination() {
  local gh_repo
  collect_destination_flags "$@"
  [ "$REQUEST_HOST" = "$ALLOWED_HOST" ] || die 'host is not github.com'
  [ "$EXPLICIT_REPO_COUNT" -le 1 ] || die 'multiple --repo values are ambiguous'

  if [ "$EXPLICIT_REPO_COUNT" -eq 1 ]; then
    [ "$EXPLICIT_REPO" != '__MISSING__' ] || die 'missing --repo value'
    [ "$EXPLICIT_REPO" != '__UNSUPPORTED_SHORT_FORM__' ] || die 'unsupported combined -R form'
    authorize_repo_spec "$EXPLICIT_REPO" "$REQUEST_HOST" || die 'repository owner or host is not allowed'
    resolve_explicit_repository "$EXPLICIT_REPO" || die 'explicit repository cannot be resolved or is not allowed'
    return 0
  fi

  if [ "${GH_REPO+x}" = x ]; then
    gh_repo="${GH_REPO}"
    [ -n "$gh_repo" ] || die 'GH_REPO is empty'
    authorize_repo_spec "$gh_repo" "$REQUEST_HOST" || die 'GH_REPO owner or host is not allowed'
    return 0
  fi

  local default_status
  if resolve_default_repository; then
    default_status=0
  else
    default_status=$?
  fi
  if [ "$default_status" -eq 0 ]; then
    return 0
  elif [ "$default_status" -eq 2 ]; then
    die 'default repository output is invalid'
  fi

  resolve_cwd_repository || die 'cwd repository cannot be resolved or is not allowed'
}

resolve_operation "$@"

if is_read_only_operation "$@"; then
  exec "$REAL_GH" "$@"
fi

is_destination_checked_write || die "operation is not classified as a safe read or destination-checked write: ${SUBCOMMAND:-<empty>}"
case "$SUBCOMMAND" in
  'run rerun')
    validate_rerun_arguments "$@"
    ;;
esac
resolve_destination "$@"
case "$SUBCOMMAND" in
  'run rerun')
    verify_rerun_target
    ;;
esac

# Preserve the older optional account-role check for PR writers. This policy is
# deliberately evaluated only after the immutable destination owner check; it
# can add a rejection but can never turn a third-party destination into an
# allowed one.
policy_get() {
  local key="$1" file="$2" line
  [ -r "$file" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'#'*) continue ;;
      "$key="*) printf '%s\n' "${line#"$key"=}"; return 0 ;;
    esac
  done < "$file"
  return 1
}

policy_map_role() {
  local map_cwd="$1" file="$2" line prefix
  prefix="map=$map_cwd="
  [ -r "$file" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      "$prefix"*) printf '%s\n' "${line#"$prefix"}"; return 0 ;;
    esac
  done < "$file"
  return 1
}

enforce_optional_pr_account_guard() {
  local policy role expected actual
  case "$SUBCOMMAND" in
    'pr create'|'pr comment'|'pr review') ;;
    *) return 0 ;;
  esac
  policy="${PR_ACCOUNT_POLICY:-$HOME/.agents/config/pr-account-policy.conf}"
  [ -r "$policy" ] || return 0
  role="$(policy_map_role "$CURRENT_CWD" "$policy" 2>/dev/null)" || return 0
  expected="$(policy_get "${role}_login" "$policy" 2>/dev/null)" || die "account policy has no ${role}_login"
  set +e
  actual="$("$REAL_GH" api user --jq .login 2>/dev/null)"
  local status=$?
  set -e
  [ "$status" -eq 0 ] && [ -n "$actual" ] && [ "$actual" = "$expected" ] || \
    die "account policy rejected role '$role' (expected $expected, got ${actual:-<unresolvable>})"
}

# Proactive account selection (agmsg#83, and the withdrawn herdr-agent-monitor
# resolve_roster_vendor() from Issue #110/PR #173 -- see that project's git
# history commit a1015ff for the reference design this draws on).
#
# A cwd->role static map (pr-account-policy.conf, above) cannot express "which
# agmsg seat is THIS calling process" -- a single cwd routinely hosts more than
# one vendor's registration (e.g. both agmsg_owner_claude and agmsg_owner_codex
# are registered at this very directory), so a cwd-keyed lookup alone cannot
# tell them apart. whoami.sh already solves exactly that disambiguation for its
# own purpose (session-start markers, ancestor-pid walk, actas locks), so this
# reuses it rather than re-deriving process identity from scratch.
#
# Deliberately narrow: harness-vendor granularity only (claude-code vs codex),
# not the full producer/reviewer role split pr-account-policy.conf's role map
# encodes -- that static map continues to own role-level policy unchanged.
# This only removes the "remember to type GH_CONFIG_DIR=..." step for the
# common case where the caller hasn't already chosen an account.
#
# Every failure mode (whoami.sh missing or ambiguous/absent identity, no
# matching gh-logged-in account) returns 1 so the caller can preserve the
# static policy fallback. This never overrides an explicit caller-set
# credential or turns a currently-working call into a failing one.
proactively_select_account() {
  case "$SUBCOMMAND" in
    'pr create'|'pr comment'|'pr review') ;;
    *) return 1 ;;
  esac
  [ -z "${GH_CONFIG_DIR:-}" ] || return 1
  { [ -z "${GH_TOKEN:-}" ] && [ -z "${GITHUB_TOKEN:-}" ]; } || return 1
  local whoami_script="${AGMSG_WHOAMI_SCRIPT:-$HOME/.agents/skills/agmsg/scripts/whoami.sh}"
  [ -x "$whoami_script" ] || return 1
  local out type expected token
  out="$(bash "$whoami_script" "$CURRENT_CWD" 2>/dev/null)" || return 1
  case "$out" in
    agent=*' teams='*' type='*) ;;
    *) return 1 ;;
  esac
  case "$out" in *' multiple=true'*) return 1 ;; esac
  type="${out#*type=}"; type="${type%% *}"
  case "$type" in
    claude-code) expected=kappaseijin4claude ;;
    codex) expected=kappaseijin4codex ;;
    *) return 1 ;;
  esac
  token="$("$REAL_GH" auth token --user "$expected" 2>/dev/null)" || return 1
  [ -n "$token" ] || return 1
  export GH_TOKEN="$token"
  return 0
}

if proactively_select_account; then
  :
else
  enforce_optional_pr_account_guard
fi
exec "$REAL_GH" "$@"
