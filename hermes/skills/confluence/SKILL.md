---
name: confluence
description: Read and write pages in the corporate on-prem Confluence (confluence.company.ru) on behalf of the current Telegram user, using their own Personal Access Token stored by the `setup` skill. Covers CQL search, fetching a page by id or title, creating drafts, commenting. Never impersonates — each call is under the requesting user's identity.
version: 0.1.0
metadata:
  hermes:
    tags: [confluence, corporate, atlassian]
---

# confluence

On-prem Confluence DC через user's PAT.

## When to Use

- «Найди страницу про X», «покажи draft release notes», «создай заметку в
  пространстве DEV про Y», «добавь комментарий на страницу 123456».
- НЕ для Jira-задач (там `jira` скилл), НЕ для кода/MR (там `gitlab`).

## Prereq

PAT Confluence через `/setup confluence <pat>`. Если нет — выведи
инструкцию: создай в
`https://confluence.company.ru/users/viewmyprofile.action` → Personal Access
Tokens → Create → `/setup confluence <token>`.

## Auth

`Authorization: Bearer <pat>`. Base URL: `https://confluence.company.ru`.

## Base call

```bash
python3 - <<'PY'
import json, os, sys, urllib.request, urllib.parse, urllib.error, pathlib

user_id = os.environ["TELEGRAM_USER_ID"]
tokens = json.loads(pathlib.Path(f"/opt/data/user_tokens/{user_id}.json").read_text())
pat = tokens.get("confluence", {}).get("token")
if not pat:
    print("NO_TOKEN"); sys.exit(2)

def api(method, path, body=None, params=None):
    url = f"https://confluence.company.ru/rest/api{path}"
    if params: url += "?" + urllib.parse.urlencode(params)
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

# === операция ниже ===
PY
```

## Key operations

### Search via CQL
```python
status, body = api("GET", "/content/search", params={
    "cql": 'space="DEV" AND text ~ "release notes"',
    "limit": 10,
    "expand": "space,version",
})
for r in body.get("results", []):
    print(r["id"], r["title"], f"({r['space']['key']})")
```

### Get page by id (с телом)
```python
status, body = api("GET", f"/content/{page_id}", params={"expand": "body.storage,version"})
title = body["title"]
html  = body["body"]["storage"]["value"]   # HTML-представление
```

### Create page
```python
body = {
    "type": "page",
    "title": "Заголовок",
    "space": {"key": "DEV"},
    "body": {"storage": {"value": "<p>Содержимое в HTML</p>", "representation": "storage"}},
    # для draft в ребёнке другой страницы:
    # "ancestors": [{"id": "1234567"}],
}
status, resp = api("POST", "/content", body)
```

### Comment on page
```python
body = {
    "type": "comment",
    "container": {"id": page_id, "type": "page"},
    "body": {"storage": {"value": "<p>Проверил, ок</p>", "representation": "storage"}},
}
status, resp = api("POST", "/content", body)
```

### Update page (нужно inc версии)
1. GET page → версия N.
2. PUT `/content/{id}` с `{"version":{"number":N+1}, ...}` (title+type+body обязательны).

## Pitfalls

- **CQL — не SQL**, синтаксис `space="DEV" AND text~"..."`.
- **body.storage.representation всегда `"storage"`** (HTML-like storage format).
  Plain text и wiki-markup — отдельные representation-типы, не путай.
- **Spaces vs keys**: "Space DEV" — это display name, key обычно всё-таки `DEV`
  (верхний регистр, часто аббревиатура). Если ищешь и не находишь — попроси
  у пользователя ключ space, не угадывай.
- **REST API разных версий**: у Confluence DC есть `/rest/api/*` (то, что мы
  используем) и новый `/rest/api/v2/*` (не во всех версиях). Если 404 на
  базовые пути — возможно используем неправильный path.

## Verification

- Search «release notes» в DEV возвращает ≥0 страниц без ошибок.
- Создание тестовой страницы в sandbox space возвращает новый id.
- Разные юзеры видят только то, к чему у них есть права на чтение.
