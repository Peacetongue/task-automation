---
name: team_directory
description: Look up teammates in the pilot team roster (config/team.yaml) by telegram_id, display_name, or role. Returns their Jira atlassian_account_id (for @mentions / assignee), gitlab_username (for MRs / reviewers), and telegram_id (for sending them DMs via team_message). Use this whenever a user mentions a teammate by name, so the bot can pick the right ID to plug into Jira / GitLab / Telegram APIs.
version: 0.1.0
metadata:
  hermes:
    tags: [team, directory, corporate]
---

# team_directory

Справочник по пилотной команде. Файл лежит внутри контейнера в
`/opt/data/config/team.yaml` (монтируется из `config/team.yaml` на хосте).

## When to Use

- Пользователь упомянул имя коллеги ("назначь Васю", "скажи Пете", "команда
  фронта"). Нужен его ID в соответствующей системе.
- Нужно перечислить состав команды ("кто в команде?").
- Перед вызовом `team_message`, `mcp_atlassian_createJiraIssue` с assignee,
  `mcp_gitlab_create_merge_request` с assignee/reviewer.

## Utilities (через terminal_tool + python3)

Все операции — один однострочник, читающий `/opt/data/config/team.yaml`.

### `find_user_by_name(name)`
```bash
python3 - <<'PY'
import yaml, sys
name = "Вася"                                    # <-- подставить
data = yaml.safe_load(open("/opt/data/config/team.yaml"))
norm = lambda s: s.lower().strip()
matches = [m for m in data["team"] if norm(name) in norm(m["display_name"])]
if not matches:
    print("NOT FOUND"); sys.exit(1)
if len(matches) > 1:
    print("AMBIGUOUS:", [m["display_name"] for m in matches]); sys.exit(2)
import json; print(json.dumps(matches[0], ensure_ascii=False, indent=2))
PY
```
Возвращает полную запись: `telegram_id`, `atlassian_email`,
`atlassian_account_id`, `gitlab_username`, `role`.

### `list_team()`
```bash
python3 -c "import yaml;d=yaml.safe_load(open('/opt/data/config/team.yaml'));[print(m['display_name'],m['role'],m['telegram_id']) for m in d['team']]"
```

### `get_gitlab_username(telegram_id)` / `get_atlassian_account_id(name)`
Комбинации выше — по полям.

## Pitfalls

- **Неоднозначность**: «Саша» может быть Александр и Александра. Если
  найдено >1 — СПРОСИ у пользователя, какого именно, не угадывай.
- **Нет сотрудника** в roster — так и скажи пользователю
  («Петра в пилоте нет, пока могу обращаться только к <list_team>»).
  Не пытайся звать Petya через Jira если его не знает бот.
- **Placeholder'ы `TODO:fill-account-id`** в свежем team.yaml означают, что
  админ ещё не заполнил Atlassian IDs. Предупреди пользователя, что не
  сможешь корректно @mention-ить в Jira — но базовые операции (создание
  issue без assignee, поиск по email) работают.
- **Случайный бардак с регистром** в именах — матчь case-insensitive.

## Verification

- `list_team()` вернёт ≥1 запись.
- `find_user_by_name("Paul")` найдёт teamlead.
- После правки team.yaml изменения видны без рестарта hermes (скилл каждый
  раз читает файл заново).
