# token-dietTOP 🧠💰

**Главная папка проекта token-diet — для тебя и твоих нейронок.**

Track. Optimize. Achieve. — экономия токенов + повышение интеллекта +
инвестиции + память + телеграм-разведка. Всё в одном месте.

---

## 📁 Структура

```
token-dietTOP/
├── token_diet/          # 149 модулей (ядро проекта)
│   ├── core.py          # сжатие токенов
│   ├── sherlock_reasoner.py   # интеллект
│   ├── self_belief.py         # заслуженная уверенность (без slop)
│   ├── claim_check.py         # аудит заявлений и цифр
│   ├── factory_droid.py       # дроиды Factory AI (0 LLM)
│   ├── creative_mind.py       # нестандартное мышление (3 угла + хайку)
│   ├── tinkoff_invest.py      # инвестиции (T-Invest API)
│   ├── financial_agents.py    # агенты как у Anthropic (DCF, отчёты)
│   ├── provider_registry.py   # авто-подключение любых LLM-ключей
│   ├── scrapling_diet.py      # умный парсинг сайтов
│   ├── system_turbo.py        # ускорение компа
│   └── ... (всего 149)
├── tests/                # 1400+ тестов (pytest tests -q)
├── memory/               # Obsidian-память (в git не попадает)
├── token-diet-library/   # библиотека знаний: книги, дайджесты (локально)
├── skills/               # скиллы: caveman, nodumb, changelog-discipline...
├── docs/                 # release notes + манифест слияния проектов
└── instructions/
    ├── ИНСТРУКЦИЯ_ДЛЯ_НЕЙРОНКИ.md   # для любой нейронки (самонастройка)
    └── ИНСТРУКЦИЯ_ДЛЯ_ЧЕЛОВЕКА.md   # для тебя (запуск, команды)
```

## 🔀 Один проект вместо пяти

Эта папка — слияние всех копий проекта (манифест: `docs/MERGE_MANIFEST.md`):
из `~/token-diet` перенесены `self_belief`/`claim_check`, из
`~/token-diet-project` — `factory_droid`, скиллы и release notes,
из `~/token-diet-memory` — всё хранилище памяти (107 заметок),
из `~/token-diet-library` — база знаний. Старые копии в домашней папке
можно удалить (сначала проверь, что всё на месте).

## 🚀 Быстрый старт

```bash
cd token-dietTOP
python3 -m venv .venv && source .venv/bin/activate
pip install -e . && pip install tiktoken fastapi uvicorn httpx pytest
python3 -m token_diet.cli --help
python3 -m pytest tests -x -q           # 1400+ тестов (1436)
```

## 📖 Инструкции

- **Для нейронки:** `instructions/ИНСТРУКЦИЯ_ДЛЯ_НЕЙРОНКИ.md`
  — скажи любой нейронке прочитать этот файл, и она настроится сама.
- **Для человека:** `instructions/ИНСТРУКЦИЯ_ДЛЯ_ЧЕЛОВЕКА.md`
  — все команды и настройка ключей простым языком.

## ✨ Новое в этой сборке

- **`creative_mind.py`** — нестандартное мышление: Inverted Dictionary (изобрести язык вместо сжатия), Dream Consolidation (ночная консолидация паттернов), Haiku Compressor (70% экономии в 3 строки) + `token-diet think` (3 угла → синтез → калибровка `self_belief`).
- **`financial_agents.py`** — реверс-инжиниринг финансовых агентов Anthropic:
  Market Researcher (отчёт по компании), Model Builder (DCF в терминале),
  Earnings Reviewer (выжимка отчёта), Stock Screener. Без платных планов.
- **OmniRoute-интеграция** — 689 моделей через один эндпоинт
  (`http://localhost:20128/v1`), включая NVIDIA NIM, OpenVecta, AnyModel.

## 🔒 Безопасность

- Ключи — только в `.env` (в git не попадает)
- Никаких секретов в коде
- Релизы на GitHub — только с разрешения хозяина
