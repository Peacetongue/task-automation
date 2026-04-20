---
name: aigateway
description: Reference handbook for the BIOCAD AI Gateway (the only LLM channel this stack is allowed to use). Covers the Provider/Name model-naming rule, how to list available models via GET /api/v2/models, custom headers (trace-id, budget-soft-check, bypass-safety-check), and the non-standard /rerank endpoint.
version: 0.1.0
metadata:
  hermes:
    tags: [task-automation, aigateway, reference]
---

# aigateway

Мини-хэндбук по корпоративному BIOCAD AI Gateway. **Единственный разрешённый канал для LLM** в этом стеке — ни OpenAI напрямую, ни Anthropic, ни локальные модели не используются.

## When to Use

- Нужно узнать, какие модели доступны прямо сейчас.
- Пришла ошибка `400 Invalid model name` — скорее всего модель передана плоским именем, а нужен формат `Provider/Name`.
- Нужен `/rerank` для RAG-сценариев (стандартный OpenAI SDK этот endpoint не знает).
- Надо включить/отключить safety-check или budget-check заголовками.

## Base

- URL: `https://aigateway.biocad.ru/api/v2`
- Auth: `Authorization: Bearer ${OPENAI_API_KEY}` (ключ начинается с `sk-`).
- OpenAI-совместимые эндпоинты: `/chat/completions`, `/embeddings`, `/models`.
- НЕстандартный: `/rerank`.
- **НЕТ** `/audio/transcriptions` — для STT используется отдельный shim (`whisper-shim` → BIOCAD Transcribe).

## Model naming — обязательный формат `Provider/Name`

Плоские имена (`gpt-4o`, `claude-3-5-sonnet`) gateway **отклоняет 400**. Всегда передавай в виде `Provider/Name`.

Примеры:
- `InHouse/Qwen3.5-122B`
- `OpenAI/GPT-5.4`, `OpenAI/GPT-5.4-mini`, `OpenAI/GPT-5.4-nano`
- `OpenRouter/Grok-4.2-beta`, `OpenRouter/Claude-Sonnet-4.6`

## List available models

```bash
python3 - <<'PY'
import json, os, urllib.request
req = urllib.request.Request(
    f'{os.environ["OPENAI_BASE_URL"].rstrip("/")}/models',
    headers={"Authorization": f'Bearer {os.environ["OPENAI_API_KEY"]}'},
)
with urllib.request.urlopen(req, timeout=10) as resp:
    data = json.loads(resp.read())
# Фильтруй по полю "task":
#   text2text — чат/инструкт-модели
#   text2vec  — эмбеддинги
#   rerank    — ранкеры
for m in data.get("data", data):
    print(m.get("task"), "-", f'{m.get("provider")}/{m.get("name") or m.get("id")}')
PY
```

## Custom headers (optional)

| Header | Значение | Когда использовать |
|--------|----------|---------------------|
| `trace-id` | UUID запроса | Трейсинг в логах gateway — указывать всегда в prod-пайплайнах, не обязательно в dev |
| `budget-soft-check` | `true` | В dev, чтобы не блокироваться по лимиту бюджета (gateway возвращает warning, а не 429) |
| `bypass-safety-check` | `true` | **Осторожно** — отключает детектор коммерческих секретов. Использовать только когда реально уверен, что в промпте их нет |

Пример с OpenAI SDK (если он используется не через Hermes):
```python
client.chat.completions.create(
    model="InHouse/Qwen3.5-122B",
    messages=[...],
    extra_headers={
        "trace-id": str(uuid.uuid4()),
        "budget-soft-check": "true",
    },
)
```

Hermes сам прокидывает `OPENAI_API_KEY` и `OPENAI_BASE_URL` — custom headers из скилла нужны только для точечных вызовов `urllib` (например, `/models` или `/rerank`).

## /rerank (non-standard)

```bash
python3 - <<'PY'
import json, os, urllib.request
body = json.dumps({
    "model": "InHouse/bge-reranker-v2-m3",   # пример; проверь через /models task=rerank
    "query": "как настроить vikunja",
    "documents": ["doc1 text", "doc2 text", "doc3 text"],
}).encode()
req = urllib.request.Request(
    f'{os.environ["OPENAI_BASE_URL"].rstrip("/")}/rerank',
    data=body, method="POST",
    headers={
        "Authorization": f'Bearer {os.environ["OPENAI_API_KEY"]}',
        "Content-Type": "application/json",
    },
)
with urllib.request.urlopen(req, timeout=30) as resp:
    print(resp.read().decode())
PY
```

Ответ — список объектов со `score` и индексами документов.

## Pitfalls

- **Не фолбэчь на прямые провайдеры.** Если gateway недоступен — агент должен честно сообщить об ошибке, а не идти в api.openai.com. Это политика.
- **`HERMES_MODEL` без Provider/** → gateway отвечает 400 и Hermes при старте не может выбрать модель. Всегда проверяй `.env`.
- **`bypass-safety-check: true`** в промптах с чем-то похожим на пароли/ключи/секреты — прямой путь к инциденту. По умолчанию не используй.
- **Vikunja API-токен не равно OPENAI_API_KEY.** У них разные переменные окружения — не перепутай (`VIKUNJA_API_TOKEN` vs `OPENAI_API_KEY`).

## Verification

- `GET /models` возвращает 200 и список моделей.
- Chat-completions запрос с `model="${HERMES_MODEL}"` возвращает 200.
- Если 400 с упоминанием `model` — проверь формат имени.
- Если 401/403 — проверь `OPENAI_API_KEY`.
