---
name: team_admin
description: All roster mutations in one skill — approve pending user (teamlead/admin), add member directly by telegram_id/display_name (teamlead/admin), remove a member (teamlead/admin). Minimal input — just telegram_id is enough; other fields (atlassian_email, atlassian_account_id, gitlab_username) get backfilled by the `setup` skill when the user later runs `/setup jira <pat>` / `/setup gitlab <pat>`. Role elevation (member→admin→teamlead) is NOT handled here — only SSH.
version: 0.2.0
metadata:
  hermes:
    tags: [team, admin, corporate, roster]
---

# team_admin

Единая точка для всех операций над `/opt/data/config/team.yaml`:
**approve**, **add-direct**, **remove**. Любая операция требует отправителя
с ролью `teamlead` или `admin`.

## Actions

### 1. `approve <telegram_id>` — одобрить pending-заявку

Запускается когда `team_approval` уже создал файл
`/opt/data/pending_approvals/<tg_id>.json` (unknown user нажал /start →
бот запросил аппрув у админов). Teamlead/admin отвечает «одобрь», «✓»,
«approve 987654321» — бот:

1. Auth-check: отправитель в `team.yaml`, `role` ∈ (`teamlead`, `admin`).
2. Читает pending-файл, вытаскивает `telegram_id` + `display_name` +
   `username` (с момента /start-а).
3. Добавляет в `team.yaml` минимальную запись:
   ```yaml
     - telegram_id: 987654321
       display_name: "Ivan"
       atlassian_email: "TODO:fill-on-first-setup"
       atlassian_account_id: "TODO:fill-on-first-setup"
       gitlab_username: "TODO:fill-on-first-setup"
       role: "member"
   ```
4. Удаляет pending-файл.
5. DM'ит одобренному: «✓ тебя добавили. Сделай `/setup jira <pat>` — я
   автоматически подтяну остальное. Гайд: https://jira.biocad.ru/... ».
6. Коротко подтверждает одобряющему: «✓ Ivan добавлен».

### 2. `add <telegram_id> [display_name]` — прямое добавление

Когда teamlead/admin в чате пишет «добавь Олега 987654321» без
pending-заявки (например, хочет завести коллегу заранее). То же что и
approve, но без pending-шага. Минимум — telegram_id. Если
display_name не указан — спроси ОДНИМ вопросом («как его называть?»).

### 3. `remove <telegram_id | display_name>` — удалить участника

Teamlead/admin пишет «убери Сашу», «выгони tg=987654321»:

1. Auth-check (sender ∈ teamlead|admin).
2. Найди запись в `team.yaml`.
3. Safety: **нельзя удалить самого себя**.
4. Safety: если target — `teamlead`, то удалять может **только другой teamlead**
   (admin отказывает с понятным сообщением).
5. Удали запись, сохрани файл.
6. Удали `/opt/data/user_tokens/<tg>.json` (чтобы токены не висели на диске).
7. DM'и удалённому: «твой доступ к боту отозван. Если это ошибка —
   напиши @<admin_handle>».
8. Подтверди удаляющему: «✓ Саша удалён».

### 4. `set_role <telegram_id | display_name> <new_role>` — смена роли (ТОЛЬКО teamlead)

Teamlead пишет «повысь Сашу до admin», «сделай Петю teamlead'ом»,
«понизь Олега до member»:

1. Auth-check — sender должен быть **teamlead** (admin и member отказывают).
2. Найди запись. Если не найдена → понятная ошибка.
3. Safety: **нельзя понизить себя** (sender cannot demote self). Если
   teamlead хочет «уйти» — пусть сначала промотит кого-то в teamlead,
   потом другой teamlead демотирует его. Это нормально.
4. Проверь что `new_role` ∈ (`member`, `admin`, `teamlead`). Иначе
   отказ.
5. Обнови поле `role:` в записи, сохрани файл.
6. DM'и target'у: «твоя роль в команде изменена: \<old> → \<new>».
7. Подтверди sender'у: «✓ Саша: member → admin».

## Procedure (one python3 block, parametrized by action)

```bash
python3 - <<'PY'
import json, os, pathlib, re, sys

TEAM = pathlib.Path("/opt/data/config/team.yaml")
PEND = pathlib.Path("/opt/data/pending_approvals")
TOK  = pathlib.Path("/opt/data/user_tokens")
sender_id = os.environ["TELEGRAM_USER_ID"]
action    = os.environ["ACTION"]           # "approve" | "add" | "remove"

# --- read yaml as plain text (ручной парсер по простому формату team.yaml) ---
text = TEAM.read_text()
def find_block(tg):
    """Возвращает (start, end) индексов записи в тексте, либо (None,None)."""
    m = re.search(rf"^  - telegram_id:\s*{re.escape(str(tg))}\b", text, re.M)
    if not m: return None, None
    start = m.start()
    nxt = re.search(r"^  - telegram_id:", text[start+1:], re.M)
    end = start + 1 + nxt.start() if nxt else len(text)
    return start, end

def block_role(start, end):
    m = re.search(r'role:\s*"?([^"\n]+)"?', text[start:end])
    return m.group(1).strip().strip('"') if m else "member"

def block_name(start, end):
    m = re.search(r'display_name:\s*"([^"]+)"', text[start:end])
    return m.group(1) if m else "?"

# --- auth-check: sender has role in (teamlead, admin) ---
s, e = find_block(sender_id)
if s is None:
    print("AUTH_DENIED: you're not in the roster"); sys.exit(1)
sender_role = block_role(s, e)
if sender_role not in ("teamlead", "admin"):
    print(f"AUTH_DENIED: need role teamlead/admin, you are {sender_role}"); sys.exit(1)

# ============== action dispatch ==============
if action == "approve":
    target = os.environ["TARGET_TG"]
    pfile = PEND / f"{target}.json"
    if not pfile.exists():
        print(f"NO_PENDING: нет pending-заявки для tg={target}"); sys.exit(2)
    pend = json.loads(pfile.read_text())
    tg, name = pend["telegram_id"], pend.get("display_name") or "Unknown"
    s2, _ = find_block(tg)
    if s2 is not None:
        print(f"ALREADY_IN: tg={tg} уже в roster'е"); pfile.unlink(); sys.exit(0)
    entry = f'''
  - telegram_id: {tg}
    display_name: "{name}"
    atlassian_email: "TODO:fill-on-first-setup"
    atlassian_account_id: "TODO:fill-on-first-setup"
    gitlab_username: "TODO:fill-on-first-setup"
    role: "member"
'''
    TEAM.write_text(text.rstrip() + entry)
    pfile.unlink()
    print(f"APPROVED: {name} (tg={tg}) added as member")

elif action == "add":
    target = os.environ["TARGET_TG"]
    name = os.environ.get("TARGET_NAME") or "Unknown"
    if not re.match(r"^\d{5,15}$", target):
        print("BAD_TG"); sys.exit(2)
    s2, _ = find_block(target)
    if s2 is not None:
        print(f"ALREADY_IN"); sys.exit(0)
    entry = f'''
  - telegram_id: {target}
    display_name: "{name}"
    atlassian_email: "TODO:fill-on-first-setup"
    atlassian_account_id: "TODO:fill-on-first-setup"
    gitlab_username: "TODO:fill-on-first-setup"
    role: "member"
'''
    TEAM.write_text(text.rstrip() + entry)
    print(f"ADDED: {name} (tg={target}) as member")

elif action == "remove":
    target = os.environ["TARGET_TG"]
    s2, e2 = find_block(target)
    if s2 is None:
        print(f"NOT_FOUND: tg={target} not in roster"); sys.exit(2)
    target_role = block_role(s2, e2)
    target_name = block_name(s2, e2)
    if str(target) == str(sender_id):
        print("REFUSE_SELF: нельзя удалить себя"); sys.exit(3)
    if target_role == "teamlead" and sender_role != "teamlead":
        print("REFUSE_NON_LEAD_REMOVE_LEAD: teamlead'а может удалить только другой teamlead"); sys.exit(3)
    new_text = text[:s2] + text[e2:]
    TEAM.write_text(new_text.rstrip() + "\n")
    tfile = TOK / f"{target}.json"
    if tfile.exists(): tfile.unlink()
    print(f"REMOVED: {target_name} (tg={target}, role={target_role})")

elif action == "set_role":
    target = os.environ["TARGET_TG"]
    new_role = os.environ["NEW_ROLE"]
    if sender_role != "teamlead":
        print("AUTH_DENIED: смена ролей — прерогатива teamlead'а"); sys.exit(1)
    if new_role not in ("member", "admin", "teamlead"):
        print(f"BAD_ROLE: {new_role} (allowed: member/admin/teamlead)"); sys.exit(2)
    s2, e2 = find_block(target)
    if s2 is None:
        print(f"NOT_FOUND: tg={target} not in roster"); sys.exit(2)
    if str(target) == str(sender_id):
        print("REFUSE_SELF_DEMOTE: promote кого-то ещё в teamlead и пусть он меняет тебе роль"); sys.exit(3)
    old_role = block_role(s2, e2)
    target_name = block_name(s2, e2)
    old_block = text[s2:e2]
    new_block = re.sub(r'role:\s*"[^"]*"', f'role: "{new_role}"', old_block, count=1)
    if new_block == old_block:
        new_block = re.sub(r"role:\s*\S+", f'role: "{new_role}"', old_block, count=1)
    TEAM.write_text(text[:s2] + new_block + text[e2:])
    print(f"ROLE_CHANGED: {target_name} (tg={target}): {old_role} → {new_role}")

else:
    print(f"UNKNOWN_ACTION: {action}"); sys.exit(2)
PY
```

После успеха — отправь нужные DM (через `send_message_tool`) и подтверди
инициатору.

## Pitfalls

- **`set_role` только от teamlead'а**. Admin / member НЕ могут менять
  роли — иначе admin промотит себя до teamlead'а. Это hard-coded в
  auth-check'е выше, не ослабляй.
- **Парсер text-based**, не полноценный YAML. Держи формат team.yaml в
  2-пробельной indent'ации; каждая запись начинается с `  - telegram_id:`.
  Если кто-то вручную сломал формат — скрипт не добавит / не удалит
  корректно, лучше fail loudly, чем молча.
- **После remove** пользователь всё ещё в `TA_TELEGRAM_ALLOWED_USERS`
  если там не `*`. При wildcard-allowlist (дефолт с approval-флоу) —
  его заново отбьёт SOUL.md-гейт (team_directory не найдёт). При строгом
  allowlist — нужен `scripts/sync-team-allowlist.sh` и рестарт hermes.
- **approve / add / remove** всегда от лица teamlead/admin, **никогда**
  от имени pending-user'а (иначе самозапись).

## Verification

- Unknown user → /start → `team_approval` создаёт pending; admin
  пишет «одобрь 987654321» → запись в team.yaml, user получает DM.
- Teamlead пишет «добавь Олега 987654321 имя=Олег» → запись сразу.
- Admin пишет «убери Сашу» → запись удалена, Саша получает DM об
  отзыве доступа, его токены стёрты.
- Member пишет «добавь» / «удали» — отказ «нужна роль teamlead/admin».
- Admin пишет «удали Vasily (teamlead)» — отказ «teamlead'ов через SSH».
- Member пишет «удали меня» — отказ (member не может).
  Admin пишет «удали <сам_себя>» — отказ «нельзя удалить себя».
- Teamlead пишет «повысь Олега до admin» → роль обновлена.
- Admin пишет «повысь себя до teamlead'а» → отказ «смена ролей только teamlead'ом».
- Teamlead пишет «понизь себя до member» → отказ no-self-demote.
