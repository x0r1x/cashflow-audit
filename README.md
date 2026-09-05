# cashflow-audit

Аудит Excel CashFlow-моделей (`.xlsx` / `.xlsm`). Файл не меняется. Детекторы — код; LLM не парсит книгу и не ищет ошибки: только неоднозначный маппинг статей и текст карточки.

Выход — `report.json`: находка с `cell_refs` из IR, доказательство, влияние, рекомендация.

```
parse → compile → layout → series → mapping → check → lineage → explain+report
```

LLM и embeddings — **отдельные** внешние OpenAI-compatible HTTP-сервисы, не в поде. Без них аудит всё равно завершается (`degraded` / `needs_input`, шаблоны + глоссарий).

## Требования

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Redis только для `serve` (sidecar в compose)

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

## Конфиг

Скопировать [`.env.example`](.env.example) → `.env` (файл в gitignore). Все ключи читает `Settings` (`src/cashflow_audit/settings.py`).

| Слой | Примеры | Где |
|---|---|---|
| Секреты и URL моделей | `REDIS_URL`, `LLM_*`, `EMBEDDING_*` | env / `.env` |
| Кнопки процесса | `DATA_DIR`, слоты, TTL, бюджеты | тот же Settings, дефолты в коде |
| Правила аудита | cosine, zip-caps, CSR, `taxonomy.yaml` | исходники, не env |

`LLM_BASE_URL` и `EMBEDDING_BASE_URL` независимы: эмбедер не берёт URL чата.

## CLI

Без Redis, `actor_id=anonymous`:

```bash
uv run cashflow-audit audit ./model.xlsx -o ./report.json --data-dir ./data
```

Артефакты: `data/audits/{audit_id}/`. Повтор того же файла пропускает готовые стадии (LLM не зовётся, если есть `mapping.json` / `report.json`).

## HTTP

Нужен Redis. Заголовок `X-Actor-Id` обязателен.

```bash
uv run cashflow-audit serve --host 127.0.0.1 --port 8080
```

| Метод | Путь |
|---|---|
| GET | `/healthz` |
| GET | `/readyz` |
| POST | `/v1/audits` (multipart `file`) |
| GET | `/v1/audits/{id}` |
| GET | `/v1/audits/{id}/report` |
| POST | `/v1/audits/{id}/answers` |

Схема и коды: [`docs/architecture/api.md`](docs/architecture/api.md).

## Docker

В образе нет весов LLM/embed. Redis — sidecar, модели — с хоста через env.

```bash
cp .env.example .env   # заполнить LLM_* и EMBEDDING_*, если есть
docker compose up --build
```

`REDIS_URL` внутри сети — `redis://redis:6379/0`. Данные аудитов — volume `/data`.

## Документы

- Требования: [`docs/требования.md`](docs/требования.md)
- Архитектура: [`docs/architecture/plan.md`](docs/architecture/plan.md)
- HTTP API: [`docs/architecture/api.md`](docs/architecture/api.md)
- Правила разработки: [`AGENTS.md`](AGENTS.md)

Apache-2.0.
