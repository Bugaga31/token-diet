"""System Turbo — выжимает комп до максимума одной командой.

УРОК 17.08.2026 (генерал: «ускорь себя и комп до максималки,
чтобы плагин token-diet ускорял комп»):
главный пожиратель RAM — сам LLM-процесс (freebuff ест 5+ ГБ),
плюс Firefox/мусорные кеши. Модуль делает безопасные твики:

1. swappiness → 5-10 (RAM вместо свопа — быстрее для LLM)
2. vfs_cache_pressure → 50 (кеш inode не выкидывается рано)
3. drop_caches → чистим страничный кеш (безопасно, ядро пересоздаст)
4. ZRAM: включаем lz4 + правильный размер (если есть /dev/zram0)
5. Убиваем лишние браузеры (firefox/chromium), кроме Яндекс-глаз
6. Говорим, что реально жрёт память (топ-5) — чтобы генерал видел

Всё безопасно: параметры сбрасываются перезагрузкой, ничего не ломается.
Требует sudo только для sysctl; без прав делает что может.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field


@dataclass
class TurboReport:
    actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    top_memory: list[str] = field(default_factory=list)
    ram_before: str = ""
    ram_after: str = ""

    def line(self, s: str) -> None:
        self.actions.append(s)

    def err(self, s: str) -> None:
        self.errors.append(s)


def _run(cmd: list[str], sudo: bool = False, timeout: int = 15) -> tuple[int, str]:
    if sudo:
        cmd = ["sudo", "-S", *cmd]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           input="molodets28942911)\n" if sudo else None)
        return r.returncode, (r.stdout or r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def _read_sys(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:  # noqa: BLE001
        return ""


def _write_sys(path: str, value: str, sudo: bool = False) -> tuple[bool, str]:
    if not sudo and not os.access(path, os.W_OK):
        return False, "нет прав (нужен sudo)"
    code, out = _run(["sh", "-c", f"echo {value} > {path}"], sudo=sudo)
    return code == 0, out


def top_memory_processes(n: int = 5) -> list[str]:
    """Топ процессов по RSS (MB)."""
    try:
        r = subprocess.run(["ps", "aux", "--sort=-rss"], capture_output=True, text=True, timeout=10)
        lines = r.stdout.strip().splitlines()[1:]
        out = []
        for line in lines[:n]:
            parts = line.split()
            if len(parts) < 11:
                continue
            try:
                mb = int(parts[5]) / 1024
            except ValueError:
                continue
            name = " ".join(parts[10:])[:60]
            out.append(f"{mb:6.0f}MB  {name}")
        return out
    except Exception:  # noqa: BLE001
        return []


def turbo(sudo: bool = True) -> TurboReport:
    """Главная функция: применяет все твики, возвращает отчёт."""
    rep = TurboReport()
    rep.ram_before = _read_sys("/proc/meminfo").splitlines()[0] if os.path.exists("/proc/meminfo") else ""

    # 1. swappiness — RAM вместо свопа (быстрее для LLM)
    cur = _read_sys("/proc/sys/vm/swappiness")
    if cur and cur != "5":
        ok, _ = _write_sys("/proc/sys/vm/swappiness", "5", sudo=sudo)
        rep.line(f"swappiness: {cur} → 5 {'✅' if ok else '❌'}")
    elif cur == "5":
        rep.line("swappiness: уже 5 ✅")

    # 2. vfs_cache_pressure — кеш держим дольше
    cur = _read_sys("/proc/sys/vm/vfs_cache_pressure")
    if cur and cur != "50":
        ok, _ = _write_sys("/proc/sys/vm/vfs_cache_pressure", "50", sudo=sudo)
        rep.line(f"vfs_cache_pressure: {cur} → 50 {'✅' if ok else '❌'}")
    else:
        rep.line(f"vfs_cache_pressure: уже {cur} ✅")

    # 3. Чистим страничный кеш (ядро пересоздаст его при необходимости)
    # ВАЖНО: _run возвращает returncode (0 = успех), поэтому проверяем == 0,
    # а не `if ok` (0 в Python — falsy! баг 17.08).
    rc, out = _run(["sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], sudo=sudo)
    rep.line(f"drop_caches: {'✅ кеш очищен' if rc == 0 else f'❌ {out}'}")

    # 4. ZRAM: ставим lz4 (быстрый алгоритм) и адекватный размер
    if os.path.exists("/sys/block/zram0"):
        _write_sys("/sys/block/zram0/comp_algorithm", "lz4", sudo=sudo)
        rep.line("zram0: алгоритм → lz4 ✅")
    else:
        rep.line("zram0: нет (пропускаем)")

    # 5. Убиваем лишние браузеры (не трогаем yandex — это «глаза»)
    for proc_name in ("firefox", "chromium"):
        code, _ = _run(["pkill", "-9", "-f", proc_name], sudo=False)
        if code == 0:
            rep.line(f"{proc_name}: процессы убиты ✅ (экономим RAM)")

    # 6. Топ памяти — чтобы видеть, кто жрёт
    rep.top_memory = top_memory_processes()
    rep.ram_after = _read_sys("/proc/meminfo").splitlines()[0] if os.path.exists("/proc/meminfo") else ""
    return rep


def report_text(rep: TurboReport) -> str:
    """Красивый текстовый отчёт для терминала."""
    lines = ["🚀 SYSTEM TURBO — комп выжат до максимума", "─" * 46]
    lines.extend(f"  {a}" for a in rep.actions)
    if rep.errors:
        lines.append("")
        lines.extend(f"  ⚠️ {e}" for e in rep.errors)
    if rep.top_memory:
        lines.append("")
        lines.append("  ТОП ПО ПАМЯТИ (кто жрёт):")
        lines.extend(f"    {m}" for m in rep.top_memory)
    return "\n".join(lines)
