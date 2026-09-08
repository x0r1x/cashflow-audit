# Shared helpers for scripts/probe/*.sh. Source only.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
ACTOR="${ACTOR:-probe}"
OUT_ROOT="${OUT_ROOT:-$ROOT/scripts/probe-out}"
SAMPLE="${SAMPLE:-$ROOT/resources/Примеры excel/sample_full_model.xlsx}"
XLSX_CT="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
POLL_SEC="${POLL_SEC:-2}"
PROBE_TIMEOUT_SEC="${PROBE_TIMEOUT_SEC:-180}"

require_cmds() {
  local missing=0
  local cmd
  for cmd in curl python3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      echo "need $cmd in PATH" >&2
      missing=1
    fi
  done
  if [[ "$missing" -ne 0 ]]; then
    return 1
  fi
}

json_field() {
  local file="$1"
  local key="$2"
  python3 -c 'import json, sys
path, key = sys.argv[1], sys.argv[2]
data = json.load(open(path, encoding="utf-8"))
if not isinstance(data, dict) or key not in data:
    sys.exit(1)
value = data[key]
if value is None:
    print("")
else:
    print(value)
' "$file" "$key"
}

http_get() {
  local path="$1"
  local out="$2"
  if [[ "${3:-}" == "auth" ]]; then
    curl -sS -o "$out" -w "%{http_code}" -H "X-Actor-Id: ${ACTOR}" "${BASE_URL}${path}"
  else
    curl -sS -o "$out" -w "%{http_code}" "${BASE_URL}${path}"
  fi
}

http_post_xlsx() {
  local file="$1"
  local out="$2"
  curl -sS -o "$out" -w "%{http_code}" \
    -H "X-Actor-Id: ${ACTOR}" \
    -F "file=@${file};type=${XLSX_CT};filename=$(basename "$file")" \
    "${BASE_URL}/v1/audits"
}

http_post_json() {
  local path="$1"
  local body="$2"
  local out="$3"
  curl -sS -o "$out" -w "%{http_code}" \
    -H "X-Actor-Id: ${ACTOR}" \
    -H "Content-Type: application/json" \
    --data-binary "@${body}" \
    "${BASE_URL}${path}"
}

poll_audit() {
  local audit_id="$1"
  local out="$2"
  local deadline=$((SECONDS + PROBE_TIMEOUT_SEC))
  local code status stage
  while ((SECONDS < deadline)); do
    code="$(http_get "/v1/audits/${audit_id}" "$out" auth)"
    if [[ "$code" != "200" ]]; then
      echo "GET /v1/audits/${audit_id} -> ${code}" >&2
      cat "$out" >&2
      return 1
    fi
    status="$(json_field "$out" status)"
    stage="$(json_field "$out" stage || true)"
    echo "status=${status} stage=${stage}"
    case "$status" in
      queued | running) sleep "$POLL_SEC" ;;
      *) return 0 ;;
    esac
  done
  echo "timeout after ${PROBE_TIMEOUT_SEC}s (status=${status:-unknown})" >&2
  return 1
}

answers_from_report() {
  local report="$1"
  local out="$2"
  python3 -c 'import json, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
answers = []
for question in report.get("questions") or []:
    qid = question.get("id")
    options = question.get("options") or []
    if qid and options:
        answers.append({"question_id": qid, "concept_id": options[0]})
json.dump({"answers": answers}, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(len(answers))
' "$report" "$out"
}

new_run_dir() {
  local dir="${OUT_ROOT}/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$dir"
  echo "$dir"
}
