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
