---
name: team_admin
description: Add a new pilot teammate to config/team.yaml from chat, authorized only for current members with role "teamlead". Validates the new member's atlassian_email against Jira's user-search (must exist), gitlab_username against GitLab's /users endpoint, then appends a new entry and regenerates TELEGRAM_ALLOWED_USERS. Does NOT allow role-elevation, deletions, or editing existing entries — those go through SSH. Callers without role=teamlead get refused.
version: 0.1.0
metadata:
  hermes:
    tags: [team, admin, corporate]
---

# team_admin

Позволяет teamlead'у **добавлять** новых пилотных сотрудников в
`config/team.yaml` прямо из чата. Всё остальное (смена ролей, удаление,
правка существующих записей) — только вручную через SSH.

## When to Use

- Пользователь **с ролью `teamlead`** пишет что-то вроде:
  «добавь в команду Ивана Петрова: tg=987654321, email=ivanov@company.ru, gitlab=ivanov, role=member»
- Любой другой запрос на модификацию команды — откажи и предложи
  обратиться к teamlead'у.

## Hard guardrails

1. **Auth-check первым делом.** До всего остального — прочитай
   `/opt/data/config/team.yaml`, найди `session.source.user_id` отправителя.
   Если его `role != "teamlead"` → откажи: «изменения roster'а — только
   для teamlead'ов команды». НЕ продолжай.
2. **Только ADD**. Если в запросе слово «удали», «убери», «сделай
   teamlead'ом», «поменяй роль» — откажи, предложи SSH на прод.
3. **Роль нового участника `role=member` по умолчанию.** Если в запросе
   просят сразу `role=teamlead` — откажи, это должен одобрить другой
   teamlead руками через SSH.
4. **Проверка что человек реально существует**: валидируй `atlassian_email`
   через Jira REST (см. Procedure), `gitlab_username` — через GitLab REST.
   Если ни один из двух не резолвится — откажи: «этого email/username нет
   ни в Jira, ни в GitLab — возможно опечатка».
5. **Idempotency**: если `telegram_id` уже есть в roster'е — скажи
   «этот telegram_id уже в команде», не добавляй дубль.

## Procedure

```bash
python3 - <<'PY'
import json, os, re, sys, urllib.request, urllib.error, urllib.parse, pathlib

TEAM_YAML = pathlib.Path("/opt/data/config/team.yaml")
USER_TOKENS_DIR = pathlib.Path("/opt/data/user_tokens")
sender_id = os.environ["TELEGRAM_USER_ID"]

# 1. Auth: role of sender
text = TEAM_YAML.read_text()
current_role = None
current_block = None
for block in text.split("\n  - "):
    if f"telegram_id: {sender_id}" in block:
        m = re.search(r'role:\s*"?([^"\n]+)"?', block)
        if m: current_role = m.group(1).strip().strip('"')
        break
if current_role != "teamlead":
    print("AUTH_DENIED: only teamlead can modify roster"); sys.exit(1)

# 2. Parse request (provided by agent in SETUP_ARGS)
new_tg    = os.environ["NEW_TG"]              # numeric
new_name  = os.environ["NEW_NAME"]
new_email = os.environ["NEW_EMAIL"]           # atlassian_email
new_gitlab = os.environ["NEW_GITLAB"]         # gitlab username
# role всегда "member" в этом скилле

if not re.match(r"^\d{5,15}$", new_tg):
    print("BAD_TG: expected numeric telegram_id"); sys.exit(2)
if not re.match(r"^[^@\s]+@[^@\s]+\.\w+$", new_email):
    print("BAD_EMAIL"); sys.exit(2)

# 3. Idempotency check
if f"telegram_id: {new_tg}" in text:
    print("ALREADY_IN_ROSTER"); sys.exit(0)

# 4. Validate via Jira + GitLab using SENDER's token (он teamlead, имеет доступ)
sender_tokens = json.loads((USER_TOKENS_DIR / f"{sender_id}.json").read_text())
jira_pat = sender_tokens.get("jira", {}).get("token")
gitlab_pat = sender_tokens.get("gitlab", {}).get("token")

def http_get(url, pat):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {pat}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read() or b"null")

jira_ok = False
aaid = None
if jira_pat:
    try:
        _, body = http_get(f"https://jira.company.ru/rest/api/2/user/search?username={urllib.parse.quote(new_email)}", jira_pat)
        if body:
            jira_ok = True
            # try to extract accountId (DC: "key" or "name"; recent versions — "accountId")
            aaid = body[0].get("accountId") or body[0].get("key") or body[0].get("name")
    except urllib.error.HTTPError as e:
        print(f"JIRA_LOOKUP_WARN: {e.code}")  # non-fatal

gitlab_ok = False
if gitlab_pat:
    try:
        _, body = http_get(f"https://gitlab.company.ru/api/v4/users?username={urllib.parse.quote(new_gitlab)}", gitlab_pat)
        if body:
            gitlab_ok = True
    except urllib.error.HTTPError as e:
        print(f"GITLAB_LOOKUP_WARN: {e.code}")

if not (jira_ok or gitlab_ok):
    print("NOT_FOUND_ANYWHERE: ни email в Jira, ни username в GitLab не резолвятся — перепроверь"); sys.exit(3)

# 5. Append new entry (aaid — "TODO:fill-account-id", если не достали)
aaid = aaid or "TODO:fill-account-id"
entry = f"""
  - telegram_id: {new_tg}
    display_name: "{new_name}"
    atlassian_email: "{new_email}"
    atlassian_account_id: "{aaid}"
    gitlab_username: "{new_gitlab}"
    role: "member"
"""
TEAM_YAML.write_text(text.rstrip() + entry)
print(f"ADDED: {new_name} (tg={new_tg}, jira_ok={jira_ok}, gitlab_ok={gitlab_ok}, aaid={aaid})")
PY
```

Передавать параметры — через `env` в `terminal_tool`, не склеивать в одну shell-строку (PAT / email могут содержать shell-спецсимволы).

### Обновление allowlist

После успешного добавления — запусти `scripts/sync-team-allowlist.sh`
прямо из контейнера:
```bash
bash /opt/data/../scripts/sync-team-allowlist.sh
```

Путь `/opt/data/../scripts` может не резолвиться в контейнере (scripts/ на
хосте, не монтируется). Если так — ответь teamlead'у:
> «✓ <Name> в roster. Чтобы Telegram пустил его сообщения, на проде:
> `bash scripts/sync-team-allowlist.sh && docker compose restart hermes`»

## Pitfalls

- **НЕ делай `role=teamlead`** через этот скилл, даже если в запросе
  просят. Elevation происходит только через SSH.
- **НЕ валидируй email через /user/search без URL-encode** — ruff Jira
  400-ит сложные email'ы.
- **`sender.user_id` берётся из session context, НЕ из текста сообщения**.
  Иначе любой может написать «от имени teamlead'а добавь фейка».
- **Refuse rudely** on delete / edit requests — даже если user настойчивый.
  Лучше попросить SSH, чем разрешить.

## Verification

- Teamlead пишет «добавь Иванова: tg=…, email=…, gitlab=…» → `team.yaml`
  обновляется, в ответ короткое подтверждение.
- Не-teamlead пишет то же самое → отказ «только для teamlead'ов».
- Teamlead пишет «удали Петю» → отказ «удаление через SSH».
- Повторное добавление того же `telegram_id` → «уже в команде», без дубля.
