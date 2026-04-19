---
name: team_approval
description: Handle an incoming message from a user who is not yet in the team roster — create a pending approval record, notify all teamleads and admins in DM asking for approval, and reply to the newcomer that their request is pending. The approval itself (or rejection) is done via team_admin skill when teamlead/admin writes "approve <tg_id>" / "одобрь" / "✓" / "reject <tg_id>". No LLM tool-calls, no Jira/GitLab contact — this is a gated entry point.
version: 0.1.0
metadata:
  hermes:
    tags: [team, onboarding, approval, corporate]
---

# team_approval

Обработка первого контакта от **unknown user'а** (не в `team.yaml`).
Срабатывает по SOUL.md-гейту «нулевого правила» — первым скиллом для
любого сообщения от tg_id, которого нет в roster'е.

## When to Use

- `team_directory.find_user_by_telegram_id(session.source.user_id)` вернул
  `NOT FOUND` для отправителя входящего сообщения.
- Сообщение может быть `/start`, «привет», любой текст — неважно. Наш
  интерес в том, чтобы **не** обращаться к LLM/Jira/GitLab для unknown
  user'а и инициировать approval-флоу.

## When NOT to Use

- Если отправитель уже в roster'е — это НЕ твой случай, идёт обычная
  маршрутизация SOUL.md.
- Отказ от /setup / /voice / других slash-команд от unknown user'а —
  молча проигнорируй или верни тот же «ждём одобрения».

## Procedure

```bash
python3 - <<'PY'
import json, os, pathlib, time

PEND = pathlib.Path("/opt/data/pending_approvals")
PEND.mkdir(parents=True, exist_ok=True, mode=0o700)

# Context provided by the agent (pass through `env VAR=... terminal_tool`):
tg        = os.environ["SENDER_TG"]                     # session.source.user_id
name      = os.environ.get("SENDER_NAME") or "Unknown"
uname     = os.environ.get("SENDER_USERNAME") or ""     # @username из Telegram профиля, если есть
first_msg = os.environ.get("FIRST_MSG") or ""

pfile = PEND / f"{tg}.json"
if pfile.exists():
    print("ALREADY_PENDING"); raise SystemExit(0)

pfile.write_text(json.dumps({
    "telegram_id":   int(tg),
    "display_name":  name,
    "username":      uname,
    "first_msg":     first_msg[:200],
    "requested_at":  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}, ensure_ascii=False))
os.chmod(pfile, 0o600)
print(f"PENDING_CREATED tg={tg} name={name}")
PY
```

После создания pending-файла:

1. Через `skill_view("team_directory")` → найди всех с
   `role in ("teamlead", "admin")`. Получи список telegram_id.
2. На каждого из них вызови `send_message_tool`:
   ```
   send_message_tool(platform="telegram", chat_id=<admin_tg>, text=f"""
   🔔 Запрос на вход в бот:
   
   <Name> (@<username>, tg={tg})
   Первое сообщение: "{first_msg[:120]}"
   
   Одобрить: `одобрь {tg}` или `approve {tg}` или просто `✓ {tg}` в этом чате.
   Отклонить: `reject {tg}`.
   """)
   ```
3. Ответь самому unknown user'у:
   ```
   "Привет! Тебя пока нет в пилотной команде. Запрос на добавление отправлен
    тимлиду и админам — ждём одобрения. Как только тебя одобрят, я пришлю
    инструкцию по настройке."
   ```

## Pitfalls

- **НЕ зови LLM, Jira, GitLab, никакие другие скиллы** для unknown user'а.
  Это hard gate. Только `team_approval` флоу и всё.
- **Idempotency**: если pending-файл уже есть — не создавай повторно и
  не спамь админов второй раз. Ответь user'у «запрос уже отправлен, жди».
- **НЕ сохраняй полный text** из первого сообщения в pending-файл: обрежь
  до 200 символов чтобы не тащить PII / случайные токены.
- **DM к админу** работает, только если админ сам когда-то писал боту
  (Telegram требует, чтобы user инициировал диалог с bot'ом). Если админ
  ещё не писал — `send_message_tool` получит `Forbidden`. В этом случае
  ответь unknown user'у: «админы ещё не на связи, напиши @<admin_handle>
  напрямую, пусть он сделает /start у бота».

## Verification

- Unknown user пишет боту → в `/opt/data/pending_approvals/<tg>.json`
  появляется файл.
- В DM всех teamlead'ов / admin'ов приходит уведомление со ссылкой на approve.
- User получает "ждём одобрения".
- При повторном сообщении от того же unknown user'а — админы НЕ получают
  спам, user получает «запрос уже отправлен».
- После approve через `team_admin` (teamlead/admin пишет «одобрь N») —
  pending-файл удалён, user получает DM о добавлении.
