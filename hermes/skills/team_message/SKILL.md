---
name: team_message
description: Send a Telegram DM to a teammate (or broadcast to the whole pilot team) on behalf of the current user. Resolves names via team_directory, sends through Hermes' send_message tool, always prefixes the payload with "[от <sender_name>]" so the recipient knows it's a relay, not a native bot message. Respects Telegram's constraint that the target user must have started a conversation with the bot first.
version: 0.1.0
metadata:
  hermes:
    tags: [team, messaging, corporate]
---

# team_message

Пересылает сообщение коллеге в команде (или всей команде) через Telegram-бота.

## When to Use

- Пользователь явно просит что-то передать коллеге:
  «скажи Васе что X», «передай Пете ссылку Y», «пингани Сашу по поводу Z».
- Broadcast — только при явной формулировке: «напиши команде», «всем», «team»,
  «сообщи ребятам».
- НЕ использовать для "а что там у Васи?" — это запрос на чтение чужих задач,
  не на отправку ему сообщения (здесь Vikunja/Jira-скиллы от имени юзера).

## Procedure

### 1. Резолв адресата

```
skill_view("team_directory") → find_user_by_name(<name>)
```
Получил `telegram_id`. Если `NOT FOUND` / `AMBIGUOUS` — спроси у юзера
уточнение, не угадывай.

### 2. Отправка через send_message_tool

Инструмент `send_message_tool` приходит из пресета `hermes-telegram` и уже
есть в платформенном toolset'е. Вызов:

```
send_message_tool(
    platform="telegram",
    chat_id=<target_telegram_id>,
    text=f"[от {sender_name}] {original_text}",
)
```

Где `sender_name` — `display_name` из roster'а по `session.source.user_id`.

### 3. Обработка ошибок

- `Forbidden: bot can't initiate conversation with a user` —
  целевой юзер ещё ни разу не писал боту. Ответь отправителю:
  «<Name> ещё не подключился к боту — попроси его написать @<bot_username>
  /start, тогда смогу пересылать».
- `Chat not found` — `telegram_id` в team.yaml неверный. Тот же ответ.
- Любое 5xx от Telegram API — повтори один раз через 2 с; если не вышло —
  скажи «Telegram недоступен, передать не могу, попробуй позже».

### 4. Broadcast

```
for member in list_team():
    if member["telegram_id"] == session.source.user_id:
        continue   # не шлём отправителю
    send_message_tool(chat_id=member["telegram_id"], text=f"[от {sender_name}] {original_text}")
```

Между отправками `time.sleep(0.1)` чтобы не упереться в Telegram rate-limit.

## Pitfalls

- **Явный префикс `[от <sender>]`** — обязателен. Бот не должен казаться
  автором чужой идеи.
- **Не пересылай секреты**. Если в сообщении похоже на токен/пароль
  (длинный случайный base64, `sk-...`, `tk_...`, `token:...`) —
  откажись и предложи передать через защищённый канал.
- **Broadcast без spam-защиты**: не делай больше одного broadcast'а в
  5 минут от одного пользователя. Если пользователь пытается спамить —
  скажи «broadcast уже отправлен минуту назад, подожди».
- **Не шли от имени бота решений/обещаний**. Фраза от Paul'а "Вася сделает
  до пятницы" всё равно должна быть "[от Paul] Вася сделает до пятницы" —
  получатель видит, кто это сказал, не думает что бот за него решил.

## Verification

- Юзер A пишет «скажи <B> привет» → юзер B получает DM `[от <A>] привет`.
- Юзер A пишет «напиши команде что демо в 15» → все остальные члены команды
  (не A) получают DM `[от <A>] демо в 15`. A не получает своё сообщение.
- Если у B не было `/start` — юзер A получает понятную ошибку про «не
  подключился к боту», а не стектрейс.
