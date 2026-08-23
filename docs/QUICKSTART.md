# QUICKSTART — 5 минут до первого `§0`

## 1. Скачать

```bash
# pip
pip install git+https://github.com/Bugaga31/token-diet.git
# или клон
git clone https://github.com/Bugaga31/token-diet.git token-dietTOP
cd token-dietTOP
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[server,tiktoken,dev]"
```

## 2. Включить

```bash
# .env в корне (пример)
cat > .env << 'EOF'
ANYMODEL_API_KEY=sk-...
TINKOFF_TOKEN=t.xxx
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=...
EOF

# автонастройка
token-diet setup
# прокси
token-diet serve &
# проверка
token-diet doctor        # ожидаешь 12/12
token-diet self-test     # ожидаешь 5/5
```

## 3. Попробовать

```bash
# сжатие
token-diet diet "Refund policy: refunds within 14 days, RMA required. Shipping free above $50." 
# → экономия 42.9% (1019→582 в ci_check.py:42)

# нестандартное мышление
token-diet think "Как сжать промпт на 50%?" --haiku
# → 3 угла → синтез → 💎 твёрдо + 🍃 хайку

# доки-тесты (каждый ``` — тест)
token-diet docs
# → 📚 DocGuardian: 12 блоков — ok:8 fail:0

# самолечение
token-diet heal
# → 🔧 AutoHeal: проверка doctor → следующий приём

# уверенность
token-diet belief "проверил token_diet/__init__.py:5 версия 3.27.0"
# → [belief] тон: твёрдо · доказательств: 2

# память
token-diet memo remember "тест: quickstart ok" 
token-diet recall "quickstart"

# портфель (если TINKOFF_TOKEN)
token-diet portfolio
token-diet status
```

## 4. Проверить честно

```bash
python3 -m pytest tests/test_creative_mind.py -v  # 8 passed
python3 -m pytest tests -q                         # 1400+ passed
python3 ci_check.py                                # → CI RESULT: PASS
```

## 5. Что дальше

- `token-diet panel` — что есть на твою задачу
- `token-diet eyes "что на экране?"` — vision
- `token-diet turbo` — выжать RAM/ZRAM
- `docs/MERGE_MANIFEST.md` — как объединили 5 копий

Если `doctor` красный — смотри `token_diet/cli.py:32 _core_checks`, там подскажет что чинить.
Если `diet` не экономит — проверь длину (>100 символов) и что не код/JSON (guard).

Всё. Ты включён. Дальше — изобретай язык вместо сжатия: `§0` ждёт.

*v3.27.0, 2026-08-23*
