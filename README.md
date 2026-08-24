# token-diet 🧠💰 — Track. Optimize. Achieve.

**Открытый стандарт оптимизации LLM.** 151 модуль, 1400+ тестов, 0 секретов в коде. Экономит токены, ускоряет мысли, бережёт планету.

[![CI](https://github.com/Bugaga31/token-diet/actions/workflows/ci.yml/badge.svg)](https://github.com/Bugaga31/token-diet/actions)
[![PyPI](https://img.shields.io/badge/pip-token--diet-blue)](https://pypi.org/project/token-diet/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](pyproject.toml)

> `pip install token-diet` → `token-diet serve` → 40% токенов остаются в кармане.

---

## 📥 Как скачать

**Вариант 1 — pip (рекомендуется):**
```bash
pip install git+https://github.com/Bugaga31/token-diet.git
# с сервером:
pip install "git+https://github.com/Bugaga31/token-diet.git[server]"
```

**Вариант 2 — one-click скрипт:**
```bash
curl -sSL https://raw.githubusercontent.com/Bugaga31/token-diet/main/install.sh | bash
```

**Вариант 3 — git clone (для разработки):**
```bash
git clone https://github.com/Bugaga31/token-diet.git token-dietTOP
cd token-dietTOP
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[server,tiktoken,dev]"
```

---

## ⚡ Как включить (3 шага)

**1. Поставь ключи в `.env` (создай в корне):**
```bash
# .env — в git не попадёт (.gitignore)
OMNIROUTE_BASE_URL=http://localhost:20128/v1  # опционально, локальный роутер 689 моделей
ANYMODEL_API_KEY=sk-...                        # AnyModel / GLM / DeepSeek
TINKOFF_TOKEN=t.xxx                            # Т-Инвестиции (tinkoff-invest-python)
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=...                          # my.telegram.org
```

**2. Автонастройка под твои инструменты:**
```bash
token-diet setup              # находит Claude Code / OpenCode / Cursor, пишет конфиги
# или
python3 -m token_diet.auto_setup setup
```

**3. Запусти прокси и проверяй:**
```bash
token-diet serve              # http://localhost:8000/v1 — OpenAI-совместимый
token-diet doctor             # 12/12 модулей живы?
token-diet self-test          # 5 офлайн-проверок
token-diet diet "твой длинный текст"  # увидишь экономию: 1019→582 (42.9%)
```

Все ключи — только из `.env`. В коде секретов нет — проверяет `token_diet/code_quality.py:129` (0 ложных срабатываний на `startswith("API_KEY=")`).

---

## 🚀 Быстрый старт (копируй и вставляй)

```bash
# 1. Сжатие — та же мысль, меньше токенов
python3 -m token_diet.cli diet 'Refund policy: refunds are issued within 14 days...' --aggressive

# 2. Нестандартное мышление — 3 угла + синтез + калибровка уверенности
python3 -m token_diet.cli think 'Как сжать промпт на 50% без потери смысла?' --haiku
# → 🎨 3 угла → 🧩 синтез → 💎 твёрдо/нужна проверка → 🍃 хайку

# 3. Уверенность по фактам (без извинений)
python3 -m token_diet.cli belief 'проверил token_diet/core.py:42, 545 экспортов'
python3 -m token_diet.cli belief --prompt  # системный промпт для LLM

# 4. Память — новая нейронка подхватывает за минуту
python3 -m token_diet.cli memo remember 'урок: ...'  # запись
python3 -m token_diet.cli recall 'полюс'              # поиск

# 5. Инвестиции — живые данные, стоп-лоссы, журнал
python3 -m token_diet.cli portfolio
python3 -m token_diet.cli status
python3 -m token_diet.cli scan          # сканер роста+объём+стакан

# 6. Армия моделей — спросить всех и собрать консенсус
python3 -m token_diet.cli army 'вопрос' --omni --omni-role brain

# 7. Глаза — vision через экран
python3 -m token_diet.cli eyes 'что на экране?'
python3 -m token_diet.cli turbo         # выжать RAM/кэш/ZRAM
```

---

## 🧩 Что внутри (151 модуль)

| Модуль | Что делает | Команда |
|---|---|---|
| `core.py` | StructPack / dedupe / BlobStore | `diet` |
| `creative_mind.py` | **NEW** InvertedDictionary (§0), Dream, Haiku 70% | `think` |
| `doc_guardian.py` | **NEW** Доки-тесты (каждый ``` — тест) | `docs` |
| `auto_heal.py` | **NEW** Самолечение (doctor → фикс) | `heal` |
| `self_belief.py` | Earned confidence (твёрдо только с пруфом) | `belief` |
| `claim_check.py` | Ловит `5.8×` без вычисления | - |
| `cognition_arsenal.py` | ToT / Debate / Decompose + Router | `panel` |
| `efficient_thinking.py` | Chain-of-Draft 80% экономия | - |
| `factory_droid.py` | Дроиды Factory AI (0 LLM) | `droid` |
| `provider_registry.py` | Авто-поиск ключей из env | - |
| `cache_master.py` | Выравнивание кэш-блоков (O(log n)) | - |
| `universal_screen.py` | Desktop + Phone (ADB/MSS) | `eyes` |
| `system_turbo.py` | RAM/кэш/ZRAM | `turbo` |
| `tinkoff_invest.py` | T-Invest API, портфель | `portfolio` |

Полный список: `python3 -m token_diet.cli panel` → 10+ категорий.

---

## 🧪 Как проверить, что всё честно

```bash
python3 -m pytest tests -q          # 1400+ тестов, ~9 мин, все зелёные
python3 ci_check.py                 # benchmark + gate + cost + packaging → PASS
python3 benchmark_compete.py        # vs LLMLingua-2/Headroom/The Token Company
python3 -m token_diet.cli self-test # офлайн, без сети
```

Каждый `README`-пример гоняется в CI (`tests/test_token_diet_review.py:ReadmeExamplesTests`) — доки не врут.

**Цифры сейчас:** `ci_check.py` → `TOTAL 3208→2477 22.8%`, `end-to-end 1019→582 42.9%`, `gate PASS`.

---

## 💡 Придумано нестандартно (новое)

- **Inverted Dictionary** — изобрести язык вместо сжатия: `refund within 14 days...` → `§0`. Словарь в system prompt, LLM распаковывает.
- **Dream Consolidation** — ночью перепроигрывает историю сжатий, находит скрытые повторы, предлагает `§N`.
- **Haiku** — 3 строки вместо абзаца, числа/отрицания защищены (`haiku_compress`).

Идея: *экономить не удалением, а изобретением*. Чем чаще фраза, тем дешевле.

---

## 🔒 Безопасность

- Ключи только в `.env` (`.gitignore:1`), в коде — плейсхолдеры `sk-...`/`t.xxx`
- `memory/` и `token-diet-library/` — локально, в git не попадают
- `token_diet/security_audit.py` + `code_quality.py` — ловят секреты/`TODO`/дубли

---

## 📖 Доки

- `docs/QUICKSTART.md` — 5-минутный старт с проверками
- `docs/MERGE_MANIFEST.md` — как объединили 5 копий в один репозиторий
- `instructions/ИНСТРУКЦИЯ_ДЛЯ_НЕЙРОНКИ.md` — дай любой нейронке, она настроится сама
- `instructions/ИНСТРУКЦИЯ_ДЛЯ_ЧЕЛОВЕКА.md` — простым языком

---

## 🤝 Вклад

```bash
git clone https://github.com/Bugaga31/token-diet.git
pip install -e ".[dev]"
python3 -m pytest tests -x -q
# пиши код, добавляй тесты, открывай PR — CI проверит всё
```

MIT. Для людей. Для планеты. 🌍

*Обновлено: 2026-08-24, v3.29.0, 158 модулей, 122 файла тестов (1570 тестов), CI: .github/workflows/ci.yml*
