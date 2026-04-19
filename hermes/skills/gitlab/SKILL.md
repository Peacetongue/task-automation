---
name: gitlab
description: Work with issues and merge requests on the corporate on-prem GitLab (gitlab.company.ru) on behalf of the current Telegram user, using their own Personal Access Token stored by the `setup` skill. Covers listing/searching issues and MRs, creating MRs, commenting, setting assignees/reviewers by name via team_directory lookup.
version: 0.1.0
metadata:
  hermes:
    tags: [gitlab, corporate, git]
---

# gitlab

On-prem GitLab через REST API v4 с user's PAT.

## When to Use

- «Покажи мои MR», «создай MR из ветки feature/X в main», «что там у Васи
  в review», «комментируй MR 42 в проекте Y», «назначь Петю на ревью».
- НЕ для Jira issue (там скилл `jira`), НЕ для отправки сообщений в
  Telegram (там `team_message`).

## Prereq

PAT через `/setup gitlab <pat>`. Создание токена: `https://gitlab.company.ru/-/user_settings/personal_access_tokens`
→ Add new token → scopes: `api` (обязательно) + `read_repository` (опционально
для просмотра кода). Срок — 6-12 месяцев.

## Auth

`Authorization: Bearer <pat>` (или `PRIVATE-TOKEN: <pat>` header, оба
работают на GitLab DC). Используем Bearer для единообразия.

Base URL: `https://gitlab.company.ru/api/v4`.

## Base call

```bash
python3 - <<'PY'
import json, os, sys, urllib.request, urllib.parse, urllib.error, pathlib

user_id = os.environ["TELEGRAM_USER_ID"]
tokens = json.loads(pathlib.Path(f"/opt/data/user_tokens/{user_id}.json").read_text())
pat = tokens.get("gitlab", {}).get("token")
if not pat:
    print("NO_TOKEN"); sys.exit(2)

BASE = "https://gitlab.company.ru/api/v4"

def api(method, path, body=None, params=None):
    url = BASE + path + (("?" + urllib.parse.urlencode(params, doseq=True)) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {pat}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"[]")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}") if e.headers.get("Content-Type","").startswith("application/json") else {"error": e.read().decode(errors="ignore")}

# === операция ===
PY
```

## Key operations

### Project ID (нужен почти всем операциям)
GitLab API использует числовой `project_id` ИЛИ url-encoded
`namespace/project`. Пример: `group/subgroup/repo` → `group%2Fsubgroup%2Frepo`.

```python
status, body = api("GET", "/projects", params={"search": "taskflow", "owned": "false"})
# выбери нужный, запомни body[0]["id"]
```

### My open MRs
```python
status, body = api("GET", "/merge_requests", params={"scope": "assigned_to_me", "state": "opened"})
```

### List MRs in project
```python
status, body = api("GET", f"/projects/{pid}/merge_requests", params={"state": "opened"})
```

### Create MR
```python
# reviewers/assignee_id — из team_directory.get_gitlab_username → потом lookup в /users
status, me = api("GET", "/user")    # мой GitLab user_id
reviewer_username = "vasya"         # из team_directory
status, rev = api("GET", "/users", params={"username": reviewer_username})
reviewer_id = rev[0]["id"] if rev else None

body = {
    "source_branch": "feature/xyz",
    "target_branch": "main",
    "title": "XYZ: короткое описание",
    "description": "### Что  \n- ...\n\n### Зачем  \n- ...",
    "assignee_id": me["id"],
    "reviewer_ids": [reviewer_id] if reviewer_id else [],
    "remove_source_branch": True,
    "squash": True,
}
status, resp = api("POST", f"/projects/{pid}/merge_requests", body)
print(resp.get("web_url"))
```

### Comment on MR
```python
status, _ = api("POST", f"/projects/{pid}/merge_requests/{mr_iid}/notes", {"body": "LGTM, мердж когда прогонит ci"})
```

### Approve MR
```python
status, _ = api("POST", f"/projects/{pid}/merge_requests/{mr_iid}/approve")
```

### Project issues
```python
status, body = api("GET", f"/projects/{pid}/issues", params={"state": "opened", "assignee_username": "vasya"})
```

## Error handling

- `401` → PAT протух или невалиден. Попроси перевыдать.
- `403` → нет прав на проект. Проверь scope'ы PAT (`api` нужен для write).
- `404` → неправильный `project_id`, `mr_iid`, username.
- `400` с `{"message":{"base":["Source branch does not exist"]}}` — очевидное.

## Pitfalls

- **`iid` vs `id`**: у MR/issue есть глобальный `id` (в рамках всего GitLab)
  и project-локальный `iid`. В URL-путях мы используем `iid`. Пользователь
  обычно говорит «MR !42» — это `iid=42`.
- **Namespace-path url-encoded**: `group/sub/repo` → `group%2Fsub%2Frepo` в URL.
  Используй `urllib.parse.quote(path, safe='')`.
- **reviewer_ids vs reviewer_id**: создание MR — `reviewer_ids: [list]`,
  update — `reviewer_ids` тоже массив. `reviewer_id` (single) — deprecated.
- **squash + remove_source_branch** — нормальные дефолты для небольших PR,
  но уточни у пользователя если что-то нестандартное.

## Verification

- «Покажи мои MR» возвращает список ≥0 MR.
- Создание тестового MR в sandbox-проекте возвращает web_url.
- Ревьюер из другой команды (не в нашем roster) — всё равно работает
  (GitLab username resolve через /users?username=...).
