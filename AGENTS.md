# AGENTS.md

Правила разработки и тестов для агента и человека. Читать **до** правок кода.

| Документ | Роль |
|---|---|
| [`docs/architecture/plan.md`](docs/architecture/plan.md) | архитектура, стадии, Redis, DuckDB, skip, слоты |
| [`docs/architecture/api.md`](docs/architecture/api.md) | HTTP, JSON отчёта, коды ошибок |
| [`docs/требования.md`](docs/требования.md) | типы ошибок, поля находки |

Конфликт код ↔ план: править код. Конфликт план ↔ «удобно»: не удобно — править план отдельным коммитом, не молча.

Детектор — код. LLM не парсит Excel и не ищет ошибки: только неоднозначный маппинг и текст карточки. Файл модели не меняется.

---

## 1. Этап 1: нельзя

Не делать, даже «на потом в том же PR»:

- Postgres, SQLite, S3, Polars, DuckDB-сервер, файл `.duckdb`, `index.json`
- GPU/веса в репозитории; LLM/embeddings внутри пода
- второй под API / общий Redis вне sidecar (loopback)
- UI, JWT, PDF, recalc Excel, исполнение VBA/XLM
- правка `xlsx`, эндпоинт `/findings` (есть `/integrity` и `/report`, не live-полл), флаг `force`
- `resume_from` у HITL (worker всегда `Pipeline.run` с parse)
- изобретать DSCR/LLCR/PLCR без mapped-строки; глобальный `cf:lock:pipeline`
- параллелить стадии **одного** аудита (`parse` ∥ `compile` запрещены)
- шарить DuckDB-соединение между потоками / прогонами
- `INCR` для inflight-слотов (только `SET` + `SCARD`)
- HTTP без `X-Actor-Id` с подстановкой `anonymous`
- Finding без `cell_refs` из IR; проза в `candidates.json`
- `cached_value` / сырые числа модели в промпт или эмбеддинги
- `requirements.txt` как источник зависимостей

Зависимости — только **uv** (`uv add`, `uv add --dev`, `uv lock`, `uv run`). Новые пакеты **разрешены**. В коммите — зачем пакет и почему не хватает stdlib / уже стоящего. Не ставить вместо ядра: Postgres, SQLite, Polars, DuckDB-сервер, S3 как store аудита.

Ядро процесса (не закрытый список): fastapi, uvicorn, typer, pydantic v2, lxml, duckdb, scipy.sparse, numpy, openai, redis (asyncio), pytest. Логи, тест-хелперы (`pytest-asyncio` и т.п.), HTTP-клиенты — нормальный `uv add`.

---

## 2. Сырые данные и утечки

Сырое (не в git, не в логи, не в промпты): `data/audits/**/source.xlsx`, клиентские книги, private eval pack.

- В git только **синтетические** xlsx с cached values (`tests/fixtures/`).
- В LLM/embeddings: лейблы и **шаблоны** формул, не `cached_value`.
- Не логировать значения ячеек, формулы и имена файлов, по которым узнаётся клиент.
- Логи JSON в stdout (`cashflow_audit.*`). Можно: audit_id, stage, event, http_status, model id из env, latency, exc_type, error codes.
  Нельзя: cached_value, formula_raw, лейблы, промпты, source_filename, actor_id, api_key.
- Сплит train/test/eval — по **книге** (entity), не случайные строки одной модели.
- Пороги, таксономию, промпты не крутить на held-out. Eval pack не трогать, пока стадия не заморожена.
- Глоссарий HITL книги A не копировать в eval той же книги.
- `audit_id = sha256(actor_id + ":" + content_sha256)` — идентичность, не фича.
- Глоссарий: `data/glossary/{actor_id}.json`, не один глобальный файл.

---

## 3. Код

Логика только в `src/cashflow_audit/`. `scripts/` и ноутбуки вызывают модули, пайплайн не копируют.

```
src/cashflow_audit/
  api/  cli.py  app/pipeline.py  ports/
  adapters/openai_chat.py  openai_embed.py  redis_jobbus.py
  store/fs.py  ir/catalog.py
  parse/  compile/  layout/  series/  mapping/  graph/  checkers/  frs/  explain/
  ontology/taxonomy.yaml
```

| Модуль | Делает | Не делает |
|---|---|---|
| api / cli | HTTP / argv | Excel |
| pipeline | порядок, skip, heartbeat, деградация портов | правила формул |
| JobBus | stream, HASH, лок на `audit_id`, слоты | IR, report |
| IrCatalog | DuckDB views | статус job, SCC |
| store | каталог диска | очередь |
| parse | OOXML → raw | AST |
| compile | AST, шаблон, рёбра, CSR | блоки, concept_id |
| layout | геометрия, роль **периода** | concept_id |
| series | SQL outliers | detector как чекер |
| mapping | concept_id, роль **статьи** | hist/forecast |
| graph | SCC, BFS | текст |
| checkers | `Candidate[]` техника + identity | проза, HTTP, adapters, FRS |
| frs | матрица F01–F14, `frs.json` | проза, HTTP, adapters |
| explain | Finding; тонкий report + integrity | новые refs/метрики/числа impact |
| adapters | OpenAI, redis-py | домен |

`checkers` не импортирует `adapters` и `explain`. `frs` не импортирует `adapters` и `explain`.

Порты: `ChatPort`, `EmbedPort`, `AuditStore`, `JobBus`. SDK только в `adapters/`. CLI и HTTP → один `Pipeline.run()`.

Три плоскости не смешивать:

| Плоскость | Где | Что |
|---|---|---|
| Control | Redis | очередь, live status/stage, лок, бюджет LLM/embed |
| Query | DuckDB `:memory:` на **каждый** `Pipeline.run` | SQL по parquet; жизнь = run |
| Durable | диск (PVC, не emptyDir) | xlsx, parquet, JSON стадий, `owner.json`, терминальный `meta.json` |

Находки техники/identity — `integrity.json`. Итог FRS — тонкий `report.json`. Ячейки только в parquet. HTTP не отдаёт parquet, формулы, `cached_value`. Артефакты писать **tmp + rename**.

`PortError` → не падать: шаблон / `degraded` / `needs_input`. Нет LLM/embeddings — аудит всё равно завершается.

---

## 4. Идемпотентность и акторы

- HTTP: заголовок `X-Actor-Id` обязателен, иначе `400 missing_actor`.
- CLI: `actor_id=anonymous`. Redis не нужен.
- Один файл у разных акторов — разные `audit_id`. У одного актора тот же файл — тот же id.
- GET/answers: `owner.json.actor_id` совпал, иначе `403`. `owner.json` пишется на POST и **не** стирается HITL.
- Повторный POST того же файла с готовым `report.json` → `200` + `report_url`, без job и без LLM.
- Свой HASH `queued`/`running` → `202` тот же id, без второго stream.
- `force` нет. Смена байт файла → новый hash.

Терминальный `status` (один, не флаги), приоритет:

`failed` > `needs_input` (questions непусты) > `degraded` (порты молчали, questions пусто) > `succeeded`

Live: `queued` | `running`. `meta.json` — только терминал job. Live — Redis HASH.

**HITL:** glossary += answers под `cf:lock:glossary:{actor_id}`; стереть `mapping.json`, `candidates.json`, `lineage.json`, `frs.json`, `integrity.json`, `report.json`, `meta.json`; `XADD` только `audit_id`. IR не пересчитывать.

**Рестарт:** Redis жив → `XAUTOCLAIM` idle > 60s. Под умер, диск цел → `XADD` каталогов с `owner.json` без `report.json`. GET: нет HASH, есть owner без report → `queued`, не `404`.

Дедлайн: `JOB_TIMEOUT_SEC` (default 3600) → `failed` `error=timeout`, слоты отпустить.  
Retention: `AUDIT_TTL_DAYS` (default 14), не трогать `running`.

---

## 5. Стадии

Порядок фиксирован: parse → compile → layout → series → mapping → check → frs → lineage → explain+report.

Skip только если артефакт есть. Worker **всегда** стартует с parse; skip сам останавливается на первой дырке.

| Стадия | Skip если есть | На skip всё равно |
|---|---|---|
| parse | `raw/cells.parquet` + `raw/workbook.json` | — |
| compile | `ir/cells.parquet` + `ir/edges.parquet` | собрать CSR из edges |
| layout | `layout.json` | оси в DuckDB этой сессии |
| series | нет файла | всегда `CREATE VIEW` после layout |
| mapping | `mapping.json` | **не** звать embed/LLM |
| check | `candidates.json` | — |
| frs | `frs.json` | — |
| lineage | `lineage.json` | — |
| explain+report | `report.json` + `integrity.json` + `meta.json` | **не** звать LLM |

Не начинать стадию N+1, пока артефакт N есть **и** тесты N зелёные.

Жёсткие инварианты стадий:

- **parse:** zip-slip, depth 0, ratio ≥ 50×, part ≤ 512 МиБ, total ≤ 2 ГиБ; encryption → failed; VBA/XLM — флаги, не исполнять; external — строка, без ФС/HTTP. Не Finding.
- **compile:** один `FormulaEngine`; локаль, не replace `;`→`,`; range → CSR с cap **2000** ячеек/ребро, иначе `truncated`. Дальше `formula_raw` не парсят.
- **layout:** роль периода ≠ роль статьи. Имя листа — слабый признак. Check-row — метка, не находка. Месяц (`янв.25`, `Jan-25`, `2025-01`) → `period_key=YYYY-MM`, иначе месячный блок не создаётся.
- **series:** majority template ≥ 70%, иначе ряд молчит; `mode([])` = не анализировать. Чекеры только `SELECT * FROM series_outliers`.
- **mapping:** каскад normalize → glossary `(label, parent)` → cosine (top-1 ≥ 0.85 и отрыв ≥ 0.08) → ChatPort только ambiguous → иначе Question. `GMV` ≠ `pnl.revenue`. Chat/embed только после `try_slot`; нет слота — ждать `LLM_SLOT_WAIT_SEC` (default 120), не обходить; истекло — шаблон, статус может быть `degraded`.
- **check:** дедуп `(detector, frozenset(cell_refs))`. IdentityResolver: нет concept → Question, не угадывать. Периоды по `period_key`, не по номеру колонки. I3a — rollforward от net CF / mapped `cf.fcf`, не `+CFO` (иначе capex даёт ложный error). I10 — opening + drawdown − repayment = closing; нет долга или нет потоков → Question. `cell_refs` всех сторон равенства. `risk.*` здесь нет (это frs).
- **frs:** закрытая матрица F01–F14 всегда; нет concept → `not_applicable` / `insufficient`, не HITL. FCF: mapped `cf.fcf` или derived `CFO+CAPEX` (capex как в книге, без двойного минуса). DSCR только если есть mapped-строка. `ready_for_credit` ∈ {false, null}, никогда true. LLM не ставит статус F-строки.
- **explain:** шаблон всегда полный. LLM только title / evidence / recommendation / `need_user_input`. `cited_refs` ⊆ вход, иначе шаблон. Не меняет detector, refs, metrics, числа impact. Drop находки без ref ∈ IR. SeverityPolicy: freeze (`hist_manual_adjustment`, `edge_period`, `likely_intentional`) не выше `warning`.
- **questions** = union mapping + identity_gap + explain, дедуп `(kind, cell_refs)`.

Параллелизм — **между** аудитами, не внутри. На каждый run: свой `:memory:` DuckDB, свой CSR, ключи Redis с `{audit_id}`. Heartbeat `cf:lock:audit:{id}`. Слоты `cf:inflight:{run,llm,embed}` = SET. `MAX_LLM_INFLIGHT=1`. Горизонтальный scale на этапе 1 не делать.

---

## 6. Как вести работу

1. Одна стадия или один вертикальный срез (порт, skip, HTTP-идемпотентность) за коммит.
2. Сначала тест на нужное поведение, потом код. Не «напишу parse целиком, тесты потом».
3. Реализация в модуле стадии, не в `pipeline.py` и не в ноутбуке.
4. `uv run pytest` по затронутым тестам, затем полный набор стадии. Lint: `uv run ruff check`.
5. Коммит стадии/фичи. В коммите нет секретов, `data/audits/`, eval pack, клиентских xlsx.
6. Следующая стадия — только после зелёных тестов текущей.

Идемпотентность LLM: существующие `mapping.json` / `report.json` → сеть не трогать, в том числе в тестах с Fake-портами (счётчик вызовов = 0).

---

## 7. Тесты

Команды:

```bash
uv run pytest
uv run pytest tests/<stage> -q
uv run ruff check src tests
```

Сеть в default-прогоне **выкл**. Живые Redis/LLM только под маркерами, не в CI по умолчанию.

### 7.1 Слои и моки

| Слой | Что | I/O |
|---|---|---|
| unit модуля | parse/compile/checkers/layout/… | синтетический xlsx / готовый parquet; диск = `tmp_path` |
| контракт стадии | skip, артефакты, CSR/view | `tmp_path`; Fake-порты |
| pipeline | порядок, HITL-хвост, status, timeout | Fake JobBus + Fake Chat/Embed |
| HTTP | коды, актор, идемпотентный POST | fakeredis или InMemory JobBus |
| live (opt-in) | `@pytest.mark.redis` / `live_llm` | не default |

| Компонент | Unit / стадия / HTTP default | Не делать |
|---|---|---|
| `ChatPort` / `EmbedPort` | Fake: фиксированный JSON/вектор, счётчик вызовов | живой Qwen в pytest |
| `JobBus` | InMemory или fakeredis | реальный sidecar как зависимость unit |
| диск | `tmp_path` / изолированный store | писать в `data/audits/` рабочего дерева |
| DuckDB | настоящий `:memory:` | мок SQL чекеров |
| CSR / SCC / BFS | настоящий scipy | «примерно как граф» на dict |
| таксономия | yaml + крошечный npz в fixtures | пересчёт эмбеддингов в unit |

`conftest.py`: Fake-порты, tmp store, фабрика синтетических книг. Чекер не тестировать через HTTP.

### 7.2 Фикстуры

- Каталог: `tests/fixtures/xlsx/`, `tests/fixtures/ir/`, `tests/fixtures/golden/`.
- Книга в git: минимальный OOXML, **cached `v`**, без клиентских имён/сумм.
- Одна фикстура — один инвариант (`circular_interest.xlsx`, `iferror_masks_ref.xlsx`, `sum_gap.xlsx`). Не одна «модель всего».
- Golden: JSON `Candidate` / фрагмент mapping, не проза LLM.
- Нельзя: продакшен-xlsx, скриншоты из `resources/` как вход парсера, копипаст `cached_value` из реальной книги.

### 7.3 Контракт по стадиям

Каждая строка — тесты, без которых стадия не считается сделанной.

| Стадия | Обязательные проверки |
|---|---|
| parse | схема raw-ячейки; encryption → failed; zip-slip / bomb отвергнуты; VBA/XLM флаги, код не исполнен; external не ходит в ФС/HTTP; Finding нет |
| compile | AST vs unparsed; типы рёбер `ref\|range\|cross_sheet\|external\|dynamic`; R1C1; INDIRECT/OFFSET → dynamic+unresolved; cap 2000 + `truncated`; `;` локали не ломает разбор |
| layout | две оси на листе = два блока; hidden колонка не label; роль периода ≠ роли статьи; check-row не Finding; `янв.25` → `2025-01` |
| series | нет файла-артефакта; majority < 70% → ряд молчит; `mode([])` skip; виды kind стабильны; чекер читает view, не считает majority сам |
| mapping | skip при `mapping.json` → 0 вызовов embed/chat; glossary exact бьёт kNN; GMV не `pnl.revenue`; в промпте нет `cached_value`; нет слота → ждать, не обход; Question если ambiguous |
| check | по детектору из плана — хотя бы один positive и один отрицательный (не-false-positive из §ложных срабатываний требований); дедуп; freeze severity; нет concept → Question I1, не Finding; I3a не бьёт модель с capex при одном CFO; DSCR не эмитится без mapped-строки |
| frs | матрица всегда 14 строк; пустой mapping → 14× NA, 0 findings; F04/F13 без FCF → insufficient, не выдуманный DSCR |
| lineage | reverse BFS до output-concept; метрики карточки только отсюда; truncated range не притворяется полнотой |
| explain | LLM не меняет detector/refs/metrics/impact; `cited_refs` ⊄ вход → шаблон; drop без ref ∈ IR; freeze не выше warning; questions union+дедуп |
| pipeline | skip-таблица; HITL не трогает `owner.json` и IR; повторный run не зовёт LLM; `PortError` → не exception наружу; timeout отпускает слот |
| api | `missing_actor` 400; чужой id 403; POST идемпотентен; `/healthz` без Redis; `/readyz` 503 если Redis down; report/integrity/layout/mapping 409 пока нет файла; нет `/findings`; статусный приоритет; probe 401 и model_missing → degraded; ping CLI без сети |

Инварианты, которые ловить в нескольких слоях:

- любой Finding: `cell_refs` ⊆ IR, поля как в `api.md`
- `report.json` + `meta.json` появляются атомарно
- Fake Chat/Embed: после skip mapping/report вызовов 0
- inflight: kill воркера не оставляет вечный слот (`SET`, не `INCR`)
- два актора, один файл → два каталога, 403 на чужой GET

### 7.4 Что не считается тестом

- «Прогнал на клиентской книге, в логе ок»
- Проверка формулировки LLM вместо схемы и `cited_refs`
- Тест, который для зелёного статуса ходит в сеть
- Случайный сплит ячеек одной книги на train/test
- Подгонка порога cosine / majority по eval pack
- HTTP-тест, который вместо контракта API проверяет детектор

### 7.5 Eval (не pytest)

Private pack **вне git**. Подключать после заморозки стадии. Не для выбора порогов и промптов. Метрика: recall/precision детекторов и доля находок с валидным `cell_refs`, не BLEU текста карточек.

---

## 8. Git

- Коммит = одна стадия или одна фича (порты, skip, HTTP).
- В репозитории: код, синтетические фикстуры, `taxonomy.yaml`, `uv.lock`.
- Не в git: `data/audits/`, `data/glossary/*` с боевыми акторами, eval pack, `.env`, клиентские xlsx.
- Не коммитить «чуть-чуть» сырого значения в golden.

---

## 9. Стоп-сигналы

Остановиться и переписать подход, если тянет:

- «пущу xlsx в LLM, так быстрее найти ошибки»
- «добавлю force, а то skip мешает отладке» (отладка = удалить артефакт)
- «общий DuckDB на процесс / глобальный lock пайплайна»
- «HITL передаст resume_from=mapping»
- «в промпт кусок листа с числами, иначе модель не поймёт»
- «чекер импортнет openai, это же один вызов»
- «тест на живой книге из Downloads»
- «порог 0.85 подкручу по eval»
- «изобрету DSCR/LLCR без строки в mapping, в требованиях же есть»
- «второй воркер-процесс с общим Redis, как в проде»

Это нарушение плана, не ускорение.
