# v3.22.1 — Fix Release: прокси ожил + чистый артефакт

**Дата:** 2026-08-16
**Тип:** bugfix + security (синхронизация версии + критический фикс прокси + whitelist-сборка)

## 🔧 Исправлено

### 1. Прокси экономии (`token-diet serve`) — endpoint `/v1/chat/completions` возвращал 422

**Симптом:** любой POST на `/v1/chat/completions` падал с
`{"detail":[{"type":"missing","loc":["query","request"],"msg":"Field required"}]}`,
запрос не доходил до хендлера, экономия не работала.

**Корень:** `from __future__ import annotations` (PEP 563) превращает аннотацию
`request: Request` в строку. FastAPI резолвит её в globals модуля, а `Request`
импортировался внутри `create_app()` — резолв проваливался, и параметр
трактовался как query-параметр.

**Фикс:** импорт `Request` поднят на уровень модуля (guard try/except —
fastapi остаётся опциональной зависимостью).

**Проверено живьём:** DeepSeek ответил сквозь прокси
(`deepseek-v4-flash`, `/health` → `{"status":"ok","proxy":true}`).

### 2. Артефакт собирался blacklist-ом и утаскивал лишнее (правило №7)

`build_artifact_zip` паковал весь каталог: `.git/` (411 файлов в закоммиченном
zip), `.env` при наличии, сам zip. Теперь сборка **whitelist**: только
`token_diet/*.py`, `tests/*.py`, `assets/`, README, pyproject, ci_check,
LICENSE, `.env.example`, `run.sh`, `RELEASE_NOTES*`. Секреты и мусор физически
не могут попасть в артефакт.

`token-diet-lib.zip` убран из git-трекинга (в истории остался старый —
без ключей, но с `.git`-метаданными от 09.08).

### 3. Рассинхрон версий

Коммиты v3.20–v3.22 не бампнули `pyproject.toml` / `__init__.py` (застряли
на 3.19.2). Синхронизировано: **3.22.1**.

## 📋 Обновление

```bash
cd /path/to/token-diet
pip install --user --no-deps --force-reinstall --break-system-packages .
token-diet doctor
```

**Запуск прокси с апстримом** (аргументы `--upstream-url` через внешний CLI
не проходят — используйте env):

```bash
UPSTREAM_URL="https://api.deepseek.com/v1" \
UPSTREAM_KEY=$(cat ~/.token-diet/deepseek_key) \
token-diet serve
```

## 📖 Контекст

Рабочая копия в /tmp сгорела при ребуте 16.08 (репо восстановлено из GitHub
в `/home/ro/token-diet` — постоянный диск). Полный аудит дыр и архитектура
«Мозг в Чемодане» — в памяти Obsidian: `АРХИТЕКТУРА_Мозг_в_Чемодане_1608.md`.
