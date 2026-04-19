---
name: task_parser
description: Parse an incoming Telegram message (already transcribed if it was a voice) into a normalized task JSON with title, description, priority, and deadline. Delegates to ask_clarification if the request is ambiguous, then hands the final JSON to vikunja_create.
version: 0.1.0
metadata:
  hermes:
    tags: [task-automation, vikunja]
---

# task_parser

Превращает сырой текст задачи от тимлида в структуру, которую понимает `vikunja_create`.

## When to Use

- Пришло новое сообщение в Telegram от пользователя из `TELEGRAM_ALLOWED_USERS`, и оно похоже на постановку задачи ("сделай…", "нужно…", "напомни…", "к пятнице…", императив + дедлайн).
- На вход — уже распознанный текст (голосовое прошло через STT shim заранее).
- НЕ использовать для чисто болтательных сообщений ("привет", "как дела") — на них отвечать обычным способом.

## Output schema

```json
{
  "title":       "короткое резюме задачи, до ~80 символов, императив",
  "description": "исходный текст + все подробности (можно пусто)",
  "project":     "Inbox",
  "priority":    "low|normal|high|urgent",
  "deadline":    "YYYY-MM-DD или null"
}
```

## Procedure

1. **Priority** — по ключевым словам в исходном тексте (регистр не важен):
   - `срочно`, `asap`, `urgent`, `немедленно` → `urgent`
   - `важно`, `important`, `приоритет`, `high` → `high`
   - `когда-нибудь`, `low`, `не горит` → `low`
   - иначе → `normal`
2. **Deadline** — если в тексте есть относительная дата, резолвь её относительно сегодняшней через `terminal_tool`:
   ```bash
   python3 -c "from datetime import date, timedelta; print((date.today() + timedelta(days=3)).isoformat())"
   ```
   Соответствия:
   - `завтра` → `+1 day`
   - `послезавтра` → `+2 days`
   - `через N дней` → `+N days`
   - `к пятнице` / `в пятницу` / `к понедельнику` → ближайший будущий weekday (если сегодня этот же день — +7). Считай через `date.today()` + разницу `(target_weekday - today.weekday()) % 7 or 7`.
   - `сегодня` → `date.today().isoformat()`
   - явная дата `DD.MM` / `DD.MM.YYYY` → нормализуй в ISO, год по умолчанию — текущий (если дата уже прошла, прибавь год).
   - ничего нет → `null` (ключ `due_date` не передаётся в Vikunja).
3. **Title** — обрежь до одной короткой фразы-императива без лишних слов. Убери "Паш, ", "плз", эмодзи. Сохрани исходник в `description`.
4. **Project** — всегда `"Inbox"` (единственный дефолтный проект, ID в `VIKUNJA_DEFAULT_PROJECT_ID`).
5. **Ambiguity check** — ЕСЛИ ты не уверен в title (например, сообщение из одного слова "сделай", или непонятно, о какой задаче речь), **делегируй `ask_clarification`** с конкретным одиночным вопросом. НЕ угадывай.
6. После получения уточнения — собери финальный JSON и вызови скилл `vikunja_create` с ним.

## Pitfalls

- **Не трогай Jira**. Интеграция с Atlassian — TODO в `config.yaml`, пока отключена.
- **Одно уточнение на задачу — максимум.** Если после первого ответа всё ещё мутно — создавай задачу с тем title, что есть, и помечай `priority: normal`. Зацикливаться на уточнениях запрещено.
- **Локаль дат.** Тимлид пишет на русском, но Vikunja принимает только ISO `YYYY-MM-DD`. Резолвь всегда в ISO.
- Если в тексте явно указан не-Inbox проект ("в проект X") — пока игнорируй эту часть (есть только Inbox). Положи упоминание проекта в `description`.

## Verification

- После обработки в Vikunja должна появиться задача с правильным title, priority и (если был дедлайн) `due_date`.
- Если делегировал `ask_clarification` — убедись, что в Telegram-чат ушёл ровно один вопрос, и что после ответа создана только одна задача (не дубль).
