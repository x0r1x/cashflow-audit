# Проверка HTTP API

Скрипты дергают живой `serve`: все HTTP-роуты, книга `sample_full_model.xlsx`, ответы в `scripts/probe-out/` (в git не попадает).

Нужны `curl` и `python3`. Сервис уже должен слушать порт (Redis для `serve` обязателен).

## Быстрый прогон

Из корня репозитория:

```bash
# терминал 1
.venv/bin/cashflow-audit serve --host 127.0.0.1 --port 8080

# терминал 2
bash scripts/probe/run.sh
```

Или Docker: `docker compose up --build`, затем тот же `run.sh`.

Успех: в конце `ok scripts/probe-out/YYYYMMDD-HHMMSS`. Код выхода 0. Падение health/readyz/загрузки/таймаута — ненулевой код, тело ответа в том же каталоге.

## Что куда пишется

Каждый прогон — новый каталог `scripts/probe-out/YYYYMMDD-HHMMSS/`:

| Файл | Запрос |
|---|---|
| `00-healthz.json` | `GET /healthz` |
| `01-readyz.json` | `GET /readyz` |
| `02-post.json` | `POST /v1/audits` (multipart `file`) |
| `03-status.json` | `GET /v1/audits/{id}` |
| `04-report.json` | `GET /v1/audits/{id}/report` |
| `05-answers-body.json` | тело HITL (первый option каждой question) |
| `05-answers.json` | `POST /v1/audits/{id}/answers` (`202` или `409`) |
| `06-status.json` | статус после answers, если был `202` |
| `07-report.json` | отчёт после HITL, если был `202` |
| `audit_id.txt` | id прогона |

`readyz` со `status=degraded` — норма: аудиты принимаются без LLM. `503 not_ready` — Redis недоступен, скрипт останавливается.

Повтор того же файла тем же `ACTOR` после готового отчёта даёт `POST` 200 и сразу `report_url`, без новой job.

## Скрипты по отдельности

```bash
bash scripts/probe/health.sh          # GET /healthz, GET /readyz
bash scripts/probe/audit.sh           # POST /v1/audits, GET status, GET report, POST answers
```

Если задать `RUN_DIR`, оба пишут в него; `run.sh` задаёт общий каталог сам.

## Переменные

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `BASE_URL` | `http://127.0.0.1:8080` | база API |
| `ACTOR` | `probe` | заголовок `X-Actor-Id` |
| `SAMPLE` | `resources/Примеры excel/sample_full_model.xlsx` | книга |
| `OUT_ROOT` | `scripts/probe-out` | куда складывать прогоны |
| `POLL_SEC` | `2` | пауза между GET статуса |
| `PROBE_TIMEOUT_SEC` | `180` | сколько ждать терминальный статус |
| `RUN_DIR` | новый штамп под `OUT_ROOT` | конкретный каталог прогона |

Пример:

```bash
BASE_URL=http://127.0.0.1:8080 \
ACTOR=probe \
PROBE_TIMEOUT_SEC=300 \
bash scripts/probe/run.sh
```

Другой xlsx:

```bash
SAMPLE="resources/Примеры excel/sample_3stmt.xlsx" bash scripts/probe/audit.sh
```

Файл должен существовать локально (`resources/` в git не хранится — книга должна быть на диске).

## Ошибки

- `need curl in PATH` / `need python3 in PATH` — поставьте утилиты.
- `sample not found` — нет xlsx по `SAMPLE`.
- `healthz failed` — процесс API не жив или не тот `BASE_URL`.
- `readyz not ready` — нет Redis (для compose: `docker compose up`).
- `upload failed` — смотрите `02-post.json` (`missing_actor` не должен случиться: заголовок ставит скрипт).
- `timeout after 180s` — job ещё `queued`/`running`; увеличьте `PROBE_TIMEOUT_SEC` или смотрите логи `serve`.
- `report not ready` — статус терминальный, но `GET …/report` не 200; смотрите `03-status.json` и `04-report.json` (`failed` отчёта не отдаёт).
- `POST …/answers` `409` — вопросов не было или job уже в очереди; маршрут всё равно вызван, тело в `05-answers.json`.
