# Вход, выход и HTTP API

Как сервис устроен по смыслу: [`docs/guide.md`](../guide.md). Ниже — контракт HTTP.

База: `http://<host>:8080`. JSON UTF-8. Файл модели сервис не меняет.

`audit_id` = hex SHA-256(`X-Actor-Id` + `:` + sha256 файла). Один пользователь + тот же файл → тот же id. Разные пользователи с одним файлом → разные аудиты.

---

## 1. Что входит в приложение

| Вход | Как | Обязателен |
|---|---|---|
| Книга CashFlow | `.xlsx` / `.xlsm`, multipart поле `file` | да |
| Кто пользователь | заголовок `X-Actor-Id` | да для HTTP (`400 missing_actor` если нет). CLI: `anonymous` |
| Ответы аналитика (HITL) | JSON к уже существующему аудиту | нет, только если в отчёте `questions` |
| Конфиг процесса | env / `.env` → `Settings` (см. `.env.example`): `REDIS_URL` (обязателен для `serve`), `DATA_DIR`, слоты, TTL, бюджеты; опционально отдельные `LLM_*` и `EMBEDDING_*` | Redis для `serve`. LLM и embeddings — внешние HTTP, не в поде. Пороги детекторов в коде, не в env |

Не принимаем: `.xls`, `.xlsb`, пароль, URL внешней книги, правки ячеек.

Лимит тела: сжатый zip практически до **250 МиБ** (zip-bomb и потолок part — в parse). Content-Type файла: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` или `application/vnd.ms-excel.sheet.macroEnabled.12`.

CLI (тот же пайплайн, без Redis):

```bash
cashflow-audit audit ./model.xlsx -o ./report.json
```

---

## 2. Что выходит

Контроль: статус job. Контент — по шагам, не один жирный JSON.

| GET | Файл | Что внутри |
|---|---|---|
| `/v1/audits/{id}` | live / meta | статус, `content` ссылки |
| `/v1/audits/{id}/layout` | `layout.json` | оси, блоки, роли периодов |
| `/v1/audits/{id}/mapping` | `mapping.json` | rows + mapping questions |
| `/v1/audits/{id}/integrity` | `integrity.json` | карточки техники и identity |
| `/v1/audits/{id}/report` | `report.json` | FRS F01–F14, issues, вердикт, conclusions, индекс questions |

Не отдаём: parquet, `cached_value`, формулы, `source.xlsx`, `candidates.json`, `frs.json`, `lineage.json`. Нет `/findings` и нет `/risk-screen` (FRS = `/report`).

Клиент: `POST` файл → poll `GET /v1/audits/{id}` пока `running`/`queued` → при терминале `GET .../report` (итог) и при необходимости `.../integrity`. Layout/mapping доступны, как только файл появился.

---

## 3. Запросы

### `GET /healthz`

Процесс жив. Redis не проверяем.

```http
GET /healthz HTTP/1.1
```

```json
{ "status": "ok" }
```

`200`.

---

### `GET /readyz`

Можно принимать трафик. Redis обязателен. LLM и embeddings не обязательны для приёма трафика.

Проверка LLM/embeddings на /readyz — дешёвая:
GET {LLM_BASE_URL}/models (timeout 2s) и наличие LLM_MODEL в data[].id.
`LLM_BASE_URL` / `EMBEDDING_BASE_URL` — префикс как в env (голый хост получает `/v1`; `/api/v1` не переписываем).
Chat/embed: `{BASE}{LLM_CHAT_PATH}` / `{BASE}{EMBEDDING_PATH}` (default `/chat/completions`, `/embeddings`).
То же для embeddings. HTTP не 2xx, timeout, connect, model_missing → порт down.
Не вызываем chat/embeddings на /readyz (слот GPU).

Unset (нет URL; для embeddings ещё нет `EMBEDDING_MODEL`): поле llm/embeddings = false, status остаётся ready. `LLM_API_KEY` / `EMBEDDING_API_KEY` опциональны: локальный сервер без ключа всё равно configured.
Configured и down: status=degraded, HTTP 200, аудиты принимаем (шаблоны + глоссарий).

Глубокий ping (POST ping, max_tokens=1) — старт процесса и CLI `cashflow-audit ping`.

```json
{
  "status": "ready",
  "redis": true,
  "llm": true,
  "embeddings": false
}
```

| HTTP | `status` | Когда |
|---|---|---|
| `200` | `ready` | Redis up |
| `200` | `degraded` | Redis up, LLM и/или embeddings down |
| `503` | `not_ready` | Redis down |

Аудиты при `degraded` принимаем: чекеры работают, карточки шаблонные.

---

### `POST /v1/audits`

Загрузить книгу и поставить в очередь.

Несколько пользователей грузят файлы сразу: очередь Redis, до `WORKER_CONCURRENCY` прогонов в поде. Чужой `audit_id` → `403`.

```http
POST /v1/audits HTTP/1.1
X-Actor-Id: analyst-42
Content-Type: multipart/form-data; boundary=----b

------b
Content-Disposition: form-data; name="file"; filename="cashflow.xlsx"
Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet

(binary)
------b--
```

**Новый файл** — `202`:

```json
{
  "audit_id": "a3f1c8e0b91d4e6a7c2f0d8b5e1a9c4d6f8e2b0a1c3d5e7f9a0b2c4d6e8f0123",
  "status": "queued",
  "stage": "queued"
}
```

**Тот же файл этим же актёром уже считается** — `202`, тот же `audit_id`, без второго job.

**Тот же файл этим же актёром уже с отчётом** — `200`:

```json
{
  "audit_id": "a3f1c8e0b91d4e6a7c2f0d8b5e1a9c4d6e8f0123",
  "status": "succeeded",
  "stage": "done",
  "report_url": "/v1/audits/a3f1c8e0b91d4e6a7c2f0d8b5e1a9c4d6e8f0123/report"
}
```

Ошибки входа:

```json
{ "error": "unsupported_media_type", "detail": "ожидается .xlsx или .xlsm" }
```

| HTTP | `error` |
|---|---|
| `400` | `unsupported_media_type` / `empty_file` / `missing_actor` |
| `413` | `file_too_large` |
| `422` | `encrypted_workbook` / `zip_rejected` |

`encrypted` и часть zip-ошибок могут прийти и позже в статусе `failed`, если отсеклись на parse, а не на multipart.

---

### `GET /v1/audits/{audit_id}`

Порядок: Redis HASH → иначе `meta.json` (терминал) → иначе `owner.json` без `report.json` → `{status: queued}` (после рестарта пода, пока reconcile не подхватил). Нет `owner.json` → `404`. Тот же `X-Actor-Id`, что на POST.

**В работе:**

```json
{
  "audit_id": "a3f1c8e0b91d4e6a7c2f0d8b5e1a9c4d6e8f0123",
  "status": "running",
  "stage": "check",
  "source_filename": "cashflow.xlsx",
  "error": null
}
```

`stage`: `queued` | `parse` | `compile` | `layout` | `mapping` | `check` | `frs` | `lineage` | `explain`.

**Готово:**

```json
{
  "audit_id": "a3f1c8e0b91d4e6a7c2f0d8b5e1a9c4d6e8f0123",
  "status": "succeeded",
  "stage": "done",
  "source_filename": "cashflow.xlsx",
  "error": null,
  "report_url": "/v1/audits/a3f1c8e0b91d4e6a7c2f0d8b5e1a9c4d6e8f0123/report",
  "content": {
    "layout": "/v1/audits/a3f1c8e0b91d4e6a7c2f0d8b5e1a9c4d6e8f0123/layout",
    "mapping": "/v1/audits/a3f1c8e0b91d4e6a7c2f0d8b5e1a9c4d6e8f0123/mapping",
    "integrity": "/v1/audits/a3f1c8e0b91d4e6a7c2f0d8b5e1a9c4d6e8f0123/integrity",
    "report": "/v1/audits/a3f1c8e0b91d4e6a7c2f0d8b5e1a9c4d6e8f0123/report"
  }
}
```

| `status` | Смысл | Есть report? |
|---|---|---|
| `queued` | в stream | нет |
| `running` | пайплайн | нет |
| `succeeded` | всё посчитано, questions пусто, порты отработали или не понадобились | да |
| `needs_input` | отчёт есть и `questions` непусты (главнее, чем degraded) | да |
| `degraded` | отчёт есть, questions пусто, LLM не ответил, хотя eligible-находки были | да |
| `failed` | файл не разобрали | нет |

Один статус, приоритет: `failed` > `needs_input` > `degraded` > `succeeded`.

`failed`:

```json
{
  "audit_id": "a3f1c8e0b91d4e6a7c2f0d8b5e1a9c4d6e8f0123",
  "status": "failed",
  "stage": "parse",
  "source_filename": "broken.xlsx",
  "error": "zip_rejected"
}
```

`404` — неизвестный id. `403` — `X-Actor-Id` не совпал с владельцем:

```json
{ "error": "not_found" }
```

```json
{ "error": "forbidden" }
```

Poll раз в 1–2 с, пока `queued` или `running`. Заголовок `Retry-After: 2`.

---

### `GET /v1/audits/{audit_id}/report`

Только диск. Пока нет файла — `409`:

```json
{ "error": "report_not_ready", "status": "running", "stage": "layout" }
```

`GET .../layout`, `.../mapping`, `.../integrity` — тот же `X-Actor-Id`, 403/404 как у report. Нет файла → `409` с `error` (`layout_not_ready` / `mapping_not_ready` / `integrity_not_ready` / `report_not_ready`), `status`, `stage`.

`200` у `/report` — тонкий итог FRS (матрица, issues, positives, verdict, conclusions, индекс questions). Карточки Excel/identity — `GET .../integrity` (`f_*`), не дублируются в `findings`. FRS-проза — в `issues` (`B-F04`). `ready_for_credit` только в `verdict`: `false` или `null`, никогда `true`.

`200` — канон выхода приложения:

```json
{
  "audit_id": "a3f1c8e0b91d4e6a7c2f0d8b5e1a9c4d6e8f0123",
  "source_filename": "cashflow.xlsx",
  "sha256": "b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1",
  "status": "needs_input",
  "llm_used": true,
  "embeddings_used": true,
  "summary": {
    "findings": 0,
    "by_severity": { "error": 0, "warning": 0, "risk": 0 },
    "questions": 1,
    "headline": "Баланс не сходится (f_001)"
  },
  "findings": [],
  "risk_screen": [
    {"id": "F01", "name": "Динамика выручки факт→прогноз", "status": "clear", "confidence": "high", "evidence": "", "metrics": {}, "cell_refs": [], "issue_id": null},
    {"id": "F02", "name": "EBITDA и маржа", "status": "clear", "confidence": "high", "evidence": "", "metrics": {"margin": 0.18}, "cell_refs": [], "issue_id": null},
    {"id": "F03", "name": "Убытки / отр. EBITDA", "status": "clear", "confidence": "high", "evidence": "", "metrics": {}, "cell_refs": [], "issue_id": null},
    {"id": "F04", "name": "CFO/FCF и разрыв прибыль→деньги", "status": "flagged", "confidence": "medium", "evidence": "", "metrics": {"ni": 100, "cfo": 40, "fcf": -20, "cause": "ops"}, "cell_refs": ["CF!E12"], "issue_id": "B-F04"},
    {"id": "F05", "name": "Оборотный капитал", "status": "insufficient", "confidence": null, "evidence": "", "metrics": {}, "cell_refs": [], "issue_id": null},
    {"id": "F06", "name": "Долговая нагрузка", "status": "clear", "confidence": "medium", "evidence": "", "metrics": {"ratio": 2.1, "prev_ratio": 3.0}, "cell_refs": [], "issue_id": null},
    {"id": "F07", "name": "ICR / DSCR", "status": "clear", "confidence": "high", "evidence": "", "metrics": {"icr": 4.5}, "cell_refs": [], "issue_id": null},
    {"id": "F08", "name": "Ликвидность", "status": "clear", "confidence": "high", "evidence": "", "metrics": {"min_cash": 50, "period_key": "2025-03"}, "cell_refs": [], "issue_id": null},
    {"id": "F09", "name": "Концентрация погашений", "status": "clear", "confidence": "medium", "evidence": "", "metrics": {"share": 0.22, "year": "2026"}, "cell_refs": [], "issue_id": null},
    {"id": "F10", "name": "Процентный / валютный", "status": "insufficient", "confidence": null, "evidence": "", "metrics": {}, "cell_refs": [], "issue_id": null},
    {"id": "F11", "name": "Агрессивность предпосылок", "status": "clear", "confidence": "high", "evidence": "", "metrics": {}, "cell_refs": [], "issue_id": null},
    {"id": "F12", "name": "Непоследовательность драйверов", "status": "not_applicable", "confidence": null, "evidence": "", "metrics": {}, "cell_refs": [], "issue_id": null},
    {"id": "F13", "name": "Дивиденды vs FCFE", "status": "not_applicable", "confidence": null, "evidence": "", "metrics": {}, "cell_refs": [], "issue_id": null},
    {"id": "F14", "name": "Headroom", "status": "insufficient", "confidence": null, "evidence": "", "metrics": {}, "cell_refs": [], "issue_id": null}
  ],
  "issues": [
    {
      "id": "B-F04",
      "control_id": "F04",
      "class_name": "cash_conversion",
      "priority": "medium",
      "metrics": {"ni": 100, "cfo": 40, "fcf": -20, "cause": "ops"},
      "cell_refs": ["CF!E12"],
      "cause": "Прибыль не конвертируется в кэш (ops)",
      "impact": "NI 100 при CFO 40 и FCF −20"
    }
  ],
  "positives": [
    {"control_id": "F02", "text": "Маржа EBITDA 0.18"},
    {"control_id": "F06", "text": "ND/EBITDA снизился с 3x до 2.1x"},
    {"control_id": "F07", "text": "ICR 4.5x"},
    {"control_id": "F08", "text": "min cash 50 (2025-03)"}
  ],
  "verdict": {
    "integrity": "Расчётная целостность нарушена.",
    "trends": "Прибыль не равна деньгам (NI vs CFO vs FCF).",
    "risks": "F04 (medium)",
    "liquidity": "F08 min cash 50 (clear); F09 концентрация погашений 22% в 2026",
    "recommendation": "Модель не готова к кредитному процессу, пока не закрыты перечисленные B-F* и вопросы целостности.",
    "ready_for_credit": false
  },
  "conclusions": [
    {
      "id": "c_001",
      "kind": "trust",
      "severity": "error",
      "metrics": ["bs.assets", "bs.equity", "bs.liabilities"],
      "title": "Баланс не сходится",
      "body": "Контрольное равенство нарушено в 2 периодах. Метрики этих периодов недостоверны, пока разрыв не объяснён.",
      "finding_ids": ["f_001"],
      "cell_refs": ["BS!E27", "BS!E63"],
      "recommendation": "Проверить указанные ячейки. Файл не изменён."
    },
    {
      "id": "c_002",
      "kind": "dynamics",
      "severity": "risk",
      "metrics": ["pnl.net_income", "cf.fcf"],
      "title": "Прибыль не равна деньгам",
      "body": "По равенствам эта метрика в этом прогоне не опровергнута; наблюдается сигнал frs.F04 (CF!E12).",
      "finding_ids": ["B-F04"],
      "cell_refs": ["CF!E12"],
      "recommendation": "Проверить указанные ячейки. Файл не изменён."
    }
  ],
  "questions": [
    {
      "id": "q_012",
      "kind": "mapping",
      "prompt": "Строка «Итого» под Assets — это bs.assets_total?",
      "cell_refs": ["BS!C27"],
      "options": ["bs.assets_total", "bs.assets_current", "unknown"]
    }
  ],
  "provenance": {
    "detector_version": "1",
    "llm_model": "qwen3.6-27b-fp8",
    "embedding_model": "bge-m3"
  }
}
```

`findings` в тонком отчёте пустой: Excel/identity не копируются сюда (они в `/integrity` как `f_*`). Поля находки integrity = требования: адрес, доказательство, метрики, влияние, рекомендация без правки файла. Без `cell_refs` из IR карточки в `/integrity` нет.

`summary.headline` — одна фраза: сначала trust-error с id integrity (`f_001`), иначе high F-issue (`B-F08`), иначе шаблон полноты. Это не вердикт «модель верна». `conclusions[]` собирает код: `finding_ids` — `f_*` из integrity **или** `B-F*` из `issues`; `cell_refs` ⊆ refs этих карточек/issues ⊆ IR. LLM текст выводов и вердикт не пишет; ChatPort может переписать только cause/impact flagged-issue (текст, не числа, не статус F-строки). Пустой прогон: `conclusions` пуст, headline про включённые проверки. Старый `report.json` без этих полей читается с defaults.

`404` если аудита не было. `403` если `X-Actor-Id` не владелец (`owner.json`). Отдельного `/findings` или `/risk-screen` нет. `sha256` в отчёте — хеш **содержимого** файла, не `audit_id`.

---

### `POST /v1/audits/{audit_id}/answers`

Ответы на `questions`. IR не пересчитывается. В очередь кладётся только `audit_id`; worker гоняет пайплайн с начала, skip доходит до mapping сам.

```http
POST /v1/audits/{audit_id}/answers HTTP/1.1
X-Actor-Id: analyst-42
Content-Type: application/json
```

```json
{
  "answers": [
    {
      "question_id": "q_012",
      "concept_id": "bs.assets_total"
    }
  ]
}
```

`202`:

```json
{
  "audit_id": "a3f1c8e0b91d4e6a7c2f0d8b5e1a9c4d6e8f0123",
  "status": "queued",
  "stage": "queued"
}
```

Дальше снова poll и `GET .../report`.

| HTTP | Когда |
|---|---|
| `403` | чужой аудит |
| `404` | нет аудита |
| `409` | ещё `running` / нет `questions` |
| `422` | неизвестный `question_id` или `concept_id` не из `options` |

---

## 4. Типичный сценарий

```
POST /v1/audits                    202  { audit_id, status: queued }
GET  /v1/audits/{id}               200  { status: running, stage: compile }
GET  /v1/audits/{id}               200  { status: needs_input, report_url }
GET  /v1/audits/{id}/report        200  { risk_screen, issues, positives, verdict, conclusions, questions }
POST /v1/audits/{id}/answers       202  { status: queued, stage: queued }
GET  /v1/audits/{id}/report        200  { status: succeeded, questions: [] }
```

Повторный `POST` того же файла после `succeeded` сразу `200` с `report_url`, без очереди и без LLM.
