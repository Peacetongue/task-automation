---
name: jira
description: Query and modify issues in the corporate on-prem Jira (jira.company.ru) on behalf of the current Telegram user, using their own Personal Access Token stored by the `setup` skill. Covers search via JQL, get/create/comment/transition issues, add assignees by name (via team_directory lookup). Every call is authenticated as the requesting user — the bot never impersonates.
version: 0.1.0
metadata:
  hermes:
    tags: [jira, corporate, atlassian]
---

# jira

Работа с on-prem Jira (`https://jira.company.ru`) от имени текущего
пользователя через его PAT.

## When to Use

- «Покажи мои баги», «что там с JIRA-123», «создай таску в проекте XYZ про Z»,
  «комментируй JIRA-456», «переведи задачу в In Progress», «назначь Васю на JIRA-…».
- ВЫБИРАЕТСЯ этим скиллом, только если пользователь говорит про **Jira/задачи/
  тикеты/баги/issue**. Для general-вопросов / болтовни / сообщений коллегам
  — другие скиллы.

## Prereq: PAT

Пользователь должен был один раз выполнить `/setup jira <pat>`. Если токена
нет — ответь инструкцией: «Нужен Jira PAT. Создай тут:
https://jira.company.ru/secure/ViewProfile.jspa → Personal Access Tokens.
Потом пришли `/setup jira <token>`.»

## Auth

On-prem Jira 8.14+ принимает PAT как Bearer:
`Authorization: Bearer <pat>`. Base URL: `https://jira.company.ru`.

## Base procedure (каждая операция — один python3 block)

```bash
python3 - <<'PY'
import json, os, sys, urllib.request, urllib.error, pathlib

user_id = os.environ["TELEGRAM_USER_ID"]   # session.source.user_id
tokens = json.loads(pathlib.Path(f"/opt/data/user_tokens/{user_id}.json").read_text())
pat = tokens.get("jira", {}).get("token")
if not pat:
    print("NO_TOKEN"); sys.exit(2)

def api(method, path, body=None):
    url = f"https://jira.company.ru/rest/api/2{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {pat}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}") if e.headers.get("Content-Type","").startswith("application/json") else {"error": e.read().decode(errors="ignore")}

# === операция (подставь) ===
# ...
PY
```

## Key operations

### Search via JQL
```python
status, body = api("GET", "/search?jql=" + urllib.parse.quote("assignee=currentUser() AND resolution=Unresolved") + "&fields=summary,status,priority&maxResults=20")
for iss in body.get("issues", []):
    print(iss["key"], "-", iss["fields"]["summary"], f"[{iss['fields']['status']['name']}]")
```
Полезные JQL-шаблоны:
- «мои открытые»: `assignee=currentUser() AND resolution=Unresolved`
- «Васины открытые»: `assignee="{atlassian_account_id from team_directory}" AND resolution=Unresolved`
- по проекту: `project="XYZ" AND resolution=Unresolved ORDER BY priority DESC`

### Get one issue
```python
status, body = api("GET", "/issue/JIRA-123?fields=summary,description,status,priority,assignee,labels,comment")
```

### Create
```python
body = {
    "fields": {
        "project":   {"key": "XYZ"},
        "summary":   "Краткое описание",
        "description": "Подробности",
        "issuetype": {"name": "Task"},      # Task / Bug / Story / Epic — зависит от проекта
        "priority":  {"name": "Medium"},    # опционально
        # assignee по account_id (from team_directory):
        # "assignee": {"accountId": "557058:xxx"},
    }
}
status, resp = api("POST", "/issue", body)
if status == 201:
    print("✓", resp["key"])
```

### Comment
```python
status, _ = api("POST", "/issue/JIRA-123/comment", {"body": "Сделано, проверь"})
```

### Transition (переход по статусам)
1. Сначала узнай доступные transitions для issue:
   `GET /issue/{key}/transitions` → список `{"id":"31","name":"Start Progress"}`.
2. Затем: `POST /issue/{key}/transitions` с `{"transition":{"id":"31"}}`.

### Assign by name
1. `skill_view("team_directory")` → `find_user_by_name("Вася")` →
   получил `atlassian_account_id`.
2. `PUT /issue/{key}/assignee` body `{"accountId": "557058:xxx"}`.

## Error handling

- `401` → токен невалиден/истёк. Ответь: «Jira токен не работает, перезаведи:
  /setup jira <new>».
- `403` → нет прав на операцию. Покажи сообщение от API как есть.
- `404` → issue key или project не найден.
- `400` → разбери `body.errors` / `body.errorMessages` — обычно поле не то.
- Сеть → «Jira недоступна, попробуй через минуту».

## Pitfalls

- **REST API v2, не v3.** On-prem Jira DC использует `/rest/api/2/*`,
  Cloud — `/rest/api/3/*`. У нас v2.
- **accountId vs username.** В Jira DC исторически был `name` (username),
  в новых версиях — `accountId`. Наш roster хранит `atlassian_account_id`
  — передавай его как `accountId`. Если 400 с жалобой на поле assignee —
  пробуй `{\"name\": \"vasya\"}` вместо `{\"accountId\": ...}`.
- **Priority/IssueType зависят от проекта** — если 400 «invalid priority»,
  запроси `/rest/api/2/project/{key}` и прочитай доступные значения.
- **Не создавай дубликаты** при ретраях. Если после 5xx не уверен, сделай
  `JQL search` по `summary` прежде чем пересоздавать.
- **Agile API может быть недоступен.** `/agile/1.0/board` и
  `/agile/1.0/board/{id}/sprint` возвращают 404 если: (1) Jira Software
  не установлена, (2) PAT не имеет прав на Agile, (3) спринты не настроены.
  В этом случае fallback — базовый JQL без спринтов:
  `assignee=currentUser() AND resolution=Unresolved`.

## Verification

- `skill_view("jira")` → запрос «мои открытые баги» → получаешь список или
  «ничего не назначено».
- Создание тестовой issue в sandbox-проекте возвращает новый key.
- Двое разных пользователей видят только свои назначенные (каждый ходит
  под своим PAT).
