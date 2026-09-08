#!/usr/bin/env bash
set -euo pipefail
# GET /healthz and /readyz. Writes JSON under RUN_DIR or a new probe-out stamp.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"
require_cmds

RUN_DIR="${RUN_DIR:-$(new_run_dir)}"
mkdir -p "$RUN_DIR"

code="$(http_get /healthz "${RUN_DIR}/00-healthz.json")"
echo "GET /healthz -> ${code}"
if [[ "$code" != "200" ]]; then
  echo "healthz failed" >&2
  exit 1
fi

code="$(http_get /readyz "${RUN_DIR}/01-readyz.json")"
echo "GET /readyz -> ${code}"
if [[ "$code" != "200" ]]; then
  echo "readyz not ready (need Redis)" >&2
  exit 1
fi
status="$(json_field "${RUN_DIR}/01-readyz.json" status || true)"
echo "readyz status=${status}"
echo "$RUN_DIR"
