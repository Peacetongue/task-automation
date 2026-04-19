---
name: setup
description: Handle the one-time onboarding command `/setup <service> <token>` from a pilot teammate. Stores the user's Personal Access Token for Jira / Confluence / GitLab in /opt/data/user_tokens/<telegram_id>.json so the jira/confluence/gitlab skills can use it. Also handles `/setup status` (show which services the user has enrolled) and `/setup clear <service>` (revoke a token locally).
version: 0.1.0
metadata:
  hermes:
    tags: [onboarding, corporate, tokens]
---

# setup

Сохраняет per-user PAT для корп.сервисов. Триггерится телеграм-командой
`/setup <service> <args...>`.

## When to Use

- Пользователь ввёл в Telegram `/setup jira <token>`, `/setup confluence <token>`,
  `/setup gitlab <token>`.
- Или `/setup status` — показать какие сервисы настроены.
- Или `/setup clear <service>` — удалить токен (ревок только локальный, у
  сервиса токен остаётся живым, пользователь сам его отзывает в UI Jira).
- НЕ вызывать для других вопросов — для onboarding-гайда есть SOUL.md.

## Storage layout

```
/opt/data/user_tokens/<telegram_user_id>.json
{
  "jira":       {"token": "...", "saved_at": "2026-04-19T13:00:00Z"},
  "confluence": {"token": "...", "saved_at": "..."},
  "gitlab":     {"token": "...", "saved_at": "..."}
}
```

Файл принадлежит uid=10000 (hermes), mode 0600. Директория `user_tokens`
защищена по правам, не попадает в git (см. `.gitignore`).

## Procedure

```bash
python3 - <<'PY'
import json, os, sys, time, re, pathlib

args = os.environ["SETUP_ARGS"].strip().split()    # <-- передать из контекста: то, что шло после /setup
if not args:
    print("usage: /setup <jira|confluence|gitlab> <token>  |  /setup status  |  /setup clear <service>")
    sys.exit(1)

user_id = os.environ["TELEGRAM_USER_ID"]           # см. session.source.user_id
tokens_dir = pathlib.Path("/opt/data/user_tokens")
tokens_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
path = tokens_dir / f"{user_id}.json"
data = json.loads(path.read_text()) if path.exists() else {}

cmd = args[0].lower()

if cmd == "status":
    if not data:
        print("nothing set up yet")
    else:
        for svc, row in data.items():
            print(f"{svc}: ✓ (saved {row.get('saved_at','?')})")
    sys.exit(0)

if cmd == "clear":
    if len(args) < 2:
        print("usage: /setup clear <service>"); sys.exit(1)
    svc = args[1].lower()
    if svc in data:
        data.pop(svc)
        path.write_text(json.dumps(data))
        os.chmod(path, 0o600)
        print(f"✓ {svc} token cleared (revoke it in the service UI too)")
    else:
        print(f"no {svc} token was stored")
    sys.exit(0)

# setup <service> <token>
if len(args) < 2:
    print("usage: /setup <service> <token>"); sys.exit(1)
svc = cmd
token = " ".join(args[1:]).strip()

if svc not in ("jira", "confluence", "gitlab"):
    print(f"unknown service '{svc}' — use jira/confluence/gitlab"); sys.exit(1)

# Простая валидация формата
if not re.match(r"^[A-Za-z0-9._\-]{10,}$", token):
    print("token looks malformed (expected >=10 safe chars)"); sys.exit(1)

data[svc] = {"token": token, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
path.write_text(json.dumps(data))
os.chmod(path, 0o600)
print(f"✓ {svc} token saved for user {user_id}")
PY
```

## Автоматический backfill team.yaml после сохранения токена

Если в `team.yaml` у записи пользователя поле `atlassian_email` / `atlassian_account_id`
или `gitlab_username` стоит как `"TODO:fill-on-first-setup"` (так делает
`team_admin` при approve/add), после успешного `/setup jira <pat>` / `/setup gitlab <pat>`
ты должен **дозаполнить** эти поля автоматически, без участия пользователя.

### После `/setup jira <pat>`
```bash
python3 - <<'PY'
import json, os, pathlib, re, urllib.request
user_id = os.environ["TELEGRAM_USER_ID"]
tokens = json.loads(pathlib.Path(f"/opt/data/user_tokens/{user_id}.json").read_text())
pat = tokens["jira"]["token"]
req = urllib.request.Request("https://jira.company.ru/rest/api/2/myself",
                             headers={"Authorization": f"Bearer {pat}", "Accept":"application/json"})
with urllib.request.urlopen(req, timeout=10) as r:
    me = json.loads(r.read())
email = me.get("emailAddress") or ""
aaid  = me.get("accountId") or me.get("key") or me.get("name") or ""

team = pathlib.Path("/opt/data/config/team.yaml")
text = team.read_text()
# найти блок этого user_id и подменить "TODO:fill-on-first-setup" в email/account_id
blk = re.search(rf"^  - telegram_id:\s*{re.escape(user_id)}\b.*?(?=^  - telegram_id:|\Z)", text, re.M | re.S)
if blk:
    old = blk.group(0)
    new = re.sub(r'atlassian_email:\s*"TODO:fill-on-first-setup"', f'atlassian_email: "{email}"', old)
    new = re.sub(r'atlassian_account_id:\s*"TODO:fill-on-first-setup"', f'atlassian_account_id: "{aaid}"', new)
    if new != old:
        team.write_text(text.replace(old, new))
        print(f"BACKFILLED_JIRA: email={email} aaid={aaid}")
    else:
        print("NO_TODO_JIRA (already filled or no TODO marker)")
else:
    print("BLOCK_NOT_FOUND")
PY
```

### После `/setup gitlab <pat>`
Аналогично, `GET https://gitlab.company.ru/api/v4/user` → `username`,
подмени `gitlab_username: "TODO:fill-on-first-setup"` на реальный.

### Тон рапорта
Если backfill прошёл — в ответ user'у допиши короткое «подтянул твой
<email / username> в ростер, тим-скиллы (упоминания в Jira, broadcast)
теперь работают точно». Если там уже не TODO — не упоминай, это шум.

## Pitfalls

- **НЕ ЛОГИРУЙ токен.** `print(token)` / echo / логи агента — запрещено.
  Агент должен ответить ровно «✓ saved», не повторяя токен.
- **Удали сообщение с токеном из чата**. После обработки `/setup jira <token>`,
  вызови `delete_message_tool` (из пресета `hermes-telegram`) на исходное
  сообщение пользователя — чтобы PAT не висел в истории Telegram. Если
  удаление не вышло (>48 ч) — предупреди пользователя, пусть удалит сам.
- **Перевыдача токена**: если пользователь делает `/setup jira <new>` поверх
  старого — перезаписываем, старый в файле пропадает. ОК.
- **Не отвечай /setup в публичном чате** — обрабатывай только в DM. Если
  пришло в группу — ответь «пришли мне в личку, чтобы не светить токен».
- **Пользователь может прислать токен без `/setup` префикса**. Если видишь
  строку, похожую на PAT (длинная base64-подобная строка, содержит `:` или
  начинается с `_`/`glpat-`/`jpat-`), но без команды — ответь: «Прими токен,
  но в следующий раз используй `/setup <service> <token>`. Токен сохранён.»
  Затем сохрани токен в файл и удали сообщение пользователя.

## Verification

- Новый пользователь пишет `/setup jira PAT_abc...` → бот отвечает `✓`.
- `ls -la /opt/data/user_tokens/` показывает файл `<user_id>.json` mode 0600.
- `/setup status` показывает `jira: ✓`.
- Сообщение с токеном исчезает из чата.
