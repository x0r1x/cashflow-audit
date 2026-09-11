# Архитектура сервиса аудита Excel CashFlow-моделей

Смысл для человека без контекста: [`docs/guide.md`](../guide.md). Ниже — спецификация стадий и инвариантов.

| Поле | Значение |
|---|---|
| Статус | Draft |
| Дата | 2026-09-04 |
| Под | app + Redis sidecar |
| Вне пода | LLM (Qwen 3.6 27B FP8), embeddings — OpenAI-compatible HTTP |
| Query | DuckDB in-process над parquet |
| API | [`api.md`](api.md) |
| Требования | [`docs/требования.md`](../требования.md) |

---

## 1. Задача

Вход: `.xlsx` / `.xlsm`. Выход: JSON-отчёт с проверяемыми находками (ячейка, доказательство, влияние, рекомендация). Файл модели не меняется. Формат книг не фиксирован.

Детектор — код. LLM не парсит Excel и не ищет ошибки: только неоднозначный маппинг статей и перепись текста кандидата.

```
REDIS_URL=redis://127.0.0.1:6379/0
LLM_BASE_URL=…/v1  LLM_MODEL=…  [LLM_API_KEY опционален]  [LLM_TLS_CA_FILE=ca.pem]
EMBEDDING_BASE_URL=…/v1  EMBEDDING_MODEL=…  [EMBEDDING_API_KEY опционален]  [EMBEDDING_TLS_CA_FILE=ca.pem]
# OpenAI-compatible: {base}/chat/completions, {base}/embeddings, {base}/models
# HTTPS: публичный CA — ничего; свой PEM — LLM_TLS_CA_FILE / EMBEDDING_TLS_CA_FILE отдельно на каждый порт.
```

Нет GPU/весов в репозитории, нет Postgres/SQLite/S3/Polars, нет DuckDB-сервера. Redis только loopback пода. Нет LLM/embeddings — аудит завершается (`degraded` или `needs_input`, шаблоны + глоссарий).

---

## 2. Три плоскости

| Плоскость | Где | Что |
|---|---|---|
| Control | Redis | очередь, live status/stage, лок, бюджет LLM/embed |
| Query | DuckDB `:memory:` | SQL по parquet аудита, жизнь = `Pipeline.run` |
| Durable | диск **на PVC** (не emptyDir) | xlsx, parquet, JSON стадий, owner, терминальный meta |

Находки только `report.json`. Ячейки только parquet. Нет `.duckdb` на диске, нет `index.json`, нет SQLite.

Порты: `ChatPort`, `EmbedPort`, `AuditStore`, `JobBus`. SDK в `adapters/`. CLI и HTTP → один `Pipeline.run()`.

---

## 3. Идемпотентность и пользователи

Заголовок `X-Actor-Id` (потом JWT `sub`). HTTP без заголовка — `400 missing_actor` (не `anonymous`: иначе все гости делят одни аудиты). CLI — `actor_id=anonymous`.

`content_sha256` = sha256(байтов файла).  
`audit_id` = sha256(`actor_id` + `:` + `content_sha256`).

Один и тот же файл у **разных** пользователей — разные аудиты (отчёт не светится соседу). У одного пользователя тот же файл — тот же id.

**POST /v1/audits** (в пределах `actor_id`)

| Уже есть | Ответ | Stream |
|---|---|---|
| свой `report.json` | `200` + `report_url` | нет |
| свой HASH `queued`/`running` | `202` тот же id | нет |
| свой каталог без отчёта | `202`, `XADD` если нет pending | один раз |
| пусто | каталог + `XADD` | да |

Повторный POST не перетирает терминальный HASH (`succeeded`/`degraded`/`needs_input`), даже если гонка прошла check до появления `report.json`. `mark_queued(..., replace_terminal=True)` только у HITL и reconcile. `failed` без report — retry.

GET/answers: `owner.json.actor_id` должен совпасть, иначе `403`. `owner.json` пишется на POST и **не** стирается HITL (в отличие от терминального `meta.json`). Глоссарий HITL — `data/glossary/{actor_id}.json`, не общий файл.

**Стадии** строго подряд. Skip, если артефакт есть (tmp + rename). `force` нет.

| Стадия | Skip если есть | На skip всё равно |
|---|---|---|
| parse | `raw/cells.parquet` + `raw/workbook.json` | — |
| compile | `ir/cells.parquet` + `ir/edges.parquet` | собрать CSR из edges |
| layout | `layout.json` | зарегистрировать оси в DuckDB |
| series | нет файла | всегда `CREATE VIEW` после layout |
| mapping | `mapping.json` | **не** звать embed/LLM |
| check | `candidates.json` | — |
| lineage | `lineage.json` | — |
| explain+report | `report.json` + `meta.json` | **не** звать LLM |

Worker **всегда** `Pipeline.run` с parse. HITL не передаёт `resume_from`: удалили хвост артефактов — skip сам остановится на mapping.

**HITL:** свой glossary += answers; стереть `mapping.json`, `candidates.json`, `lineage.json`, `report.json`, `meta.json`; `XADD` только `audit_id`.

**Рестарт app** (Redis жив): `XAUTOCLAIM` idle > 60s. **Рестарт пода** (Redis emptyDir пуст, диск PVC цел): `XADD` каталогов с `owner.json` без `report.json`. GET в этот момент: нет HASH, есть `owner.json` без report → `queued` (reconcile), не `404`.

**Дедлайн прогона:** `JOB_TIMEOUT_SEC` (default 3600). Превышение → `failed` `error=timeout`, слоты отпустить.

**Retention:** `AUDIT_TTL_DAYS` (default 14). При старте и раз в сутки удалять каталоги старше TTL, кроме live `running` и `queued`. Не копить PVC бесконечно.

**CLI:** тот же skip по диску, Redis не нужен; `actor_id=anonymous`.

---

## 3.1 Параллелизм (много пользователей, много файлов)

Глобального `cf:lock:pipeline` **нет**: иначе все пользователи стоят в одной очереди CPU.

Изоляция прогона:

| Ресурс | Как не столкнуть потоки |
|---|---|
| Диск | каталог `data/audits/{audit_id}/` — чужие аудиты не пишут сюда |
| DuckDB | **свой** `:memory:` на каждый `Pipeline.run`; соединение не шарить между потоками |
| CSR | в RAM этого run, не глобальный |
| Redis job | HASH/budget ключи с `{audit_id}` |
| Glossary | файл на `actor_id`, запись под `SET NX` локом `cf:lock:glossary:{actor_id}` |
| LLM GPU | отдельный слот, уже чем CPU (см. ниже) |

**Пул воркеров** в процессе API:

- `WORKER_CONCURRENCY` (env, default **4**) задач: каждая `XREADGROUP` с именем `worker-{i}` и `asyncio.to_thread(Pipeline.run)`.
- HTTP poll не блокируется parse/compile.
- Heartbeat **на аудит**: `cf:lock:audit:{id}` NX + таймер, чтобы один и тот же id не взяли два consumer.

**Слоты** (`MAX_INFLIGHT` = `WORKER_CONCURRENCY`, default 4):

```
WORKER_CONCURRENCY=4    # число consumer-циклов
MAX_INFLIGHT=4          # = concurrency; одновременных Pipeline.run
MAX_LLM_INFLIGHT=1      # ChatPort (одна GPU у соседа)
MAX_EMBED_INFLIGHT=4
```

Воркер: сначала членство в `cf:inflight:run` (SET, не INCR — иначе слот течёт при kill), потом `XREADGROUP`; пустой read — убрать id из SET. Потолок = `SCARD`. Те же SET для llm/embed.

Нет LLM-слота — ждать **с таймаутом** (`LLM_SLOT_WAIT_SEC`, default 120). Истекло — mapping/explain идут шаблоном, статус может стать `degraded`, run-слот не держим вечно (иначе 4 воркера встанут в очередь к одной GPU и parse других файлов остановится).

Горизонтальный scale (второй под) на этапе 1 **не делаем**: Redis sidecar на loopback не общий. Несколько пользователей обслуживает пул воркеров **внутри одного пода**.

Не параллелить **стадии одного** аудита (parse∥compile запрещены). Параллель — **между** аудитами.

---

## 4. Компоненты

```
HTTP POST ─┐
HTTP POST ─┼─► Stream cf:audits ─► worker-0 ─► to_thread(run) ─► DuckDB mem A
HTTP POST ─┘                      worker-1 ─► to_thread(run) ─► DuckDB mem B
                                  worker-2 ─► …
CLI ──────────────────────────────────────────► Pipeline.run (один файл)
```

| Компонент | Владеет | Не делает |
|---|---|---|
| api / cli | HTTP/argv | Excel |
| pipeline | порядок, skip, heartbeat, деградация портов | правила формул |
| JobBus | stream, HASH, лок **на audit_id**, слоты | IR, report |
| IrCatalog | DuckDB views | статус job, SCC |
| store | каталог диска | очередь |
| parse | OOXML → raw | AST |
| compile | AST, шаблон, рёбра, CSR | блоки |
| layout | геометрия, роль **периода** | concept_id |
| series | SQL outliers | detector как чекер |
| mapping | concept_id, роль **статьи** | hist/forecast |
| graph | SCC, BFS | текст |
| checkers | Candidate[] | проза, HTTP |
| explain | Finding | новые refs/метрики |
| adapters | OpenAI, redis-py | домен |

---

## 5. HTTP

Тела и примеры: [`api.md`](api.md). `serve` требует Redis. `WORKER_CONCURRENCY` consumer’ов в процессе API.

`POST /v1/audits` · `GET /v1/audits/{id}` · `GET /v1/audits/{id}/report` · `POST /v1/audits/{id}/answers` · `/healthz` · `/readyz`

Терминальный `status` (один, не флаги):

`failed` > `needs_input` (questions непусты) > `degraded` (LLM не ответил при eligible-находках, questions пусто) > `succeeded`

Live: `queued` | `running`. Нет `/findings`.

---

## 6. Redis sidecar

```
Pod: cashflow-audit :8080 + redis:7-alpine :6379 (не наружу)
```

| Ключ | Зачем |
|---|---|
| `cf:audits` STREAM поле `audit_id` | очередь |
| `cf:audits:workers` | consumer group, несколько `worker-{i}` |
| `cf:job:{id}` HASH | live status, stage, error, actor_id |
| `cf:lock:audit:{id}` | один run этого файла + heartbeat |
| `cf:lock:glossary:{actor}` | запись HITL-словаря |
| `cf:inflight:run` `:llm` `:embed` | SET audit_id, потолок = SCARD (не INCR: не течёт при kill) |
| `cf:budget:{id}:llm` `:emb` | потолок вызовов на прогон; DEL в конце |

В Redis нет ячеек, карточек, векторов онтологии. TTL HASH 24h после терминала.

```python
class JobBus(Protocol):
    async def enqueue(self, audit_id: str) -> None: ...
    async def claim(self) -> Job | None: ...
    async def ack(self, job: Job) -> None: ...
    async def set_progress(self, audit_id: str, stage: str) -> None: ...
    async def get_live(self, audit_id: str) -> JobState | None: ...
    async def acquire_audit(self, audit_id: str) -> bool: ...
    async def try_slot(self, kind: Literal["run", "llm", "embed"], audit_id: str) -> bool: ...
    async def release_slot(self, kind: Literal["run", "llm", "embed"], audit_id: str) -> None: ...
```

---

## 7. DuckDB

Не контейнер. Один `IrCatalog` на **каждый** `Pipeline.run` (не общий на процесс: DuckDB connection не thread-safe).

1. После compile (и если compile skip) — `open(":memory:")`, views `cells`/`edges` = `read_parquet`.
2. После layout — таблицы оси + `CREATE VIEW series_outliers`.
3. `finally: close()`. `.duckdb` не пишем.

Чекеры только `catalog.sql`. `COPY TO parquet` — единственный writer колонок. SCC/BFS, LLM, Redis — не SQL.

---

## 8. Стадии (детально)

Каждая: вход с диска → артефакт → `set_progress`.

### 8.0 Приём (API)

HTTP: обязателен `X-Actor-Id`. Считать `content_sha256` и `audit_id`. Записать `source.xlsx` и `owner.json` `{actor_id, content_sha256, source_filename}` (tmp+rename). HSET `queued` (поля actor_id, stage). `XADD` если нужно.

Воркер: `try_slot("run")` → XREADGROUP → `cf:lock:audit:{id}` + heartbeat → `to_thread(run)` → ACK → release lock и slot. `Pipeline.run` в `finally` закрывает DuckDB; ACK делает воркер, не стадия explain.

### 8.1 parse

**Зачем:** факты OOXML, без смысла формул.

**Вход:** `source.xlsx`. **Выход:** `raw/cells.parquet`, `raw/workbook.json`.

Zip-slip, depth 0, ratio ≥ 50×, part ≤ 512 МиБ, total ≤ 2 ГиБ. Encryption → failed. `vbaProject.bin` / `xl/macrosheets` → флаги, не исполнять. lxml: `f`+`v`+hidden+style+comments; shared → A1. Names, `iterate`. External — строка, без ФС/HTTP.

Raw-ячейка: sheet, row, col, addr, formula_raw, cached_value, hidden, number_format, comment.  
workbook.json: листы, макро/xlm, externals[], locale_hint.

Не Finding.

### 8.2 compile

**Зачем:** формула → AST, шаблон ряда, граф.

**Вход:** raw parquet. **Выход:** `ir/cells.parquet`, `ir/edges.parquet`. CSR в RAM.

На каждую формулу, один `FormulaEngine`: локаль (не replace `;`→`,`); AST или unparsed; рёбра `ref|range|cross_sheet|external|dynamic`; R1C1 (D24+`Inputs!D5` → `Inputs!R[-19]C[0]`); INDIRECT/OFFSET → dynamic+unresolved. Cached `v` only.

**Range → CSR:** раскрыть адресный диапазон до **cap 2000** ячеек на ребро; хвост — флаг `truncated`, unused/lineage не делают вид полноты. Иначе `A:A` взрывает память.

Дальше `formula_raw` не парсят. Открыть IrCatalog.

### 8.3 layout

**Зачем:** геометрия и время, не имена статей.

**Вход:** catalog.cells. **Выход:** `layout.json`.

Label column — левая видимая строковая в блоке (skip hidden A/B). Блок — пустые ряды, merged, bold, заливка, смена кластера шаблонов. Ось — regex `2025E` / `1 кв. 2025` / факт|план; строка биндится к оси **внутри блока**. Две оси на листе = два блока. Роль колонки: `historical|forecast|stub|scenario|total`. Иерархия indent/bold. Check-row — метка, не находка.

Имя листа — слабый признак. Роль периода ≠ роль статьи.

После skip/успеха: залить оси в DuckDB этой сессии.

### 8.4 series (нет артефакта)

Один анализ ряда. View из cells ⨯ axis: trim leading/trailing empty|0; majority template ≥ 70% иначе ряд молчит; `mode([])` = не анализировать; край forecast → `edge_period`; kind = template_change | value_instead_of_formula | literal_in_ast | ref_shift | source_sheet_change.

Чекеры: `SELECT * FROM series_outliers WHERE kind = …`. Majority не считают сами.

### 8.5 mapping

**Зачем:** строка → `concept_id`.

**Вход:** лейблы+parent из layout. **Выход:** `mapping.json` `{rows, questions}`. Skip → сеть не трогать.

`row_key = sheet+row+block_id`. Каскад: normalize (скобки целиком) → glossary exact `(label, parent)` → EmbedPort + cosine к `taxonomy_embeddings.npz` (top-1 ≥ 0.85 и отрыв ≥ 0.08) → ChatPort только ambiguous, в промпте top-k id → иначе Question. GMV ≠ `pnl.revenue`.

Нет embed → без kNN. Нет chat → без шага 4. Кэш векторов: ключ hash(taxonomy.yaml)+EMBEDDING_MODEL+dim; пересчёт под `cf:lock:taxonomy_emb` (общий read-only файл, гонка записи). Chat/embed только после `try_slot("llm"|"embed")`; нет слота — ждать, не обходить.

Роль статьи: `assumption|calculation|database_like|check|output|actual_adjustment`.

### 8.6 check

**Выход:** `candidates.json`. Прозы нет.

| detector | Опора |
|---|---|
| `excel_error` | cached error tokens |
| `error_masking` | `IFERROR`/`IFNA` скрывают `#REF!`/`#DIV/0!` на FALSE-ветке (FAST / spreadsheet-auditor: Excel не показывает ошибку) |
| `circular` | SCC; `likely_intentional` если ∩ {interest,tax,sweep} или mapped debt/tax; не из-за iterate |
| `unresolved_dynamic` | рёбра dynamic |
| `external_link` | workbook.externals + рёбра |
| `xlm_or_vba` | флаги, один Candidate на книгу |
| pattern_break … source_switch | series kind |
| `agg_range_gap` | SUM vs статьи блока |
| `unused_cell` | не reaches(mapped outputs), cap 50 |
| `hidden_input` | hidden в формуле видимого output; иначе tag |
| I1, I3a, I3b, I5, I7 | IdentityResolver; нет concept → Question |
| `risk.*` | ряд mapped concept по оси периода; не ошибка модели |

IdentityResolver: один total **в блоке** (не сумма с детьми; не смешивать итоги двух блоков). Если ни один блок не содержит полный набор concept — fallback на книгу (межлистовые I3a/I3b). Период с оси layout: только `historical|forecast|stub`, не `scenario`/`total`. Finding на **каждый** сломанный период, не первый. Check-row кросс-проверка I1, не второй finding.

Сигналы риска (`risk.*`, severity `risk`): падение выручки/EBITDA г/г (10%), маржа −5 п.п., отрицательная EBITDA/NI ≥ 2 периода, CFO < 0, касса < 0, рост долг/EBITDA ≥ 1.0x (не net debt), ICR = EBIT/|interest| < 1.5, прогнозный рост выручки выше истории на 15 п.п., дивиденды > CFO. Нет concept — молчание, не Question. FCF и DSCR не считаем, пока нет `cf.fcf` / строки DSCR в онтологии.

Дедуп `(detector, frozenset(cell_refs))`.

```python
class Candidate(BaseModel):
    detector: str
    cell_refs: list[str]
    tags: list[str]
    payload: dict
    base_severity: Literal["error", "warning", "risk"]
```

### 8.7 lineage

**Выход:** `lineage.json`. Reverse BFS по тому же CSR до output-concept. Impact: число из identity delta, иначе направление. Метрики карточки только отсюда.

### 8.8 explain + report

ChatPort под `try_slot("llm")`. Шаблон всегда полный. SeverityPolicy: freeze (`hist_manual_adjustment`, `edge_period`, `likely_intentional`) не выше warning. Top-N, бюджет Redis INCR → ChatPort только title/evidence/recommendation/need_user_input; cited_refs ⊆ вход иначе шаблон. Не меняет detector, refs, metrics, числа impact. `related_ids` — общий downstream.

Drop находки без ref ∈ IR.

Нет eligible-находок (после drop / top-N пуст) → LLM не зовём → не `degraded`, даже если ChatPort задан.

`questions` = union mapping + identity_gap + explain, дедуп `(kind, cell_refs)`.

Атомарно `report.json` + `meta.json` (терминальный снимок job; `owner.json` уже есть). Воркер после return: HSET терминал, DEL budget, unlock audit, ACK, release run-slot.

---

## 9. Порты

```python
class EmbedPort(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...

class ChatPort(Protocol):
    def complete_json(self, schema: type[BaseModel], messages: list) -> BaseModel: ...
```

`PortError` → не падать. В эмбеддер только лейблы.

---

## 10. Выход

Канон — `report.json` (поля = требования). Схема и примеры: [`api.md`](api.md).

---

## 11. Диск

```
data/audits/{audit_id}/
  source.xlsx
  owner.json                # actor_id, content_sha256; с POST, HITL не трогает
  meta.json                 # только терминал job
  raw/workbook.json
  raw/cells.parquet
  ir/cells.parquet
  ir/edges.parquet
  layout.json
  mapping.json
  candidates.json
  lineage.json
  report.json
data/glossary/{actor_id}.json
data/taxonomy_embeddings.npz
src/cashflow_audit/ontology/taxonomy.yaml
```

---

## 12. Стек и пакеты

Источник зависимостей — `pyproject.toml` + `uv.lock`. Новые пакеты — `uv add` / `uv add --dev`. `requirements.txt` не заводим. Список ниже — ядро, не запрет на всё остальное.

fastapi, uvicorn, typer, pydantic v2, lxml, duckdb, scipy.sparse, numpy, openai, redis (asyncio), pytest.

Не подменять ядро: Postgres, SQLite, Polars, DuckDB-сервер. Prometheus не must этапа 1. Логи и тест-хелперы ставить можно.

```
src/cashflow_audit/
  api/  cli.py  app/pipeline.py  ports/
  adapters/openai_chat.py  openai_embed.py  redis_jobbus.py
  store/fs.py  ir/catalog.py
  parse/  compile/  layout/  series/  mapping/  graph/  checkers/  explain/
```

`checkers` ↛ adapters, explain.

---

## 13. Качества и пределы

| Критерий | Как закрыто | Предел этапа 1 |
|---|---|---|
| Непротиворечивость | Три плоскости; skip по артефактам; один status; owner ≠ meta; glossary на actor | `X-Actor-Id` — ключ изоляции, не аутентификация (подмена заголовка = чужой id) |
| Актуальность | Qwen 3.6 27B FP8 снаружи; schema-on-read; cached values, не recalc | Формулы LAMBDA/XLOOKUP в AST могут быть `unparsed` |
| Масштаб | N воркеров в **одном** поде; слоты CPU ≠ слот LLM | Нет второго пода: Redis sidecar не кластер. Упираемся в CPU/RAM пода и одну GPU |
| Отказоустойчивость | PVC для аудитов; Redis можно потерять и reconcile с диска; SET inflight не течёт как INCR; timeout job; шаблоны без LLM | Redis emptyDir: live HASH пропадает, очередь восстанавливается с диска. Нет HA Redis |
| Гибкость | Порты Chat/Embed; env слотов; taxonomy.yaml; HITL glossary | Нет force-recompute тех же байт; смена файла → новый hash. Нет смены модели внутри аудита |
| Идемпотентность | POST по (actor, content); LLM не зовём при готовом mapping/report | Объяснения LLM недетерминированы — поэтому skip, а не повторный вызов |
| Изоляция данных | audit_id от actor; 403; отдельный glossary | Нет шифрования at rest в спецификации этапа 1 (PVC/диск хоста) |
| Наблюдаемость | JSON stdout: audit_id, stage, event; /readyz cheap /models; CLI ping | Нет Prometheus в этапе 1 |
| HOL к GPU | таймаут LLM-слота → degraded/шаблон | Пока 4 run ждут 1 GPU, parse других файлов всё равно идут на свободных run-слотах; если все 4 уже в mapping — очередь CPU стоит. Смягчение: `MAX_INFLIGHT` > типичного числа «застрявших в LLM» не лечит; таймаут обязателен |

Память: 4 × крупная книга (CSR + DuckDB). Держать `MAX_INFLIGHT` по RAM пода (ориентир: не 4×1M ячеек на 4Gi без проверки).

---

## 14. Вне этапа 1

Контейнеры LLM/embed (кроме клиента); облако как must; Postgres/SQLite; **общий** Redis вне пода (нужен для второго реплики API); UI; recalc; VBA; правка xlsx; сигналы FCF/DSCR; PDF; JWT вместо `X-Actor-Id`.

Готово: повторный POST того же файла **тем же** актёром не создаёт job и не зовёт LLM; несколько актёров крутятся параллельно в одном поде; отчёт с `cell_refs` из IR.

---

## 15. Что взяли из открытых решений (2024–2026)

Исследование: академические работы, OSS-аудиторы финмоделей, коммерческие add-in. Наш гибрид **не выдуман** — это сходящийся паттерн. Агенты «прочитай xlsx целиком» на бенчмарках слабые и неаудитны.

| Источник | Суть | У нас |
|---|---|---|
| [SpreadsheetLLM / SheetCompressor](https://arxiv.org/abs/2407.09025) (Microsoft) | Сжатие листа ~25× (anchors, inverted index, format aggregation) чтобы влезть в контекст LLM | **Только срез для ChatPort** (explain/mapping), не парсер книги. Целая модель в промпт не идёт |
| [FRTR](https://arxiv.org/html/2601.08741) | Retrieve → Verify → Compose; цитаты; ~8k токенов независимо от размера | `cell_refs` = citation URI; lineage = retrieve; чекер = verify; explain = compose |
| [spreadsheet-auditor](https://github.com/petehottelet/spreadsheet-auditor) | DET vs HEUR; не переписывает xlsx; formula drift, hardcode, SUM off-by-one, hidden в итогах, IFERROR masking; LLM опционален | Те же DET-чекеры + `error_masking`; HEUR (знаки, BS) = наши I1/I3 после mapping; файл не меняем |
| [ModelLens / excel-model-eval](https://github.com/bdschi1/excel-model-eval) | Dual ingest values+formulas, graph, plugs, BS 0.1%/$1k, LLM **только нарратив находок** | То же разделение; CSR scipy вместо networkx (масштаб); materiality как у них |
| [excel-parser](https://github.com/knowledgestack/excel-parser) | JSON + formula graph + `file.xlsx#Sheet!A1` + RAG chunks | Идея citation; parse свой (lxml: hidden, XLM, zip-bomb). Библиотеку не подставляем как ingest этапа 1 |
| [formulas](https://github.com/vinci1it2000/formulas) | Recalc + graph; `circular=True` | Не default: EUPL + ломается на structured table refs. У нас cached `v` + свой tokenizer |
| [graphedexcel](https://github.com/dalager/graphedexcel) | Range = узел, затем expand | Наш cap 2000 на range-ребро |
| FAST / ICAEW, Spreadsheet Detective, PerfectXL, ZAFtool, Macabacus Formulate | Карта copied vs unique формул; hardcode; BS/cash tie-out; не править книгу | `series_outliers` = карта протяжки; I1/I3a/I3b = tie-out |
| SheetCopilot / Copilot Agent / SpreadsheetAgent | Агент крутит Excel | **Не берём.** [Splinde](https://www.splinde.io/blog/llm-vs-spreadsheet): агенты ~35–59% Pass@1; сериализация значений **убивает** граф формул |

Практический вывод рынка: аудит = **детерминированный граф + правила**, LLM = сжатый срез и текст. Это уже §1–8; таблица фиксирует, откуда паттерн, и что агентский Excel отвергнут данными, не вкусом.
