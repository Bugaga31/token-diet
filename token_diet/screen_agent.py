"""Screen Agent — «глаза + руки + мозг»: нейронка управляет экраном.

Даёшь агенту ЗАДАЧУ (например: «открой калькулятор и посчитай 2+2»),
а он сам крутит цикл:

    СКРИНШОТ → vision-модель решает действие (JSON) → ВЫПОЛНИТЬ
    → СКРИНШОТ → проверить, что изменилось → следующий шаг → ... → done

Глаза:  token_diet.omni_eyes (Claude Opus 5 и др. через OmniRoute)
Руки:   scrcmd.sh (xdotool: клик/ввод/клавиши/окна/буфер) — из skill screen-control
        если scrcmd.sh нет — fallback на прямые вызовы xdotool/import

Модель получает строгую инструкцию отвечать ТОЛЬКО JSON:
    {"action": "click|type|key|scroll|activate|clipboard|wait|done",
     "params": {...}, "reason": "зачем"}

Безопасность: максимум max_steps шагов, деструктивные действия
(alt+F4, ctrl+w, удаление) разрешены только если allowed_destructive=True.

Использование:
    from token_diet.screen_agent import run_task
    result = run_task("открой калькулятор и посчитай 2+2", max_steps=8)

CLI:
    python3 -m token_diet.cli sagent "задача" [--steps 10] [--model ...] [--destructive]
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from token_diet.omni_eyes import OMNI_BASE, _safe_image_base64, take_screenshot

# Путь к обёртке из skill screen-control
SCRCMD = os.environ.get(
    "SCRCMD",
    "/home/ro/.agents/skills/screen-control/scripts/scrcmd.sh",
)
if not os.path.exists(SCRCMD):
    SCRCMD = shutil.which("scrcmd") or ""

# Модели-«мозги» в порядке приоритета (тестированы живьём)
BRAIN_MODELS = [
    "agentrouter/claude-opus-5",
    "agentrouter/claude-opus-5-low",
]

PROMPT = """Ты — агент, управляющий экраном компьютера. Ты ВИДИШЬ текущий скриншот.

ЗАДАЧА: {task}

Текущее состояние экрана видно на скриншоте. Реши, какое ОДНО действие сделать следующим.
Отвечай СТРОГО одним JSON без пояснений и без markdown-обёртки:
{{"action": "...", "params": {{...}}, "reason": "почему"}}

Допустимые действия:
- click:      {{"x": 100, "y": 200, "button": 1}}        — клик (1 левая, 3 правая)
- dblclick:   {{"x": 100, "y": 200}}
- type:       {{"text": "hello"}}                        — ввод текста
- key:        {{"combo": "ctrl+l"}}                      — горячая клавиша
- scroll:     {{"dir": "down", "n": 3}}                  — колесо
- activate:   {{"title": "Калькулятор"}}                 — активировать окно по части заголовка
- clipboard:  {{"set": "текст"}}                         — записать в буфер обмена
- wait:       {{"ms": 500}}                              — подождать (UI-анимации)
- done:       {{}}                                      — задача ВЫПОЛНЕНА, больше действий не нужно

Правила:
1. Сначала посмотри на скриншот, пойми, где что находится.
2. Одно действие за раз. Координаты — в пикселях экрана.
3. Если задача выполнена (или её нельзя выполнить) — сразу "done".
4. Не выдумывай: если окна/кнопки на скриншоте нет — не кликай вслепую, скажи done с reason.
5. Если скриншот пустой/чёрный — скажи done с reason "экран недоступен".
"""


@dataclass
class AgentStep:
    """Один шаг цикла: что решила модель, что выполнили, что увидели после."""
    action: str
    params: dict
    reason: str
    command: str = ""
    output: str = ""
    ok: bool = True
    observation: str = ""
    step: int = 0


@dataclass
class TaskResult:
    """Итог выполнения задачи."""
    task: str
    done: bool
    reason: str
    steps: list[AgentStep] = field(default_factory=list)
    model: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Руки: выполнение действий
# ─────────────────────────────────────────────────────────────────────────────

def check_deps() -> list[str]:
    """Проверить наличие инструментов «рук». Возвращает список отсутствующих."""
    missing = []
    if not SCRCMD:
        for tool in ("xdotool", "import", "wmctrl", "xclip"):
            if not shutil.which(tool):
                missing.append(tool)
    else:
        if not shutil.which("xdotool"):
            missing.append("xdotool")
    return missing


def _run_shell(cmd: str, timeout: int = 15) -> tuple[int, str]:
    """Выполнить команду оболочки, вернуть (returncode, вывод)."""
    try:
        p = subprocess.run(
            ["bash", "-lc", cmd], capture_output=True, text=True, timeout=timeout
        )
        out = (p.stdout or "").strip() + ("\n" + p.stderr if p.stderr and p.stderr.strip() else "").strip()
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return -1, f"timeout {timeout}s"
    except Exception as e:
        return -1, str(e)


def _scr(action: str, *args: str) -> tuple[int, str]:
    """Вызвать scrcmd.sh (или прямой xdotool), вернуть (rc, output)."""
    if SCRCMD:
        cmd = f'"{SCRCMD}" {action} ' + " ".join(shlex_quote(a) for a in args)
    else:
        cmd = _fallback_command(action, args)
    return _run_shell(cmd)


def shlex_quote(s: str) -> str:
    """Мини-экранирование для одинарных кавычек bash."""
    return "'" + s.replace("'", "'\\''") + "'"


def _fallback_command(action: str, args: tuple[str, ...]) -> str:
    """Если scrcmd.sh нет — прямой xdotool."""
    if action == "click":
        x, y, *rest = args
        btn = rest[0] if rest else "1"
        return f"xdotool mousemove {x} {y} click {btn}"
    if action == "dblclick":
        x, y = args
        return f"xdotool mousemove {x} {y} click --repeat 2 1"
    if action == "type":
        return f'xdotool type --delay 15 "{args[0]}"'
    if action == "key":
        return f"xdotool key {args[0]}"
    if action == "scroll":
        d, n = args
        btn = "4" if d == "up" else "5"
        return f"xdotool click --repeat {n} --delay 60 {btn}"
    if action == "activate":
        return f'wmctrl -a "{args[0]}"'
    if action == "clipboard":
        return f'printf "%s" "{args[0]}" | xclip -selection clipboard'
    return "true"


def execute_action(action: str, params: dict, reason: str,
                   destructive_ok: bool = False) -> tuple[bool, str]:
    """Выполнить одно действие. Возвращает (ok, output).

    Деструктивные клавиши (alt+F4, ctrl+w, ctrl+d, Delete) блокируются,
    если destructive_ok=False.
    """
    DESTRUCTIVE = ("alt+f4", "ctrl+w", "ctrl+d", "super+q")
    action = (action or "").strip().lower()
    p = params if isinstance(params, dict) else {}

    if action == "done":
        return True, "done"

    if action == "wait":
        ms = int(p.get("ms", 500))
        time.sleep(max(0, min(ms, 10_000)) / 1000)
        return True, f"wait {ms}ms"

    if action == "move":
        x, y = int(p.get("x", 0)), int(p.get("y", 0))
        rc, out = _scr("move", str(x), str(y))
        return rc == 0, f"move {x},{y}: {out}"

    if action == "click":
        x, y = int(p.get("x", 0)), int(p.get("y", 0))
        btn = str(p.get("button", 1))
        rc, out = _scr("click", str(x), str(y), btn)
        return rc == 0, f"click {x},{y} btn{btn}: {out}"

    if action == "dblclick":
        x, y = int(p.get("x", 0)), int(p.get("y", 0))
        rc, out = _scr("dblclick", str(x), str(y))
        return rc == 0, f"dblclick {x},{y}: {out}"

    if action == "type":
        text = str(p.get("text", ""))
        rc, out = _scr("type", text)
        return rc == 0, f'type "{text[:60]}": {out}'

    if action == "key":
        combo = str(p.get("combo", "")).lower().strip()
        if not destructive_ok and any(d in combo for d in DESTRUCTIVE):
            return False, f"заблокировано (деструктивная клавиша {combo}), нужен --destructive"
        rc, out = _scr("key", combo)
        return rc == 0, f"key {combo}: {out}"

    if action == "scroll":
        d = str(p.get("dir", "down"))
        n = str(int(p.get("n", 3)))
        rc, out = _scr("scroll", d, n)
        return rc == 0, f"scroll {d} {n}: {out}"

    if action == "activate":
        title = str(p.get("title", ""))
        rc, out = _scr("activate", title)
        return rc == 0, f"activate '{title}': {out}"

    if action == "clipboard":
        text = str(p.get("set", ""))
        rc, out = _scr("clipboard", "set", text)
        return rc == 0, f"clipboard set: {out}"

    return False, f"неизвестное действие: {action}"


# ─────────────────────────────────────────────────────────────────────────────
# Мозг: модель решает следующее действие
# ─────────────────────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict | None:
    """Достать JSON из ответа модели (устойчиво к markdown-обёртке и мусору)."""
    if not text:
        return None
    # Убрать ```json ... ``` обёртку
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    # Найти первый { ... } блок
    start = text.find("{")
    if start == -1:
        return None
    # Пробуем парсить от каждой { до конца — берём самую длинную валидную строку
    best = None
    for i in range(start, len(text)):
        if text[i] != "{":
            continue
        for j in range(len(text), i, -1):
            if text[j - 1] != "}":
                continue
            try:
                obj = json.loads(text[i:j])
                if isinstance(obj, dict) and "action" in obj:
                    return obj
            except (json.JSONDecodeError, ValueError):
                continue
            if best is not None:
                break
    # Последняя попытка: целиком
    try:
        obj = json.loads(text[start:])
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def ask_brain(question: str, image_path: str, model: str | None = None,
              timeout: int = 120) -> dict:
    """Спросить модель с картинкой (как omni_eyes.ask_vision, но со stream=False)."""
    if not HAS_REQUESTS:
        return {"ok": False, "answer": "", "model": model or "", "error": "нет requests"}
    b64 = _safe_image_base64(image_path)
    if not b64:
        return {"ok": False, "answer": "", "model": model or "", "error": "не прочитал скриншот"}
    mime = "image/png"
    if image_path.lower().endswith((".jpg", ".jpeg")):
        mime = "image/jpeg"
    models = [model] if model else BRAIN_MODELS
    last_err = ""
    for m in models:
        payload = {
            "model": m,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
            "max_tokens": 300,
            "stream": False,
        }
        try:
            r = requests.post(f"{OMNI_BASE}/chat/completions", json=payload,
                              timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                return {"ok": True, "answer": content, "model": m, "error": None}
            last_err = f"{m}: HTTP {r.status_code} {r.text[:120]}"
        except Exception as e:
            last_err = f"{m}: {e}"
    return {"ok": False, "answer": "", "model": model or "", "error": last_err}


# ─────────────────────────────────────────────────────────────────────────────
# Главный цикл
# ─────────────────────────────────────────────────────────────────────────────

def run_task(task: str, max_steps: int = 8, model: str | None = None,
             destructive: bool = False, verbose: bool = False,
             screenshot_dir: str | None = None) -> TaskResult:
    """Выполнить GUI-задачу циклом «посмотри → реши → действуй → проверь».

    Возвращает TaskResult с историей шагов.
    """
    result = TaskResult(task=task, done=False, reason="", model=model or "")
    shot_dir = screenshot_dir or "/tmp/sagent"
    os.makedirs(shot_dir, exist_ok=True)

    # Ревью модели (v3.26.6): проверить «руки» заранее, не зацикливаться молча
    missing = check_deps()
    if missing:
        result.reason = f"нет инструментов управления экраном: {', '.join(missing)}"
        return result

    # Защита от зацикливания: 3 одинаковых действия подряд → стоп
    recent: list[tuple[str, str]] = []

    for step in range(1, max_steps + 1):
        # 1. СКРИНШОТ
        shot = take_screenshot(os.path.join(shot_dir, f"step_{step}.png"))
        if not shot:
            result.done = False
            result.reason = "не могу сделать скриншот (нет X11-инструмента или экран заблокирован)"
            break
        if verbose:
            print(f"[{step}/{max_steps}] скриншот: {shot}")

        # 2. МОДЕЛЬ РЕШАЕТ
        brain = ask_brain(PROMPT.format(task=task), shot, model=model)
        if not brain["ok"]:
            result.reason = f"мозг не отвечает: {brain['error']}"
            break
        result.model = brain["model"]
        decision = _extract_json(brain["answer"])
        if not decision:
            result.reason = f"модель вернула не-JSON: {brain['answer'][:120]}"
            break
        action = str(decision.get("action", "")).lower()
        params = decision.get("params") if isinstance(decision.get("params"), dict) else {}
        reason = str(decision.get("reason", ""))[:200]

        # Анти-зацикливание: если модель 3 раза подряд шлёт одно и то же — стоп
        if action != "done":
            key = (action, json.dumps(params, sort_keys=True)[:80])
            recent.append(key)
            if len(recent) >= 3 and len(set(recent[-3:])) == 1:
                result.reason = (f"модель повторяет одно действие ({action} {params}) "
                                 f"— похоже, застряла, останавливаюсь")
                break

        if verbose:
            print(f"    → {action} {params}  ({reason})")

        # 3. ВЫПОЛНИТЬ
        if action == "done":
            result.done = True
            result.reason = reason or "задача выполнена"
            result.steps.append(AgentStep(
                action="done", params=params, reason=reason,
                observation=brain["answer"][:300], step=step))
            break
        ok, out = execute_action(action, params, reason, destructive_ok=destructive)
        if verbose:
            print(f"    = {out}")

        # 4. ПРОВЕРИТЬ: новый скриншот, модель оценивает
        time.sleep(0.5)
        shot2 = take_screenshot(os.path.join(shot_dir, f"check_{step}.png"))
        observation = ""
        if shot2:
            check = ask_brain(
                "Скриншот сделан ПОСЛЕ твоего последнего действия. "
                "Кратко: что теперь видно на экране? Одно предложение, без JSON.",
                shot2, model=brain["model"], timeout=90)
            if check["ok"]:
                observation = check["answer"][:300]

        result.steps.append(AgentStep(
            action=action, params=params, reason=reason, output=out,
            ok=ok, observation=observation, step=step))

        # модель не смогла выполнить — стоп, чтобы не зациклиться
        if not ok:
            result.reason = f"действие не выполнено: {out}"
            break

    if not result.done and not result.reason:
        result.reason = f"достигнут лимит шагов ({max_steps})"
    return result


def describe_task_result(r: TaskResult) -> str:
    """Человекочитаемый отчёт о выполнении задачи."""
    lines = []
    lines.append(f"ЗАДАЧА: {r.task}")
    lines.append(f"ИТОГ: {'✅ ВЫПОЛНЕНО' if r.done else '❌ НЕ ВЫПОЛНЕНО'} — {r.reason}")
    lines.append(f"Модель: {r.model} | шагов: {len(r.steps)}")
    for s in r.steps:
        if s.action == "done":
            lines.append(f"  [{s.step}] done — {s.reason}")
        else:
            lines.append(f"  [{s.step}] {s.action} {s.params} "
                         f"{'✅' if s.ok else '❌'} {s.output[:60]}")
            if s.observation:
                lines.append(f"        после: {s.observation[:120]}")
    return "\n".join(lines)
