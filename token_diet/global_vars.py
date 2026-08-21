"""global_vars — переиспользуемые переменные {token} (reverse-engineering SonicJS).

УРОК 16.08 (sonicjs.com/plugins): Global Variables Plugin позволяет
определять переменные и инжектить их в контент как {variable_key},
с KV-кэшем и программным доступом.

У нас это экономит токены в правилах/промптах: вместо повторения
«стоп 39.5, тейк 43.5, тикер SNGSP» — {stop}, {target}, {ticker}.
Один раз определил → подставляется везде. Плюс кэш (не перечитывать
файл на каждый вызов).

Хранилище: state/global_vars.json (персистентно, переживает рестарт).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:
    from .config import state_path
except Exception:  # pragma: no cover
    def state_path(name: str) -> Path:
        return Path.home() / f".token-diet-{name}"


_TOKEN_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class GlobalVars:
    def __init__(self, store_file: str | Path | None = None):
        self.store_file = Path(store_file) if store_file else state_path("global_vars.json")
        self._vars: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.store_file.exists():
            return
        try:
            self._vars = json.loads(self.store_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._vars = {}

    def _save(self) -> None:
        try:
            self.store_file.parent.mkdir(parents=True, exist_ok=True)
            self.store_file.write_text(
                json.dumps(self._vars, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def set(self, key: str, value: str) -> None:
        self._vars[key] = value
        self._save()

    def set_many(self, items: dict[str, str]) -> None:
        self._vars.update(items)
        self._save()

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._vars.get(key, default)

    def all(self) -> dict[str, str]:
        return dict(self._vars)

    def expand(self, text: str) -> str:
        """Подставить {key} → значение. Неизвестные ключи остаются как есть."""
        def _sub(m: re.Match[str]) -> str:
            return self._vars.get(m.group(1), m.group(0))
        return _TOKEN_RE.sub(_sub, text)

    def expand_many(self, texts: list[str]) -> list[str]:
        return [self.expand(t) for t in texts]

    def keys(self) -> list[str]:
        return sorted(self._vars)


_default: GlobalVars | None = None


def vars_store() -> GlobalVars:
    """Единый инстанс (кэш на процесс)."""
    global _default
    if _default is None:
        _default = GlobalVars()
    return _default


# Удобные обёртки
def set_var(key: str, value: str) -> None:
    vars_store().set(key, value)


def get_var(key: str, default: str | None = None) -> str | None:
    return vars_store().get(key, default)


def expand(text: str) -> str:
    return vars_store().expand(text)
