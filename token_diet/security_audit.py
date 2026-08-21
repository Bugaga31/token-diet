"""security_audit — защита от брутфорса (reverse-engineering SonicJS Security Audit).

УРОК 16.08 (sonicjs.com/plugins): Security Audit Plugin делает:
  - мониторинг попыток входа;
  - автоматический IP-lockout при повторных неудачах;
  - лог событий безопасности;
  - дашборд аудита.

У нас это защищает: Telegram-командера (чужие юзеры), serve-прокси
(чужие ключи), панель. Всё детерминированно, без нейронок.

Логика lockout (как у SonicJS):
  - N неудач за окно → бан на lockout_seconds;
  - удача сбрасывает счётчик;
  - события пишутся в state/security_audit.jsonl (переживает рестарт).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    from .config import state_path
except Exception:  # pragma: no cover
    def state_path(name: str) -> Path:
        return Path.home() / f".token-diet-{name}"


@dataclass
class SecurityAudit:
    max_failures: int = 5          # сколько неудач до бана
    window_seconds: int = 300      # окно (5 мин)
    lockout_seconds: int = 900     # бан (15 мин)
    log_file: Path = field(default_factory=lambda: state_path("security_audit.jsonl"))
    _failures: dict[str, list[float]] = field(default_factory=dict)
    _locked_until: dict[str, float] = field(default_factory=dict)

    def _load(self) -> None:
        """Догрузить прошлые баны/неудачи (персистентность через state/)."""
        if not self.log_file.exists():
            return
        try:
            for line in self.log_file.read_text(encoding="utf-8").splitlines():
                ev = json.loads(line)
                who = ev.get("who", "")
                if ev.get("event") == "lockout":
                    self._locked_until[who] = ev.get("until", 0.0)
                elif ev.get("event") == "fail":
                    self._failures.setdefault(who, []).append(ev.get("ts", 0.0))
        except (OSError, ValueError):
            pass

    def _log(self, who: str, event: str, detail: str = "") -> None:
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(
                    {"ts": time.time(), "who": who, "event": event, "detail": detail},
                    ensure_ascii=False,
                ) + "\n")
        except OSError:
            pass

    def _prune(self, who: str) -> None:
        now = time.time()
        self._failures[who] = [
            t for t in self._failures.get(who, [])
            if now - t < self.window_seconds
        ]

    def check(self, who: str) -> bool:
        """Можно ли пускать who? False = забанен."""
        now = time.time()
        if self._locked_until.get(who, 0.0) > now:
            return False
        self._prune(who)
        return len(self._failures.get(who, [])) < self.max_failures

    def fail(self, who: str, detail: str = "") -> bool:
        """Зафиксировать неудачу. True = теперь забанен."""
        now = time.time()
        self._prune(who)
        self._failures.setdefault(who, []).append(now)
        self._log(who, "fail", detail)
        if len(self._failures[who]) >= self.max_failures:
            until = now + self.lockout_seconds
            self._locked_until[who] = until
            self._log(who, "lockout", f"until {until:.0f}")
            return True
        return False

    def success(self, who: str, detail: str = "") -> None:
        """Успешный вход — сброс счётчика."""
        self._failures.pop(who, None)
        self._locked_until.pop(who, None)
        self._log(who, "success", detail)

    def status(self, who: str) -> dict:
        self._prune(who)
        locked = self._locked_until.get(who, 0.0) > time.time()
        return {
            "who": who,
            "allowed": not locked,
            "failures": len(self._failures.get(who, [])),
            "locked_until": self._locked_until.get(who, 0.0),
        }


def make_audit() -> SecurityAudit:
    """Единый инстанс (создаётся один раз на процесс, кэш в модуле)."""
    global _AUDIT
    if _AUDIT is None:
        _AUDIT = SecurityAudit()
        _AUDIT._load()
    return _AUDIT


_AUDIT: SecurityAudit | None = None
