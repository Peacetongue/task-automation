# Corporate Hermes Assistant (pilot)

Локальный стек, поднимающий корпоративного Telegram-ассистента для
пилотной команды. Сотрудник пишет боту — бот ходит от его имени в:

- **on-prem Jira** (`jira.company.ru`) — поиск/создание/обновление issue.
- **on-prem Confluence** (`confluence.company.ru`) — поиск/чтение/создание страниц.
- **on-prem GitLab** (`gitlab.company.ru`) — MR, issues, комментарии.
- пересылка сообщений между членами команды.

LLM — **только** через корп.AI Gateway (`aigateway.company.ru/api/v2`).
Транскрипция voice-сообщений — через shim поверх COMPANY Transcribe.

## Архитектура

```
Telegram ──► Hermes (per-user session)
              │
              ├──► AI Gateway  (LLM, InHouse/Qwen3.5-122B)
              │
              ├──► whisper-shim ──► COMPANY Transcribe  (voice)
              │
              └──► (REST с PAT пользователя) ──► Jira / Confluence / GitLab

metrics-sidecar ── tail agent.log + poll state.db ── /metrics → VictoriaMetrics (prod)

Все контейнеры в сети `monitoring` (external, Docker SD для корп.скрейпера).
```

## Prereq

- Docker Desktop ≥ 4.30 (macOS dev) или Engine + compose plugin v2 (Linux prod).
- `jq` для `scripts/smoke_test.sh`.
- Доступ в корп.сеть COMPANY (`aigateway.company.ru`, `ml-platform-big.company.loc`,
  `jira.company.ru`, `confluence.company.ru`, `gitlab.company.ru`).
- Сеть `monitoring` (Docker external):
  ```bash
  docker network create monitoring
  ```
  На prod её уже создал devops; на dev-Mac'е один раз вручную.

## Шаги деплоя

1. **.env**:
   ```bash
   cp .env.example .env
   ```
   Заполни `TELEGRAM_BOT_TOKEN` (`@BotFather`), `OPENAI_API_KEY`.
   `TELEGRAM_ALLOWED_USERS` позже сгенерится скриптом.

2. **Team roster**:
   ```bash
   cp config/team.yaml.example config/team.yaml
   # заполни telegram_id / display_name / atlassian_email /
   # atlassian_account_id / gitlab_username для всех пилотных сотрудников
   bash scripts/sync-team-allowlist.sh     # перепишет TELEGRAM_ALLOWED_USERS из roster'а
   ```

3. **Поднять стек**:
   ```bash
   docker compose up -d
   bash scripts/smoke_test.sh              # ожидаем ALL PASS
   ```

4. **Онбординг каждого пилотного сотрудника** (~3 мин, один раз):
   - сотрудник пишет боту в DM `/start` (чтобы Telegram разрешил боту
     слать ему DM);
   - бот отвечает гайдом по `/setup`;
   - сотрудник создаёт PAT-ы в Jira/Confluence/GitLab (ссылки в гайде),
     присылает их командами `/setup jira <pat>`, `/setup confluence <pat>`,
     `/setup gitlab <pat>`;
   - бот сохраняет в `/opt/data/user_tokens/<telegram_id>.json` (том
     `corp_tokens`) и удаляет сообщение с токеном из чата.

5. **Реальные запросы**:
   - «покажи мои баги в Jira» → skill `jira` через его PAT.
   - «создай MR из feature/xyz в main в проекте таск-флоу, назначь Васю на ревью» →
     skill `gitlab` (+ `team_directory` для lookup Васи).
   - «пингани Васю что деплой в 18» → skill `team_message` → DM коллеге.

## Онбординг нового сотрудника (14-го, 15-го, ...)

```bash
# 1. Добавил запись в config/team.yaml
# 2. Перегенерировать allowlist в .env
bash scripts/sync-team-allowlist.sh
# 3. Перезапустить hermes (подхватит новый allowlist и team directory)
docker compose restart hermes
# 4. Новый сотрудник пишет боту /start и проходит /setup.
```

## Метрики

`metrics-sidecar` слушает `:8000/metrics` в формате Prometheus:

- `hermes_messages_total{platform, user_hash}` — входящие (user_hash = sha256[:8]).
- `hermes_responses_total`, `hermes_api_calls_total` — ответы и LLM-вызовы.
- `hermes_response_latency_seconds_bucket` — latency histogram.
- `hermes_response_chars_bucket` — размер ответа.
- `hermes_tool_invocations_total{tool}` — счётчик tool-вызовов из `state.db`.
- `hermes_errors_total{kind}` — ошибки из логов.

На prod-серверах агент VictoriaMetrics сам скрейпит контейнер через Docker SD
(контейнер в сети `monitoring` с labels `prometheus_scrape=true`/`_port=8000`/
`_path=/metrics`). На macOS dev эти labels просто лежат без скрейпера —
проверить метрики можно ручным `curl localhost:8000/metrics`.

## Known caveats

- **Voice вне корп.сети.** Без VPN `ml-platform-big.company.loc` не резолвится —
  shim отдаст 503, бот сообщит "Transcribe unreachable". Текстовые задачи
  работают.
- **Перенос на prod.** Compose тот же. Сеть `monitoring` уже есть у devops'а.
  После первого старта на prod — тот же `/setup` для каждого сотрудника
  отдельно (тома `corp_tokens` и `hermes_state` живут в named volumes, между
  машинами не шарятся).
- **Gitlab.com vs on-prem.** Скиллы заточены под on-prem `gitlab.company.ru`.
  При миграции на другой URL — правка base-URL в `hermes/skills/gitlab/SKILL.md`.
- **PAT expiry.** Pилотные PAT-ы обычно на 6-12 месяцев. Бот вернёт
  `401 unauthorized` когда истекут, и попросит `/setup` заново.
- **LLM — только gateway.** Никаких fallback'ов на api.openai.com / api.anthropic.com.
  Если gateway лежит — бот честно скажет "LLM недоступен".
- **Rate-limit не стоит**. Для 13 человек не нужен. Если увидим абуз в Grafana —
  добавим middleware в отдельной итерации.
- **OAuth 2.1 — phase B2**. Пока per-user через PAT; это компромисс ради
  скорости запуска. Если Atlassian выдаст нормальный on-prem MCP с OAuth —
  мигрируем.

## Структура

```
config/
  team.yaml.example            # шаблон roster'а (коммитится)
  team.yaml                    # реальный roster (НЕ коммитится)
hermes/
  config.yaml                  # модель, STT, telegram, platform_toolsets
  SOUL.md                      # персона + маршрутизация + security rules
  skills/
    aigateway/                 # справочник про COMPANY AI Gateway
    team_directory/            # find_user_by_name, list_team, ...
    team_message/              # cross-user DM и broadcast
    setup/                     # /setup <service> <pat>
    jira/                      # Jira REST через PAT
    confluence/                # Confluence REST через PAT
    gitlab/                    # GitLab REST через PAT
whisper_shim/                  # OpenAI-compat /v1/audio/transcriptions shim
metrics_sidecar/               # tail logs + SQLite → /metrics (Prometheus)
scripts/
  smoke_test.sh                # проверка стека
  sync-team-allowlist.sh       # team.yaml → TELEGRAM_ALLOWED_USERS в .env
  git-sanitize.sh              # clean-фильтр company→company при git add
  setup-git-sanitize.sh        # бутстрап фильтра на свежем клоне
docker-compose.yml             # hermes + whisper-shim + metrics-sidecar
```
