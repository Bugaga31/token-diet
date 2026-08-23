"""Self-Belief — говорить твёрдо там, где есть доказательство.

УРОК (аудит 19.08): в этом проекте самоуверенность уже стоила денег.
README обещал «5.8× умнее при 33% дешевле» — а строка «Cost is lower than
RAW» была вбита литералом и никогда не вычислялась. Прокси отдавал 200 OK
с заглушкой вместо ответа модели. Уверенность БЕЗ проверки — это не сила,
это тот самый slop, который ловит metacognition.

Поэтому здесь уверенность ЗАРАБАТЫВАЕТСЯ, а не назначается:

1. strip_apology — убрать извинения и самоуничижение («извините», «я всего
   лишь», «возможно я ошибаюсь»). Они не несут информации и жгут токены.
2. firm_up — убрать хедж ТОЛЬКО в предложениях с доказательством (число,
   файл:строка, «проверил», «замерил»). Без доказательства хедж остаётся:
   это честность, а не слабость.
3. persist — после ошибки не сдаваться: дать следующий приём вместо
   «я не могу это сделать».
4. earned_confidence — итоговый вердикт: твёрдо / твёрдо-с-оговоркой /
   нужна проверка. Основан на плотности доказательств, не на желании.

Всё — чистая эвристика, 0 LLM-вызовов, работает на любой модели и языке.
Дополняет metacognition: тот ловит пере-уверенность, этот убирает
недо-уверенность. Вместе — калибровка.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Извинения и самоуничижение — вычистить полностью
# ═══════════════════════════════════════════════════════════════════════════════

# Извинение целым предложением: «Извините за путаницу.» — режется вместе с точкой.
_APOLOGY_SENTENCE = re.compile(
    r"[^.!?\n]*\b(извин\w*|прошу прощения|виноват\w*|сожалею|мо[яй]\s+ошибка|"
    r"sorry|apolog\w+|my (?:bad|mistake|apologies))\b[^.!?\n]*[.!?]*",
    re.IGNORECASE,
)

# Самоуничижение внутри предложения: вырезается фраза, смысл остаётся.
_SELF_DEPRECATION = re.compile(
    r"\b(я всего лишь\s+\w+|я лишь\s+\w+|я просто\s+\w+|как (?:ии|ai|модель)[,\s]|"
    r"возможно,?\s+я (?:ошиба\w+|не прав\w*)|я мог\w*\s+ошиб\w+|"
    r"не судите строго|надеюсь,?\s+это поможет|"
    r"i'?m just an? \w+|as an ai(?: model)?[,\s]|i (?:may|might) be wrong|"
    r"i could be wrong|hope this helps|please forgive)\b[,\s]*",
    re.IGNORECASE,
)

# Просьба-о-разрешении-думать: «Дайте мне подумать...» — пустой ход.
_PERMISSION_SEEKING = re.compile(
    r"\b(дайте мне подумать|позвольте (?:мне )?подумать|мне нужно подумать|"
    r"let me think(?: about (?:this|it))?|i(?:'| a)m not sure (?:if|whether) i can|"
    r"i'?ll try my best|i'?ll do my best)\b[.,!\s]*",
    re.IGNORECASE,
)


def strip_apology(text: str) -> str:
    """Убрать извинения, самоуничижение и просьбы-о-разрешении.

    Не трогает содержательную часть: «Извините, файл не найден» → «Файл не найден»
    остаётся невозможным (режется всё предложение), поэтому извинение-с-фактом
    надо писать двумя предложениями. Так честнее: факт не должен зависеть
    от извинения.
    """
    out = text or ""
    out = _APOLOGY_SENTENCE.sub("", out)
    out = _SELF_DEPRECATION.sub("", out)
    out = _PERMISSION_SEEKING.sub("", out)
    # схлопнуть дыры, оставшиеся после вырезания
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"(?m)^[ \t]+", "", out)
    # запятая/точка, оставшаяся в начале строки после вырезания фразы
    out = re.sub(r"(?m)^[,;:]\s*", "", out)
    # заглавная буква после вырезанного начала предложения
    out = re.sub(r"(?m)^([a-zа-яё])", lambda m: m.group(1).upper(), out)
    return out.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Хедж — убрать ТОЛЬКО там, где есть доказательство
# ═══════════════════════════════════════════════════════════════════════════════

# Признаки доказательства в предложении. Если есть — хедж лишний.
_EVIDENCE = re.compile(
    r"("
    r"\b\w+\.(?:py|js|ts|md|json|yaml|yml|toml|sh|rs|go|java|c|cpp|h):\d+"  # file.py:42
    r"|\b\d+([.,]\d+)?\s?(%|мс|ms|с\b|sec|байт|byte|kb|mb|gb|токен|token|руб|₽|\$)"
    r"|\b(проверил|замерил|измерил|прогнал|воспроизвёл|воспроизвел|подтвердил|"
    r"тест[ыи]? (?:прошли|зелён\w+)|exit=?\s?\d+|"
    r"verified|measured|reproduced|confirmed|benchmarked)\b"
    r"|\b(показал|вернул|выдал|отдал|returns?|returned|output)\b.{0,30}\d"
    r")",
    re.IGNORECASE,
)

# Хедж-обороты, которые можно снять без потери смысла.
_REMOVABLE_HEDGE = re.compile(
    r"\b(мне кажется,?\s*|я думаю,?\s*|кажется,?\s*|похоже,?\s*|"
    r"вероятно,?\s*|наверное,?\s*|по-видимому,?\s*|видимо,?\s*|"
    r"как будто\s*|что-то вроде\s*|как бы\s*|"
    r"i think,?\s*|i believe,?\s*|it seems (?:that|like)?\s*|"
    r"it (?:would )?appears?(?: that)?\s*|probably,?\s*|perhaps,?\s*|"
    r"sort of\s*|kind of\s*)",
    re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class FirmUpResult:
    """Результат ужесточения тона."""

    text: str
    hedges_removed: int = 0
    hedges_kept: int = 0          # оставлены — доказательства нет, это честно
    sentences_touched: int = 0

    @property
    def honest(self) -> bool:
        """True, если хоть один хедж СОХРАНЁН — значит модуль не врёт."""
        return self.hedges_kept > 0


def firm_up(text: str) -> FirmUpResult:
    """Снять хедж в предложениях с доказательством, оставить — без.

    Это ключевая функция модуля и главная защита от slop. «Вероятно, 42%»
    без пруфа остаётся «вероятно» — потому что мы правда не знаем.
    «Вероятно, 42% (замерил)» → «42% (замерил)» — здесь хедж был лишним.
    """
    src = text or ""
    if not src.strip():
        return FirmUpResult(text="")

    parts = _SENTENCE_SPLIT.split(src)
    rebuilt: list[str] = []
    removed = kept = touched = 0

    for part in parts:
        if not part.strip():
            continue
        hedges = _REMOVABLE_HEDGE.findall(part)
        if not hedges:
            rebuilt.append(part)
            continue
        if _EVIDENCE.search(part):
            new_part = _REMOVABLE_HEDGE.sub("", part)
            new_part = re.sub(r"\s{2,}", " ", new_part).strip()
            new_part = re.sub(r"^([a-zа-яё])", lambda m: m.group(1).upper(), new_part)
            removed += len(hedges)
            touched += 1
            rebuilt.append(new_part)
        else:
            kept += len(hedges)
            rebuilt.append(part)

    joined = " ".join(p.strip() for p in rebuilt if p.strip())
    return FirmUpResult(
        text=joined,
        hedges_removed=removed,
        hedges_kept=kept,
        sentences_touched=touched,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Не сдаваться — после ошибки дать следующий приём
# ═══════════════════════════════════════════════════════════════════════════════

# Формулировки сдачи. Ловим их, чтобы заменить на конкретный следующий шаг.
_GIVING_UP = re.compile(
    r"\b(не могу (?:это |этого )?(?:сделать|выполнить|помочь)|"
    r"это невозможно|ничего не (?:получается|выйдет)|у меня нет способа|"
    r"сдаюсь|бесполезно|не получилось,? извин\w*|"
    r"i can'?t (?:do|help)|i'?m unable to|it'?s impossible|"
    r"there'?s no way|i give up|nothing (?:i can do|works))\b",
    re.IGNORECASE,
)

# Следующий приём по типу препятствия. Порядок = приоритет проверки.
_NEXT_MOVE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(permission denied|access denied|EACCES|отказано в доступе|not permitted)\b", re.I),
     "проверить владельца и права (ls -la), при необходимости запросить доступ у пользователя"),
    (re.compile(r"\b(no such file|not found|ENOENT|не найден|нет такого файла)\b", re.I),
     "найти файл поиском по имени (find/glob) — путь мог измениться"),
    (re.compile(r"\b(ModuleNotFoundError|ImportError|No module named)\b", re.I),
     "проверить, объявлена ли зависимость, и импортировать под try/except"),
    (re.compile(r"\b(timeout|timed out|таймаут|deadline exceeded|exit=?124)\b", re.I),
     "поднять таймаут или запустить в фоне и опрашивать результат"),
    (re.compile(r"\b(connection refused|network is unreachable|ECONNREFUSED|DNS)\b", re.I),
     "проверить, поднят ли сервис и слушает ли он нужный порт"),
    (re.compile(r"\b(401|403|unauthorized|forbidden|invalid token|неверный токен)\b", re.I),
     "проверить, что ключ живой и подхватился из окружения — не истёк ли"),
    (re.compile(r"\b(429|rate limit|too many requests)\b", re.I),
     "подождать и повторить с экспоненциальной задержкой"),
    (re.compile(r"\b(syntax ?error|SyntaxError|invalid syntax|parse error)\b", re.I),
     "прочитать точную строку из трейсбека — ошибка там, а не в логике"),
    (re.compile(r"\b(AssertionError|test failed|тест(ы)? (?:упал|провалил)\w*|FAILED)\b", re.I),
     "прочитать сам ассерт: ожидание и факт — тест уже показал, что не так"),
    (re.compile(r"\b(KeyError|IndexError|AttributeError|TypeError|NoneType)\b", re.I),
     "напечатать фактическую форму данных перед падением — предположение о структуре неверно"),
)

_FALLBACK_MOVE = "сузить задачу: воспроизвести минимальным примером и посмотреть на фактический вывод"


@dataclass
class PersistPlan:
    """План «не сдаваться»: что дальше, сколько попыток осталось."""

    next_move: str
    attempt: int
    attempts_left: int
    should_retry: bool
    escalate: bool = False       # True → пора звать пользователя, а не молча долбить
    diagnosis: str = ""

    def as_line(self) -> str:
        if self.escalate:
            return f"попытка {self.attempt}: исчерпано — доложить пользователю факты и спросить направление"
        return f"попытка {self.attempt}: {self.next_move}"


def persist(error_text: str, attempt: int = 1, max_attempts: int = 3) -> PersistPlan:
    """Из текста ошибки получить конкретный следующий приём.

    Не «попробуй ещё раз», а именно ЧТО сделать. После max_attempts —
    escalate: честно доложить пользователю, а не имитировать прогресс.
    """
    err = error_text or ""
    diagnosis = ""
    move = _FALLBACK_MOVE
    for rx, hint in _NEXT_MOVE:
        m = rx.search(err)
        if m:
            diagnosis = m.group(0)
            move = hint
            break

    left = max(0, max_attempts - attempt)
    return PersistPlan(
        next_move=move,
        attempt=attempt,
        attempts_left=left,
        should_retry=left > 0,
        escalate=left == 0,
        diagnosis=diagnosis,
    )


def rewrite_surrender(text: str, error_text: str = "", attempt: int = 1) -> str:
    """Заменить формулировку сдачи на конкретный следующий шаг."""
    src = text or ""
    if not _GIVING_UP.search(src):
        return src.strip()
    plan = persist(error_text or src, attempt=attempt)
    replacement = (
        "исчерпал известные приёмы — нужны данные от пользователя"
        if plan.escalate
        else f"следующий приём: {plan.next_move}"
    )
    # count=1: несколько формулировок сдачи в одном тексте — это ОДНА сдача.
    # Подставлять приём в каждую даёт дубли («приём: X, приём: X»).
    out = _GIVING_UP.sub(replacement, src, count=1)
    # остальные формулировки сдачи просто убираем вместе с висящей пунктуацией
    out = _GIVING_UP.sub("", out)
    out = re.sub(r"\s*,\s*\.", ".", out)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Заработанная уверенность — итоговый вердикт
# ═══════════════════════════════════════════════════════════════════════════════

_UNVERIFIED_BOAST = re.compile(
    r"\b(всё (?:работает|отлично|готово|идеально)|полностью готов\w*|"
    r"идеально работает|без(?:о)? (?:единой )?ошиб\w+|"
    r"everything works|all good|fully (?:working|done)|works perfectly|flawless)\b",
    re.IGNORECASE,
)

# Пере-уверенные обороты, внутри которых число НЕ является измерением:
# «точно 100%», «гарантированно 100% работает». Такое «доказательство»
# надо снять до подсчёта, иначе slop получает твёрдый тон.
_FAKE_EVIDENCE = re.compile(
    r"\b(точно|стопроцентно|гарантированно|абсолютно|безусловно|однозначно|"
    r"certainly|definitely|absolutely|guaranteed|without a doubt)\b[\s,]*"
    r"(100\s?%|\d{2,3}\s?%)?",
    re.IGNORECASE,
)
# Одинокое «100%» как усилитель, а не замер.
_BARE_HUNDRED = re.compile(r"(?<![\d.,])100\s?%(?!\s*(?:of|от|из))", re.IGNORECASE)

_TONE = ("firm", "firm_with_caveat", "needs_proof")


@dataclass
class BeliefVerdict:
    """Сколько уверенности ЗАСЛУЖЕНО этим текстом."""

    tone: str                       # firm / firm_with_caveat / needs_proof
    evidence_count: int
    unverified_boasts: int
    apologies_removed: int
    hedges_removed: int
    hedges_kept: int
    text: str
    overconfident_markers: int = 0   # «точно», «100%» без замера
    notes: list[str] = field(default_factory=list)

    @property
    def earned(self) -> bool:
        """Твёрдый тон заслужен только при доказательствах и без голых похвал."""
        return self.tone == "firm"


def earned_confidence(text: str) -> BeliefVerdict:
    """Полный проход: вычистить извинения, ужесточить где есть пруф, оценить тон.

    Возвращает tone:
      firm              — есть доказательства, нет голословных похвал
      firm_with_caveat  — доказательства есть, но не на всё
      needs_proof       — доказательств нет: тон остаётся осторожным

    Голословное «всё работает» СНИЖАЕТ вердикт. Именно так в этом проекте
    появилось «5.8× при 33% дешевле» — похвала без вычисления.
    """
    src = text or ""
    before_apologies = len(_APOLOGY_SENTENCE.findall(src)) + len(_SELF_DEPRECATION.findall(src))

    cleaned = strip_apology(src)
    firmed = firm_up(cleaned)

    # Считаем доказательства по тексту, из которого убраны ПСЕВДО-пруфы:
    # «точно 100%» — это усилитель, а не измерение. Иначе пере-уверенный
    # оборот сам себе выдаёт мандат на твёрдый тон.
    for_scoring = _FAKE_EVIDENCE.sub(" ", firmed.text)
    for_scoring = _BARE_HUNDRED.sub(" ", for_scoring)

    evidence = len(_EVIDENCE.findall(for_scoring))
    boasts = len(_UNVERIFIED_BOAST.findall(firmed.text))
    overconfident = len(_FAKE_EVIDENCE.findall(firmed.text))

    notes: list[str] = []
    if boasts and not evidence:
        tone = "needs_proof"
        notes.append(f"{boasts} голословн. похвал(ы) без единого доказательства — заменить на факты или убрать")
    elif overconfident and not evidence:
        tone = "needs_proof"
        notes.append(
            f"{overconfident} пере-уверенн. оборот(ов) («точно», «100%») без замера — "
            "это усилитель, а не доказательство"
        )
    elif evidence == 0:
        tone = "needs_proof"
        notes.append("доказательств нет: числа, file:line или «проверил» — тон остаётся осторожным")
    elif firmed.hedges_kept > 0:
        tone = "firm_with_caveat"
        notes.append(f"{firmed.hedges_kept} хедж(ей) сохранены — там пруфа нет, и это честно")
    else:
        tone = "firm"

    if boasts and evidence:
        notes.append(f"{boasts} похвал(ы) рядом с пруфом — сослаться на конкретный замер")

    return BeliefVerdict(
        tone=tone,
        evidence_count=evidence,
        unverified_boasts=boasts,
        apologies_removed=before_apologies,
        hedges_removed=firmed.hedges_removed,
        hedges_kept=firmed.hedges_kept,
        text=firmed.text,
        overconfident_markers=overconfident,
        notes=notes,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Промпт — вставляется в system, ~60 токенов, кешируется
# ═══════════════════════════════════════════════════════════════════════════════

_BELIEF_PROMPT = """Говори твёрдо там, где проверил, и осторожно там, где нет.
- Не извиняйся и не принижай себя. Факт важнее извинения.
- Проверил — говори прямо: «сделал X, замерил Y». Без «кажется» и «вроде».
- Не проверил — так и скажи. Честное «не знаю» сильнее уверенной догадки.
- Ошибка — не повод сдаться: назови следующий приём и применяй.
- Никогда не рапортуй об успехе, которого не видел. Это не уверенность, а ложь."""


def belief_prompt() -> str:
    """Готовый блок для system-промпта: заслуженная уверенность."""
    return _BELIEF_PROMPT


def confident_rewrite(text: str, error_text: str = "", attempt: int = 1) -> str:
    """One-liner: вычистить извинения, ужесточить по пруфу, убрать сдачу."""
    out = rewrite_surrender(text, error_text=error_text, attempt=attempt)
    return earned_confidence(out).text
