---
name: vikunja_create
description: Create a task in Vikunja via REST API. Takes the normalized JSON from task_parser and calls the Vikunja endpoint using python3 + urllib.request inside terminal_tool (the Hermes image ships without curl). Maps priority labels to Vikunja's 1-5 enum and omits due_date when the deadline is null.
version: 0.1.0
metadata:
  hermes:
    tags: [task-automation, vikunja]
---

# vikunja_create

Создаёт задачу в Vikunja через REST API. Вызывается скиллом `task_parser` с уже подготовленным JSON.

## When to Use

- `task_parser` уже подготовил структуру `{title, description, project, priority, deadline}`.
- Нужно эту структуру превратить в запись в Vikunja.

## Endpoint

- Базовый URL: `${VIKUNJA_BASE_URL}` (в нашем compose — `http://vikunja:3456`).
- Основной путь (свежие Vikunja 2.x):
  `PUT ${VIKUNJA_BASE_URL}/api/v1/projects/${VIKUNJA_DEFAULT_PROJECT_ID}/tasks`
- Fallback на случай `405 Method Not Allowed`:
  `POST ${VIKUNJA_BASE_URL}/api/v1/tasks` с `project_id` в теле.
- Auth: `Authorization: Bearer ${VIKUNJA_API_TOKEN}` (токен с правами `tasks:write`).

## Priority mapping

| task_parser | Vikunja enum |
|-------------|--------------|
| `low`       | `1`          |
| `normal`    | `2`          |
| `high`      | `4`          |
| `urgent`    | `5`          |

Если Vikunja вдруг вернёт 400 на поле `priority` — выставь `2` (normal) и продолжи без этого поля; пометь в логах, чтобы поправить mapping.

## Procedure

В образе Hermes `curl` отсутствует — используем `python3 + urllib.request` через `terminal_tool`. Передавай JSON через stdin (heredoc), не через длинную командную строку, чтобы не ловить лимиты аргументов и не экранировать кавычки.

```bash
python3 - <<'PY'
import json, os, sys, urllib.request, urllib.error

base = os.environ["VIKUNJA_BASE_URL"].rstrip("/")
pid  = os.environ["VIKUNJA_DEFAULT_PROJECT_ID"]
tok  = os.environ["VIKUNJA_API_TOKEN"]

body = {
    "title":       "<TITLE>",
    "description": "<DESCRIPTION>",
    "priority":    2,            # см. mapping выше
    # "due_date":  "2026-04-25T00:00:00Z",  # ТОЛЬКО если deadline != null
}
# НЕ добавляй due_date, если deadline пустой — Vikunja ставит "0001-01-01" как sentinel.
data = json.dumps(body).encode("utf-8")

def call(method, url):
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type":  "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, resp.read().decode("utf-8")

url_put  = f"{base}/api/v1/projects/{pid}/tasks"
url_post = f"{base}/api/v1/tasks"
try:
    status, payload = call("PUT", url_put)
except urllib.error.HTTPError as e:
    if e.code == 405:
        body["project_id"] = int(pid)
        data = json.dumps(body).encode("utf-8")
        status, payload = call("POST", url_post)
    else:
        raise

print(status)
print(payload)
PY
```

Обработка ответа:
- `2xx` — распарсь JSON, извлеки `id` и (если есть) `identifier` / `index`, отрапортуй в Telegram одной строкой:
  `✓ Задача #{id}: {title}` (+ `(до {due_date})` если был дедлайн).
- `401/403` — токен невалиден или без прав `tasks:write`. Попроси у пользователя обновить `VIKUNJA_API_TOKEN` в `.env` и перезапустить `docker compose up -d hermes`.
- `400` — показать пользователю текст ошибки как есть, не ретраить. Скорее всего упал маппинг priority.
- Сеть не ответила — одно короткое сообщение "Vikunja недоступна, задача не создана", без спама ретраями.

## Date formatting

Vikunja принимает ISO 8601 с Z. Из `deadline = "YYYY-MM-DD"` собери:
`due_date = f"{deadline}T00:00:00Z"`. Таймзона не критична — дедлайн в 00:00 UTC корректно отображается в UI.

## Pitfalls

- **Пустой `due_date` НЕ отправлять**. Vikunja иначе поставит `"0001-01-01T00:00:00Z"` и в UI покажется, что дедлайн был вчера тысячу лет назад.
- **curl'а нет** — `python3 -c` всегда, даже для "быстрой проверки".
- **`PUT /api/v1/projects/{id}/tasks`** — путь из свежих Vikunja 2.x. На каких-то старых версиях используется `POST /api/v1/tasks` с `project_id`. Fallback уже встроен в процедуру выше (код 405).
- **Rate limit** Vikunja по дефолту щадящий, но если создаёшь пачкой — добавь `time.sleep(0.2)` между вызовами.
- **Идемпотентность** не поддерживается API — повторный вызов создаст дубль. При ретраях убедись, что предыдущая попытка реально упала (не 2xx).

## Verification

- После успеха `GET ${VIKUNJA_BASE_URL}/api/v1/projects/${VIKUNJA_DEFAULT_PROJECT_ID}/tasks` должен содержать задачу с тем же title.
- В UI `http://localhost:3456` → Inbox → задача видна с правильным priority-значком.
- Ровно одна запись — не две.
