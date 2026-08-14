"""OmniRoute-подход: саб-агентная маршрутизация задач между моделями.

Что такое OmniRoute: AI-шлюз на 330+ провайдеров, 19 стратегий
маршрутизации, auto-fallback, комбо-цепочки, агентная оркестрация (A2A).
Мы воспроизводим ключевые механики в чистом Python (0 обязательных
зависимостей — прямой HTTP к Ollama, опционально OpenAI-совместимые ключи):

1. МУЛЬТИ-МОДЕЛЬНЫЙ РОУТЕР: сложная задача разбивается на подзадачи,
   каждая отправляется на ПОДХОДЯЩУЮ модель по стратегии:
   - auto/cheap  → маленькая дешёвая локальная модель
   - auto/fast   → минимальная латентность
   - auto/coding → модель для кода/рассуждений (deepseek-r1)
   - auto/balanced → баланс качества и цены
   - auto/smart  → внешняя дорогая (если задан ключ)

2. САБ-АГЕНТЫ ИЗОЛИРОВАНЫ (Sub-Agent Isolation): каждый саб-агент
   получает только свою подзадачу + мини-контекст и возвращает
   СЖАТЫЙ ИТОГ (distilled summary). Главный контекст не замусоривается
   тяжёлыми промежуточными выводами.

3. AUTO-FALLBACK (combos): модель упала/недоступна/квота → запрос
   автоматически уходит следующей в цепочке.

4. FUSION + СУДЬЯ: для важных подзадач две модели отвечают независимо,
   детерминированный судья (длина/числа/совпадение ключевых терминов)
   выбирает лучший ответ.

5. ЭВРИСТИЧЕСКИЙ СКОР ПРОВАЙДЕРА: доступность, стоимость, латентность,
   качество → упрощённые 4 фактора OmniRoute (14 → 4 рабочих).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# ── локальный Ollama (всегда доступен, 0 ключей) ─────────────────────────
OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434")

DEFAULT_PROVIDERS: list[dict] = [
    {
        "name": "ollama-qwen3-4b", "kind": "ollama", "model": "qwen3:4b",
        "base_url": OLLAMA_BASE, "cost": 0.0, "latency": 1.0,
        "quality": 7, "tags": ["balanced", "general", "russian"],
    },
    {
        "name": "ollama-qwen3-1.7b", "kind": "ollama", "model": "qwen3:1.7b",
        "base_url": OLLAMA_BASE, "cost": 0.0, "latency": 0.3,
        "quality": 5, "tags": ["fast", "cheap"],
    },
    {
        "name": "ollama-deepseek-r1-7b", "kind": "ollama",
        "model": "deepseek-r1:7b", "base_url": OLLAMA_BASE,
        "cost": 0.0, "latency": 1.6, "quality": 8,
        "tags": ["coding", "reasoning"],
    },
]

# ── AnyModel (проверено генералом 14.08.2026, ключ из .env) ─────────────
# OpenAI-совместимый API: https://anymodel.org/v1/chat/completions
# Модели подтверждены живыми: deepseek-v4-pro, glm-5.2, minimax-m3.
_ANYMODEL_KEY = os.environ.get("ANYMODEL_API_KEY", "")
if not _ANYMODEL_KEY:
    try:
        if os.path.exists(".env"):
            for _l in open(".env", encoding="utf-8"):
                if _l.strip().startswith("ANYMODEL_API_KEY="):
                    _ANYMODEL_KEY = _l.strip().split("=", 1)[1]
                    break
    except Exception:
        pass

ANYMODEL_BASE = "https://anymodel.org/v1"
if _ANYMODEL_KEY:
    DEFAULT_PROVIDERS += [
        {
            "name": "anymodel-deepseek-v4-pro", "kind": "openai",
            "model": "am/deepseek-v4-pro", "base_url": ANYMODEL_BASE,
            "api_key": _ANYMODEL_KEY, "cost": 0.02, "latency": 1.2,
            "quality": 9, "tags": ["smart", "coding", "reasoning", "balanced"],
        },
        {
            "name": "anymodel-glm-5.2", "kind": "openai",
            "model": "am/glm-5.2", "base_url": ANYMODEL_BASE,
            "api_key": _ANYMODEL_KEY, "cost": 0.015, "latency": 1.8,
            "quality": 9, "tags": ["smart", "analyst", "balanced"],
        },
        {
            "name": "anymodel-minimax-m3", "kind": "openai",
            "model": "am/minimax-m3", "base_url": ANYMODEL_BASE,
            "api_key": _ANYMODEL_KEY, "cost": 0.005, "latency": 0.4,
            "quality": 7, "tags": ["fast", "cheap", "general"],
        },
    ]


@dataclass
class Provider:
    name: str
    kind: str                      # ollama | openai
    model: str
    base_url: str
    api_key: str = ""
    cost: float = 0.0              # $ за 1K токенов
    latency: float = 0.5           # условная (сек на вызов)
    quality: int = 7               # 0..10
    tags: list[str] = field(default_factory=list)

    def score_for(self, strategy: str) -> float:
        """Эвристический скор по стратегии (эхо 4 факторов OmniRoute)."""
        if strategy == "auto/cheap":
            return self.quality - self.cost * 50.0 - self.latency * 3.0
        if strategy == "auto/fast":
            return 10.0 - self.latency * 4.0
        if strategy == "auto/coding":
            base = self.quality * 1.2 - self.latency * 0.3
            if "coding" in self.tags or "reasoning" in self.tags:
                base += 3.0
            return base
        if strategy == "auto/smart":
            return self.quality * 1.5 - self.cost * 5.0
        # auto/balanced (по умолчанию): качество, но с заметным штрафом
        # за латентность — чтобы не выбирать самую медленную модель
        return self.quality - self.cost * 20.0 - self.latency * 2.0


# ═════════════════════════════════════════════════════════════════════════
# 1. HTTP-клиент: прямой вызов (Ollama / OpenAI-совместимый)
# ═════════════════════════════════════════════════════════════════════════

def _call_model(provider: Provider, system: str, user: str,
                max_tokens: int = 900, timeout: int = 90) -> str:
    """Вызывает модель. Кидает исключение → триггер fallback."""
    if provider.kind == "ollama":
        url = provider.base_url.rstrip("/") + "/api/chat"
        payload = {
            "model": provider.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        return str(data.get("message", {}).get("content", "")).strip()

    # OpenAI-совместимый (DeepSeek, OpenRouter, любой /v1/chat/completions)
    url = provider.base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": provider.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {provider.api_key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    return str(data["choices"][0]["message"]["content"]).strip()


# ═════════════════════════════════════════════════════════════════════════
# 2. Роутер + саб-агенты
# ═════════════════════════════════════════════════════════════════════════

@dataclass
class SubAgentResult:
    task: str
    provider: str
    model: str
    summary: str
    tokens_approx: int = 0
    strategy: str = ""
    fallbacks: int = 0
    ok: bool = True
    error: str = ""


class OmniRouter:
    """Реестр провайдеров + маршрутизация + саб-агенты."""

    def __init__(self, providers: list[dict] | None = None):
        self.providers: list[Provider] = []
        for p in providers or DEFAULT_PROVIDERS:
            self.add(p)

    def add(self, spec: dict) -> Provider:
        p = Provider(**spec)
        self.providers.append(p)
        return p

    # ── выбор модели по стратегии ──────────────────────────────────
    def pick(self, strategy: str = "auto/balanced",
             exclude: list[str] | None = None) -> Provider | None:
        exclude = exclude or []
        cands = [p for p in self.providers if p.name not in exclude]
        if not cands:
            return None
        return max(cands, key=lambda p: p.score_for(strategy))

    # ── саб-агент с fallback-цепочкой ──────────────────────────────
    def subagent(self, task: str, strategy: str = "auto/balanced",
                 context: str = "", max_tokens: int = 900,
                 timeout: int = 90) -> SubAgentResult:
        system = (
            "Ты — изолированный саб-агент. Реши ТОЛЬКО свою подзадачу. "
            "Верни компактный итог: суть, цифры, вывод. Без лишних слов."
        )
        if context:
            system += f"\nКонтекст (кратко): {context[:1200]}"
        tried: set[str] = set()
        fallbacks = 0
        last_err = ""
        start = time.time()
        while True:
            provider = self.pick(strategy, exclude=sorted(tried))
            if provider is None:
                return SubAgentResult(
                    task=task, provider="none", model="", summary="",
                    ok=False, error=f"нет доступных моделей: {last_err}",
                    strategy=strategy, fallbacks=fallbacks)
            try:
                text = _call_model(provider, system, task, max_tokens, timeout)
                if not text.strip() and fallbacks == 0:
                    # холодный старт на CPU / обрезанный инференс — ретрай
                    time.sleep(1.0)
                    text = _call_model(provider, system, task,
                                       max_tokens, timeout)
                return SubAgentResult(
                    task=task, provider=provider.name, model=provider.model,
                    summary=text, tokens_approx=int(len(text) / 4),
                    strategy=strategy, fallbacks=fallbacks,
                )
            except Exception as e:  # noqa: BLE001 — fallback, не падаем
                tried.add(provider.name)
                fallbacks += 1
                last_err = str(e)[:120]
                if fallbacks >= len(self.providers):
                    return SubAgentResult(
                        task=task, provider="none", model="", summary="",
                        ok=False, error=f"все модели упали: {last_err}",
                        strategy=strategy, fallbacks=fallbacks)
                time.sleep(0.4)  # пауза перед резервной моделью

    # ── делегирование: список подзадач → саб-агенты ────────────────
    def delegate(self, tasks: list[str], strategy: str = "auto/balanced",
                 context: str = "") -> list[SubAgentResult]:
        return [self.subagent(t, strategy=strategy, context=context)
                for t in tasks]

    # ── оркестрация: сложная задача → подзадачи → итог ─────────────
    def orchestrate(self, complex_task: str, strategy: str = "auto/balanced",
                    max_subtasks: int = 4) -> dict:
        subtasks = _split_tasks(complex_task, max_subtasks)
        results = self.delegate(subtasks, strategy=strategy,
                                context=complex_task)
        ok = [r for r in results if r.ok]
        failed = [r for r in results if not r.ok]
        total_tokens = sum(r.tokens_approx for r in results)
        fallbacks = sum(r.fallbacks for r in results)
        body = "\n\n".join(f"[{r.provider}] {r.summary}" for r in ok)
        return {
            "subtasks": len(subtasks),
            "ok": len(ok),
            "failed": len(failed),
            "errors": [r.error for r in failed],
            "total_tokens_approx": total_tokens,
            "fallbacks": fallbacks,
            "body": body,
        }

    # ── fusion: две модели на одну подзадачу + детерминированный судья ──
    def fusion(self, task: str, strategies=("auto/fast", "auto/balanced"),
               max_tokens: int = 700) -> dict:
        answers: list[SubAgentResult] = []
        for st in strategies:
            r = self.subagent(task, strategy=st, max_tokens=max_tokens)
            answers.append(r)
        ok = [a for a in answers if a.ok]
        if not ok:
            return {"ok": False, "answers": answers}
        best = _judge(task, ok)
        return {"ok": True, "chosen": best.provider, "answers": ok,
                "judge_reason": _judge_reason(task, ok)}


# ═════════════════════════════════════════════════════════════════════════
# 3. Вспомогательные: декомпозиция и судья (0 LLM-вызовов)
# ═════════════════════════════════════════════════════════════════════════

def _split_tasks(task: str, max_subtasks: int = 4) -> list[str]:
    """Декомпозиция по пунктам/«и»/номерам. Детерминированно."""
    cleaned = re.sub(r"\s+", " ", task).strip()
    # нумерованные пункты
    numbered = re.split(r"\s+(?=\d+[.)])", cleaned)
    if len(numbered) > 1:
        parts = [p.strip(" .,;") for p in numbered if len(p.strip()) >= 8]
        if parts:
            return parts[:max_subtasks]
    # перечисления через «и», «;»
    sep = re.split(r"\s+и\s+|;", cleaned)
    if len(sep) > 1:
        parts = [p.strip() for p in sep if len(p.strip()) >= 8]
        if parts:
            return parts[:max_subtasks]
    return [cleaned]


def _judge(task: str, answers: list[SubAgentResult]) -> SubAgentResult:
    """Эвристический судья: предпочитаем ответ с числами + средней длиной."""
    best, best_score = None, -1.0
    for a in answers:
        score = 0.0
        nums = len(re.findall(r"\d", a.summary))
        score += min(nums, 5) * 1.5            # конкретика
        score += min(len(a.summary), 900) / 900.0  # полнота
        if any(k in a.summary.lower() for k in
               ("вывод", "итог", "рекомендац", "следовательно", "итак")):
            score += 1.0                        # есть заключение
        if best_score < score:
            best, best_score = a, score
    return best or answers[0]


def _judge_reason(task: str, answers: list[SubAgentResult]) -> str:
    b = _judge(task, answers)
    return (f"судья выбрал {b.provider}: больше конкретики/полноты "
            f"({len(b.summary)} символов)")
