"""Единый конфиг token-diet: пути + секреты. Ноль хардкода в модулях.

УРОК 16.08 (архитектура «Мозг в Чемодане»):
- раньше пути были размазаны по 8 модулям (/media/ro/KINGSTON1/...,
  /tmp/token-diet-clone, /tmp/autopilot*...) — новый инстанс «из файла»
  не собирался. Теперь ВСЕ пути резолвятся отсюда от TD_HOME.
УРОК 17.08: внешний диск Kingston больше НЕ используется — всё на
внутреннем диске (~/token-diet-project, память TD_HOME/memory).

Правила:
1. TD_HOME   — корень проекта (там run.sh, .env, state/, memory/)
2. TD_VAULT  — Obsidian-хранилище (по умолчанию TD_HOME/memory)
3. state/    — персистентное состояние (переживает ребут)
4. load_secrets() — читает ТОЛЬКО .env + мигрирует старые места
"""

from __future__ import annotations

import os
from pathlib import Path


# ── Корень проекта ────────────────────────────────────────────────────────
# Приоритет: TD_HOME env → родитель пакета (там run.sh/.env) → cwd
def _default_home() -> Path:
    env = os.environ.get("TD_HOME")
    if env:
        return Path(env).expanduser()
    # token_diet/config.py → корень проекта
    here = Path(__file__).resolve()
    for cand in (here.parent.parent, Path.cwd()):
        if (cand / "pyproject.toml").exists():
            return cand
    return here.parent.parent


TD_HOME = _default_home()

# ── Obsidian-хранилище ─────────────────────────────────────────────────────
def _env_from_file(key: str) -> str:
    """Читает ключ из .env (TD_HOME/.env и ~/.env) без полной загрузки."""
    for env_file in (TD_HOME / ".env", Path.home() / ".env"):
        if not env_file.exists():
            continue
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def _default_vault() -> Path:
    env = os.environ.get("TD_VAULT") or _env_from_file("TD_VAULT")
    if env:
        return Path(env).expanduser()
    # УРОК 17.08: внешний диск Kingston больше НЕ используется — память
    # живёт на внутреннем диске (TD_HOME/memory), как и проект.
    return TD_HOME / "memory"


TD_VAULT = _default_vault()

# ── Директории ─────────────────────────────────────────────────────────────
TD_STATE = TD_HOME / "state"
TD_LOGS = TD_HOME / "logs"
TD_ENV_FILE = TD_HOME / ".env"


def ensure_dirs() -> None:
    """Создать state/ и logs/ при первом запуске (переживают ребут)."""
    for d in (TD_STATE, TD_LOGS, TD_VAULT):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


ensure_dirs()

# ── Состояние (персистентное, не /tmp!) ───────────────────────────────────
def state_path(name: str) -> Path:
    """Файл состояния в state/ (autopilot_state.json, proxy_stats.json, ...)."""
    return TD_STATE / name


def log_path(name: str) -> Path:
    """Лог-файл в logs/ (market_guard.log, autopilot.log, ...)."""
    return TD_LOGS / name


# ── Секреты: ОДНО место на земле ──────────────────────────────────────────
# Старые места для миграции (ничего не теряется при первом запуске):
_LEGACY_SECRET_FILES = [
    Path.home() / ".token-diet" / "deepseek_key",
    Path.home() / ".token-diet" / "telegram_bot_token",
    Path.home() / ".tinkoff" / "token",
    Path.home() / ".env",
]


def load_secrets() -> dict[str, str]:
    """Читает .env (TD_HOME/.env) + мигрирует старые места.

    Возвращает dict вида {"ANYMODEL_API_KEY": "...", ...}.
    Никакой ключ не хардкодится в коде.
    """
    secrets: dict[str, str] = {}

    # 1. env-переменные — высший приоритет
    for k in (
        "ANYMODEL_API_KEY", "ANYMODEL_BASE_URL", "DEEPSEEK_API_KEY",
        "TINKOFF_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID",
        "UPSTREAM_URL", "TD_VAULT", "TD_HOME",
    ):
        v = os.environ.get(k)
        if v:
            secrets[k] = v

    # 2. .env проекта
    for env_file in (TD_ENV_FILE, Path.home() / ".env"):
        if not env_file.exists():
            continue
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v and k not in secrets:
                    secrets[k] = v
        except OSError:
            continue

    # 3. миграция со старых мест (только если ключа ещё нет)
    for legacy in _LEGACY_SECRET_FILES:
        if not legacy.exists():
            continue
        try:
            v = legacy.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not v or v.startswith("#"):
            continue
        mapping = {
            "deepseek_key": "DEEPSEEK_API_KEY",
            "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
            "token": "TINKOFF_TOKEN",
        }
        key = mapping.get(legacy.name)
        if key and key not in secrets:
            secrets[key] = v

    return secrets


def get_secret(name: str, default: str | None = None) -> str | None:
    """Один секрет (кэш внутри процесса)."""
    return load_secrets().get(name, default)
