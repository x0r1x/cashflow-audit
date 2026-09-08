#!/usr/bin/env bash
set -euo pipefail
# Full probe: healthz + readyz, then sample_full_model.xlsx → report in scripts/probe-out/.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"
require_cmds

export RUN_DIR
RUN_DIR="$(new_run_dir)"
echo "run dir ${RUN_DIR}"
echo "base ${BASE_URL}"
echo "sample ${SAMPLE}"

"${SCRIPT_DIR}/health.sh"
"${SCRIPT_DIR}/audit.sh"
echo "ok ${RUN_DIR}"
