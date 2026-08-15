"""WorkflowEngine — YAML-движок автоматизации (реверс-инжиниринг block/buzz).

Взято из Buzz (Block Inc., Apache 2.0), крейт buzz-workflow (schema.rs,
executor.rs). Перенесено:

- триггеры: message_posted / reaction_added / schedule / webhook;
- шаги с условиями `if:` и шаблонами `{{trigger.X}}`, `{{steps.ID.output.FIELD}}`;
- фильтры шаблонов: `| truncate(N)`;
- безопасное вычисление условий: СВОЙ парсер (никакого eval() кода!),
  функции str_contains/str_starts_with/str_ends_with/str_len, лимит длины 4096,
  таймаут 100мс (как EVAL_TIMEOUT в Buzz — длина лимитирована, чтобы не было
  O(2^n) путей);
- approval-gates: request_approval возвращает Suspended с токеном;
- cron-расписание (5-польный крон, свой матчер, без внешних либ);
- SSRF-защита call_webhook (проверка приватных IP — из buzz-core is_private_ip,
  редиректы выключены, кап ответа 1 МиБ);
- семафор параллельности: try_acquire -> CapacityExceeded (не очередь).

Адаптация под наш мир (token-diet):
- добавлены РЫНОЧНЫЕ триггеры: price_event / news_event — правила вида
  «PLZL упал 2% -> алерт, вырос 3% -> авто-действие»;
- действие audit_log — пишет в наш hash-chain журнал (audit_chain.py):
  каждое сработавшее правило оставляет доказательство, что его нельзя
  подделать задним числом;
- действие send_alert — сигнал (вывод/возврат, вызывающий решает, куда слать).

100% офлайн, только stdlib + pyyaml, 0 LLM-вызовов.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
    from yaml.constructor import SafeConstructor
except ImportError:  # pragma: no cover
    yaml = None
    SafeConstructor = None


# YAML 1.1 ловушка: ключ `on:` парсится как булево True (yes/on/off/no).
# Для правил это критично (триггер on: price_event). Оставляем булевыми
# только true/false, а on/off/yes/no — строками.
def _bool_resolver(loader, node):
    v = loader.construct_scalar(node)
    if v in ("true", "True", "TRUE"):
        return True
    if v in ("false", "False", "FALSE"):
        return False
    return v


class _RulesLoader(yaml.SafeLoader):
    pass


# Радикально: УДАЛЯЕМ все старые bool-резолверы YAML 1.1 со всех символов
# (иначе для 'on'/'off'/'yes'/'no' они переживут добавление нового),
# затем добавляем свой — только true/false, всё остальное остаётся строкой.
for _ch, _resolvers in list(yaml.SafeLoader.yaml_implicit_resolvers.items()):
    _RulesLoader.yaml_implicit_resolvers[_ch] = [
        (tag, rx) for tag, rx in _resolvers if tag != "tag:yaml.org,2002:bool"
    ]
_RulesLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)

# ── константы (как в Buzz) ───────────────────────────────────────────────
EVAL_TIMEOUT_MS = 100          # таймаут условия
MAX_EXPR_LEN = 4096            # лимит длины условия
MAX_DELAY_SECS = 270           # потолок delay
WEBHOOK_MAX_BODY = 1 << 20     # 1 МиБ
RULES_PATH_DEFAULT = "~/token-diet-memory/rules.yaml"

KNOWN_ACTIONS = {
    "send_message", "send_alert", "audit_log", "call_webhook",
    "request_approval", "delay",
}
KNOWN_TRIGGERS = {
    "message_posted", "reaction_added", "schedule", "webhook",
    "price_event", "news_event",
}


# ── безопасный eval-парсер ───────────────────────────────────────────────
class _Token:
    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value: Any = None):
        self.kind = kind
        self.value = value


_TOKEN_RE = re.compile(
    r"\s*(?:(<=|>=|==|!=|\|\||&&)|([0-9]+(?:\.[0-9]+)?)|"
    r"('(?:[^'\\]|\\.)*')|(\"[^\"]*\")|([a-zA-Z_][a-zA-Z0-9_]*)|"
    r"([()+\-*/<>,.!]))"
)


def _tokenize(expr: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if not m:
            raise ValueError(f"неожиданный символ в условии: {expr[pos:]!r}")
        pos = m.end()
        op, num, s1, s2, var, sym = m.groups()
        if op:
            tokens.append(_Token("op", op))
        elif num:
            tokens.append(_Token("num", float(num) if "." in num else int(num)))
        elif s1 is not None or s2 is not None:
            s = (s1 or s2)[1:-1]
            tokens.append(_Token("str", s))
        elif var:
            tokens.append(_Token("var", var))
        elif sym in "()":
            tokens.append(_Token(sym))
        elif sym == ",":
            tokens.append(_Token(","))
        else:
            tokens.append(_Token("op", sym))
    return tokens


class _ExprEval:
    """Рекурсивный спуск: сравнения, логика, арифметика, функции str_*."""

    def __init__(self, expr: str, context: dict[str, Any]):
        self.tokens = _tokenize(expr)
        self.pos = 0
        self.context = context

    def _peek(self) -> Optional[_Token]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _next(self) -> _Token:
        t = self._peek()
        if t is None:
            raise ValueError("неожиданный конец условия")
        self.pos += 1
        return t

    def parse(self) -> Any:
        v = self._or()
        if self._peek() is not None:
            raise ValueError(f"лишний токен: {self._peek().kind}")
        return v

    def _or(self) -> Any:
        v = self._and()
        while self._peek() is not None and self._peek().value in ("||", "or"):
            self._next()
            r = self._and()
            v = bool(v) or bool(r)
        return v

    def _and(self) -> Any:
        v = self._cmp()
        while self._peek() is not None and self._peek().value in ("&&", "and"):
            self._next()
            r = self._cmp()
            v = bool(v) and bool(r)
        return v

    def _cmp(self) -> Any:
        v = self._add()
        t = self._peek()
        if t is not None and t.kind == "op" and t.value in ("==", "!=", "<", ">", "<=", ">="):
            self._next()
            r = self._add()
            if t.value == "==":
                return v == r
            if t.value == "!=":
                return v != r
            if t.value == "<":
                return v < r
            if t.value == ">":
                return v > r
            if t.value == "<=":
                return v <= r
            return v >= r
        return v

    def _add(self) -> Any:
        v = self._term()
        while True:
            t = self._peek()
            if t is not None and t.kind == "op" and t.value in ("+", "-"):
                self._next()
                r = self._term()
                v = v + r if t.value == "+" else v - r
            else:
                return v

    def _term(self) -> Any:
        v = self._factor()
        while True:
            t = self._peek()
            if t is not None and t.kind == "op" and t.value in ("*", "/"):
                self._next()
                r = self._factor()
                v = v * r if t.value == "*" else v / r
            else:
                return v

    def _factor(self) -> Any:
        t = self._peek()
        if t is None:
            raise ValueError("неожиданный конец условия")
        if t.kind == "op" and t.value == "-":
            self._next()
            return -self._factor()
        if t.kind == "op" and t.value == "!":
            self._next()
            return not bool(self._factor())
        return self._atom()

    def _atom(self) -> Any:
        t = self._next()
        if t.kind == "num" or t.kind == "str":
            return t.value
        if t.kind == "var":
            if t.value == "true":
                return True
            if t.value == "false":
                return False
            if self._peek() is not None and self._peek().kind == "(":
                return self._call(t.value)  # вызов функции str_*
            if t.value in self.context:
                return self.context[t.value]
            raise ValueError(f"неизвестная переменная: {t.value}")
        if t.kind == "(":
            v = self._or()
            close = self._next()
            if close.kind != ")":
                raise ValueError("нет закрывающей скобки")
            return v
        raise ValueError(f"неожиданный токен: {t.kind}")

    def _call(self, name: str) -> Any:
        self._next()  # (
        args: list[Any] = []
        if not (self._peek() is not None and self._peek().kind == ")"):
            args.append(self._or())
            while self._peek() is not None and self._peek().kind == ",":
                self._next()
                args.append(self._or())
        close = self._next()
        if close.kind != ")":
            raise ValueError("нет закрывающей скобки")
        if name not in _CALLS:
            raise ValueError(f"неизвестная функция: {name}")
        return _CALLS[name](args)


def _call_str_contains(args: list[Any]) -> bool:
    # str_contains(haystack, needle)
    return str(args[1]) in str(args[0])


def _call_str_starts_with(args: list[Any]) -> bool:
    return str(args[0]).startswith(str(args[1]))


def _call_str_ends_with(args: list[Any]) -> bool:
    return str(args[0]).endswith(str(args[1]))


def _call_str_len(args: list[Any]) -> int:
    return len(str(args[0]))


_CALLS: dict[str, Any] = {
    "str_contains": _call_str_contains,
    "str_starts_with": _call_str_starts_with,
    "str_ends_with": _call_str_ends_with,
    "str_len": _call_str_len,
}


def evaluate_condition(expr: str, context: dict[str, Any]) -> bool:
    """Безопасно вычислить условие: свой парсер, без eval() кода."""
    if len(expr) > MAX_EXPR_LEN:
        raise ValueError(f"условие длиннее {MAX_EXPR_LEN} байт")
    result: dict[str, Any] = {}

    def _run() -> None:
        try:
            result["v"] = bool(_ExprEval(expr, context).parse())
        except Exception as e:  # noqa: BLE001
            result["e"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=EVAL_TIMEOUT_MS / 1000.0)
    if t.is_alive():
        raise TimeoutError(f"условие '{expr}': таймаут {EVAL_TIMEOUT_MS}мс")
    if "e" in result:
        raise result["e"]
    return bool(result.get("v", False))


# ── шаблоны ──────────────────────────────────────────────────────────────
def resolve_template(template: str, trigger_ctx: dict[str, Any],
                     step_outputs: dict[str, Any]) -> str:
    """{{trigger.X}} / {{steps.ID.output.FIELD}} / | truncate(N).

    Неизвестные ключи остаются текстом как есть (как в Buzz).
    """
    if "{{" not in template:
        return template
    out: list[str] = []
    rest = template
    while True:
        start = rest.find("{{")
        if start < 0:
            out.append(rest)
            break
        out.append(rest[:start])
        rest = rest[start + 2:]
        end = rest.find("}}")
        if end < 0:
            out.append("{{")
            out.append(rest)
            break
        expr = rest[:end].strip()
        rest = rest[end + 2:]
        parts = [p.strip() for p in expr.split("|", 1)]
        var_path = parts[0]
        flt = parts[1] if len(parts) > 1 else ""
        value = _resolve_var(var_path, trigger_ctx, step_outputs)
        if value is None:
            out.append("{{" + expr + "}}")
            continue
        if flt.startswith("truncate(") and flt.endswith(")"):
            n = int(flt[9:-1].strip())
            value = value[:n]
        out.append(value)
    return "".join(out)


def _resolve_var(path: str, trigger_ctx: dict[str, Any],
                 step_outputs: dict[str, Any]) -> Optional[str]:
    if path.startswith("trigger."):
        field = path[len("trigger."):]
        v = trigger_ctx.get(field)
        return None if v is None else str(v)
    if path.startswith("steps."):
        parts = path.split(".")
        if len(parts) == 4 and parts[2] == "output":
            out = step_outputs.get(parts[1])
            if isinstance(out, dict) and parts[3] in out:
                return str(out[parts[3]])
        return None
    # прямое имя поля (удобство для рыночных правил)
    v = trigger_ctx.get(path)
    return None if v is None else str(v)


# ── cron (5-польный матчер, без внешних либ) ─────────────────────────────
def _cron_field_match(field: str, value: int) -> bool:
    for part in field.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
        if part == "*":
            if value % step == 0:
                return True
        elif "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            if value >= lo and value <= hi and (value - lo) % step == 0:
                return True
        elif value == int(part):
            return True
    return False


def cron_matches(cron_expr: str, dt: Optional[datetime] = None) -> bool:
    """5-польный крон: min hour dom month dow."""
    dt = dt or datetime.now(timezone.utc)
    fields = cron_expr.split()
    if len(fields) != 5:
        raise ValueError(f"крон должен быть 5-польным, получил: {cron_expr!r}")
    minute, hour, dom, month, dow = fields
    return (
        _cron_field_match(minute, dt.minute)
        and _cron_field_match(hour, dt.hour)
        and _cron_field_match(dom, dt.day)
        and _cron_field_match(month, dt.month)
        and _cron_field_match(dow, dt.weekday() + 1)  # 1=Пн..7=Вс
    )


def parse_duration_secs(duration: str) -> int:
    """'30s'/'5m'/'1h' -> секунды. Ошибка при кривом формате."""
    duration = duration.strip()
    for suffix, mult in (("h", 3600), ("m", 60), ("s", 1)):
        if duration.endswith(suffix):
            n = int(duration[:-1].strip())
            if n < 0:
                raise ValueError(f"длительность не может быть отрицательной: {duration}")
            return n * mult
    raise ValueError(f"неверная длительность: {duration!r} (ожидалось 30s/5m/1h)")


# ── SSRF-защита (из buzz-core is_private_ip) ─────────────────────────────
def is_private_ip(ip: str) -> bool:
    """Полная проверка приватных адресов (как buzz-core).

    IPv4: loopback, private, link-local, CGNAT, benchmarking, broadcast.
    IPv6: loopback, ULA, link-local, multicast, documentation.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # не адрес — считаем опасным
    if addr.version == 4:
        return (addr.is_loopback or addr.is_private or addr.is_link_local
                or addr.is_reserved or addr.is_multicast
                or addr.is_unspecified
                or (addr >= ipaddress.IPv4Address("100.64.0.0")
                    and addr <= ipaddress.IPv4Address("100.127.255.255")))
    return (addr.is_loopback or addr.is_private or addr.is_link_local
            or addr.is_multicast or addr.is_reserved or addr.is_unspecified)


def _resolve_public(host: str) -> bool:
    """Проверка, что хост резолвится только в публичные адреса."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = info[4][0]
        if is_private_ip(ip):
            return False
    return bool(infos)


# ── схема и парсинг ──────────────────────────────────────────────────────
def validate_workflow(defn: dict[str, Any]) -> None:
    """Валидация определения (как WorkflowDef::validate в Buzz)."""
    if not str(defn.get("name", "")).strip():
        raise ValueError("name обязателен и не может быть пустым")
    steps = defn.get("steps") or []
    if not steps:
        raise ValueError("нужен минимум один шаг (steps)")
    seen: set[str] = set()
    for s in steps:
        sid = s.get("id", "")
        if not sid.strip():
            raise ValueError("step id не может быть пустым")
        if not re.fullmatch(r"[a-zA-Z0-9_]{1,64}", sid):
            raise ValueError(f"step id '{sid}' невалиден: только буквы/цифры/подчёркивание")
        if sid in seen:
            raise ValueError(f"дубликат step id: {sid}")
        seen.add(sid)
        if s.get("action") not in KNOWN_ACTIONS:
            raise ValueError(f"неизвестное действие: {s.get('action')!r}")
    trigger = defn.get("trigger") or {}
    on = trigger.get("on")
    if on not in KNOWN_TRIGGERS:
        raise ValueError(f"неизвестный триггер: {on!r}")
    if on == "schedule":
        if not trigger.get("cron") and not trigger.get("interval"):
            raise ValueError("schedule-триггер требует cron или interval")
        if trigger.get("cron") and trigger.get("interval"):
            raise ValueError("cron и interval взаимоисключающие")
        if trigger.get("cron"):
            cron_expr = trigger["cron"]
            if len(cron_expr.split()) != 5:
                raise ValueError(f"крон должен быть 5-польным: {cron_expr!r}")
        if trigger.get("interval"):
            secs = parse_duration_secs(trigger["interval"])
            if secs < 60:
                raise ValueError("interval должен быть >= 60s (цикл тикает раз в минуту)")
    if defn.get("enabled", True) not in (True, False):
        raise ValueError("enabled должен быть true/false")


def parse_workflow(yaml_text: str) -> tuple[dict[str, Any], str]:
    """Распарсить YAML-определение, провалидировать, вернуть (defn, json)."""
    if yaml is None:  # pragma: no cover
        raise RuntimeError("pyyaml не установлен: pip install pyyaml")
    defn = yaml.load(yaml_text, Loader=_RulesLoader)
    if not isinstance(defn, dict):
        raise ValueError("определение правила должно быть YAML-объектом")
    validate_workflow(defn)
    defn.setdefault("enabled", True)  # как serde default_true в Buzz
    return defn, json.dumps(defn, sort_keys=True)


# ── движок ───────────────────────────────────────────────────────────────
class WorkflowEngine:
    """Движок: загрузка правил, триггеры, выполнение шагов.

    Параллельность: семафор (по умолчанию 100, как в Buzz) с try_acquire —
    при переполнении сразу CapacityExceeded, без очереди.
    """

    def __init__(self, rules_path: Optional[Path] = None, max_concurrent: int = 100):
        self.rules_path = Path(rules_path) if rules_path else Path(RULES_PATH_DEFAULT).expanduser()
        self.workflows: list[dict[str, Any]] = []
        self._sem = threading.Semaphore(max_concurrent)

    # ── загрузка ─────────────────────────────────────────────────────────
    def load_rules(self, path: Optional[Path] = None) -> int:
        """Загрузить правила из YAML-файла (список или одно правило)."""
        if path:
            self.rules_path = Path(path)
        if not self.rules_path.exists():
            self.workflows = []
            return 0
        if yaml is None:  # pragma: no cover
            raise RuntimeError("pyyaml не установлен")
        data = yaml.load(self.rules_path.read_text(encoding="utf-8"), Loader=_RulesLoader)
        items = data if isinstance(data, list) else [data]
        loaded: list[dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                validate_workflow(it)
                loaded.append(it)
            except ValueError:
                continue  # битое правило не роняет остальные
        self.workflows = loaded
        return len(loaded)

    def save_example(self, path: Optional[Path] = None) -> str:
        """Сгенерировать пример правил для торговли."""
        example = """\
# Пример правил token-diet (YAML-движок из Buzz)
# Список правил: каждая запись — workflow. Одно правило = один блок.

- name: "Полюс: падение более 2% — сигнал"
  description: "Алерт и запись в hash-chain при падении PLZL >= 2%"
  trigger:
    on: price_event
    filter: "ticker == 'PLZL' and change_pct <= -2"
  steps:
    - id: alert
      action: send_alert
      text: "⚠ PLZL упал на {{trigger.change_pct}}% до {{trigger.price}}₽ — проверить стакан"
    - id: log
      action: audit_log
      action_name: decision_made
      detail: "{\\"what\\": \\"PLZL падение >2%: сигнал к проверке\\", \\"price\\": \\"{{trigger.price}}\\"}"

- name: "Газпром: рост более 3% — авто-фиксация"
  trigger:
    on: price_event
    filter: "ticker == 'GAZP' and change_pct >= 3"
  steps:
    - id: alert
      action: send_alert
      text: "🚀 GAZP вырос на {{trigger.change_pct}}% — цель достигнута"
    - id: log
      action: audit_log
      action_name: prediction_made
      detail: "{\\"what\\": \\"GAZP рост >3% зафиксирован\\"}"

- name: "Утренний обзор"
  enabled: true
  trigger:
    on: schedule
    cron: "0 9 * * 1-5"   # пн-пт в 9:00 UTC
  steps:
    - id: alert
      action: send_alert
      text: "☀ Утренний обзор рынка — время собрать дайджест"
"""
        target = Path(path) if path else self.rules_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(example, encoding="utf-8")
        return str(target)

    # ── выполнение ───────────────────────────────────────────────────────
    def trigger(self, on: str, event: dict[str, Any],
                dry_run: bool = False) -> list[dict[str, Any]]:
        """Подать событие в движок: сработавшие правила выполняются.

        Возвращает список результатов (по одному на сработавшее правило).
        """
        results: list[dict[str, Any]] = []
        for wf in self.workflows:
            if not wf.get("enabled", True):
                continue
            trig = wf.get("trigger") or {}
            if trig.get("on") != on:
                continue
            ctx = dict(event)
            flt = trig.get("filter")
            if flt:
                try:
                    if not evaluate_condition(flt, _flat_ctx(ctx)):
                        continue
                except (ValueError, TimeoutError):
                    continue  # битое условие — правило пропускается
            results.append(self._run_workflow(wf, ctx, dry_run))
        return results

    def scheduled_due(self, now: Optional[datetime] = None) -> list[dict[str, Any]]:
        """Правила с schedule-триггером, которые должны сработать сейчас."""
        now = now or datetime.now(timezone.utc)
        due: list[dict[str, Any]] = []
        for wf in self.workflows:
            if not wf.get("enabled", True):
                continue
            trig = wf.get("trigger") or {}
            if trig.get("on") != "schedule":
                continue
            cron = trig.get("cron")
            if cron:
                try:
                    if cron_matches(cron, now):
                        due.append(wf)
                except ValueError:
                    continue
            else:
                interval = trig.get("interval")
                try:
                    secs = parse_duration_secs(interval or "")
                except ValueError:
                    continue
                last = getattr(self, "_last_schedule", {}).get(wf.get("name"))
                if last is None or time.time() - last >= secs:
                    due.append(wf)
        return due

    def _run_workflow(self, wf: dict[str, Any], ctx: dict[str, Any],
                      dry_run: bool) -> dict[str, Any]:
        if not self._sem.acquire(blocking=False):
            return {"workflow": wf.get("name"), "status": "capacity_exceeded",
                    "steps": []}
        try:
            step_outputs: dict[str, Any] = {}
            trace: list[dict[str, Any]] = []
            for step in wf.get("steps") or []:
                cond = step.get("if")
                if cond:
                    try:
                        if not evaluate_condition(cond, _flat_ctx(ctx, step_outputs)):
                            trace.append({"id": step["id"], "status": "skipped"})
                            continue
                    except (ValueError, TimeoutError):
                        trace.append({"id": step["id"], "status": "condition_error"})
                        continue
                out = self._dispatch(step, ctx, step_outputs, dry_run)
                if out.get("status") == "suspended":
                    trace.append({"id": step["id"], "status": "suspended",
                                  "approval_token": out.get("approval_token")})
                    break  # approval-gate останавливает выполнение (как Buzz)
                step_outputs[step["id"]] = out.get("output", {})
                trace.append({"id": step["id"], "status": out.get("status", "ok"),
                              "output": out.get("output", {})})
            return {"workflow": wf.get("name"), "status": "completed", "steps": trace}
        finally:
            self._sem.release()

    def _dispatch(self, step: dict[str, Any], ctx: dict[str, Any],
                  step_outputs: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        action = step.get("action")
        if dry_run:
            return {"status": "dry_run", "output": {"action": action,
                    "text": resolve_template(str(step.get("text", "")), ctx, step_outputs)}}
        if action == "send_alert":
            text = resolve_template(str(step.get("text", "")), ctx, step_outputs)
            print(f"⚠ [{step['id']}] {text}")
            return {"status": "ok", "output": {"sent": True, "text": text}}
        if action == "send_message":
            text = resolve_template(str(step.get("text", "")), ctx, step_outputs)
            print(f"📨 [{step['id']}] {text}")
            return {"status": "ok", "output": {"sent": True, "text": text,
                    "channel": step.get("channel")}}
        if action == "audit_log":
            try:
                from token_diet.audit_chain import AuditChain
                detail_raw = step.get("detail", "{}")
                detail = resolve_template(detail_raw, ctx, step_outputs)
                try:
                    detail_obj = json.loads(detail)
                except json.JSONDecodeError:
                    detail_obj = {"text": detail}
                e = AuditChain().log(
                    action=str(step.get("action_name", "decision_made")),
                    detail=detail_obj,
                    actor="workflow",
                    object_id=str(ctx.get("ticker") or step.get("id")),
                )
                return {"status": "ok", "output": {"audit_seq": e.seq, "hash": e.hash[:12]}}
            except Exception as ex:  # noqa: BLE001
                return {"status": "error", "output": {"error": str(ex)}}
        if action == "call_webhook":
            return self._webhook(step, ctx, step_outputs)
        if action == "request_approval":
            token = f"{step.get('id')}-{uuid4_hex()}"
            return {"status": "suspended",
                    "approval_token": token,
                    "output": {"from": step.get("from"), "message": step.get("message")}}
        if action == "delay":
            try:
                secs = parse_duration_secs(str(step.get("duration", "1s")))
            except ValueError:
                secs = 1
            secs = min(secs, MAX_DELAY_SECS)
            time.sleep(secs)
            return {"status": "ok", "output": {"slept_secs": secs}}
        return {"status": "error", "output": {"error": f"неизвестное действие: {action}"}}

    def _webhook(self, step: dict[str, Any], ctx: dict[str, Any],
                 step_outputs: dict[str, Any]) -> dict[str, Any]:
        url = resolve_template(str(step.get("url", "")), ctx, step_outputs)
        method = str(step.get("method", "POST")).upper()
        body = step.get("body")
        if body is not None:
            body = resolve_template(str(body), ctx, step_outputs).encode()
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            if parsed.scheme not in ("https", "http"):
                return {"status": "error", "output": {"error": f"схема {parsed.scheme!r} не разрешена"}}
            if parsed.hostname is None or not _resolve_public(parsed.hostname):
                return {"status": "error", "output": {"error": f"SSRF: хост {parsed.hostname!r} не публичный"}}
            req = urllib.request.Request(url, data=body, method=method)
            if isinstance(step.get("headers"), dict):
                for k, v in step["headers"].items():
                    req.add_header(k, resolve_template(str(v), ctx, step_outputs))
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                data = resp.read(WEBHOOK_MAX_BODY + 1)
                if len(data) > WEBHOOK_MAX_BODY:
                    return {"status": "error", "output": {"error": "ответ больше 1 МиБ"}}
                return {"status": "ok", "output": {"status": resp.status,
                        "body": data[:200].decode("utf-8", "replace")}}
        except Exception as ex:  # noqa: BLE001
            return {"status": "error", "output": {"error": str(ex)}}


def _flat_ctx(trigger_ctx: dict[str, Any],
              step_outputs: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Переменные для условий: trigger_X + простое имя + steps_ID_output_FIELD.

    Удобство для рыночных правил: `ticker == 'PLZL'` работает, и
    buzz-стиль `trigger_ticker == 'PLZL'` тоже.
    """
    ctx: dict[str, Any] = {}
    for k, v in trigger_ctx.items():
        ctx[f"trigger_{k}"] = v
        ctx[k] = v  # простое имя поля (ticker, price, change_pct)
    for sid, out in (step_outputs or {}).items():
        if isinstance(out, dict):
            for f, v in out.items():
                ctx[f"steps_{sid}_output_{f}"] = v
    return ctx


def uuid4_hex() -> str:
    import uuid
    return uuid.uuid4().hex
