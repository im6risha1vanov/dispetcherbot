# bt-dispatch-bot — техническое состояние проекта

Дата сборки: **24 сентября 2026**.
Документ описывает систему целиком для человека, который не видел код и не имеет
доступа к серверу.

> **О секретах.** В файле нет ни одного логина, пароля, токена, id чата,
> @username, телефона или адреса. Там, где значение существует, но не может быть
> названо, стоит `<REDACTED>` или описание смысла.

---

## 1. Состояние

| Что | Значение |
|---|---|
| Ветка в продакшене | `report-closing-v2` |
| Коммит | `<см. журнал>` |
| Всего коммитов в репозитории | 14 |
| Первый коммит в истории | 20.09.2026 |
| Последний деплой | 23.09.2026, 22:26:52 UTC |
| Службы | `bt-dispatch-bot`, `bt-dispatch-poller` — обе `active` с момента деплоя |
| Сервер | VPS, Нидерланды, адрес `<REDACTED>`, Ubuntu, Python 3.14.4 |
| База | PostgreSQL 18, 74 заявки в таблице `requests` на момент сборки |
| Тесты на сервере | 218 прошли, 32 пропущены, 0 упавших |

### История репозитория

Полной истории разработки в git **нет**. 20 сентября рабочее состояние залили на
GitHub одним коммитом `Sync production dispatch bot to GitHub`, предыдущие ~30
коммитов с подробными сообщениями остались только на машине разработчика.
Поэтому `git log` не годится для археологии — этот файл её заменяет.

### Что работает в продакшене

- опрос CRM каждые 60 секунд, распознавание новых и закрытых заявок;
- утренний сбор смены и очередь мастеров на день;
- раздача заявок мастерам по кругу с учётом занятости и гарантийной привязки;
- отметки мастера «В пути» / «На месте» / «В работе» с записью статуса в CRM;
- запись назначенного мастера в CRM;
- отчёт мастера при закрытии заявки, подтверждение администратором или
  директором, **запись закрытия и проведение заявки в CRM**;
- отправка мастеру блока расчёта из CRM после проведения;
- перевод заявки в «В работе СД» и последующее закрытие СД;
- ручная передача заявки другому мастеру кнопкой;
- лента событий владельцу, тревоги администратору и директору;
- итоги дня в 22:00 и сводка сбоев в 21:00;
- сообщения о сбоях в Telegram вместо журнала сервера.

### Что выключено или не настроено

| Что | Состояние | Следствие |
|---|---|---|
| `DISPATCHERS_CHAT_ID` | не задан | Кнопки «Номер для дозвона», «Запрос квартиры» и запрос номера для дистанционного решения гарантии отвечают «Чат диспетчеров не настроен». Ветка кода живая, но никогда не работала в бою |
| `CRM_WRITE_ONLY_FOR` | пуст | Предохранитель снят: бот вправе писать и проводить **любую** заявку филиала. При старте бот шлёт владельцу предупреждение об этом |
| `OWNER_CHAT_ID` | не задан | Лента и сбои уходят в чат из `TELEGRAM_TEST_CHAT_ID` — тот же чат, что и раньше. Работает, но настроено «по умолчанию», а не явно |
| Привязка администратора | не выполнена | `@<REDACTED>` ещё не написал боту `/start`, поэтому отчёты на подтверждение идут владельцу и директору, а не администратору |

### Что недоделано

- **Обкатка проведения не проводилась.** Полный цикл «мастер заполнил отчёт →
  администратор подтвердил → CRM провела заявку» ни разу не наблюдался вживую.
  Код покрыт тестами на моках CRM, но живая карточка не проверялась.
- **Новая анкета отчёта не видела ни одного мастера.** Выложена 23.09 вечером,
  рабочий день уже закончился.
- **Классификатор повторов** (LLM, который отличал бы «клиент просит другого
  мастера» от обычного повтора) — в ТЗ был, решено не делать.
- Два брошенных отчёта в базе висят с 18 и 21 сентября, блокируют новые отчёты
  по тем же заявкам у того же мастера.

---

## 2. Карта файлов

```
bt-dispatch-bot/
├── bot.py                 1356  Telegram-бот: все кнопки, команды, анкета отчёта
├── poller.py               683  Цикл опроса CRM, раздача, таймеры, расписание
├── db.py                   989  Весь доступ к PostgreSQL, ни одного SQL вне этого файла
├── crm.py                  821  Единственное место, знающее про HTML и URL CRM
├── closing.py              541  Анкета закрытия: шаги, переходы, поля формы, сводка
├── messages.py             351  Тексты и клавиатуры, общие для бота и поллера
├── reporting.py            323  Сбои из журнала → в Telegram и в таблицу failures
├── config.py               176  Чтение .env с проверкой обязательных параметров
├── demo.py                 178  Показательный прогон сценария в один чат, ничего не пишет
├── roles.py                145  Кто что получает: владелец, администратор, директор
├── check.py                 99  Диагностика связности перед запуском, только чтение
├── photos.py                74  Снимки отчёта файлами на диске
├── pinning.py               72  Закрепление заявки в чате мастера и уборка мусора
├── dispatch_queue.py        51  Очередь дня и выбор следующего мастера по кругу
├── migrations/             23 файла, 355 строк — схема базы по шагам
├── tests/                  14 файлов, 4313 строк
├── systemd/                 2 unit-файла служб
├── README.md               360  Рабочая документация для разработчика
├── requirements.txt          6  aiogram, asyncpg, httpx, selectolax, python-dotenv
├── requirements-dev.txt      2  pytest, pytest-asyncio
├── pytest.ini                3
├── .env.example             79  Образец конфигурации без значений
├── .gitattributes            1  Нормализация переводов строк в LF
└── .gitignore               10  .env, .venv, __pycache__, *.log, *.backup
```

Вендорного и сгенерированного кода в репозитории нет: `.venv` исключён.

---

## 3. Модули

### `config.py` — параметры из `.env`

Читает переменные окружения, приводит типы, проверяет обязательные. Импортируется
всеми остальными модулями и сам не зависит ни от чего внутри проекта.

- `validate(required: list) -> None` — бросает `ConfigError` со списком
  незаполненного. Элемент-кортеж означает «хотя бы один из»: например, роль
  наблюдателя удовлетворяется любым из трёх чатов.

Всё остальное — константы модуля, перечислены в разделе 5.

### `roles.py` — кто что получает

Отвечает на единственный вопрос: в какие чаты уходит это сообщение. Зависит от
`config` и `db`.

| Функция | Что делает |
|---|---|
| `owner_chat() -> str` | Чат владельца: лента и сбои. `OWNER_CHAT_ID`, иначе `TELEGRAM_TEST_CHAT_ID`, иначе `ADMIN_CHAT_ID` |
| `director_chat() -> str` | Чат директора |
| `admin_chat() -> str` | Запомненный чат администратора; при сбое базы возвращает значение из `.env`, а не пустоту |
| `admin_bound() -> bool` | Писал ли администратор боту хоть раз |
| `is_admin_username(username) -> bool` | Сверка @username без учёта регистра и собачки |
| `remember_admin_chat(chat_id) -> bool` | Запоминает чат в `app_state`; `True` — если запомнили впервые |
| `deciders() -> list[str]` | Администратор и директор. Владельца здесь нет |
| `is_decider(chat_id)`, `is_supervisor(chat_id)` | Право решать / право на служебные команды (второе включает владельца) |
| `alert_chats() -> list[str]` | Решающие плюс владелец, без повторов |
| `feed_chats() -> list[str]` | Владелец и директор |
| `send_to(bot, chats, text, *, markup)` | Рассылка; упавший чат не глушит остальные |
| `notify_owner(bot, text, *, markup)` | Только владельцу |

**Неочевидное.** Telegram не позволяет написать человеку по одному @username —
нужен внутренний `chat_id`, который появляется только после того, как человек сам
напишет боту. Поэтому `chat_id` администратора ловится при первом его сообщении
и кладётся в `app_state`, а не в `.env`: так его видят обе службы и он переживает
перезапуск.

### `photos.py` — снимки отчёта

Файлы на диске в каталоге `PHOTO_DIR`, в базе остаётся только имя файла.

- `save(content: bytes) -> str` — записывает файл, возвращает имя вида
  `<32 hex>.jpg`;
- `read(name) -> bytes | None` — `None`, если имя не похоже на наше или файла нет;
- `forget_old(keep_days) -> int` — удаляет старые, возвращает количество.

**Неочевидное.** Имя проверяется регуляркой `^[0-9a-f]{32}\.jpg$`. Это не только
защита от выхода за каталог: по несовпадению отличается старая запись, где вместо
имени файла лежала ссылка Telegram. Такие отчёты продолжают работать — вызывающий
код при `None` идёт качать из Telegram.

### `pinning.py` — закрепление и уборка

- `pin_assignment(bot, employee_id, chat_id, message_id)` — закрепляет текущую
  заявку мастера, сняв прежнюю;
- `unpin_assignment(bot, employee_id, chat_id)`;
- `drop_message(bot, chat_id, message_id) -> bool` — удаляет сообщение,
  потерявшее смысл.

**Неочевидное.** Все три обёрнуты в `_with_retry`, который читает
`TelegramRetryAfter` и ждёт столько, сколько просит Telegram. Без этого закрепление
терялось при всплеске сообщений, а номер сообщения не сохранялся — повторить было
нечем. Telegram не отдаёт ботам сообщения старше двух суток, поэтому `drop_message`
возвращает `False` вместо исключения, и вызывающий код гасит кнопки вместо удаления.

### `dispatch_queue.py` — очередь дня

- `next_master(slots, cursor_position, exclude) -> ShiftSlot | None` — следующий по
  кругу после курсора, пропуская занятых и перечисленных в `exclude`;
- `in_assign_window(now, start, end) -> bool`.

`ShiftSlot` — `employee_id`, `full_name`, `position`, `is_busy`, `chat_id`,
`telegram_username`.

### `reporting.py` — сбои в Telegram

`TelegramErrorHandler` подключается к корневому логгеру и превращает каждое
`WARNING`/`ERROR` в русскую строку для человека.

- `classify(text) -> str` — класс сбоя из восьми: права в чате, вход в CRM, запись
  в CRM, база данных, Telegram просит подождать, Telegram недоступен, связь с
  Telegram, связь с CRM, плюс «прочее»;
- `humanize_for_chat(text) -> str` — выбрасывает имена исключений и технический шум;
- `FIXES: dict` — что делать человеку при каждом классе;
- `attach(bot, chat_id, source)` — подключение обработчика.

**Неочевидное.** В чат уходит не всё: одинаковые сообщения схлопываются на 15
минут, поток ограничен двенадцатью в час, а сетевые заминки Telegram требуют пяти
подряд, прежде чем разбудят человека. Но **в таблицу `failures` пишется каждый
случай**, включая те, о которых промолчали, — иначе в вечерней сводке не видно,
что CRM отваливалась весь день. Собственные ошибки модуля и штатные предупреждения
библиотек (`Received SIGTERM`, `Polling stopped`, …) отбрасываются до всего
остального.

### `messages.py` — тексты и клавиатуры

Чистые функции без побочных эффектов, никаких обращений к базе и сети. Полный
список текстов — раздел 9.

### `closing.py` — анкета закрытия

Описывает четыре сценария отчёта и превращает ответы в поля формы CRM.

| Что | Значение |
|---|---|
| `KIND_CLOSE` | обычное закрытие заявки |
| `KIND_SD_OPEN` | перевод в «В работе СД» |
| `KIND_SD_CLOSE` | закрытие заявки, которая была на СД |
| `KIND_REMOTE` | гарантия решена звонком, всё по нулям |

- `next_step_for(kind, step, answers) -> str | None` — следующий вопрос;
- `canonical_step(kind, step) -> str` — приводит шаг старой анкеты к новой;
- `parse_amount(text) -> (Decimal | None, str)` — сумма или причина отказа;
- `crm_payload(row) -> dict[str, str]` — ответы в виде полей формы;
- `has_bso(row) -> bool` — выписан ли БСО (выводится из наличия снимков);
- `summary(row, master_name) -> str` — сводка проверяющему;
- `report_warnings(row) -> list[str]` — странности, которые не блокируют проведение.

**Неочевидное.** Ноль в сумме заявки шлётся строкой `"0"`, потому что без суммы
Yii не проводит заявку. А ноль в стоимости ЗПЧ и предоплате шлётся **пустой
строкой**, потому что Yii рисует ноль в number input как `value=""` — иначе сверка
после записи решит, что поле не легло.

### `db.py` — доступ к PostgreSQL

Единственное место с SQL. Около 70 функций, сгруппированных по темам: заявки,
мастера, смены, назначения, запросы диспетчерам, отчёты, итоги, сбои, состояние.
Полный список сигнатур опущен — они однозначны по именам; ниже только то, что
неочевидно.

- `insert_request(row, city_id, *, notified, is_open) -> bool` — `True`, если
  заявка увидена впервые. Различие вставки и обновления определяется трюком
  `RETURNING (xmax = 0)`.
- `mark_shift(shift_date, employee_id, city_id) -> (position, is_new)` — ставит
  мастера в очередь дня. Мастера жмут кнопку почти одновременно, поэтому позиция
  берётся под уникальным индексом, а не `count(*) + 1`.
- `advance_assignment(assignment_id, state)` — двигает назначение по шагам; на
  повторное нажатие возвращает `None`, а не ошибку.
- `class request_lock(crm_id)` — асинхронный контекст поверх
  `pg_advisory_lock(crm_id)`. Запись в CRM — это read-modify-write целой формы,
  поэтому два одновременных изменения одной карточки затрут друг друга. Блокировка
  межпроцессная: бот и поллер живут в разных процессах.
- `add_closure_photo(...) -> int` — возвращает, сколько снимков стало в этом окне.
- `failures_between(start, end)` — сбои, сгруппированные по причине.

**Неочевидное.** При подключении ставится кодек типов `jsonb`/`json` с
`format="text"`. Без него asyncpg 0.31 отдаёт jsonb строкой, и сводка отчёта
падает на `.items()`. Это уже ломало отчёты мастеров в бою восемь раз подряд.

Второе: `close()` обнуляет ссылку на пул. Иначе следующая попытка взять соединение
падает с `pool is closed` вместо честного «база не подключена».

### `crm.py` — стык с CRM

Подробно — раздел 6. Публичное API клиента:

| Метод | Что делает |
|---|---|
| `login()` | Вход, чтение csrf, проверка по `.navbar` |
| `fetch_open_requests() -> list[RequestRow]` | Разбор грида |
| `fetch_request_card(crm_id) -> RequestCard` | Разбор карточки целиком |
| `assign_master(crm_id, employee_id) -> list[str]` | Запись мастера |
| `set_status(crm_id, status_code) -> list[str]` | Смена статуса |
| `open_sd(crm_id, comment, photos)` | Перевод в СД |
| `close_remote(crm_id, fields)` | Закрытие гарантии по нулям |
| `close_request(crm_id, fields, photos)` | Закрытие и проведение |

Все пишущие методы возвращают список «задетых чужих полей» — пустой список
означает чистую запись.

### `poller.py` — цикл и расписание

Один процесс, один бесконечный цикл с шагом `POLL_INTERVAL_SEC`. Внутри цикла по
порядку: опрос грида → лента новых → сбор смены → сверка смены → раздача →
напоминания → таймауты приёма → таймауты «на месте» → расчёты → сводка сбоев →
итоги дня. Подробно — раздел 8.

### `bot.py` — обработчики Telegram

Самый большой файл. Команды, кнопки мастера, анкета отчёта, решения проверяющих.
Порядок регистрации обработчиков важен: aiogram отдаёт обновление первому
подошедшему, и обработчик, вернувший `None`, останавливает цепочку.

### `check.py` — диагностика

Отдельный скрипт: проверяет `.env`, вход в CRM, чтение грида, подключение к базе и
доступность чатов. Только чтение, ничего не меняет.

---

## 4. База данных

### DDL (снят с боевой базы, без данных)

```sql
CREATE TABLE public.requests (
    crm_id bigint NOT NULL,
    city_id integer DEFAULT 206 NOT NULL,
    status_code integer,
    status_text text,
    is_recall boolean DEFAULT false NOT NULL,
    req_type text,
    opened_at timestamp with time zone,
    customer_name text,
    address text,
    master_name text,
    created_at_local timestamp with time zone,
    closed_at_local timestamp with time zone,
    total_cost numeric(12,2),
    raw_grid_row jsonb,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    notified_at timestamp with time zone,
    is_open boolean DEFAULT true NOT NULL,
    last_seen_at timestamp with time zone,
    info_line text,
    prior_master_name text,
    CONSTRAINT requests_pkey PRIMARY KEY (crm_id)
);

CREATE TABLE public.masters (
    employee_id bigint NOT NULL,
    city_id integer DEFAULT 206 NOT NULL,
    full_name text NOT NULL,
    telegram_id bigint,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    telegram_username text,
    chat_id bigint,
    pinned_message_id bigint,
    CONSTRAINT masters_pkey PRIMARY KEY (employee_id),
    CONSTRAINT masters_telegram_id_key UNIQUE (telegram_id)
);
COMMENT ON COLUMN public.masters.telegram_id IS 'кто нажал кнопку';
COMMENT ON COLUMN public.masters.chat_id   IS 'рабочий чат мастера, куда уходит заявка';

CREATE TABLE public.shifts (
    shift_date date NOT NULL,
    employee_id bigint NOT NULL,
    city_id integer DEFAULT 206 NOT NULL,
    "position" integer NOT NULL,
    marked_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT shifts_pkey PRIMARY KEY (shift_date, employee_id)
);

CREATE TABLE public.assignments (
    id bigint NOT NULL,
    crm_id bigint NOT NULL,
    employee_id bigint NOT NULL,
    city_id integer DEFAULT 206 NOT NULL,
    state text DEFAULT 'assigned' NOT NULL,
    assigned_at timestamp with time zone DEFAULT now() NOT NULL,
    enroute_at timestamp with time zone,
    onsite_at timestamp with time zone,
    inwork_at timestamp with time zone,
    reassigned_at timestamp with time zone,
    chat_id bigint,
    message_id bigint,
    reminder_sent_at timestamp with time zone,
    reminder_message_id bigint,
    onsite_alert_sent boolean DEFAULT false NOT NULL,
    payout_sent_at timestamp with time zone,
    CONSTRAINT assignments_pkey PRIMARY KEY (id)
);

CREATE TABLE public.closures (
    id bigint NOT NULL,
    crm_id bigint NOT NULL,
    employee_id bigint NOT NULL,
    chat_id bigint NOT NULL,
    kind text DEFAULT 'close' NOT NULL,
    step text DEFAULT 'payed' NOT NULL,
    state text DEFAULT 'collecting' NOT NULL,
    payed_by_customer numeric(12,2),
    prepayment_sum numeric(12,2),
    spares_cost numeric(12,2),
    with_bso text,
    with_zip text,
    receipt_mode text,
    fback_mode text,
    photos jsonb DEFAULT '{}' NOT NULL,
    chat_messages jsonb DEFAULT '[]' NOT NULL,
    question_message_id bigint,
    branch_comment text,
    prepayment text,
    agreed_sum text,
    deadline text,
    malfunction text,
    admin_chat_id bigint,
    admin_message_id bigint,
    decider_messages jsonb DEFAULT '[]' NOT NULL,
    decided_by text,
    reject_reason text,
    conduct_error text,
    conduct_attempts integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    submitted_at timestamp with time zone,
    decided_at timestamp with time zone,
    written_at timestamp with time zone,
    CONSTRAINT closures_pkey PRIMARY KEY (id)
);

CREATE TABLE public.info_requests (
    id bigint NOT NULL,
    crm_id bigint NOT NULL,
    employee_id bigint,
    master_chat_id bigint NOT NULL,
    dispatcher_message_id bigint,
    kind text DEFAULT 'phone' NOT NULL,
    asked_at timestamp with time zone DEFAULT now() NOT NULL,
    answered_at timestamp with time zone,
    answered_by text,
    CONSTRAINT info_requests_pkey PRIMARY KEY (id)
);

CREATE TABLE public.failures (
    id bigint NOT NULL,
    happened_at timestamp with time zone DEFAULT now() NOT NULL,
    source text NOT NULL,
    category text NOT NULL,
    summary text NOT NULL,
    level text NOT NULL,
    CONSTRAINT failures_pkey PRIMARY KEY (id)
);

CREATE TABLE public.app_state (
    key text NOT NULL,
    value text,
    CONSTRAINT app_state_pkey PRIMARY KEY (key)
);
```

Индексы и внешние ключи:

```sql
CREATE UNIQUE INDEX assignments_active_idx
    ON assignments (crm_id) WHERE state <> 'reassigned';
CREATE INDEX assignments_payout_pending_idx
    ON assignments (city_id) WHERE payout_sent_at IS NULL;
CREATE INDEX assignments_state_idx ON assignments (state, assigned_at);

CREATE UNIQUE INDEX closures_active_master_crm_idx
    ON closures (employee_id, crm_id)
    WHERE state IN ('collecting', 'pending_admin', 'approved');
CREATE INDEX closures_crm_idx ON closures (crm_id);

CREATE INDEX failures_happened_idx ON failures (happened_at);

CREATE UNIQUE INDEX info_requests_message_idx
    ON info_requests (dispatcher_message_id) WHERE dispatcher_message_id IS NOT NULL;

CREATE UNIQUE INDEX masters_username_idx
    ON masters (lower(telegram_username)) WHERE telegram_username IS NOT NULL;

CREATE INDEX requests_pending_notify_idx
    ON requests (city_id, crm_id) WHERE notified_at IS NULL;

CREATE UNIQUE INDEX shifts_position_idx ON shifts (shift_date, city_id, "position");

-- внешние ключи
assignments.crm_id       → requests.crm_id
assignments.employee_id  → masters.employee_id
closures.employee_id     → masters.employee_id
info_requests.employee_id→ masters.employee_id
shifts.employee_id       → masters.employee_id
```

### Назначение таблиц и нетривиальных колонок

**`requests`** — зеркало грида CRM. `is_open` не означает «есть в гриде»: грид
вопреки контракту отдаёт и закрытые заявки, поэтому активность определяется
статусом. `notified_at` — отправлено ли уведомление в ленту. `info_line` — строка
заявки, как её печатает карточка; в гриде её нет, читается отдельно.
`prior_master_name` — кто делал прошлую работу у этого клиента, по нему гарантия
возвращается прежнему мастеру. `raw_grid_row` — все ячейки строки грида как есть,
на случай если разбор что-то потеряет.

**`masters`** — справочник филиала. `telegram_id` — кто нажал кнопку,
`chat_id` — рабочий чат, куда уходит заявка. Это разные вещи: заявки приходят в
групповой чат, а кнопку нажимает конкретный человек. `telegram_username`
прописывается заранее, чтобы бот привязал мастера сам при первом `/start`.
`pinned_message_id` — что сейчас закреплено в его чате.

**`shifts`** — очередь дня. `position` — порядок утреннего «плюса», он же порядок
раздачи. Уникальный индекс по `(дата, город, позиция)` не даёт двум мастерам занять
одну позицию при одновременном нажатии.

**`assignments`** — кому и когда отдана заявка. Частичный уникальный индекс
гарантирует одно активное назначение на заявку. Отметки времени отдельными
колонками, а не одним полем состояния: по ним считаются таймеры.

**`closures`** — отчёт мастера. Живёт по одной строке на попытку. Колонки
`prepayment`, `agreed_sum`, `deadline`, `malfunction` — остатки прежней анкеты СД,
сейчас не заполняются, но в старых строках данные есть. `prepayment_sum` — текущая
предоплата как деньги. `photos` — словарь «вид снимка → список имён файлов».
`chat_messages` — номера сообщений, которые надо убрать из чата мастера.
`decider_messages` — все копии отчёта у проверяющих, чтобы погасить кнопки у того,
кто не успел нажать.

**`info_requests`** — запросы диспетчерам (номер, квартира, номер для гарантии).
Таблица создавалась под телефоны, отсюда имена ограничений `phone_requests_*`.

**`failures`** — журнал сбоев для вечерней сводки.

**`app_state`** — пары «ключ-значение» для всего, что должно пережить перезапуск.

### Состояние, переживающее перезапуск

Всё состояние — в базе, ничего в памяти процесса.

| Ключ в `app_state` | Смысл |
|---|---|
| `baseline_seeded_at` | Первый запуск состоялся; заявки, открытые на тот момент, новостью не считаются |
| `shift_prompt:<город>:<дата>` | Сбор смены за этот день отправлен |
| `roll_call:<город>:<дата>` | Сверка смены проведена |
| `rotation:<дата>` | Позиция курсора очереди: кому отдали последнюю заявку |
| `no_masters:<дата>` | Тревога «никто не на смене» за этот день отправлена |
| `escalated:<заявка>` | Тревога «нет свободных» по заявке отправлена |
| `warranty_wait:<заявка>` | Уведомление «гарантия ждёт мастера» отправлено |
| `digest:<город>:<дата>` | Итоги дня отправлены |
| `failure_report:<город>:<дата>` | Сводка сбоев за день отправлена |
| `failure_report_since` | Момент прошлой сводки: от него считается следующий период |
| `admin_chat_id` | Запомненный чат администратора |

Шаг анкеты и ответы лежат в `closures`, поэтому перезапуск службы посреди диалога
ничего не теряет: мастер отвечает на тот же вопрос дальше.

### Миграции

| № | Что делает |
|---|---|
| 001 | `requests`, `app_state` — базовый каркас |
| 002 | `masters`, `shifts`, `assignments`; наполняет справочник мастеров филиала из контракта, уволенный выключен сразу |
| 003 | `masters.telegram_username` + уникальный индекс по нему |
| 004 | `masters.chat_id` — рабочий чат мастера |
| 005 | `assignments.reminder_sent_at` |
| 006 | `phone_requests` — запрос номера у диспетчеров |
| 007 | Переименование в `info_requests`, добавлен `kind` |
| 008 | `requests.info_line` |
| 009 | `assignments.payout_sent_at` + индекс ожидающих расчёта |
| 010 | `assignments.inwork_at` |
| 011 | `masters.pinned_message_id` |
| 012 | `assignments.reminder_message_id` |
| 013 | `requests.prior_master_name` |
| 014 | `closures` — отчёт мастера целиком |
| 015 | Поля СД: `kind`, `prepayment`, `agreed_sum`, `deadline`, `malfunction` |
| 016 | `closures.chat_messages` |
| 017 | `closures.branch_comment` |
| 018 | Директор заведён как мастер (чтобы закрывать чужие заявки) |
| 019 | Директор выключен как мастер — решение отменено |
| 020 | Уникальность активного отчёта по паре (мастер, заявка), а не по мастеру |
| 021 | `closures.decider_messages`, `closures.decided_by` |
| 022 | Таблица `failures` |
| 023 | `prepayment_sum`, `conduct_error`, `conduct_attempts`, `question_message_id`; состояние `rejected` → `discarded`; сброс незавершённых отчётов на первый шаг новой анкеты |

Миграции применяются вручную: `psql "$DATABASE_URL" -f migrations/NNN_*.sql`.
Механизма учёта применённых миграций нет — см. раздел 11.

---

## 5. Конфигурация

Все переменные читаются из `.env` в корне проекта. Значения показаны только для
безопасных настроек.

### Обязательные

Без них служба не стартует и печатает список недостающего.

| Переменная | Тип | Смысл |
|---|---|---|
| `CRM_USERNAME` | строка | Логин сервисной учётки CRM. `<REDACTED>` |
| `CRM_PASSWORD` | строка | Пароль. `<REDACTED>` |
| `CRM_LOGIN_FIELD_USERNAME` | строка | Имя поля логина в форме входа Yii |
| `CRM_LOGIN_FIELD_PASSWORD` | строка | Имя поля пароля |
| `TELEGRAM_BOT_TOKEN` | строка | Токен бота. `<REDACTED>` |
| `DATABASE_URL` | строка | Строка подключения к PostgreSQL. `<REDACTED>` |
| `MASTERS_CHAT_ID` | id чата | Общий чат мастеров: утренний сбор смены |
| `DIRECTOR_CHAT_ID` | id чата | Чат директора |
| хотя бы один из `OWNER_CHAT_ID`, `ADMIN_CHAT_ID`, `TELEGRAM_TEST_CHAT_ID` | id чата | Иначе лента и сбои уйдут в никуда |

`MASTERS_CHAT_ID` и `DIRECTOR_CHAT_ID` обязательны только для поллера.

### Роли

| Переменная | Значение | Что будет при отсутствии |
|---|---|---|
| `OWNER_CHAT_ID` | id чата владельца | Владельцем считается `TELEGRAM_TEST_CHAT_ID`, затем `ADMIN_CHAT_ID` |
| `ADMIN_CHAT_ID` | id чата администратора | Отчёты идут только директору, пока администратор не напишет боту |
| `ADMIN_USERNAME` | @username администратора, по умолчанию задан в коде | Привязка по username не сработает |
| `DIRECTOR_CHAT_ID` | id чата директора | Директор не получает ни тревог, ни права решать |
| `DISPATCHERS_CHAT_ID` | id чата диспетчеров | Кнопки запроса номера и квартиры отвечают «чат не настроен» |
| `TELEGRAM_TEST_CHAT_ID` | id прежнего чата ленты | Оставлен для совместимости, заменён на `OWNER_CHAT_ID` |

### CRM

| Переменная | Значение | Смысл |
|---|---|---|
| `CRM_BASE_URL` | `<домен CRM>` | Корень платформы |
| `CRM_READ_ONLY` | `true` по умолчанию, **в бою `false`** | При `true` ни один пишущий запрос не уходит, только запись в журнал |
| `CRM_ALLOW_CLOSING` | `false` по умолчанию, **в бою `true`** | Отдельный флаг на запись закрытия и проведение |
| `CRM_WRITE_ONLY_FOR` | пусто | Список номеров заявок через запятую. Непустой список = писать **только** в них, остальные обходить как при read-only. Ограничивает всю запись, не только проведение |
| `CRM_TIMEOUT_SEC` | 30 | Таймаут HTTP-запроса |
| `CRM_FAILURES_BEFORE_ALERT` | 5 | Сколько неудачных опросов подряд до тревоги |

### Филиал и распределение

| Переменная | Значение | Смысл |
|---|---|---|
| `CITY_ID` | 206 | Филиал (Сыктывкар) |
| `POLL_INTERVAL_SEC` | 60 | Шаг цикла опроса |
| `ACTIVE_STATUSES` | `Ожидает,В пути,В работе,В работе СД` | Незакрытые статусы. Незнакомый статус считается закрытым |
| `ASSIGNABLE_STATUSES` | `Ожидает` | Какие заявки можно раздавать |
| `SILENT_STATUSES` | `В работе СД` | По ним бот молчит: ни таймеров, ни ленты |
| `DONE_STATUSES` | `Готов,Готов ОФ` | Закрытые для итогов дня |
| `ASSIGN_LEAD_MIN` | 60 | За сколько минут до визита отдавать заявку мастеру |
| `ESCALATE_LEAD_MIN` | 60 | За сколько минут до визита бить тревогу «нет свободных» |
| `ASSIGN_WINDOW_START` / `_END` | `09:00` / `22:00` | Вне окна заявки копятся |
| `ACCEPT_REMINDER_MIN` | 5 | Через сколько минут напомнить мастеру |
| `ACCEPT_TIMEOUT_MIN` | 15 | Через сколько отдать следующему |
| `ONSITE_TIMEOUT_MIN` | 55 | Выехал и не отметился на месте — тревога |
| `SHIFT_CHECK_TIME` | `09:00` | Когда звать на смену |
| `SHIFT_ROLL_CALL_MIN` | 30 | Через сколько сверить список отметившихся |

### Отчёт, сводки, снимки

| Переменная | Значение | Смысл |
|---|---|---|
| `CLOSING_MAX_SUM` | 1 000 000 | Потолок суммы в отчёте: защита от лишнего нуля |
| `PHOTO_DIR` | `photos` | Каталог снимков, относительно рабочего каталога службы |
| `PHOTO_KEEP_DAYS` | 14 | Сколько дней держать снимки |
| `FAILURE_REPORT_TIME` | `21:00` | Вечерняя сводка сбоев владельцу |
| `FAILURE_KEEP_DAYS` | 30 | Сколько дней держать журнал сбоев |
| `DIGEST_TIME` | `22:00` | Итоги дня |
| `TIMEZONE` | `Europe/Moscow` | Часовой пояс всех расписаний |
| `LOG_LEVEL` | `INFO` | При `DEBUG` в журнал попадает трасса httpx |

### Только для тестов

`TEST_DATABASE_URL` — отдельная база для тестов, которые ходят в настоящий
PostgreSQL. Без неё эти тесты пропускаются. **На боевом сервере не задана**, так
что прогон тестов там боевые данные не трогает.

---

## 6. Стык с CRM

Платформа — Yii2, авторизация по сессионной куке, защита CSRF, гриды через PJAX.
REST API у платформы есть, но токена филиалу не выдали, поэтому работа идёт через
HTML. Весь разбор сосредоточен в `crm.py`; при появлении токена переписывается
только этот файл.

### Адреса

| Константа | Путь | Назначение |
|---|---|---|
| `LOGIN_PATH` | `/admin/login` | Форма входа |
| `GRID_PATH` | `/admin/domain/customer-request/index` | Грид заявок |
| `CARD_PATH` | `/admin/domain/customer-request/update` | Карточка: чтение и запись |
| `IMAGE_UPLOAD_PATH` | `/admin/domain/customer-request/image-upload` | Загрузка снимков |

Заголовок `User-Agent: bt-dispatch-bot/1.0` — бот не маскируется под браузер.

### Вход и удержание сессии

1. `GET /admin/login`. Со страницы читаются `meta[name="csrf-param"]` и
   `meta[name="csrf-token"]`, а также **все скрытые поля формы** — их состав у Yii
   меняется между версиями, поэтому они не перечисляются в коде, а снимаются.
2. `POST /admin/login` с логином, паролем, csrf и скрытыми полями. Любое поле,
   в имени которого есть `rememberme`, выставляется в `1`: чем реже релогин, тем
   меньше шансов разойтись с CRM на ровном месте.
3. `GET /` для проверки. **Признак успешного входа — наличие `.navbar` на
   странице.** Его отсутствие означает, что вход не прошёл.

Вход не подчиняется `CRM_READ_ONLY`: это аутентификация, а не запись данных.

**Детект разлогина.** Любой GET считается разлогиненным, если ответ — редирект,
либо код 401/403, либо на странице нет `.navbar`. Тогда выполняется один релогин
и один повтор запроса. Если и после этого признак разлогина — бросается
`CrmAuthError`. Отдельного счётчика попыток нет: повтор ровно один.

**Сетевые сбои** не ретраятся внутри клиента. Цикл опроса ловит `CrmError` и
`httpx.HTTPError`, считает неудачи подряд и тревожит человека только после
`CRM_FAILURES_BEFORE_ALERT` (5) подряд. Одна заминка проходит незамеченной.

### Чтение грида

`GET /admin/domain/customer-request/index?sort=-id`.

- таблица: `.grid-view table.table__tr-link`;
- строки: `tbody tr[data-key]`;
- ячейки: `td`, ожидается ровно **13** ячеек (`GRID_CELLS`), строка с другим
  числом пропускается.

Разбор ячеек по индексам: 0 — номер заявки, 3 — тип, 4 — статус, 6 — клиент,
7 — адрес, 8 — мастер, 9 — время визита, 10 — создано, 11 — закрыто, 12 — сумма.
Суффикс `(Отз)` в статусе означает отзывную заявку и срезается регуляркой.

Грид **вопреки контракту отдаёт и закрытые заявки** — проверено 16.09.2026: среди
35 строк были «Готов», «Отказ», «Отмена Филиала». Поэтому активность заявки
определяется статусом, а не фактом присутствия в гриде.

### Чтение карточки

`GET /admin/domain/customer-request/update?id=<номер>`.

- форма: `#customerRequestForm`;
- поля: `input, select, textarea` внутри формы;
- у select текущее значение берётся из `option[selected]`, а список допустимых —
  из всех `option`;
- блок расчёта ищется текстовым якорем `Проведенная сумма по заявке` до ближайшего
  `<hr>`, затем разбирается регуляркой на три части: проведённая сумма, сумма к
  сдаче, группа расчёта;
- история заказов клиента: `#customer-requests-grid`, строки `tbody tr[data-key]`,
  ячейки 1 — тип, 5 — статус, 6 — мастер;
- сообщения Yii после POST: `.alert-danger, .alert-container .alert`;
- адреса загрузки снимков вытаскиваются регуляркой из скриптов страницы:
  `image-upload?id=<токен>&target=images_*`;
- на **проведённой** заявке статус — не select, а `disabled input` со значением
  «Готов», часто без атрибута `name`; для этого есть отдельный разбор через
  `.field-customerrequest-status`.

### Поля формы, которые отправляются при записи

| Поле | Когда |
|---|---|
| `CustomerRequest[employee_id]` | Назначение мастера |
| `CustomerRequest[status]` | Смена статуса: 1 Ожидает, 4 В пути, 8 В работе, 9 В работе СД, 64 Отказ |
| `CustomerRequest[payed_by_customer]` | Закрытие: сумма заявки, итоговая |
| `CustomerRequest[spares_cost]` | Закрытие: стоимость ЗПЧ |
| `CustomerRequest[with_bso]` | 0/1, выводится из наличия снимков документов |
| `CustomerRequest[with_zip]` | 0/1, выводится из стоимости ЗПЧ |
| `CustomerRequest[receipt_mode]` | Всегда `0` — филиал работает без чека |
| `CustomerRequest[fback_mode]` | 1 Да, 2 Нет, 3 Нет возможности |
| `CustomerRequest[is_req_fback]` | Всегда `1` при заполненном отзыве |
| `CustomerRequest[recommendation_comment]` | Комментарий филиала (только СД) |
| `CustomerRequest[work_in_sd_ready_at]` | Срок сдачи СД, формат `dd-mm-yyyy` |
| `save_close` | `1` в первом запросе закрытия |
| `CustomerRequest[prepayment]` | **Не заполняется.** Уходит в теле формы ровно таким, каким прочитан |

Окна для снимков: `images_main` — договор и чеки на услуги, `images_check` — чеки
на комплектующие, `images_spare` — фото запчастей, `images_safetyreceipt` —
сохранная расписка (только СД), `images_feedback` — отзыв. Чек за запчасти CRM
ждёт **в двух окнах сразу**, поэтому один снимок отправляется дважды.

`is_req_fback` и `save_close` в HTML-форме отсутствуют: первый рисуется
JavaScript, второй вставляется по `onclick` кнопки «Сохранить и закрыть». Yii их
принимает, поэтому они перечислены в `EXTRA_POST_FIELDS` и не считаются лишними
при сверке.

### Запись: read-modify-write и сверка

Любая запись выполняется так, под блокировкой `pg_advisory_lock` по номеру заявки:

1. `GET` карточки, снимается вся форма;
2. поверх неё кладутся изменяемые поля;
3. проверка, что каждое изменяемое поле вообще есть в форме — иначе исключение
   «поля недоступны для записи», обычно это значит, что заявка уже закрыта;
4. при наличии снимков — загрузка каждого отдельным `POST` на `image-upload` с
   заголовком `X-Requested-With: XMLHttpRequest`; ответ ожидается JSON, поле
   `error` в нём означает отказ;
5. `POST` формы целиком на `/admin/domain/customer-request/update?id=<номер>`;
6. `GET` карточки заново и **сверка**: задуманные поля должны лечь, соседние —
   не измениться.

Сверка различает три исхода: поле не легло (исключение), поле легло, но задето
чужое (запись в журнал и предупреждение человеку), всё чисто.

Тонкости сверки:

- пустая строка и `0`/`0.00` для number input Yii — одно и то же (`_blank_or_zero`);
- у селектов это **не так**: пустое — «Выберите...», а `0` — «Нет». Поэтому после
  закрытия дополнительно проверяются подписи выбранных option: если вместо «Нет»
  осталось «Выберите...», запись не прошла;
- `CustomerRequest[employee_id]` тянет за собой скрытое `_employee_id` — это
  ожидаемо и тревогой не считается.

### Проведение заявки

Статус «Готов» в селекте карточки недоступен. Проведение — это **два POST подряд**:

1. вся форма + скрытое `save_close=1`. Без него Yii сохраняет черновик: суммы
   могут лечь, а нули, «Нет» и отзыв — нет;
2. та же форма на тот же адрес с `&finish=1` и **без** `save_close`.

Затем карточка перечитывается: проведённой считается заявка, чей статус начинается
на «Готов» либо у которой появился блок расчёта.

Отдельные случаи:

- **Заявка в статусе «Отказ».** Футер карточки пустой, кнопок сохранения нет, Yii
  игнорирует POST. Бот сначала возвращает заявку в работу, перечитывает карточку и,
  если статус не сдвинулся, отказывается работать с просьбой провести вручную.
- **Заявка уже проведена.** Перед записью проверяется статус; если «Готов», бот
  молча выходит, ничего не отправив. Это защита от повторного нажатия «Провести».
  Проверка нарочно строгая (только статус, не блок расчёта): ошибка в эту сторону
  безопасна, а ошибка в обратную пометила бы отчёт записанным при незаписанных
  деньгах.

### Хрупкие места разбора

Всё перечисленное сломается при смене вёрстки CRM, и сломается молча, если не
следить:

```
.navbar                                  признак, что мы залогинены
.grid-view table.table__tr-link          таблица грида
tbody tr[data-key]                       строки грида и истории
td                                       ячейки, ожидается ровно 13
#customerRequestForm                     форма карточки
option[selected]                         текущее значение селекта
#customer-requests-grid                  история заказов клиента
.field-customerrequest-status            статус на проведённой заявке
.alert-danger, .alert-container .alert   сообщения Yii
meta[name="csrf-param"] / [name="csrf-token"]
form[action="/admin/login"]              форма входа
текстовый якорь «Проведенная сумма по заявке»
регулярка image-upload?id=…&target=images_*
```

---

## 7. Автоматы состояний

### Заявка (таблица `requests` + `assignments`)

```
                    [появилась в гриде CRM]
                              │
                              ▼
                        Ожидает ──────────────────── раздача возможна
                              │
             (за ASSIGN_LEAD_MIN до визита, в рабочем окне)
                              │
                              ▼
            assignments.state = assigned ──► сообщение мастеру, закрепляется
                              │
        ┌─────────────────────┼──────────────────────────┐
        │                     │                          │
  мастер молчит          мастер нажал            директор нажал
  ACCEPT_TIMEOUT         «🚗 В пути»            «🔄 Передать другому»
        │                     │                          │
        ▼                     ▼                          ▼
   state=reassigned      state=enroute            старое назначение
   заявка уходит         CRM: статус 4            → reassigned,
   следующему            заявка остаётся          сообщение удаляется,
   по кругу              закреплённой             новое уходит другому
                              │
                              ▼  мастер нажал «📍 На месте»
                        state=onsite
                        (статус в CRM НЕ меняется: мастер ещё осматривает)
                              │
                              ▼  мастер нажал «🔧 В работе»
                        state=inwork
                        CRM: статус 8
                              │
              ┌───────────────┴────────────────┐
              ▼                                ▼
      «📋 Отчёт»                        «📦 В работе СД»
      (см. автомат отчёта)              CRM: статус 9,
              │                         расписка и комментарий филиала
              ▼                                │
      CRM: save_close + finish=1               ▼
      статус «Готов»                    заявка молчит, пока мастер
              │                         не упомянет её номер боту
              ▼                                │
   поллер видит «Готов», читает                ▼
   блок расчёта, шлёт мастеру           отчёт СД → тот же путь
   и снимает закрепление                к «Готов»
```

Отказ и перенос заявки оформляет администратор в CRM — кнопки отказа у мастера
нет.

### Отчёт мастера (таблица `closures`)

Состояния строки:

```
collecting ──► pending_admin ──► approved ──► written
     ▲               │              │
     │               │              └─ CRM отказала: остаётся approved,
     │               │                 кнопка «Повторить проведение»
     │               ▼
     │          discarded  (отклонён проверяющим)
     │               │
     └───────────────┘  заводится НОВАЯ строка, анкета с нуля,
                        фото прежней строки в CRM не попадут
```

Анкета обычного закрытия (`kind = close`):

```
1. docs_photo   «Загрузите фото БСО и чека СМЗ»          [Готово]
                снимок засчитывается ТОЛЬКО реплаем на вопрос бота;
                каждый скачивается, сразу удаляется из чата,
                счётчик дописывается в сам вопрос
                → есть снимки = with_bso 1, нет = 0
2. prepay       «Предоплата? …если её не было — 0»
3. total        «Сумма заявки? … итоговое — вместе с предоплатой»
4. zip_sum      «Стоимость ЗПЧ? Если их не было — 0»
        ├─ сумма > 0 ──► 5. zip_photo «Пришлите фото чека на ЗПЧ» [Готово]
        └─ сумма = 0 ──────────────┐
                                   ▼
6. feedback     «Отзыв?»   [Да] [Нет] [Нет возможности]
        └─ «Да» ──► 7. feedback_photo «Пришлите фото отзыва»  [Готово]
                                   │
                                   ▼
            сводка мастеру, администратору и директору
                   [✅ Провести] [❌ Отклонить]
```

Анкета СД-открытия (`kind = sd_open`): расписка → комментарий филиала, у которого
третья строка обязана содержать дату в формате `ДД.ММ.ГГГГ`.

Анкета закрытия СД (`kind = sd_close`): сумма → комплектующие → чек → договор →
отзыв. Порядок оставлен прежним: техника у мастера неделями, менять вопросы на
полпути незачем.

Дистанционное решение (`kind = remote`): вопросов нет, всё по нулям.

### Смена (таблица `shifts`)

```
09:00  бот пишет в общий чат «Доброе утро. Кто на смене - нажмите +»
       и тегает всех активных мастеров
          │
          ▼
       мастер жмёт «➕ На смене» ──► строка в shifts, позиция = порядок нажатия
          │
          ▼
09:30  сверка: кто не отметился ──► список администратору и директору
       (или «📋 Смена собрана полностью»)
          │
          ▼
       очередь дня готова; курсор раздачи хранится в app_state
```

Мастер, не отметившийся на смене, заявок не получает. Директор в очередь не
встаёт.

---

## 8. Таймеры и расписание

Всё расписание крутится внутри одного цикла поллера с шагом `POLL_INTERVAL_SEC`
(60 секунд). Отдельного планировщика нет.

| Когда | Что делает | Если процесс лежал |
|---|---|---|
| каждые 60 с | Опрос грида, обновление `requests`, отметка закрытых | Наверстает при старте: грид отдаёт текущее состояние |
| каждые 60 с | Лента новых заявок владельцу и директору | Наверстает: непосланные заявки помечены `notified_at IS NULL` |
| `SHIFT_CHECK_TIME` 09:00 | Сбор смены в общий чат | Отправится при первом опросе после запуска — даже если это 11:00. Ключ в `app_state` не даст отправить дважды |
| +`SHIFT_ROLL_CALL_MIN` 30 мин | Сверка отметившихся | То же |
| каждые 60 с, в окне 09:00–22:00 | Раздача заявок за `ASSIGN_LEAD_MIN` до визита | Наверстает; заявки не теряются |
| +`ACCEPT_REMINDER_MIN` 5 мин | Повтор заявки мастеру и сигнал директору | Сработает с опозданием на следующем опросе |
| +`ACCEPT_TIMEOUT_MIN` 15 мин | Заявка уходит следующему по кругу | То же |
| +`ONSITE_TIMEOUT_MIN` 55 мин | «В пути больше 55 минут» администратору и директору | То же, один раз на назначение |
| каждые 60 с | Расчёт мастеру по заявкам, ставшим «Готов» | Наверстает |
| `FAILURE_REPORT_TIME` 21:00 | Сводка сбоев владельцу за сутки | Отправится при первом опросе после запуска. Период считается **от прошлой сводки**, поэтому пропущенные часы не выпадают |
| `DIGEST_TIME` 22:00 | Итоги дня владельцу и директору | Отправится при первом опросе после запуска |
| раз в сутки, вместе со сводкой | Уборка снимков старше `PHOTO_KEEP_DAYS` и записей `failures` старше `FAILURE_KEEP_DAYS` | Отложится до следующей сводки |

Общее правило: **ничего не теряется, но всё сдвигается**. Каждое разовое событие
защищено ключом в `app_state`, поэтому повторно не отправится, но и не пропустится.

Единственное исключение — таймеры приёма заявки. Если процесс лежал два часа,
мастер получит напоминание и потеряет заявку сразу после старта, хотя по календарю
это должно было случиться давно.

---

## 9. Сообщения бота

Дословно, как их видит человек. `{…}` — подстановка.

### Мастеру

**Заявка** (`assignment_text`). Префикс: тег мастера (только при выдаче) и пометка
типа. Дальше строка заявки из CRM, затем подсказка по состоянию:

```
@{username}
⚠️ ГАРАНТИЯ (первым был: {мастер})      ← только для гарантии
🔁 Повтор (первым был: {мастер})         ← только для повтора
{строка заявки из CRM}

Время ожидания принятия — 5 минут
```

Подсказки по состояниям:

| Состояние | Хвост сообщения |
|---|---|
| `assigned` | `Время ожидания принятия — {ACCEPT_REMINDER_MIN} минут` |
| `enroute` | `В пути. Нажмите «На месте», когда приедете.` |
| `onsite` | `На месте. Нажмите «В работе», когда приступите.` |
| `inwork` | `В работе.` |
| `reassigned` | `Заявка передана другому мастеру.` |

**Кнопки мастера** по состояниям:

| Состояние | Кнопки |
|---|---|
| `assigned` | `🚗 В пути`; для гарантии добавляется `📞 Дистанционное решение`, после запроса номера — `✅ Решено дистанционно` |
| `enroute` | `📍 На месте`, `📞 Номер для дозвона` |
| `onsite` | `🔧 В работе`, `🏠 Запрос квартиры`, `📞 Номер для дозвона` |
| `inwork` | `📋 Отчёт`, `📦 В работе СД` |

**Напоминание** (`reminder_text`):

```
@{username}
⏰ Напоминание

{строка заявки из CRM}
```

**Вопрос анкеты** (`closing_question`):

```
Закрытие заказа {номер}

{текст вопроса}

↩️ Ответьте на это сообщение.      ← для сумм и текста

📷 Принято снимков: {N}             ← для фото, когда уже что-то прислали
```

Тексты вопросов обычного закрытия:

```
Загрузите фото БСО и чека СМЗ. Когда всё отправите — нажмите «Готово».
Предоплата? Сумма в рублях, если её не было — 0.
Сумма заявки? Число в рублях, итоговое — вместе с предоплатой.
Стоимость ЗПЧ? Если их не было — 0.
Пришлите фото чека на ЗПЧ. Когда всё отправите — нажмите «Готово».
Отзыв?
Пришлите фото отзыва. Когда всё отправите — нажмите «Готово».
```

Кнопки: под фото-шагом `Готово`, под отзывом `Да` / `Нет` / `Нет возможности`.

Вопрос комментария филиала при открытии СД:

```
Комментарий филиала

Заполните комментарий по следующему скрипту:
- неисправность
- *сумма предоплаты*
- срок сдачи *строго по формату 00.00.2026 (без "до", "в конце недели", "завтра" и т.д.)*
```

Отказ при неверной дате:

```
Третья строка — только дата сдачи, например 19.09.2026.
Без «до», «завтра», «в конце недели».
```

**Отказ от мусорного ответа:**

```
⚠️ Нужно число в рублях, без слов. Например: 3500 или 3500,50

{вопрос повторяется}
```

```
⚠️ Сумма больше 1000000 р. — похоже на опечатку. Проверьте и пришлите ещё раз.
```

**Ответ не реплаем** — одинаково для текста, сумм и фото:

```
↩️ Ответьте реплаем на вопрос бота — так я отличу отчёт от переписки в чате.

{вопрос повторяется}
```

Несколько снимков одним альбомом достаточно отправить одним реплаем: Telegram
проставляет ссылку на вопрос каждому сообщению альбома.

**Прочее мастеру:**

```
Отчёт отправлен администратору на проверку.
⚠️ Не смог забрать это фото. Пришлите его ещё раз.
Отчёт готов, но проверяющим не дошёл — сообщите администратору.
❌ Отчёт по заказу {номер} отклонён.
Заполняем заново — отвечайте на вопросы ниже. Прежние фото не в счёт.
✅ Заказ {номер} закрыт.
✅ Отчёт по заказу {номер} принят.
⏳ Заказ {номер}: отчёт принят, но CRM пока не провела заявку.
Администратор уже знает. Заполнять заново не нужно.
Заявку {номер} не нахожу. Проверьте номер.
Нашёл заявку:

{строка заявки}
```

**Расчёт** (`payout_text`):

```
💰 Заказ {номер} закрыт

Проведенная сумма по заявке: {сумма}
Сумма к сдаче: {сумма}
Группа расчета: {группа}
```

**Всплывающие ответы на нажатия:** `Отметил. Вы {N}-й в очереди на сегодня`,
`Уже отмечены, вы {N}-й в очереди`, `Вас нет в списке мастеров`,
`Эта заявка сейчас не за вами`, `Уже отмечено`, `Сначала завершите предыдущий
отчёт`, `Эта заявка не за вами`, `Этот вопрос уже не актуален`,
`Отчёт уже закрыт`, `Пропущено`, `Заполним отчёт по СД`,
`Чат диспетчеров не настроен, скажите администратору`, `Спросил у диспетчеров`,
`Запросил номер у диспетчеров`, `Отправил администратору`.

### Общий чат мастеров

```
Доброе утро. Кто на смене - нажмите +

@{username} @{username} …
```
Кнопка: `➕ На смене`.

### Проверяющим (администратор и директор)

**Сводка отчёта** (`summary`):

```
📋 Отчёт по заказу {номер}
Мастер: {ФИО}

Сумма заявки: 3500 р. (в том числе предоплата 1000 р.)
ЗПЧ: 500 р.
БСО: есть
Отзыв: нет
Фото: bso: 2, spare: 1

⚠️ ЗПЧ дороже заявки: 5000 р. против 3500 р.
⚠️ Предоплата больше суммы заявки: 4000 р. против 3500 р.
⚠️ Фото БСО и чека СМЗ не приложены — в CRM уйдёт «БСО: нет»
```

Кнопки: `✅ Провести` / `❌ Отклонить`. Следом отдельными сообщениями уходят сами
снимки с подписью `{вид} · заказ {номер}`.

Для СД:

```
📦 Сложная диагностика по заказу {номер}
Мастер: {ФИО}

{комментарий филиала как есть}

Фото: safety: 1
```

Для дистанционного решения:

```
📞 Гарантия решена дистанционно, заказ {номер}
Мастер: {ФИО}

Выезда не было. Сумма 0, БСО нет, комплектующих нет.
```

**При сбое проведения** — с кнопкой `🔄 Повторить проведение`:

```
⚠️ Заказ {номер}: CRM не приняла закрытие (попытка {N}).

{текст ошибки от CRM}

Отчёт цел. Нажмите «Повторить проведение» или проведите заявку вручную.
```

**Тревоги** (администратору, директору и владельцу):

```
⚠️ Некому отдать заявки: на смену сегодня никто не отметился.
Ждут распределения: {N}

📋 Не отметились на смену ({N}):
· {ФИО}

📋 Смена собрана полностью

🔔 Заявка #{номер}: {ФИО} не принял её за 5 мин.
{адрес}
Через 10 мин заявка уйдёт следующему мастеру.

🔔 Заявка #{номер}: {ФИО} в пути больше 55 мин и не отметился на месте.
{адрес}

⏳ Гарантия ждёт своего мастера: {ФИО}

{строка заявки}

Заявка закреплена за ним и будет ждать, пока он не освободится.

🚨 Некому отдать заявку — все мастера заняты

{строка заявки}

Заняты: {ФИО, ФИО}
Назначьте вручную или дождитесь освобождения.

⚠️ Заявка #{номер}: мастер назначен в боте, но CRM не приняла запись

⚠️ Заявка #{номер} никому не ушла: {ФИО} её не принял, свободных мастеров нет

‼️ Заказ {номер}: смена статуса задела чужие поля в CRM.
{перечень}
Проверьте карточку руками.
```

У тревоги «нет свободных» есть кнопка `🔄 Передать другому` — но только у
администратора и директора, владельцу она уходит без кнопок.

### Владельцу — лента событий

```
📤 Заказ {номер} → {ФИО} ({N}-й в очереди)
🔄 Заказ {номер}: {ФИО} не принял за 15 мин, передаю дальше
🔄 Заказ {номер}: передан вручную мастеру {ФИО}
🚗 Заказ {номер}: {ФИО} — в пути
📍 Заказ {номер}: {ФИО} — на месте
🔧 Заказ {номер}: {ФИО} — в работе
📝 Заказ {номер}: {ФИО} сдал отчёт, ждёт подтверждения.
👍 Заказ {номер}: отчёт подтверждён (администратор).
↩️ Заказ {номер}: отчёт отклонён (директор), мастер заполняет заново.
✅ Заказ {номер}: закрыт в CRM.
🔧 Заказ {номер}: переведён в «В работе СД», записан в CRM.
📞 Заказ {номер}: решён дистанционно, закрыт в CRM.
💰 Заказ {номер}: расчёт отправлен, {ФИО}
👤 Администратор @{username} подключился — отчёты на подтверждение идут ему и директору.
```

**Итоги дня** (владельцу и директору, 22:00):

```
📊 Итоги 23.09.2026

Закрыто заявок: 7
Оборот: 84 000 р.
Средний чек: 12 000 р.
Самый крупный: 31 000 р. — {ФИО} (заказ {номер})

⚠️ Не закрыто: 2
· {номер} — {ФИО} (В работе)

📦 Забрали на сложную диагностику: 1
· {номер} — {ФИО}
```

Если закрытых нет — `Закрытых заявок нет.`; если всё закрыто — `✅ Все заявки дня
закрыты`.

**Сводка сбоев** (владельцу, 21:00):

```
🧾 Сводка сбоев за сутки (22.09 21:00 — 23.09 21:00)

Всего 12, причин 2.

1. 🛑 заявка {номер}: закрытие не записано в CRM
   один раз, в 12:05, в боте
   → Откройте заявку в CRM и проведите её руками: бот записать не смог,
     сам он второй раз не попробует.

2. ⚠️ Сбой связи с Telegram, бот сам переподключится
   11 раз, последний в 18:05, в боте
   → Ничего делать не нужно: бот переподключается сам. Если таких строк
     больше сотни за сутки — скажите мне, посмотрю сеть сервера.
```

Тихие сутки: `✅ Всё штатно: сбоев не было.`

**Сообщение о сбое сразу:**

```
🛑 Сбой в боте

{одна русская строка причины}
```

`⚠️` вместо `🛑` для предупреждений, `в опросе CRM` вместо `в боте` для поллера.

### Диспетчерам

Запрос — короткий скрипт в том виде, как его привыкли читать:

```
{номер} мс на месте номер для стыка
{номер} мс на месте кВ
{номер} номер для дист решения
```

Ответ диспетчера реплаем пересылается мастеру:

```
📞 Номер по заявке #{номер}:
{ответ}

🏠 Квартира по заявке #{номер}:
{ответ}
```

Диспетчеру бот отвечает `Передал мастеру`.

### Служебные команды

Доступны владельцу, администратору и директору:

```
/roles                                кто сейчас владелец, админ и директор
/masters                              список мастеров и привязок
/master_link <employee_id> <tg_id>    привязать телеграм мастера
/master_user <employee_id> <@user>    прописать username заранее
/master_chat <employee_id>            закрепить чат за мастером (в его чате)
/master_off <employee_id>             выключить мастера
/master_on  <employee_id>             включить обратно
/chatid                               показать id чата и свой id
/ping                                 → pong
/start                                представиться
```

---

## 10. Тесты

250 тестов в 14 файлах, 4313 строк. На сервере: **218 прошли, 32 пропущены, 0
упавших**.

| Файл | Что покрывает |
|---|---|
| `test_report_flow.py` | Весь автомат отчёта: обе ветки, прерывание и «перезапуск процесса», отказ с полным сбросом, двойное «Провести», падение CRM, повтор, запрет владельцу |
| `test_crm_write.py` | Запись в CRM: read-modify-write, состав обоих POST, `save_close`, `finish=1`, сверка, «Отказ», идемпотентность, read-only |
| `test_closing.py` | Анкета, переходы, состав полей формы, сводка, разбор сумм, обработчик сбоев |
| `test_director_close.py` | Директор закрывает чужую заявку: чей `employee_id` попадает в карточку |
| `test_db_integration.py` | **Требует живую БД** через `TEST_DATABASE_URL`. SQL против настоящего PostgreSQL: очередь, назначения, таймеры, итоги |
| `test_roles.py` | Кто что получает, привязка администратора, сбой базы не глушит тревогу |
| `test_failure_report.py` | Классы сбоев, советы, вечерняя сводка, период от прошлой сводки |
| `test_poller_escalate.py` | Куда уходит «нет свободных», когда бить тревогу относительно визита |
| `test_closure_per_request.py` | СД на одной заявке не мешает закрыть другую |
| `test_session.py` | Вход в CRM, детект разлогина, релогин |
| `test_parsing.py` | Разбор грида и карточки |
| `test_dispatch_queue.py` | Очередь по кругу, окно раздачи |
| `test_binding.py` | Привязка мастера по username |
| `test_photos.py` | Снимки на диске, отличие от ссылки Telegram, уборка старых |
| `test_notification.py` | Текст уведомления о новой заявке |

Пропускаются 32 теста — это `test_db_integration.py`, которому нужна отдельная
база. **На боевом сервере `TEST_DATABASE_URL` не задан**, поэтому они там не
выполняются, а боевые данные прогон не трогает.

### Чего тесты не покрывают

- **Живую CRM.** Все тесты записи работают на подставном HTTP-транспорте с
  зафиксированной разметкой. Если CRM сменит вёрстку, тесты останутся зелёными,
  а бот сломается. Это главная дыра.
- **Полный цикл проведения на настоящей заявке.** Ни разу не выполнялся.
- **Загрузку снимков в CRM.** Есть проверка, что запрос уходит с нужным полем, но
  живой ответ krajee не видели.
- **Telegram.** Ни одного теста против настоящего API: всё на моках. Ограничения
  частоты, удаление старых сообщений, права в чатах не проверяются. В частности,
  не проверено вживую, что Telegram проставляет ссылку на вопрос каждому
  сообщению альбома — на этом держится удобство отправки нескольких снимков.
- **Запросы диспетчерам.** Ветка живая, но в бою не работала ни разу — чат не
  настроен.
- **Одновременность.** Блокировка `pg_advisory_lock` покрыта только в
  интеграционных тестах, которые на сервере не гоняются.
- **Восстановление после долгого простоя.** Поведение таймеров при часах простоя
  описано, но не проверено.

---

## 11. Что сломано и что костыль

Меток `TODO` и `FIXME` в коде нет — ниже то, что известно по существу.

### Известные проблемы

1. **Нет учёта применённых миграций.** Ни таблицы `schema_migrations`, ни
   инструмента. Порядок держится в голове и в README. Все миграции написаны
   идемпотентно (`IF NOT EXISTS`), поэтому повторный прогон безопасен, но узнать,
   что применено, можно только глядя на схему.

2. **Четыре отчёта застряли в состоянии «подтверждён».** Администратор их
   одобрил, но в CRM они так и не записались: все четыре заявки — отказы, а у
   заявки в статусе «Отказ» в CRM нет кнопок сохранения, Yii игнорирует POST.
   Денег это не касается — во всех четырёх сумма, ЗПЧ и БСО нули, в CRM они
   стоят как «Отказ» без суммы, расхождений с отчётом нет.

   Строки оставлены намеренно: «подтверждён, но CRM отказала» — правда о том,
   что произошло, и стирать её незачем. Вреда они не приносят: состояние
   `approved` ничего не перехватывает, а новый отчёт по закрытому отказу
   никому не нужен. Кнопки «Повторить проведение» у них нет — она появилась
   позже этих отчётов.

3. **Снимки, съеденные брошенным отчётом.** До 24.09 фото, присланное мастером
   в чат **не** реплаем, уходило в последний незавершённый отчёт: в чате
   исчезало, в CRM не попадало, мастер об этом не узнавал. Причина устранена —
   фото теперь требует реплая так же, как текст.

   Один случай подтверждён фактом: в брошенном отчёте по заявке от 18.09 лежали
   пять снимков сохранной расписки, которые в карточку CRM не попали. Заявка
   закрыта администратором, документы он оформил своим путём, но в карточке их
   нет. Сам отчёт убран 24.09.

4. **`.env.example` отстал от `config.py`.** Не описаны `CRM_ALLOW_CLOSING`,
   `CRM_FAILURES_BEFORE_ALERT`, `SILENT_STATUSES`, `SHIFT_ROLL_CALL_MIN`,
   `DIGEST_TIME`, `DONE_STATUSES`, `TEST_DATABASE_URL`. Человек, поднимающий
   систему с нуля по этому файлу, получит рабочую конфигурацию только по умолчанию.

5. **`git` на сервере тянет только `main`.** Refspec узкий, поэтому
   `git pull` на выложенной ветке молча отвечает «уже актуально». На сервере
   refspec расширен вручную для текущей ветки, но это не в репозитории.

6. **Медленный SSH-баннер на сервере.** Ответ приходит дольше 40 секунд, похоже на
   обратный DNS-запрос в настройках sshd. Ни на что не влияет, кроме удобства, но
   ломает инструменты с коротким таймаутом.

7. **Бот и поллер держат разные HTTP-сессии к CRM.** Два процесса — два входа.
   Ничего не ломается благодаря блокировке по заявке, но нагрузка на CRM вдвое
   больше необходимой и релогины происходят независимо.

### Костыли и места, сделанные наспех

- **`_blank_or_zero`.** Пустая строка и `0` считаются одним и тем же, потому что
  Yii2 рисует ноль в number input как `value=""`. Держится на наблюдении за живой
  карточкой, а не на документации.
- **`EXTRA_POST_FIELDS`.** Поля `save_close` и `is_req_fback` отправляются, хотя в
  HTML их нет: первый вставляет JavaScript по клику, второй рисуется скриптом.
  Найдено опытным путём.
- **Разбор блока расчёта текстовым якорем.** `html.find("Проведенная сумма по
  заявке")` до ближайшего `<hr>` — ни класса, ни идентификатора у блока нет.
- **`GRID_CELLS = 13`.** Строка грида с другим числом ячеек молча пропускается.
  Если CRM добавит колонку, бот перестанет видеть заявки и не скажет об этом
  внятно.
- **`SD_MENTION_RE`.** Закрытие СД начинается с того, что мастер упоминает бота и
  пишет номер заявки в свободной форме. Регулярка `@\w*bot\b.*?(\d{5,})` —
  договорённость, а не интерфейс.
- **Колонки `prepayment`, `agreed_sum`, `deadline`, `malfunction`** в `closures`
  остались от прежней анкеты СД. Не заполняются, но и не удалены: в старых строках
  данные есть.
- **`FIELD_PREPAY_UNUSED`.** Константа поля CRM, в которое намеренно не пишем.
  Оставлена, чтобы поле не «дозаполнили» по имени из формы.
- **Директор опознаётся по `DIRECTOR_CHAT_ID`,** то есть по совпадению telegram id
  человека с id чата. Работает, потому что чат личный. В групповом чате сломается.

### Решения, о которых стоит пожалеть

- **Миграции 018 и 019.** Директор был заведён как мастер, чтобы закрывать чужие
  заявки, и на следующий день выключен: в селекте филиала его нет, и CRM получила
  бы несуществующего сотрудника. Две миграции, которые гасят друг друга.
- **Анкету закрытия переписали дважды за три дня.** Сначала по ТЗ добавили вопросы
  о режиме чека и наличии ЗПЧ, потом убрали обратно — ТЗ в этой части
  противоречило более раннему решению заказчика. Надо было сверяться с историей
  договорённостей до, а не после.
- **Снимки хранились ссылкой Telegram** до 23.09. Между отправкой фото и записью в
  CRM лежит проверка администратора — всё это время документ существовал только у
  Telegram. Исправлено, но надо было так с самого начала.
- **`_is_admin` изначально означал «чат совпадает с `ADMIN_CHAT_ID`»,** а этот чат
  в бою был чатом владельца. Роли разъехались с названиями и путали всех, включая
  автора кода.

---

## 12. Расхождения с ТЗ

### Есть в ТЗ, нет в коде

| Что | Почему |
|---|---|
| Классификатор повторных обращений (модель, которая отличала бы «клиент просит другого мастера» от обычного повтора) | Заказчик решил не делать |
| Чат диспетчеров | Код есть, чат не создан. Отложено заказчиком |
| Обкатка проведения на согласованной тестовой заявке | Заказчик сказал, что тестовой заявки нет. Предохранитель `CRM_WRITE_ONLY_FOR` остался пустым |

### Сделано иначе, чем в ТЗ

| В ТЗ | В коде | Почему |
|---|---|---|
| Вопрос «Режим для чека» с тремя вариантами | Не спрашивается, в CRM всегда `0` | Филиал всегда работает без чека — прямое указание заказчика, более раннее и более верное, чем ТЗ |
| Вопрос «Есть ли ЗПЧ?» | Не спрашивается, выводится из стоимости | То же: ответ следует из суммы |
| Предоплата → `CustomerRequest[prepayment]` | Спрашивается, но в CRM не пишется | Сумма заявки итоговая, предоплата внутри неё; филиал это поле не заполняет |
| Фото БСО → `images_safetyreceipt` | Идёт в `images_main` | Подпись окна `images_safetyreceipt` — «Сохранная расписка», оно только для СД |
| Отдельный вопрос про БСО | Нет вопроса, `with_bso` выводится из наличия снимков | Ответ уже дан делом |
| Проведение — «отдельный экшен или скрытое поле» | Два POST: `save_close=1`, затем `&finish=1` | Выяснено по живой карточке |
| Файлы «обычным multipart вместе с формой или отдельным ajax» | Отдельный ajax на `image-upload`, **до** сохранения формы | То же |

### Добавлено сверх ТЗ

- **Разделение ролей** владелец / администратор / директор с привязкой
  администратора по @username.
- **Вечерняя сводка сбоев** в 21:00 с советом по каждой причине.
- **Сообщения о сбоях в Telegram** вместо журнала сервера, со схлопыванием
  повторов и ограничением потока.
- **Кнопка «Повторить проведение»** и сохранение отчёта при отказе CRM.
- **Сверка после каждой записи** в CRM с предупреждением о задетых чужих полях.
- **Предохранитель `CRM_WRITE_ONLY_FOR`** и флаг `CRM_ALLOW_CLOSING`.
- **Закрепление текущей заявки** в чате мастера и уборка неактуальных сообщений.
- **Итоги дня** с оборотом, средним и максимальным чеком.
- **Ручная передача заявки** другому мастеру кнопкой.
- **Предупреждение при старте**, если проведение включено без предохранителя.

---

## 13. Открытые вопросы

### Про CRM

1. **Поле `prepayment` на живой форме.** Заказчик сказал, что его не заполняют, и
   бот в него не пишет. Но есть ли оно в форме вообще — не проверено. Если нет,
   read-modify-write его просто не увидит, и всё равно ничего не сломается. Стоит
   подтвердить при первом же закрытии.
2. **Поведение `finish=1` на заявке с нулевой суммой.** Дистанционное решение
   гарантии закрывается по нулям. Проведёт ли Yii такую заявку или откажет —
   неизвестно, живого случая не было.
3. **Что именно возвращает `image-upload`** при отказе: формат поля `error`
   угадан по обычному поведению krajee, живого отказа не видели.
4. **Статусы «Готов ОФ», «Отмена Филиала», «Отказ»** — точная семантика и кто их
   ставит. Бот считает их закрытыми, но в итогах дня учитывает только «Готов» и
   «Готов ОФ».
5. **Есть ли у платформы REST API для филиала.** В контракте он упомянут, токена
   не выдали. С токеном `crm.py` переписывается целиком и вся хрупкость разбора
   HTML исчезает.

### Про бизнес-процесс

6. **Что делать с заявкой, которую мастер взял, но не смог выполнить.** Кнопки
   отказа у мастера нет по решению заказчика — отказ оформляет администратор в CRM.
   Но бот об этом узнаёт только по смене статуса и до тех пор продолжает считать
   заявку активной.
7. **Кто отвечает за очередь, если мастер отметился на смене и уехал.** Занятость
   определяется по статусу заявки, а не по реальной доступности человека.
8. **Нужно ли ограничить число заявок на мастера в день.** Сейчас ограничений нет:
   круг идёт, пока есть заявки.
9. **Что считать рабочим днём для итогов.** Сейчас — календарные сутки по
   `Europe/Moscow`. Заявка, закрытая в 00:30, попадает в следующий день.
10. **Кто и как выводит мастера из справочника при увольнении.** Есть команда
    `/master_off`, но регламента нет — человек может остаться в очереди.

### Мешает двигаться прямо сейчас

- **Нет тестовой заявки для обкатки проведения.** Пока полный цикл не пройден на
  живой карточке, любое закрытие в бою — первый запуск.
- **Администратор не привязан.** Пока `@<REDACTED>` не напишет боту, отчёты идут
  владельцу, то есть ровно тому, кого от кнопок отключали.

---

## Журнал

| Дата | Коммит | Что изменилось |
|---|---|---|
| 24.09.2026 | `0d028a8` | Первая сборка документа. Зафиксировано состояние после выкладки 23.09: разделение ролей, вечерняя сводка сбоев, переписанный отчёт мастера с проведением заявки в CRM, снимки файлами на диске. |
| 24.09.2026 | `513af80` | Фото в отчёте засчитывается только реплаем на вопрос бота — как текст. До этого снимок, присланный в чат просто так, уходил в последний незавершённый отчёт мастера: в чате исчезал, в CRM не попадал. Уточнён раздел 11 про два брошенных отчёта — обе их заявки давно проведены администратором вручную. |
| 24.09.2026 | `<этот коммит>` | Убраны два брошенных отчёта (18.09 и 21.09) — переведены в `discarded`, незавершённых отчётов в базе не осталось. При проверке найдены четыре отчёта, застрявших в состоянии «подтверждён»: все четыре заявки — отказы, расхождений по деньгам нет, строки оставлены как достоверная запись. Подтверждён фактом вред от приёма фото без реплая: пять снимков сохранной расписки по заявке от 18.09 в CRM не попали. |
