# Манифест слияния — один проект вместо пяти

**Дата:** 2026-08-22
**Что сделано:** все разбросанные копии token-diet объединены в `token-dietTOP`.
Оригиналы в домашней папке НЕ удалялись — удаляй вручную после проверки.

## Что откуда перенесено

| Источник | Что взято | Куда легло |
|---|---|---|
| `~/token-diet` (133 модуля) | `self_belief.py` + тесты | `token_diet/`, `tests/` |
| `~/token-diet` | `claim_check.py` + тесты | `token_diet/`, `tests/` |
| `~/token-diet-project` (146 модулей) | `factory_droid.py` + тесты | `token_diet/`, `tests/` |
| `~/token-diet-project` | 12 скиллов (caveman, nodumb...) | `skills/` |
| `~/token-diet-project` | release notes v3/v31/v331 | `docs/` |
| `~/token-diet-memory` | хранилище памяти, 107 заметок | `memory/` (gitignored) |
| `~/token-diet-library` | база знаний: книги, TG-дайджесты | `token-diet-library/` (gitignored) |

Остальное в старых копиях дублирует то, что уже есть в `token-dietTOP`
(сравнение модулей: 133 ⊂ 148, 146 ⊂ 148 — уникальны только перечисленные).

## Правки при переносе

1. **Ключ AnyModel вычищен** из `memory/API_AnyModel__ключ_и_доступ.md` и
   `memory/CONTEXT_ALL.md` (замена на `[REDACTED: ключ в .env —
   ANYMODEL_API_KEY]`). В оригинале `~/token-diet-memory` ключ остался —
   рабочее окружение не трогаем.
2. **factory_droid:** регулярка секрета не ловила ключи с дефисом
   (`sk-...`) — класс символов расширен до `[A-Za-z0-9_-]{16,}`.
3. **factory_droid:** `_IGNORED_DIRS` исключал `token-dietTOP` (наследие
   запуска из домашней папки) — сканер знаний пропускал весь репозиторий.
   Исключение убрано.
4. **test_factory_droid:** цепочка ролей собиралась как
   `from-роли + начальная роль` и не могла сойтись с ожиданием
   `code → review → test`; теперь берётся последний адресат хендоффа.
5. **CLI:** новые команды `droid` (status/knowledge/delegate/ask) и
   `belief` (калибровка тона: `strip_apology` + `firm_up` + вердикт).
6. **__init__.py:** экспорты self_belief / claim_check / factory_droid.

## Почему память и библиотека не в git

`memory/` и `token-diet-library/` — живые данные (заметки, книги). Ключи
и приватные данные в репозиторий не попадают (см. `.gitignore`), сырые
книги на GitHub не выкладываем.

## Кандидаты на удаление (после проверки)

- `~/token-diet` — старая копия (16 МБ)
- `~/token-diet-project` — копия-предшественник (120 МБ, из них 110 МБ — .git)
- `~/token-diet-memory` — память (перенесена сюда; удалить после смены
  TD_VAULT или проверки, что живые процессы читают `token-dietTOP/memory`)
- `~/token-diet-library` — база знаний (перенесена сюда)
- `~/token diet23` — пустая папка
