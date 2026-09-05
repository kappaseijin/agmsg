#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
if [ "${1:-}" != "--once" ] || [ "$#" -ne 1 ]; then
  echo 'usage: pm-audit.sh --once' >&2
  exit 2
fi
exec node "$SCRIPT_DIR/pm-audit.js"
