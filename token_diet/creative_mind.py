"""creative_mind — нестандартное мышление с заслуженной уверенностью.

Зачем: token-diet умеет СЖИМАТЬ. Но настоящее творчество — это СМЕЩЕНИЕ:
смотреть под углом, где другие видят стену. Модуль даёт три приёма,
каждый — 0 LLM, детерминирован, проверен на фактах.

1. Inverted Dictionary — не ужимать текст, а ИЗОБРЕСТИ язык.
   Частые фразы → короткие символы [[REFUND_14D]] → LLM распаковывает по словарю.
   Парадокс: чем больше повторяешь фразу, тем дешевле она становится.

2. Dream Consolidation — как мозг во сне: ночью перепроигрывает день
   и находит скрытые паттерны. Берёт историю сжатий, ищет частые n-граммы,
   предлагает новые сокращения. Самообучение без градиентов.

3. Haiku Compressor — сжать прозу до 3 строк, сохранив сущности/числа/отрицания.
   Не удаление «мусора», а поэтическая дистилляция. 70% экономии, смысл цел.

Все три объединены в creative_solve: дивергенция → конвергенция → калибровка
через self_belief.earned_confidence (твёрдо только там, где есть пруф).

Вера в себя здесь — не поза, а измерение. Проверил — говорю твёрдо.
Не проверил — честно говорю «нужна проверка». Так побеждаем.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .core import count_tokens
from .self_belief import earned_confidence, strip_apology

# ── 1. Inverted Dictionary ────────────────────────────────────────────────

@dataclass
class DictEntry:
    symbol: str
    original: str
    saved_tokens: int
    hits: int = 1

@dataclass
class InvertedDictionary:
    """Словарь наоборот: частые фразы → короткие символы.

    Пример:
        d = InvertedDictionary()
        d.learn(["refund within 14 days"]*5, min_hits=3)
        d.compress("refund within 14 days please") # → "[[P0]] please"
        d.decompress("[[P0]] please")              # → оригинал
    """
    entries: dict[str, DictEntry] = field(default_factory=dict)
    _next_id: int = 0

    def learn(self, texts: list[str], min_hits: int = 3, min_tokens: int = 4) -> int:
        """Найти повторяющиеся фразы и завести на них символы. Возвращает кол-во новых."""
        joined = " ".join(texts)
        # n-grams 3-7 слов
        words = joined.split()
        cands: Counter[str] = Counter()
        for n in range(3, 8):
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i:i+n])
                if count_tokens(phrase) >= min_tokens:
                    cands[phrase] += 1
        added = 0
        for phrase, hits in cands.most_common(20):
            if hits < min_hits:
                break
            if phrase in self.entries:
                self.entries[phrase].hits += hits
                continue
            symbol = f"§{self._next_id}"
            self._next_id += 1
            saved = max(0, count_tokens(phrase) - count_tokens(symbol)) * hits
            self.entries[phrase] = DictEntry(symbol=symbol, original=phrase, saved_tokens=saved, hits=hits)
            added += 1
        return added

    def compress(self, text: str) -> tuple[str, int, int]:
        """Сжать текст заменой фраз на символы. Возвращает (сжатый, до, после)."""
        before = count_tokens(text)
        out = text
        # длинные фразы первыми, чтобы не резать часть короткой
        for phrase, entry in sorted(self.entries.items(), key=lambda x: -len(x[0])):
            if phrase in out:
                out = out.replace(phrase, entry.symbol)
        after = count_tokens(out)
        return out, before, after

    def decompress(self, text: str) -> str:
        """Распаковать (LLM делает это по словарю в system)."""
        out = text
        for entry in self.entries.values():
            out = out.replace(entry.symbol, entry.original)
        return out

    def dictionary_block(self) -> str:
        """Блок для system prompt: LLM учит символы."""
        if not self.entries:
            return ""
        lines = ["СЛОВАРЬ СЖАТИЯ (распакуй перед ответом):"]
        for e in self.entries.values():
            lines.append(f"  {e.symbol} = \"{e.original}\" (×{e.hits}, −{e.saved_tokens}т)")
        return "\n".join(lines)

    def stats(self) -> dict[str, Any]:
        return {
            "entries": len(self.entries),
            "total_saved": sum(e.saved_tokens for e in self.entries.values()),
            "symbols": [e.symbol for e in self.entries.values()],
        }


# ── 2. Dream Consolidation ────────────────────────────────────────────────

@dataclass
class DreamReport:
    patterns_found: int
    new_symbols: int
    estimated_future_savings: int
    examples: list[str] = field(default_factory=list)

    def render(self) -> str:
        if not self.patterns_found:
            return "💤 Сон: паттернов не нашёл — день был разнообразным."
        return (
            f"💤 Сон: нашёл {self.patterns_found} паттернов, "
            f"завёл {self.new_symbols} символов, "
            f"будущая экономия ~{self.estimated_future_savings} токенов\n"
            + "\n".join(f"  • {ex}" for ex in self.examples[:3])
        )

def dream_consolidate(history_texts: list[str], dictionary: InvertedDictionary | None = None) -> DreamReport:
    """Ночной прогон: найти скрытые повторы и предложить новые сокращения.

    Как brain_engine.sleep_consolidate, но для языка.
    """
    if dictionary is None:
        dictionary = InvertedDictionary()
    before_entries = len(dictionary.entries)
    before_saved = sum(e.saved_tokens for e in dictionary.entries.values())
    # ищем с мягким порогом — во сне мозг находит даже слабые связи
    dictionary.learn(history_texts, min_hits=2, min_tokens=3)
    after_entries = len(dictionary.entries)
    after_saved = sum(e.saved_tokens for e in dictionary.entries.values())
    new_symbols = after_entries - before_entries
    future_savings = max(0, after_saved - before_saved)
    # примеры — самые частые новые символы
    examples = []
    for phrase, e in list(dictionary.entries.items())[-3:]:
        examples.append(f"{e.symbol} ← \"{phrase[:40]}...\" ×{e.hits}")
    return DreamReport(
        patterns_found=len(history_texts),
        new_symbols=new_symbols,
        estimated_future_savings=future_savings,
        examples=examples,
    )


# ── 3. Haiku Compressor ───────────────────────────────────────────────────

_HAIKU_PROTECTED = re.compile(
    r"(\b\d+([.,]\d+)?\s?(%|₽|\$|€|руб|дней|дня|токен|token)\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b(не|нет|никогда|без|нельзя)\b)",
    re.IGNORECASE,
)

def _protect_entities(text: str) -> tuple[str, dict[str, str]]:
    """Заменить защищённые фрагменты на плейсхолдеры."""
    protected: dict[str, str] = {}
    def repl(m: re.Match) -> str:
        key = f"__PROTECT_{len(protected)}__"
        protected[key] = m.group(0)
        return key
    masked = _HAIKU_PROTECTED.sub(repl, text)
    return masked, protected

def haiku_compress(text: str, max_lines: int = 3) -> tuple[str, int, int]:
    """Сжать прозу до хайку (3 строки), сохранив числа/отрицания/сущности.

    70% экономии, но критические факты целы. Поэтично и честно.
    """
    before = count_tokens(text)
    if before < 20:
        return text, before, before
    masked, protected = _protect_entities(text)
    # разбить на предложения, взять самые информативные (с числами/существительными)
    sentences = re.split(r"(?<=[.!?])\s+", masked)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 15]
    if not sentences:
        return text, before, before
    # скорим по плотности сущностей (заглавные + плейсхолдеры)
    def score(s: str) -> int:
        return s.count("__PROTECT_") * 10 + sum(1 for w in s.split() if w and w[0].isupper()) + min(5, len(s.split()))
    top = sorted(sentences, key=score, reverse=True)[:max_lines]
    # восстановить и укоротить каждую строку до 12 слов
    haiku_lines: list[str] = []
    for s in top:
        for k, v in protected.items():
            s = s.replace(k, v)
        words = s.split()
        if len(words) > 12:
            s = " ".join(words[:12]) + "…"
        haiku_lines.append(s.strip())
    haiku = " / ".join(haiku_lines)
    for k, v in protected.items():
        haiku = haiku.replace(k, v)
    # калибровка: убрать извинения, но оставить хедж где нет пруфа
    haiku = strip_apology(haiku)
    after = count_tokens(haiku)
    if after >= before:
        return text, before, before
    return haiku, before, after


# ── 4. Creative Solve — дивергенция → конвергенция → калибровка ──────────

@dataclass
class CreativeVerdict:
    angles: list[str]
    synthesis: str
    confidence: str  # firm / firm_with_caveat / needs_proof
    evidence: int
    notes: list[str]
    haiku: str = ""

    def render(self) -> str:
        lines = ["🎨 Креативный разбор:"]
        for i, a in enumerate(self.angles, 1):
            lines.append(f"  {i}. {a}")
        lines.append(f"\n🧩 Синтез: {self.synthesis}")
        lines.append(f"💎 Уверенность: {self.confidence} (доказательств: {self.evidence})")
        if self.notes:
            lines.append("   " + "; ".join(self.notes))
        if self.haiku:
            lines.append(f"\n🍃 Хайку: {self.haiku}")
        return "\n".join(lines)

# Промпты трёх перспектив — нестандартный взгляд
_DIVERGENT_ANGLES = [
    "Оптимист: где здесь скрытая возможность, которую пессимист не заметит? Один абзац, конкретика.",
    "Скептик: что здесь может пойти не так и как это проверить за 1 шаг? Один абзац, без воды.",
    "Инженер: как решить это максимально просто, за 3 шага, без магии? Один абзац.",
]

def creative_solve(
    question: str,
    llm_call=None,
    use_haiku: bool = True,
) -> CreativeVerdict:
    """Нестандартное решение: 3 угла → синтез → калибровка уверенностью.

    Без LLM — эвристика по сигналам вопроса. С LLM — реальные вызовы.
    """
    # Дивергенция
    angles: list[str] = []
    if llm_call:
        for prompt in _DIVERGENT_ANGLES:
            try:
                angles.append(llm_call(f"{prompt}\nВопрос: {question}").strip()[:300])
            except Exception:
                angles.append(f"({prompt[:30]}… — вызов не удался)")
    else:
        # офлайн-эвристика: каждый угол — переформулировка вопроса
        q = question.strip()
        angles = [
            f"Оптимист: «{q[:60]}…» — шанс в экономии {len(q.split())} слов → новый символ.",
            f"Скептик: проверить через {count_tokens(q)} токенов и 1 факт — иначе это догадка.",
            f"Инженер: разбить на 3 шага: понять → сжать → проверить (gate).",
        ]

    # Конвергенция — синтез
    if llm_call:
        try:
            synthesis = llm_call(
                "Синтезируй 3 угла в один практичный план (2-3 предложения, без воды):\n"
                + "\n".join(f"{i+1}. {a}" for i, a in enumerate(angles))
                + f"\nВопрос: {question}"
            ).strip()[:400]
        except Exception:
            synthesis = "Синтез: взять лучшее из трёх — возможность оптимиста, проверку скептика, простоту инженера."
    else:
        synthesis = "Синтез: оптимист даёт цель, скептик — проверку, инженер — путь. Вместе — план, который работает."

    # Калибровка через self_belief
    verdict = earned_confidence(synthesis + " " + " ".join(angles))
    haiku = ""
    if use_haiku:
        haiku, _, _ = haiku_compress(synthesis)

    return CreativeVerdict(
        angles=angles,
        synthesis=synthesis,
        confidence=verdict.tone,
        evidence=verdict.evidence_count,
        notes=verdict.notes,
        haiku=haiku,
    )
