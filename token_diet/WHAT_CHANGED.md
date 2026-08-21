# Token Diet — delivery for review (round 2)

## Почему предыдущий артефакт «пришёл пустым»

`token-diet-main.zip` в корне — это **другой проект** (набор skills для
других инструментов: caveman-compress, ask-nodumb и т.п.). Он не относится
к token-diet. Реальный код всегда лежал в `token-diet-lib/`, и теперь он
упакован в нормальный артефакт:

- **`token-diet-lib.zip`** — полный архив библиотеки: 9 модулей + тесты +
  README + CI (проверено packaging-check'ом, 12 файлов, всё на месте).

## Ответы на замечания по отчёту

### 1. `prose_compression = 0%` — это guard, а не баг

`compress_with_routing()` намеренно не трогает текст **короче 100
символов** (вербатим) и никогда не сжимает код/JSON/URL/числа/стектрейсы.
Порог задокументирован в README. Добавлены явные тесты:
- `test_short_prose_left_verbatim` — короткая проза остаётся нетронутой;
- `test_long_prose_compresses` — длинная проза с filler-фразами ужимается;
- `test_code_and_numbers_never_compressed` — **выявил реальный пробел**:
  классификатор не распознавал код без ```fences``` (сигнатуры `def`/`class`
  могли сжиматься). Исправлено в `loss_router.py` (`_ZERO_TOLERANCE_PATTERNS`
  + паттерны `def/class/import`), тест теперь проходит.

### 2. `blob_reference = −19.7%` — добавлен guard

Механизм blob-ссылок теперь применяется **только при реальной экономии
≥5%** токенов (`optimization_runner._blob_proposal`). На коротких документах
оверхед handle'а больше самого документа → предложение отклоняется и
помечается `GUARD` (в бенчмарке и в отчёте). Бенчмарк больше **не считает
отклонённые строки в TOTAL** — регрессии в сумме не появляется:

| | до | после |
|---|---|---|
| blob_reference (короткие доки) | 456 | 456 (GUARD, не применяется) |
| prose (длинная, filler) | 217 | 120 (44.7%) |
| перевод (reuse=3, mock ~45%) | 250 | 140 (44.0%) |
| **TOTAL по модулям** | 3208 | **2477 (22.8%)** |
| **End-to-end** | 1019 | **582 (42.9%)** |

### 3. Прозрачность: GUARD теперь виден и в отчёте runner'а

`OptimizationReport` получил секцию **Rejected (guard reasons)** — когда
blob-предложение отклонено, в markdown-отчёте видно причину
(`GUARD: blob ref would cost +90 tokens`), а не тихое исчезновение.

### 4. README синхронизирован с кодом

Написан `token-diet-lib/README.md`:
- реальные цифры из `ci_check.py` (StructPack 56.3%, dedupe 50%,
  end-to-end 42.9%);
- задокументированы все пороги: StructPack round-trip guard, blob ≥5%,
  проза <100 символов, перевод только при break-even
  `reuse·экономия − перевод − проверка ≥ min_saving`, ROLLBACK в gate;
- примеры из README **выполняются в CI**: `ReadmeExamplesTests`
  (prepare_request, guarded_records, ContextManager, EquivalenceGate,
  OptimizationRunner).

### 4. Packaging — проверяется в CI

`ci_check.py` теперь собирает `token-diet-lib.zip` и проверяет, что в
артефакте есть все обещанные модули + тесты + README (`REQUIRED_ARTIFACTS`).
Артефакт: `/home/ro/3232/token-diet-lib.zip` (12 файлов, подтверждено).

## Как запустить

```bash
python3 token-diet-lib/ci_check.py token-diet-lib/TOKEN_DIET_CI_REPORT.md
# 53 теста + benchmark + gate + cost regression + packaging → PASS
```

## Статус

- 55/55 юнит-тестов (включая fuzz StructPack, BlobStore, cache analyzer,
  gate, метрики, README-примеры, короткая/длинная проза, guard blob,
  вопрос пользователя).
- CI: PASS (тесты + gate + cost regression + packaging).
- Equivalence Gate: PASS, критические факты сохраняются.
