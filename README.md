# task-automation

Локальный стек, который превращает сообщения тимлида в Telegram (текст или голосовое) в задачи Vikunja — автоматически, через Hermes-агент и корпоративный COMPANY AI Gateway.

- **LLM** — только через COMPANY AI Gateway (`https://aigateway.company.ru/api/v2`). Никаких прямых провайдеров.
- **Транскрипция** — COMPANY Transcribe (`ml-platform-big.company.loc:9204`) через локальный OpenAI-совместимый shim.
- **Задачи** — Vikunja 2.x (Inbox-проект).
- **Jira** — TODO: Atlassian Remote MCP, подключим позже (заглушка в `hermes/config.yaml`).

## Prereqs

- Docker Desktop ≥ 4.30 (macOS dev) или Docker Engine + compose plugin v2 (Linux prod).
- `jq` для `scripts/smoke_test.sh`.
- Доступ в корп.сеть COMPANY для `aigateway.company.ru` и `ml-platform-big.company.loc` (на macOS без VPN второй недоступен — войсы вернут 503, текстовые задачи работают).

## Архитектура

```
Telegram ──► Hermes ──► AI Gateway (LLM)
                │
                ├──► whisper-shim ──► COMPANY Transcribe   (voice)
                │
                └──► Vikunja REST API                     (create task)
```

## Шаги деплоя

1. **Скопируй env-файл:**
   ```bash
   cp .env.example .env
   ```

2. **Сгенери секреты Vikunja** и впиши в `.env`:
   ```bash
   openssl rand -hex 32    # → VIKUNJA_JWT_SECRET
   openssl rand -hex 16    # → VIKUNJA_DB_PASSWORD (любой сильный пароль)
   ```

3. **Первый запуск — только БД и Vikunja** (чтобы получить API-токен):
   ```bash
   docker compose up -d postgres vikunja
   sleep 20
   ```

4. **Настрой Vikunja UI** → `http://localhost:3456`:
   - Зарегистрируй первого пользователя — он автоматом становится админом.
   - Создай проект **"Inbox"**. Его ID обычно `1` (если нет — посмотри в URL проекта).
   - Settings → API Tokens → New token → права `tasks:read`, `tasks:write`.
   - Впиши токен в `VIKUNJA_API_TOKEN`, project ID — в `VIKUNJA_DEFAULT_PROJECT_ID`.

5. **Настрой Telegram:**
   - @BotFather → `/newbot` → скопируй токен в `TELEGRAM_BOT_TOKEN`.
   - @userinfobot → узнай numeric ID свой и тимлида → в `TELEGRAM_ALLOWED_USERS` через запятую.

6. **Настрой AI Gateway:**
   - `OPENAI_BASE_URL=https://aigateway.company.ru/api/v2` (БЕЗ trailing slash).
   - `OPENAI_API_KEY` — получи у админа gateway'а (`POST /api/v1/auth/tokens` или админка).
   - `HERMES_MODEL` — **в формате `Provider/Name`**, например `InHouse/Qwen3.5-122B`. Список:
     ```bash
     curl -s -H "Authorization: Bearer $OPENAI_API_KEY" \
       https://aigateway.company.ru/api/v2/models | jq '.[] | "\(.provider)/\(.name) (\(.task))"'
     ```

7. **Поднять весь стек:**
   ```bash
   docker compose up -d
   ```

8. **Прогнать smoke-тест:**
   ```bash
   bash scripts/smoke_test.sh
   ```
   Ожидаемый вывод — `ALL PASS` (создаётся `smoke-test-<epoch>` в Inbox через API, без Telegram).

9. **Боевая проверка:** отправь боту текстом «напомни завтра сделать ревью PR» → в Vikunja появится задача с дедлайном на завтра.

## Перенос на Linux-prod

Те же команды. `OPENAI_BASE_URL` и `TRANSCRIBE_SERVICE_URL` одинаковые на обеих площадках — оба ресурса живут внутри корпоративной сети `company.*`. На prod не забудь:
- сменить `VIKUNJA_PUBLIC_URL` на реальный внешний URL;
- открыть порт 3456 (или поставить reverse-proxy);
- проверить, что `/Users/...` → `~/Documents/task-automation/` не захватывается как bind-маунт (на Linux это `/home/<user>/Documents/task-automation/`).

## Структура

```
hermes/
  config.yaml                  # модель, STT, telegram, MCP TODO
  skills/
    task_parser/SKILL.md       # парсит текст → JSON
    vikunja_create/SKILL.md    # дёргает Vikunja API
    ask_clarification/SKILL.md # один уточняющий вопрос
    aigateway/SKILL.md         # справочник по корпоративному LLM-gateway
whisper_shim/
  Dockerfile                   # python:3.12-slim + fastapi
  app.py                       # OpenAI-compat /v1/audio/transcriptions
scripts/
  smoke_test.sh                # идемпотентная проверка без Telegram
docker-compose.yml             # postgres + vikunja + whisper-shim + hermes
.env.example                   # все переменные с комментариями
```

## Known caveats

- **После правки `SKILL.md`** индекс скиллов обновится только на новой сессии Hermes. Перезапусти агента:
  ```bash
  docker compose restart hermes
  ```
  или отправь `/reset` в чат.
- **Vikunja priority enum 1–5** — правдоподобное предположение (1=low, 2=normal, 4=high, 5=urgent). Если API ответит 400 на поле `priority` — скорректируй маппинг в `hermes/skills/vikunja_create/SKILL.md`.
- **`PUT /api/v1/projects/{id}/tasks`** — путь из свежей документации Vikunja. В скилле и в `smoke_test.sh` уже есть fallback на `POST /api/v1/tasks` с `project_id` в теле (срабатывает на HTTP 405).
- **Voice вне корп.сети.** На macOS без VPN `company.loc` не резолвится — shim отдаст 503, бот сообщит "Transcribe unreachable". Текстовые задачи работают штатно.
- **`curl` отсутствует в образе Hermes.** Все HTTP-вызовы из скиллов сделаны через `python3 + urllib.request` — если будешь добавлять новые скиллы, не забывай про это (или устанавливай `curl` сам).
- **Atlassian MCP** при активации потребует ручного OAuth-логина:
  ```bash
  docker compose exec hermes hermes mcp auth atlassian
  ```
  Инструкция оставлена в TODO-комментарии внутри `hermes/config.yaml`.
- **LLM — только gateway.** Никаких fallback'ов на api.openai.com или anthropic.com. Если gateway лежит, бот честно говорит "LLM недоступен".

## Верификация end-to-end

1. `docker compose ps` — все 4 сервиса `healthy`/`running`.
2. `bash scripts/smoke_test.sh` → `ALL PASS`.
3. `http://localhost:3456` → проект Inbox → смотри свежесозданные smoke-задачи.
4. Telegram (текст): «сделай тестовую задачу приоритет high» → в Vikunja task с priority 4.
5. Telegram (voice, только в корп.сети): голосовое → транскрипция → парсинг → задача.
