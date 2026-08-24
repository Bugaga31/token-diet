"""Agent Brain — любой LLM владеет всеми инструментами token-diet.

Идея из экосистемы MCP/function-calling: нейронка сама решает, какой
инструмент позвать; плагин даёт безопасный реестр и цикл исполнения.
Работает с **любой** моделью:

- локальной (Ollama /api/chat c tools — qwen3, deepseek-r1...);
- облачной OpenAI-совместимой — ``openai_tools()`` отдаёт готовые
  схемы function-calling, а ``execute()`` исполняет их ответы.

Безопасность: только allowlist-инструменты, аргументы валидируются
схемой по-простому (типы), каждый вызов пишется в аудит-журнал,
цикл ограничен ``max_steps``. Реальные ордера в реестре отсутствуют
принципиально — только чтение/расчёты/paper.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .ollama_bridge import OllamaBridge


@dataclass
class ToolSpec:
    """Один инструмент: имя, описание, JSON-схема аргументов, функция."""

    name: str
    description: str
    parameters: dict[str, Any]          # JSON Schema (type/object/properties)
    handler: Callable[..., Any]
    danger: str = ""                    # "" | "net" | "slow" — для фильтрации

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class AgentBrain:
    """Реестр инструментов + исполнитель + цикл tool-calling."""

    bridge: OllamaBridge | None = None
    audit_path: str | None = None
    max_steps: int = 8
    tools: dict[str, ToolSpec] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.bridge is None:
            self.bridge = OllamaBridge()

    # ── реестр ──────────────────────────────────────────────────

    def register(self, spec: ToolSpec) -> None:
        self.tools[spec.name] = spec

    def unregister(self, name: str) -> None:
        self.tools.pop(name, None)

    def openai_tools(self, include_danger: str | None = None) -> list[dict]:
        """Схемы для любого OpenAI-совместимого клиента."""
        return [
            s.openai_schema() for s in self.tools.values()
            if include_danger is None or s.danger == include_danger
        ]

    # ── исполнение ──────────────────────────────────────────────

    def execute(self, name: str, arguments: dict[str, Any] | str) -> dict[str, Any]:
        """Вызвать инструмент по имени. Результат JSON-сериализуемый.

        Неизвестное имя и не-словарные аргументы отклоняются без вызова.
        """
        spec = self.tools.get(name)
        if spec is None:
            return {"ok": False, "error": f"нет инструмента {name!r}"}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError as e:
                return {"ok": False, "error": f"аргументы не JSON: {e}"}
        if not isinstance(arguments, dict):
            return {"ok": False, "error": "аргументы должны быть объектом"}
        t0 = time.time()
        try:
            result = spec.handler(**arguments)
            out = {"ok": True, "tool": name, "result": _jsonable(result)}
        except TypeError as e:
            out = {"ok": False, "tool": name, "error": f"неверные аргументы: {e}"}
        except Exception as e:  # noqa: BLE001 - инструмент не должен ронять мозг
            out = {"ok": False, "tool": name, "error": f"{type(e).__name__}: {e}"}
        out["elapsed_s"] = round(time.time() - t0, 3)
        self._audit(out)
        return out

    def _audit(self, record: dict[str, Any]) -> None:
        if not self.audit_path:
            return
        line = json.dumps({"ts": round(time.time(), 3), **record},
                          ensure_ascii=False)
        with open(self.audit_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # ── цикл для локальных моделей (Ollama tools) ───────────────

    def run_local(self, user_prompt: str, model: str | None = None) -> str | None:
        """Полный цикл: промпт → tool-calls → исполнение → ответ.

        Возвращает финальный текст модели или None (сервер недоступен).
        """
        if not self.bridge or not self.bridge.available:
            return None
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
        schemas = self.openai_tools()
        for _ in range(self.max_steps):
            resp = self.bridge.chat(messages, model=model, tools=schemas or None)
            if not resp:
                return None
            msg = resp.get("message") or {}
            calls = msg.get("tool_calls") or []
            if not calls:
                return msg.get("content")
            messages.append(msg)
            for call in calls:
                fn = call.get("function") or {}
                result = self.execute(
                    fn.get("name", ""), fn.get("arguments", {}),
                )
                messages.append({
                    "role": "tool",
                    "content": json.dumps(result, ensure_ascii=False),
                })
        return "лимит шагов исчерпан — задача слишком большая"

    # ── экспорт подсказок ───────────────────────────────────────

    def prompt_block(self) -> str:
        """Токен-бережная шпаргалка инструментов для системного промпта."""
        lines = [f"Инструменты ({len(self.tools)}):"]
        for s in self.tools.values():
            args = ", ".join(s.parameters.get("properties", {}))
            lines.append(f"- {s.name}({args}): {s.description}")
        return "\n".join(lines)


def _jsonable(value: Any) -> Any:
    """Привести результат к JSON-сериализуемому виду (dataclass → dict)."""
    if hasattr(value, "__dataclass_fields__"):
        return {k: _jsonable(v) for k, v in value.__dict__.items()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


# ── Дефолтный набор инструментов ────────────────────────────────────


def default_tools(brain: AgentBrain | None = None) -> AgentBrain:
    """Мозг с полным безопасным набором: токены, сжатие, инвестиции, сеть."""

    brain = brain or AgentBrain()

    def t(name, desc, params, handler, danger=""):
        brain.register(ToolSpec(name, desc, params, handler, danger))

    from .core import count_tokens as _count
    from .loss_router import compress_with_routing as _cwr

    t("count_tokens", "Сколько токенов в тексте",
      {"type": "object", "properties": {"text": {"type": "string"}},
       "required": ["text"]},
      lambda text: {"tokens": _count(text)})

    t("compress_text", "Сжать прозу с сохранением смысла",
      {"type": "object",
       "properties": {"text": {"type": "string"}},
       "required": ["text"]},
      lambda text: dict(zip(("compressed", "before", "after"),
                            _cwr(text), strict=False)))

    from .question_normalizer import normalize_question as _nq
    t("normalize_question", "Нормализовать вопрос для кэша/поиска",
      {"type": "object", "properties": {"q": {"type": "string"}},
       "required": ["q"]},
      lambda q: {"normalized": _nq(q)})

    from .spend_forecast import (
        UsageDay,
        budget_runway,
        forecast_from_pairs,
    )

    def _forecast(pairs, budget_rub=None):
        fc = forecast_from_pairs(pairs)
        out = {
            "baseline_daily_tokens": round(fc.baseline_daily_tokens),
            "projected_month_tokens": round(fc.projected_month_tokens),
            "projected_month_cost_rub": round(fc.projected_month_cost_rub),
            "trend": fc.trend,
            "anomalies": [a.date for a in fc.anomalies[-5:]],
        }
        if budget_rub:
            days = fc.baseline_daily_tokens and None
            rw = budget_runway(
                [UsageDay(*p) for p in pairs], float(budget_rub),
            )
            out["runway_days"] = rw[0] if rw else None
            del days
        return out

    t("forecast_spend", "Прогноз месячного расхода по дневной истории "
      "[[дата, токены, рубли], ...]",
      {"type": "object",
       "properties": {"pairs": {"type": "array"},
                      "budget_rub": {"type": "number"}},
       "required": ["pairs"]},
      _forecast)

    from .rebalance_advisor import Holding, rebalance_plan
    def _rebalance(holdings, targets, cash_rub=0.0, drift_band=0.05):
        hs = [Holding(h["ticker"], float(h.get("quantity", 0)),
                      float(h.get("price", 0))) for h in holdings]
        plan = rebalance_plan(hs, targets, cash_rub=cash_rub,
                              drift_band=float(drift_band))
        return {"total_value_rub": round(plan.total_value_rub, 2),
                "actions": [
                    {"ticker": a.ticker, "action": a.action,
                     "amount_rub": round(a.amount_rub, 2)}
                    for a in plan.advices if a.action != "HOLD"
                ],
                "block": plan.block()}
    t("rebalance_plan", "План ребалансировки портфеля к целевым весам "
      "(holdings=[{ticker,quantity,price}])",
      {"type": "object",
       "properties": {"holdings": {"type": "array"},
                      "targets": {"type": "object"},
                      "cash_rub": {"type": "number"}},
       "required": ["holdings", "targets"]},
      _rebalance)

    from .position_guard import Position as GP
    from .position_guard import watch_positions as _wp
    t("portfolio_stops", "Страж стопов по позициям "
      "(live-цены; positions=[{ticker,quantity,avg_price}])",
      {"type": "object",
       "properties": {"positions": {"type": "array"}},
       "required": ["positions"]},
      lambda positions: {"report": _wp(
          [GP(p["ticker"], float(p.get("quantity", 0)),
              float(p.get("avg_price", 0))) for p in positions]).block()},
      danger="net")

    from .order_orchestra import plan_stop_bracket as _psb
    t("stop_bracket", "Связка вход+стоп+тейк из ATR (paper-расчёт)",
      {"type": "object",
       "properties": {"ticker": {"type": "string"},
                      "entry": {"type": "number"},
                      "atr_value": {"type": "number"},
                      "atr_multiplier": {"type": "number"},
                      "rr": {"type": "number"}},
       "required": ["ticker", "entry", "atr_value"]},
      lambda ticker, entry, atr_value, atr_multiplier=2.5, rr=2.0: [
          {"side": i.side, "limit_price": i.limit_price,
           "stop_loss": i.stop_loss, "take_profit": i.take_profit}
          for i in _psb(ticker, float(entry), float(atr_value),
                        atr_multiplier=float(atr_multiplier), rr=float(rr))
      ])

    from .reddit_reader import digest as _digest
    from .reddit_reader import fetch_subreddit
    t("reddit_digest", "Свежие посты сабреддита (только чтение)",
      {"type": "object",
       "properties": {"subreddit": {"type": "string"},
                      "limit": {"type": "integer"}},
       "required": ["subreddit"]},
      lambda subreddit, limit=8: _digest(
          fetch_subreddit(subreddit, limit=min(int(limit), 10))),
      danger="net")

    from .youtube_learner import extract_transcript
    t("youtube_transcript", "Транскрипт YouTube-видео (субтитры)",
      {"type": "object",
       "properties": {"url": {"type": "string"},
                      "max_chars": {"type": "integer"}},
       "required": ["url"]},
      lambda url, max_chars=12000: extract_transcript(url,
                                                      max_chars=int(max_chars)),
      danger="net slow")

    from .video_frames import video_metadata
    t("youtube_info", "Метаданные видео: название, длительность, главы",
      {"type": "object", "properties": {"url": {"type": "string"}},
       "required": ["url"]},
      lambda url: (m.block() if (m := video_metadata(url)) else "yt-dlp недоступен"),
      danger="net")

    from .network_control import connectivity_block
    t("net_connectivity", "Проверить доступность MOEX/Reddit/YouTube/GitHub",
      {"type": "object", "properties": {}},
      lambda: connectivity_block(), danger="net")

    from .ollama_bridge import neural_status_block
    t("neural_status", "Какие бесплатные нейронки доступны локально",
      {"type": "object", "properties": {}},
      lambda: neural_status_block())

    return brain
