"""Plugin Pack — десятки маленьких полезных инструментов для AgentBrain.

Философия: агенту чаще нужны «ножницы и линейка», чем второй реактор.
Здесь — офлайн-утилиты, которые нейронка обычно умеет плохо (арифметика
денег, даты, кодировки) и которые стоят ноль токенов. Всё чистый Python,
без сети, без зависимостей.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from datetime import date, timedelta
from typing import Any


def register_pack(brain) -> None:  # noqa: ANN001 - AgentBrain без цикла импорта
    """Зарегистрировать пак утилит в мозг."""

    def t(name, desc, params, handler):
        from .agent_brain import ToolSpec

        brain.register(ToolSpec(name, desc, params, handler))

    # ── деньги ──────────────────────────────────────────────────

    t("compound_interest", "Будущая стоимость вклада: сумма × (1+ставка)^лет",
      {"type": "object",
       "properties": {"principal": {"type": "number"},
                      "annual_rate_pct": {"type": "number"},
                      "years": {"type": "number"},
                      "monthly_add": {"type": "number"}},
       "required": ["principal", "annual_rate_pct", "years"]},
      lambda principal, annual_rate_pct, years, monthly_add=0: {
          "future_value": round(_fv(float(principal), float(annual_rate_pct),
                                    float(years), float(monthly_add)), 2),
      })

    t("cagr", "Среднегодовая доходность (CAGR): из начального в конечное за годы",
      {"type": "object",
       "properties": {"begin": {"type": "number"}, "end": {"type": "number"},
                      "years": {"type": "number"}},
       "required": ["begin", "end", "years"]},
      lambda begin, end, years: (
          {"cagr_pct": round(100 * ((float(end) / float(begin)) ** (1 / float(years)) - 1), 2)
           if float(begin) > 0 and float(years) > 0 else None}
      ))

    t("percent_change", "Изменение в процентах: было → стало",
      {"type": "object",
       "properties": {"was": {"type": "number"}, "now": {"type": "number"}},
       "required": ["was", "now"]},
      lambda was, now: {"change_pct": round(100 * (float(now) - float(was))
                                            / float(was), 3) if float(was) else None})

    t("vat_rub", "Выделить НДС 20% из суммы с НДС",
      {"type": "object", "properties": {"amount_with_vat": {"type": "number"}},
       "required": ["amount_with_vat"]},
      lambda amount_with_vat: {
          "net": round(float(amount_with_vat) / 1.2, 2),
          "vat": round(float(amount_with_vat) - float(amount_with_vat) / 1.2, 2),
      })

    t("loan_payment", "Аннуитетный платёж по кредиту",
      {"type": "object",
       "properties": {"principal": {"type": "number"},
                      "annual_rate_pct": {"type": "number"},
                      "months": {"type": "integer"}},
       "required": ["principal", "annual_rate_pct", "months"]},
      lambda principal, annual_rate_pct, months: {
          "monthly_payment": round(_annuity(float(principal),
                                            float(annual_rate_pct), int(months)), 2)})

    t("split_bill", "Разделить счёт на N с чаевыми",
      {"type": "object",
       "properties": {"total": {"type": "number"}, "people": {"type": "integer"},
                      "tip_pct": {"type": "number"}},
       "required": ["total", "people"]},
      lambda total, people, tip_pct=10: {
          "per_person": round(float(total) * (1 + float(tip_pct) / 100)
                              / max(int(people), 1), 2)})

    def _rr(entry, stop, target, direction="long"):
        risk = abs(entry - stop) or 1e-9
        reward = abs(target - entry)
        return {"risk": round(risk, 4), "reward": round(reward, 4),
                "rr": round(reward / risk, 2)}
    t("risk_reward", "Соотношение риск/прибыль сделки",
      {"type": "object",
       "properties": {"entry": {"type": "number"}, "stop": {"type": "number"},
                      "target": {"type": "number"}},
       "required": ["entry", "stop", "target"]},
      _rr)

    # ── даты ────────────────────────────────────────────────────

    t("days_between", "Дней между датами YYYY-MM-DD",
      {"type": "object",
       "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}},
       "required": ["from_date", "to_date"]},
      lambda from_date, to_date: {
          "days": (date.fromisoformat(to_date) - date.fromisoformat(from_date)).days})

    t("date_shift", "Дата + N дней → ISO и день недели",
      {"type": "object",
       "properties": {"base": {"type": "string"}, "days": {"type": "integer"}},
       "required": ["days"]},
      lambda days, base=None: (
          lambda d: {"iso": d.isoformat(),
                     "weekday": ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][d.weekday()]}
      )(date.fromisoformat(base or date.today().isoformat()) + timedelta(days=int(days))))

    # ── текст ───────────────────────────────────────────────────

    t("text_stats", "Символы/слова/приблизительное время чтения",
      {"type": "object", "properties": {"text": {"type": "string"}},
       "required": ["text"]},
      lambda text: {
          "chars": len(text), "words": len(text.split()),
          "read_min": max(1, len(text.split()) // 180)})

    t("slugify", "Строка → слаг для файла/URL",
      {"type": "object", "properties": {"text": {"type": "string"}},
       "required": ["text"]},
      lambda text: {"slug": _slugify(text)})

    t("translit_ru", "Русские буквы латиницей",
      {"type": "object", "properties": {"text": {"type": "string"}},
       "required": ["text"]},
      lambda text: {"translit": _translit(text)})

    # ── данные ──────────────────────────────────────────────────

    t("hash_text", "SHA-256 строки (первые 32 hex)",
      {"type": "object", "properties": {"text": {"type": "string"}},
       "required": ["text"]},
      lambda text: {"sha256_32": hashlib.sha256(text.encode()).hexdigest()[:32]})

    t("b64_encode", "Текст → base64",
      {"type": "object", "properties": {"text": {"type": "string"}},
       "required": ["text"]},
      lambda text: {"b64": base64.b64encode(text.encode()).decode()})

    t("csv_to_json", "CSV-строка (первая строка — заголовки) → JSON",
      {"type": "object", "properties": {"csv": {"type": "string"}},
       "required": ["csv"]},
      lambda csv: {"json": _csv_to_json(csv)})

    t("stats_numbers", "Мин/макс/медиана/среднее по списку чисел",
      {"type": "object", "properties": {"numbers": {"type": "array"}},
       "required": ["numbers"]},
      lambda numbers: _stats([float(x) for x in numbers]))

    t("flatten_json", "Вложенный JSON → плоские ключи a.b.c",
      {"type": "object", "properties": {"data": {"type": "object"}},
       "required": ["data"]},
      lambda data: {"flat": _flatten(data)})


# ── реализации ──────────────────────────────────────────────────────


def _fv(principal: float, rate_pct: float, years: float,
        monthly_add: float = 0.0) -> float:
    """Будущая стоимость с ежемесячными пополнениями."""
    r = rate_pct / 100 / 12
    n = max(0, int(years * 12))
    value = principal
    if r:
        value *= (1 + r) ** n
        for month in range(1, n + 1):
            value += monthly_add * (1 + r) ** (n - month)
    else:
        value += monthly_add * n
    return value


def _annuity(principal: float, rate_pct: float, months: int) -> float | None:
    """Ежемесячный аннуитетный платёж; при ставке 0 — простое деление."""
    if months <= 0:
        return None
    r = rate_pct / 100 / 12
    if r == 0:
        return principal / months
    k = r * (1 + r) ** months / ((1 + r) ** months - 1)
    return principal * k


_RU_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _translit(text: str) -> str:
    out = []
    for ch in text:
        low = ch.lower()
        if low in _RU_LAT:
            rep = _RU_LAT[low]
            out.append(rep.capitalize() if ch.isupper() else rep)
        else:
            out.append(ch)
    return "".join(out)


def _slugify(text: str) -> str:
    s = _translit(text).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:80] or "item"


def _csv_to_json(csv_text: str) -> str:
    lines = [ln for ln in csv_text.strip().splitlines() if ln.strip()]
    if len(lines) < 1:
        return "[]"
    header = [h.strip() for h in lines[0].split(",")]
    rows = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.split(",")]
        rows.append(dict(zip(header, cells, strict=False)))
    return json.dumps(rows, ensure_ascii=False)


def _stats(nums: list[float]) -> dict[str, Any]:
    if not nums:
        return {}
    s = sorted(nums)
    n = len(s)
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return {
        "min": min(nums), "max": max(nums),
        "mean": round(sum(nums) / n, 4), "median": median,
        "stdev": round(math.sqrt(sum((x - sum(nums) / n) ** 2 for x in nums)
                                 / max(n - 1, 1)), 4),
    }


def _flatten(data: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten(value, path))
        elif isinstance(value, list):
            out[path] = json.dumps(value, ensure_ascii=False)
        else:
            out[path] = value
    return out
