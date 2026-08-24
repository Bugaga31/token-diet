"""Metacognition — думать о том, как думаешь (2026).

Свежая техника (LessWrong, февраль 2026): человеческие метакогнитивные навыки
снижают «slop» — когда модель уверенно несёт чушь. Модель, которая следит за
СВОЕЙ уверенностью и ловит моменты неопределённости, ошибается реже.

Три вещи, которых в арсенале не было:
1. Оценка уверенности ДО ответа — калибровка по сложности вопроса.
2. Флаги неопределённости ПОСЛЕ ответа — хеджи («наверное», «я думаю»),
   пере-уверенность («точно», «100%») и пустые ответы («не знаю»).
3. Self-critique (back-prompting) — детерминированная критика собственного
   ответа: полнота, конкретность, противоречия, голословность.

Всё — чистая эвристика, 0 LLM-вызовов, работает на ЛЮБОЙ модели и языке.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Оценка уверенности ДО ответа (калибровка по сложности)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DifficultyEstimate:
    """Детерминированная оценка сложности вопроса."""
    score: float                      # 0..1, чем выше — тем сложнее
    reasons: list[str] = field(default_factory=list)
    level: str = "simple"             # simple / moderate / hard


_WHY_HOW = re.compile(r"\b(почему|зачем|как|why|how|объясни|обоснуй|explain)\b", re.IGNORECASE)
_WHAT_WHO = re.compile(r"\b(что такое|кто|what is|who is|когда|where|который)\b", re.IGNORECASE)
_COMPARE = re.compile(r"\b(сравни|vs|против|лучше|хуже|отличие|разница|compare|versus|better)\b", re.IGNORECASE)
_NUMBERS = re.compile(r"\d+[\d\s.,]*\s?(руб|₽|доллар|usd|%|процент|акци|цен|цена)")
_MULTI = re.compile(r"[?]{1,}|[;]{1,}|\b(и еще|также|плюс|кроме того|во-вторых|в-третьих|затем)\b", re.IGNORECASE)
_DOMAIN_HARD = re.compile(
    r"\b(докажи|proof|теорема|алгоритм|сложность|оптимиз|рефактор|безопасност|"
    r"уязвимост|анализ рынка|прогноз|инвестиц|диагноз|legal|юридич)\b",
    re.IGNORECASE,
)


def difficulty(question: str) -> DifficultyEstimate:
    """Оценить сложность вопроса до ответа — чтобы знать, сколько думать.

    Чем выше score, тем ниже априорная уверенность и тем сильнее стоит
    включить перепроверку (метакогнитивный «переключатель»).
    """
    reasons: list[str] = []
    score = 0.0
    text = question or ""

    if len(text) < 15:
        reasons.append("короткий вопрос")
    else:
        score += 0.15
        reasons.append("развёрнутый вопрос")

    if _WHY_HOW.search(text):
        score += 0.25
        reasons.append("требует объяснения (почему/как) — глубже факта")
    elif _WHAT_WHO.search(text):
        reasons.append("фактологический вопрос (что/кто) — проще")

    if _COMPARE.search(text):
        score += 0.25
        reasons.append("сравнение нескольких сущностей")
    if _NUMBERS.search(text):
        score += 0.2
        reasons.append("есть числа/цены — легко соврать, нужна проверка")
    if _MULTI.search(text):
        score += 0.15
        reasons.append("составной вопрос (несколько частей)")
    if _DOMAIN_HARD.search(text):
        score += 0.2
        reasons.append("сложная доменная область")

    score = round(min(score, 1.0), 3)
    level = "hard" if score >= 0.55 else ("moderate" if score >= 0.25 else "simple")
    return DifficultyEstimate(score=score, reasons=reasons, level=level)


def prior_confidence(question: str) -> float:
    """Априорная уверенность = 1 - сложность. Для сложных вопросов — ниже."""
    return round(1.0 - difficulty(question).score, 3)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Флаги неопределённости ПОСЛЕ ответа
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class UncertaintyFlag:
    kind: str       # hedge / overconfident / evasive
    token: str
    context: str    # 40 символов вокруг токена


_HEDGE = re.compile(
    r"\b(наверное|возможно|вероятно|кажется|похоже|скорее всего|я думаю|"
    r"мне кажется|наверняка не|не уверен|не уверена|видимо|предположительно|"
    r"probably|maybe|perhaps|likely|might|could be|i think|i guess|"
    r"i'm not sure|not certain|approximately|roughly)\b",
    re.IGNORECASE,
)
_OVERCONFIDENT = re.compile(
    r"\b(точно|100%|стопроцентно|гарантированно|абсолютно|безусловно|наверняка|"
    r"однозначно|certainly|definitely|absolutely|guaranteed|100% sure|"
    r"without a doubt|no doubt)\b",
    re.IGNORECASE,
)
_EVASIVE = re.compile(
    r"\b(не знаю|не имею данных|нет данных|недостаточно информации|затрудняюсь|"
    r"не могу сказать|i don't know|no data|insufficient information|cannot say)\b",
    re.IGNORECASE,
)


def _ctx(text: str, start: int, end: int) -> str:
    lo = max(0, start - 20)
    hi = min(len(text), end + 20)
    return text[lo:hi].replace("\n", " ").strip()


def flag_uncertainty(answer: str) -> list[UncertaintyFlag]:
    """Найти в ответе маркеры неопределённости трёх типов:

    - hedge: «наверное», «возможно» — модель сама не уверена
    - overconfident: «точно», «100%» — пере-уверенность, главный признак slop
    - evasive: «не знаю», «нет данных» — уход от ответа
    """
    flags: list[UncertaintyFlag] = []
    text = answer or ""
    for kind, rx in (("hedge", _HEDGE), ("overconfident", _OVERCONFIDENT), ("evasive", _EVASIVE)):
        for m in rx.finditer(text):
            flags.append(UncertaintyFlag(kind=kind, token=m.group(), context=_ctx(text, m.start(), m.end())))
    return flags


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Self-critique (back-prompting) — детерминированная критика ответа
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Critique:
    issue: str
    severity: str   # info / warn / critical
    hint: str


@dataclass
class CritiqueResult:
    critiques: list[Critique] = field(default_factory=list)
    score: float = 1.0  # 1.0 = идеально, ниже = есть замечания

    @property
    def is_clean(self) -> bool:
        return not any(c.severity == "critical" for c in self.critiques)

    def render(self) -> str:
        if not self.critiques:
            return "Критика: замечаний нет."
        lines = [f"[{c.severity}] {c.issue} — {c.hint}" for c in self.critiques]
        return "Критика:\n" + "\n".join(lines)


_VAGUE = re.compile(
    r"\b(и так далее|и т\.д\.|и т\.п\.|в общем|короче|всякое|разное|ну такое|"
    r"etc|and so on|stuff|things|whatever|somehow)\b",
    re.IGNORECASE,
)


def _numbers_in(s: str) -> list[float]:
    return [float(m) for m in re.findall(r"\d+(?:[.,]\d+)?", s)]


def self_critique(question: str, answer: str) -> CritiqueResult:
    """Прокритиковать собственный ответ по 4 осям (без LLM):

    1. Полнота — затронуты ли все части составного вопроса
    2. Конкретность — нет ли воды («и т.д.», «короче»)
    3. Противоречия — не конфликтуют ли числа в ответе с вопросом
    4. Голословность — есть ли вывод без рассуждения
    """
    res = CritiqueResult()
    q, a = (question or ""), (answer or "").strip()

    if not a:
        res.critiques.append(Critique("пустой ответ", "critical", "ответь по существу"))
        res.score = 0.0
        return res

    # 1. Полнота: части составного вопроса
    q_parts = [p for p in re.split(r"[?;]", q) if len(p.strip()) > 5]
    if len(q_parts) > 1:
        missing = sum(1 for p in q_parts if len(a) < len(p) * 1.5)
        if missing:
            res.critiques.append(Critique(
                f"не раскрыты {missing} из {len(q_parts)} частей вопроса",
                "warn", "ответь по каждой части отдельно",
            ))
            res.score -= 0.2

    # 2. Конкретность
    vague = _VAGUE.findall(a)
    if vague:
        res.critiques.append(Critique(
            f"вода: {', '.join(vague[:4])}", "warn",
            "замени общими слова конкретикой",
        ))
        res.score -= 0.15

    # 3. Противоречия чисел
    q_nums = _numbers_in(q)
    a_nums = _numbers_in(a)
    if q_nums and a_nums:
        q_min, q_max = min(q_nums), max(q_nums)
        for n in a_nums:
            if n < q_min * 0.9 or n > q_max * 1.1:
                res.critiques.append(Critique(
                    f"число {n} выбивается из диапазона вопроса [{q_min}..{q_max}]",
                    "critical", "перепроверь расчёт",
                ))
                res.score -= 0.3

    # 4. Голословность: короткий ответ на сложный вопрос
    if difficulty(q).score >= 0.5 and len(a) < 60:
        res.critiques.append(Critique(
            "сложный вопрос, но ответ без рассуждения",
            "warn", "покажи ход мысли, а не только вывод",
        ))
        res.score -= 0.15

    res.score = round(max(res.score, 0.0), 3)
    return res


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Сводный метакогнитивный вердикт + промпт
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MetaVerdict:
    confidence: float          # итоговая уверенность 0..1
    should_double_check: bool
    reasons: list[str]


def calibrated_verdict(question: str, answer: str) -> MetaVerdict:
    """Соединить всё: априорная уверенность + флаги + критика.

    Возвращает честный вердикт — стоит ли перепроверить ответ.
    """
    conf = prior_confidence(question)
    reasons: list[str] = []

    flags = flag_uncertainty(answer)
    if any(f.kind == "evasive" for f in flags):
        conf -= 0.3
        reasons.append("ответ уклончивый — модель не дала ответа")
    if any(f.kind == "overconfident" for f in flags):
        conf -= 0.15
        reasons.append("пере-уверенность («точно/100%») — признак slop")
    if any(f.kind == "hedge" for f in flags):
        conf -= 0.1
        reasons.append("много хеджирования — модель сама сомневается")

    crit = self_critique(question, answer)
    if crit.critiques:
        conf -= 0.1 * len(crit.critiques)
        reasons.extend(f"критика: {c.issue}" for c in crit.critiques[:3])

    conf = round(max(conf, 0.0), 3)
    return MetaVerdict(
        confidence=conf,
        should_double_check=conf < 0.6 or not crit.is_clean,
        reasons=reasons,
    )


def metacognition_prompt(question: str) -> str:
    """Инструкция, которую инжектим в промпт — модель начинает следить за собой.

    Даёт +точность на сложных вопросах бесплатно (это промпт-инженерия,
    не дополнительный вызов).
    """
    d = difficulty(question)
    if d.level == "simple":
        return ""
    return (
        "Прежде чем ответить:\n"
        "1) оцени свою уверенность по 10-балльной шкале;\n"
        "2) если уверенность ниже 7 — скажи об этом прямо и отметь, что неясно;\n"
        "3) не используй слова «точно» и «100%», если не можешь это доказать;\n"
        "4) покажи ход рассуждения, а не только вывод."
    )
