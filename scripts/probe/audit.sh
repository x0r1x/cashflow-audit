#!/usr/bin/env bash
set -euo pipefail
# All audit routes: POST file, GET status, GET step content, POST answers (HITL).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"
require_cmds

if [[ ! -f "$SAMPLE" ]]; then
  echo "sample not found: ${SAMPLE}" >&2
  exit 1
fi

RUN_DIR="${RUN_DIR:-$(new_run_dir)}"
mkdir -p "$RUN_DIR"

code="$(http_post_xlsx "$SAMPLE" "${RUN_DIR}/02-post.json")"
echo "POST /v1/audits -> ${code}"
if [[ "$code" != "202" && "$code" != "200" ]]; then
  echo "upload failed" >&2
  cat "${RUN_DIR}/02-post.json" >&2
  exit 1
fi

audit_id="$(json_field "${RUN_DIR}/02-post.json" audit_id)"
echo "audit_id=${audit_id}"
printf '%s\n' "$audit_id" >"${RUN_DIR}/audit_id.txt"

poll_audit "$audit_id" "${RUN_DIR}/03-status.json"

get_content() {
  local prefix="$1"
  local name code
  for name in layout mapping integrity report; do
    code="$(http_get "/v1/audits/${audit_id}/${name}" "${RUN_DIR}/${prefix}-${name}.json" auth)"
    echo "GET /v1/audits/${audit_id}/${name} -> ${code}"
    if [[ "$code" != "200" ]]; then
      echo "${name} not ready" >&2
      cat "${RUN_DIR}/${prefix}-${name}.json" >&2
      return 1
    fi
  done
}

get_content "04"

n_answers="$(answers_from_report "${RUN_DIR}/04-report.json" "${RUN_DIR}/05-answers-body.json")"
echo "POST answers n=${n_answers}"
code="$(http_post_json "/v1/audits/${audit_id}/answers" "${RUN_DIR}/05-answers-body.json" "${RUN_DIR}/05-answers.json")"
echo "POST /v1/audits/${audit_id}/answers -> ${code}"
if [[ "$code" != "202" && "$code" != "409" ]]; then
  echo "answers failed" >&2
  cat "${RUN_DIR}/05-answers.json" >&2
  exit 1
fi

if [[ "$code" == "202" ]]; then
  poll_audit "$audit_id" "${RUN_DIR}/06-status.json"
  get_content "07"
fi

echo "saved ${RUN_DIR}"
echo "$RUN_DIR"
