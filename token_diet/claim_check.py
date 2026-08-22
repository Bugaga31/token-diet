"""Claim Check — числовые заявления против фактического вывода.

УРОК (аудит 19.08): в README жило три числа, которые нечем подтвердить.

1. «5.8× умнее при 33% дешевле» — склейка ДВУХ несовместимых сценариев.
   5.8× берётся из DIET×5 ($0.00978), 33% — из DIET ($0.00102). Сценарий,
   дающий 5.8×, стоит в 6.2 раза ДОРОЖЕ RAW, а не на 33% дешевле.
2. `smart_multiplier.py:181` печатает «Cost is lower than RAW» литералом —
   строка не вычисляется и остаётся на экране, когда вывод её опровергает.
3. 46.6% захардкожена в green_calculator/premium_savings/cost_projection,
   но ci_check даёт 22.8% (модули) и 42.9% (e2e). Скрипта, печатающего
   46.6%, в дереве нет — а 204K тонн CO₂ посчитаны именно от неё.

Модуль отвечает на один вопрос: ЧЕМ подтверждено это число?

- extract_claims  — вытащить числовые заявления из текста (README, отчёт).
- verify_claim    — сверить заявление с фактическим выводом скрипта.
- check_coherence — поймать взаимоисключающие заявления в одном абзаце.
- audit_text      — полный проход: что подтверждено, что голословно.

Всё — чистая эвристика, 0 LLM-вызовов. Не заменяет бенчмарк: говорит,
какие числа бенчмарком НЕ покрыты.
"""

from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass, field
from urllib.parse import unquote

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Извлечение числовых заявлений
# ═══════════════════════════════════════════════════════════════════════════════

# Тип заявления определяет, чем его можно опровергнуть.
_CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # «на 33% дешевле», «33% savings», «сокращает на 46.6%»
    #
    # Словарь пришлось расширить: первая версия знала `cheaper|savings|less`,
    # но НЕ знала `lower cost` — а именно так склейка и была написана в
    # README:20 («5.8× smarter at 33% lower cost»). Гейт давал PASS на строке,
    # которую этот модуль в своём же докстринге объявляет главной целью.
    # Тест ловил синтетику «33% cheaper cost» и создавал видимость покрытия.
    # Отсюда правило: сравнительное слово (`lower`/`ниже`) + предмет цены
    # (`cost`/`price`/`spend`/`цена`/`расход`) — такое же заявление о цене,
    # как и `cheaper`, в любом порядке слов.
    ("cost_reduction", re.compile(
        r"(\d+(?:[.,]\d+)?)\s?%\s*(?:дешевле|экономи\w*|savings?|cheaper|less|"
        r"reduction|saved|сокращ\w*|меньше|off\b|"
        r"(?:lower|ниже|сниж\w*)\s*(?:cost|price|spend|spending|расход\w*|"
        r"цен\w*|трат\w*)?|"
        r"cost\s+reduction)",
        re.IGNORECASE)),
    ("cost_reduction", re.compile(
        r"(?:дешевле|экономи\w*|savings?|cheaper|saves?)\s*(?:на\s*)?(\d+(?:[.,]\d+)?)\s?%",
        re.IGNORECASE)),
    # «cuts cost by 33%», «reduces spend by 33%», «снижает расходы на 33%»,
    # «cost down 33%», «цена ниже на 33%» — глагол сокращения впереди числа.
    ("cost_reduction", re.compile(
        r"(?:cuts?|reduc\w*|lowers?|trims?|сокращ\w*|сниж\w*|урезa\w*)\s+"
        r"(?:\w+\s+){0,3}?(?:by\s*|на\s*|to\s*)?(\d+(?:[.,]\d+)?)\s?%",
        re.IGNORECASE)),
    ("cost_reduction", re.compile(
        r"(?:cost|price|spend\w*|расход\w*|цен\w*|трат\w*)\s+"
        r"(?:\w+\s+){0,2}?(?:down|lower|ниже|меньше)\s*(?:на\s*|by\s*)?"
        r"(\d+(?:[.,]\d+)?)\s?%",
        re.IGNORECASE)),
    # «5.8× умнее», «3.4x context», «5× richer»
    ("multiplier", re.compile(
        r"(\d+(?:[.,]\d+)?)\s?[×xX]\s*(?:умнее|smarter|more|richer|context|"
        r"больше|intelligence|контекст\w*)",
        re.IGNORECASE)),
    # «204,000 тонн», «340 billion liters», «204K tonnes» (в т.ч. из URL бейджа)
    ("absolute", re.compile(
        r"(\d[\d\s,.]*)\s*(?P<mag>[KMB]|тыс\.?|млн|млрд|thousand|million|billion)?"
        r"\s*(?:тонн\w*|tonnes?|tons?|литр\w*|liters?|litres?|"
        r"trees?|деревь\w*|домов|homes)",
        re.IGNORECASE)),
    # «603 tests passed», «1172 passed»
    ("test_count", re.compile(
        r"(\d+)\s*(?:tests?\s*passed|passed|тест\w*\s*(?:прошл\w*|passed))",
        re.IGNORECASE)),
    # «version 3.5.0», «v3.22.1»
    ("version", re.compile(r"v(?:ersion[-\s])?(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE)),
)

# Маркеры того, что число ЧЕМ-ТО подтверждено рядом.
_PROOF_NEARBY = re.compile(
    r"\b(измерен\w*|замерен\w*|проверен\w*|прогнан\w*|воспроизвод\w*|"
    r"measured|verified|reproduced|benchmark\w*|ci_check|pytest|"
    r"см\.\s?\w+\.py|see \w+\.py|\w+\.py:\d+)\b",
    re.IGNORECASE,
)

# Маркеры того, что число — модель/прогноз, а не замер. Это ОК, если помечено.
_MODELLED = re.compile(
    r"\b(assum\w+|предполага\w+|потенциал\w*|potential|estimate\w*|оценк\w*|"
    r"если бы|if every|projection|прогноз|модел\w+|synthetic|синтетич\w*|"
    r"simulated|симулир\w*)\b",
    re.IGNORECASE,
)


@dataclass
class Claim:
    """Одно числовое заявление, найденное в тексте."""

    kind: str            # cost_reduction / multiplier / absolute / test_count / version
    value: float
    raw: str             # как написано в тексте
    line: int            # 1-индексированная строка
    context: str
    has_proof_nearby: bool = False
    is_modelled: bool = False

    @property
    def needs_verification(self) -> bool:
        """Число без пруфа и без пометки «прогноз» — требует проверки."""
        return not self.has_proof_nearby and not self.is_modelled


def _to_float(s: str) -> float | None:
    cleaned = s.replace(" ", "").replace(",", "").replace(" ", "")
    # «3.5.0» — версия, не число
    if cleaned.count(".") > 1:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


# Множители из бейджей и прозы: «204K tonnes», «340B liters», «1.2 млрд».
_MAGNITUDES: dict[str, float] = {
    "k": 1e3, "тыс": 1e3, "thousand": 1e3,
    "m": 1e6, "млн": 1e6, "million": 1e6,
    "b": 1e9, "млрд": 1e9, "billion": 1e9,
}


def _scale(val: float, mag: str | None) -> float:
    if not mag:
        return val
    return val * _MAGNITUDES.get(mag.strip().lower().rstrip("."), 1.0)


def _decode_line(line: str) -> str:
    """Раскодировать %20 и %2F в URL бейджей.

    Без этого `tests-603%20passed` парсится как «20 passed»: настоящее
    число теряется, а на его месте появляется несуществующее. Именно так
    устаревший бейдж «603 tests» умеет прятаться от проверки.
    """
    if "%" not in line:
        return line
    try:
        return unquote(line)
    except (ValueError, UnicodeDecodeError):
        return line


def extract_claims(text: str) -> list[Claim]:
    """Вытащить числовые заявления с номером строки и признаком пруфа."""
    out: list[Claim] = []
    if not text:
        return out
    seen: set[tuple[str, float, int]] = set()

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = _decode_line(raw_line)
        proof = bool(_PROOF_NEARBY.search(line))
        modelled = bool(_MODELLED.search(line))
        for kind, rx in _CLAIM_PATTERNS:
            for m in rx.finditer(line):
                if kind == "version":
                    val = 0.0
                else:
                    val = _to_float(m.group(1)) or 0.0
                    if val == 0.0:
                        continue
                    if "mag" in rx.groupindex:
                        val = _scale(val, m.group("mag"))
                key = (kind, val, lineno)
                if key in seen:
                    continue
                seen.add(key)
                out.append(Claim(
                    kind=kind,
                    value=val,
                    raw=m.group(0).strip(),
                    line=lineno,
                    context=line.strip()[:120],
                    has_proof_nearby=proof,
                    is_modelled=modelled,
                ))
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Сверка заявления с фактическим выводом
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Verification:
    """Результат сверки одного заявления с фактом."""

    claim: Claim
    verdict: str          # confirmed / contradicted / unsupported
    actual: float | None = None      # ближайший кандидат, НЕ «единственный факт»
    tolerance: float = 1.0
    detail: str = ""
    candidates: list[float] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict == "confirmed"


# Любой процент в машинном выводе — кандидат в факты, даже без ключевого слова:
# `| 22.8% |` в колонке таблицы, `saved: 437 (42.9%)`, `TOTAL ... 22.8%`.
_ANY_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s?%")


def _fact_candidates(actual_output: str, kind: str) -> list[float]:
    """Числа нужного типа в фактическом выводе.

    Асимметрия намеренная: ЗАЯВЛЕНИЕ в прозе обязано быть явным («на 33%
    дешевле»), иначе каждый процент в тексте станет заявлением. А ФАКТ в
    выводе скрипта лежит как есть — в колонке таблицы, в скобках, без слов.
    Раньше этой асимметрии не было, и единственным «фактом» экономии
    оказывался порог гарда `need <5% savings` — вердикт выходил верным по
    неверной причине.
    """
    vals = [c.value for c in extract_claims(actual_output or "") if c.kind == kind]
    if kind == "cost_reduction":
        for line in (actual_output or "").splitlines():
            for m in _ANY_PERCENT.finditer(_decode_line(line)):
                v = _to_float(m.group(1))
                if v is not None:
                    vals.append(v)
    # порядок сохраняем, дубли убираем
    return list(dict.fromkeys(vals))


def verify_claim(claim: Claim, actual_output: str, tolerance: float = 1.0) -> Verification:
    """Сверить заявление со ВСЕМИ числами того же типа в фактическом выводе.

    tolerance — допуск в абсолютных единицах (для процентов: 1.0 = ±1 п.п.).

    Вердикт:
    - confirmed   — хоть один кандидат попал в допуск;
    - contradicted — кандидаты есть, ни один не попал (в detail: ближайший
      и полный диапазон, чтобы не выдавать случайное совпадение за факт);
    - unsupported — чисел нужного типа в выводе нет вообще.
    """
    cands = _fact_candidates(actual_output, claim.kind)
    if not cands:
        return Verification(
            claim=claim,
            verdict="unsupported",
            detail=f"в фактическом выводе нет ни одного значения типа {claim.kind}",
        )

    nearest = min(cands, key=lambda v: abs(v - claim.value))
    delta = abs(nearest - claim.value)
    span = f"диапазон {min(cands):g}–{max(cands):g}, кандидатов {len(cands)}"

    if delta <= tolerance:
        return Verification(
            claim=claim, verdict="confirmed", actual=nearest, tolerance=tolerance,
            candidates=cands, detail=f"совпало с {nearest:g} (Δ={delta:.2f}; {span})",
        )
    return Verification(
        claim=claim, verdict="contradicted", actual=nearest, tolerance=tolerance,
        candidates=cands,
        detail=(
            f"заявлено {claim.value:g}; ни одно измеренное значение не совпало, "
            f"ближайшее {nearest:g} (Δ={delta:.2f}; {span})"
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Взаимоисключающие заявления — баг «5.8× при 33% дешевле»
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Incoherence:
    """Два заявления, которые не могут быть верны одновременно."""

    left: Claim
    right: Claim
    reason: str


def check_coherence(text: str) -> list[Incoherence]:
    """Поймать склейку «сильно больше контекста» + «дешевле» в одном месте.

    Больше контекста при той же модели = больше входных токенов = дороже.
    Утверждать одновременно рост объёма в N× и снижение цены можно ТОЛЬКО
    если это разные сценарии — и тогда их нельзя писать одной фразой.
    """
    out: list[Incoherence] = []
    claims = extract_claims(text)
    by_line: dict[int, list[Claim]] = {}
    for c in claims:
        by_line.setdefault(c.line, []).append(c)

    # окно: заявления в пределах 2 строк считаем «одной мыслью»
    lines = sorted(by_line)
    for i, ln in enumerate(lines):
        window: list[Claim] = []
        for ln2 in lines[i:]:
            if ln2 - ln > 2:
                break
            window.extend(by_line[ln2])
        mults = [c for c in window if c.kind == "multiplier" and c.value > 1.5]
        cuts = [c for c in window if c.kind == "cost_reduction" and c.value > 5]
        for m in mults:
            for cut in cuts:
                out.append(Incoherence(
                    left=m, right=cut,
                    reason=(
                        f"{m.value:g}× больше контекста и −{cut.value:g}% цены "
                        "не бывает одновременно на одной модели: рост объёма = рост "
                        "входных токенов. Это два РАЗНЫХ сценария — развести их."
                    ),
                ))
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Литеральные похвалы в коде — строка, которая не вычисляется
# ═══════════════════════════════════════════════════════════════════════════════

_COMPARATIVE = r"(?:lower|cheaper|faster|better|дешевле|быстрее|лучше|улучшил\w*)"

# Сравнительное слово ловим только в роли ВЕРДИКТА о состоянии, а не как
# прилагательное в инструкции. «Cost is lower than RAW» — вердикт; «provide an
# improved response» — часть промпта. Без этого различия сканер выдавал 15
# ложных срабатываний на 2 настоящих и им никто бы не пользовался.
_HARDCODED_VERDICT = re.compile(
    rf"(?:\b(?:is|was|are|were|стал\w*|стало|получил\w*|вышло)\s+\*{{0,2}}{_COMPARATIVE}\b"
    rf"|\b{_COMPARATIVE}\*{{0,2}}\s+(?:than|чем)\b)",
    re.IGNORECASE,
)


@dataclass
class HardcodedVerdict:
    """Сравнительное утверждение, вбитое в строку вместо вычисления."""

    line: int
    text: str
    why: str


def _is_regex_arg(node: ast.AST) -> bool:
    """Строка — аргумент re.compile/re.search и т.п.: это паттерн, не текст."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return isinstance(fn.value, ast.Name) and fn.value.id == "re"
    return isinstance(fn, ast.Name) and fn.id in {"compile", "search", "match", "sub"}


def _is_keyword_list(node: ast.AST) -> bool:
    """Контейнер из трёх и более коротких строк — список ключевых слов."""
    if not isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return False
    elts = node.elts
    if len(elts) < 3:
        return False
    words = [e.value for e in elts
             if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if len(words) != len(elts):
        return False
    return sum(1 for w in words if len(w.split()) <= 3) >= len(words) - 1


def _computed(node: ast.AST) -> bool:
    """В подстановке f-строки или в тернарнике — значит, вычисляется."""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    return isinstance(node, (ast.Compare, ast.IfExp))


def find_hardcoded_verdicts(source: str) -> list[HardcodedVerdict]:
    """Найти в исходнике сравнительные утверждения-литералы.

    Ловит `smart_multiplier.py:181`: «Cost is **lower** than RAW» — f-строка
    БЕЗ единого плейсхолдера, печатается всегда, независимо от фактических
    чисел.

    Разбор идёт по AST, а не по строкам: вердикт считается вычисленным, если
    он под сравнением (пусть и на другой строке — как `cache_keepalive.py:133`
    внутри `if idle > ...`) или содержит подстановку. Отбрасываются docstring'и,
    аргументы re.compile и списки ключевых слов.
    """
    out: list[HardcodedVerdict] = []
    if not source:
        return out
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return out

    docstrings: set[int] = set()
    parents: dict[int, ast.AST] = {}
    # Константы внутри f-строк обходятся дважды (как JoinedStr и как Constant) —
    # считаем такую строку один раз, по внешнему JoinedStr.
    inside_fstring: set[int] = set()
    for parent in ast.walk(tree):
        if isinstance(parent, ast.JoinedStr):
            for v in parent.values:
                inside_fstring.add(id(v))
        if isinstance(parent, (ast.Module, ast.ClassDef, ast.FunctionDef,
                               ast.AsyncFunctionDef)):
            body = getattr(parent, "body", [])
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    def ancestors(node: ast.AST):
        cur = parents.get(id(node))
        while cur is not None:
            yield cur
            cur = parents.get(id(cur))

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(v.value for v in node.values
                           if isinstance(v, ast.Constant) and isinstance(v.value, str))
        else:
            continue

        if id(node) in docstrings or id(node) in inside_fstring:
            continue
        if not _HARDCODED_VERDICT.search(text):
            continue
        if _computed(node):
            continue

        skip = False
        for anc in ancestors(node):
            if isinstance(anc, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                break
            if _is_regex_arg(anc) or _is_keyword_list(anc) or _computed(anc):
                skip = True
                break
            if isinstance(anc, (ast.If, ast.While)) and any(
                    isinstance(n, (ast.Compare, ast.IfExp)) for n in ast.walk(anc.test)):
                skip = True
                break
        if skip:
            continue

        out.append(HardcodedVerdict(
            line=node.lineno,
            text=text.strip()[:120],
            why="сравнительное утверждение в литерале: печатается всегда, "
                "даже когда фактические числа его опровергают",
        ))
    return sorted(out, key=lambda h: h.line)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Полный аудит
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ClaimAudit:
    """Итог: сколько заявлений подтверждено, сколько голословно."""

    total: int
    unverified: list[Claim] = field(default_factory=list)
    modelled: list[Claim] = field(default_factory=list)
    proven: list[Claim] = field(default_factory=list)
    incoherent: list[Incoherence] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def trustworthy(self) -> bool:
        """Доверять можно, если нет ни голословных чисел, ни склеек."""
        return not self.unverified and not self.incoherent

    def summary(self) -> str:
        parts = [
            f"заявлений: {self.total}",
            f"подтверждено: {len(self.proven)}",
            f"помечено как прогноз: {len(self.modelled)}",
            f"голословно: {len(self.unverified)}",
        ]
        if self.incoherent:
            parts.append(f"взаимоисключающих: {len(self.incoherent)}")
        return " | ".join(parts)


def audit_text(text: str) -> ClaimAudit:
    """Полный проход по тексту: какие числа подтверждены, какие висят."""
    claims = extract_claims(text)
    audit = ClaimAudit(total=len(claims))
    for c in claims:
        if c.has_proof_nearby:
            audit.proven.append(c)
        elif c.is_modelled:
            audit.modelled.append(c)
        else:
            audit.unverified.append(c)

    audit.incoherent = check_coherence(text)

    if audit.unverified:
        audit.notes.append(
            f"{len(audit.unverified)} числ(а) без ссылки на замер — "
            "либо сослаться на скрипт, либо пометить как прогноз"
        )
    for inc in audit.incoherent:
        audit.notes.append(inc.reason)
    return audit


def claim_check_prompt() -> str:
    """Блок для system-промпта: правила обращения с числами."""
    return (
        "Каждое число в тексте должно быть подтверждено или помечено.\n"
        "- Привёл цифру — скажи, чем измерил (скрипт, тест, замер).\n"
        "- Не измерял — пиши «прогноз» или «оценка», а не факт.\n"
        "- Не склеивай числа из разных сценариев в одну фразу.\n"
        "- Сравнительный вывод («дешевле», «быстрее») вычисляй, не вписывай строкой."
    )
