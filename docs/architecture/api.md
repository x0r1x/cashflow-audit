# Вход, выход и HTTP API

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

Два внешних объекта. Всё остальное (parquet, mapping) — внутренние артефакты диска, по HTTP не отдаём.

1. **Статус job** — пока считается или после финала.
2. **Отчёт** — когда есть `report.json`.

Клиент: `POST` файл → poll `GET /v1/audits/{id}` пока `running`/`queued` → `GET .../report`.

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
`LLM_BASE_URL` / `EMBEDDING_BASE_URL` — OpenAI-compatible корень со `/v1` (не native `/api/v1/chat`).
То же для embeddings. HTTP не 2xx, timeout, connect, model_missing → порт down.
Не вызываем /chat/completions и /embeddings здесь (слот GPU).

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

`stage`: `queued` | `parse` | `compile` | `layout` | `mapping` | `check` | `lineage` | `explain`.

**Готово:**

```json
{
  "audit_id": "a3f1c8e0b91d4e6a7c2f0d8b5e1a9c4d6e8f0123",
  "status": "succeeded",
  "stage": "done",
  "source_filename": "cashflow.xlsx",
  "error": null,
  "report_url": "/v1/audits/a3f1c8e0b91d4e6a7c2f0d8b5e1a9c4d6e8f0123/report"
}
```

| `status` | Смысл | Есть report? |
|---|---|---|
| `queued` | в stream | нет |
| `running` | пайплайн | нет |
| `succeeded` | всё посчитано, questions пусто, порты отработали или не понадобились | да |
| `needs_input` | отчёт есть и `questions` непусты (главнее, чем degraded) | да |
| `degraded` | отчёт есть, questions пусто, LLM и/или embeddings не ответили | да |
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
    "findings": 4,
    "by_severity": { "error": 1, "warning": 2, "risk": 1 },
    "questions": 1
  },
  "findings": [
    {
      "id": "f_014",
      "severity": "warning",
      "detector": "hardcode_in_formula",
      "cell_refs": ["P&L!D24"],
      "title": "Ставка роста 20% зашита в формулу",
      "evidence": "D23=C23*(1+Inputs!D5); D24=C24*1.20. Прогнозный период.",
      "affected_metrics": ["pnl.revenue", "pnl.ebitda"],
      "impact": "направление: искажение прогнозной выручки",
      "recommendation": "Сверить ставку с листом Inputs, заменить константу на ссылку. Файл не изменён.",
      "related_ids": ["f_022"],
      "tags": [],
      "need_user_input": false
    },
    {
      "id": "f_001",
      "severity": "error",
      "detector": "identity.I1",
      "cell_refs": ["BS!E27", "BS!E63"],
      "title": "Баланс не сходится",
      "evidence": "Assets 475557 − (Equity+Liabilities) 475557 ≠ 0 в 2023E; в 2025E delta = 1200 (порог 475).",
      "affected_metrics": ["bs.assets", "bs.equity", "bs.liabilities"],
      "impact": "≈1200 ед. отчётности; прочие метрики периода могут быть недостоверны",
      "recommendation": "Проверить скрытые строки и ручную корректировку у итога. Файл не изменён.",
      "related_ids": [],
      "tags": ["hidden"],
      "need_user_input": false
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

Поля находки = требования: адрес, доказательство, метрики, влияние, рекомендация без правки файла. Без `cell_refs` из IR находки в ответе нет.

`404` если аудита не было. `403` если `X-Actor-Id` не владелец (`owner.json`). Отдельного `/findings` нет. `sha256` в отчёте — хеш **содержимого** файла, не `audit_id`.

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
GET  /v1/audits/{id}/report        200  { findings, questions }
POST /v1/audits/{id}/answers       202  { status: queued, stage: queued }
GET  /v1/audits/{id}/report        200  { status: succeeded, questions: [] }
```

Повторный `POST` того же файла после `succeeded` сразу `200` с `report_url`, без очереди и без LLM.
