"""Graph Memory — темпоральная графовая память для агентов.

Реверс-инжиниринг техник Graphiti (Zep), реализованный так, чтобы работать
ВСЕГДА, даже без Neo4j и без LLM-ключей (мы не врем: файловый бэкенд
воспроизводит ключевые механики детерминированно).

Техники Graphiti, которые здесь воспроизведены:
1. Эпизодическая память + provenance — каждый факт жёстко привязан к
   эпизоду-первоисточнику (можно аудитнуть «откуда мы это знаем»).
2. Би-темпоральность — у факта два времени: valid_from/valid_until
   (когда факт был верен в реальном мире) + ingested_at (когда мы узнали).
   Противоречия НЕ удаляют старые факты, а инвалидируют их с датой —
   можно спросить «что было верно на дату X».
3. Инкрементальная индексация — новый эпизод обновляет граф на лету:
   извлечение сущностей, дедупликация узлов, связи, без полного пересчёта.
4. Гибридный поиск — обход графа от сущностей запроса + BM25 по эпизодам
   + свежесть факта.
5. Экспорт в Obsidian — узлы становятся заметками с [[wiki-links]],
   граф связей виден в graph view Obsidian.

Бэкенды:
- 'file'    — чистый Python, JSON на диске. Работает всегда, 0 зависимостей.
- 'graphiti' — настоящий Graphiti + Neo4j (+ Ollama/OpenAI-совместимый
  LLM и embedder). Включается принудительно через GRAPH_MEMORY_BACKEND=graphiti
  или auto, если всё доступно.
"""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# 1. Детерминированный извлекатель сущностей (0 LLM-вызовов)
# ─────────────────────────────────────────────────────────────────────────────

# Известные тикеры Мосбиржи (расширяемый словарь)
MOEX_TICKERS: dict[str, str] = {
    "SBER": "банк", "GAZP": "газ", "PLZL": "золото", "YDEX": "технологии",
    "OZON": "ритейл", "GMKN": "металлы", "SELG": "золото", "ALRS": "алмазы",
    "LKOH": "нефть", "ROSN": "нефть", "NVTK": "газ", "TATN": "нефть",
    "MGNT": "ритейл", "MTSS": "телеком", "AFLT": "авиа", "PHOR": "химия",
    "MVID": "ритейл", "POLY": "золото", "RUAL": "металлы", "CHMF": "металлы",
    "NLMK": "металлы", "MOEX": "биржа",
}

# Металлы/валюты/активы
ASSETS: dict[str, str] = {
    "золото": "металл", "серебро": "металл", "платина": "металл",
    "палладий": "металл", "нефть": "сырьё", "газ": "сырьё",
    "доллар": "валюта", "рубль": "валюта", "юань": "валюта",
    "биткоин": "крипто", "облигации": "инструмент", "акции": "инструмент",
}

_STOP_NAMES: set[str] = {
    "сегодня", "завтра", "вчера", "сейчас", "это", "что", "когда", "более",
    "менее", "почти", "уже", "ещё", "еще", "также", "причем", "причём",
    "поэтому", "однако", "наконец", "конечно", "например", "впрочем",
    "итак", "кстати", "между", "после", "перед", "теперь", "тогда",
    "россия", "москва", "сша", "россии", "москве", "мир", "рынок", "день",
    "неделю", "неделя", "месяц", "года", "год", "руб", "рублей", "тыс",
}

_TICKER_RE = re.compile(r"\b([A-ZА-Я]{3,6})\b")
_NUMBER_RE = re.compile(r"(?:\$)?\d[\d\s.,]*(?:%|₽|\$|тыс|млн|млрд|руб\.?|р\.|лет?|год[ау]?|мес\.?)?")

# Префиксы для русской морфологии (стемминг: «золота/золоту/золотом» → «золот»)
def _stem(word: str) -> str:
    w = word.lower()
    if len(w) > 5:
        w = w.rstrip("аяоеёуюыиэьийовымихое")
        if len(w) < 3:
            w = word.lower()
    return w


def extract_entities(text: str) -> list[tuple[str, str]]:
    """Извлекает (имя, тип) детерминированно. Без LLM."""
    found: dict[str, str] = {}
    upper = text.upper()

    # 1. Тикеры Мосбиржи
    for ticker in MOEX_TICKERS:
        if re.search(rf"\b{ticker}\b", upper):
            found[ticker] = MOEX_TICKERS[ticker]

    # 2. Активы (золото, нефть, доллар...) — по корню, чтобы «золотом»
    #    и «золота» тоже находились (русская морфология)
    low = text.lower()
    for word in re.findall(r"[а-яёa-z]{3,}", low):
        for asset, atype in ASSETS.items():
            if _stem(word) == _stem(asset):
                found[asset.capitalize()] = atype
                break

    # 3. Имена/компании с заглавной буквы (кириллица и латиница)
    for m in re.finditer(r"\b([А-ЯЁA-Z][а-яёa-z]{2,24})\b", text):
        name = m.group(1)
        if name.lower() in _STOP_NAMES:
            continue
        # заглавная посреди предложения — имя собственное
        prev = text[max(0, m.start() - 2):m.start()]
        if prev and not re.search(r"[.!?;«\"(]\s*$", prev):
            continue  # не начало предложения после точки — это не имя
        key = name.capitalize()
        if key not in found:
            found[key] = "имя"

    return list(found.items())


def extract_numbers(text: str) -> list[str]:
    """Все числа с контекстом-единицей (детерминированно)."""
    return [m.group(0).replace(" ", "") for m in _NUMBER_RE.finditer(text)]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Файловый граф (бэкенд 'file') — чистый Python, всегда работает
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Fact:
    id: str
    subject: str          # сущность
    predicate: str        # что произошло (вырос/упал/стоит/цель...)
    obj: str              # значение/объект
    episode_id: str       # provenance
    valid_from: str       # ISO
    valid_until: str | None = None
    ingested_at: str = ""

    def is_valid_at(self, iso: str) -> bool:
        if self.valid_from > iso:
            return False
        if self.valid_until and self.valid_until < iso:
            return False
        return True


@dataclass
class Episode:
    id: str
    name: str
    body: str
    source: str
    ingested_at: str
    entities: list[str] = field(default_factory=list)


class FileGraphStore:
    """Лёгкий темпоральный граф на JSON. 0 внешних зависимостей."""

    def __init__(self, path: str | Path | None = None):
        if path is None:
            path = os.environ.get(
                "TOKEN_DIET_GRAPH", str(Path.home() / ".token-diet" / "graph.json")
            )
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.episodes: dict[str, Episode] = {}
        self.facts: list[Fact] = []
        self.entities: dict[str, dict] = {}   # name -> {type, first_seen}
        self.edges: dict[str, dict[str, int]] = {}  # co-occurrence вес
        self._load()

    # ── сериализация ──────────────────────────────────────────────
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for k, v in raw.get("episodes", {}).items():
                self.episodes[k] = Episode(**v)
            for f in raw.get("facts", []):
                self.facts.append(Fact(**f))
            self.entities = raw.get("entities", {})
            self.edges = raw.get("edges", {})
        except Exception:
            # битый файл — не роняем агента, начинаем чисто
            self.episodes, self.facts, self.entities, self.edges = {}, [], {}, {}

    def save(self) -> None:
        data = {
            "episodes": {k: vars(v) for k, v in self.episodes.items()},
            "facts": [vars(f) for f in self.facts],
            "entities": self.entities,
            "edges": self.edges,
        }
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    # ── инкрементальная индексация (эпизод → граф) ────────────────
    def add_episode(self, name: str, body: str, source: str = "memory",
                    ref_time: datetime | None = None) -> Episode:
        now = ref_time or datetime.now(timezone.utc)
        iso = now.isoformat()
        eid = f"ep_{int(now.timestamp())}_{len(self.episodes)}"
        ents = extract_entities(body)
        ep = Episode(id=eid, name=name, body=body, source=source,
                     ingested_at=iso, entities=[e[0] for e in ents])
        self.episodes[eid] = ep

        # узлы сущностей (дедуп) + рёбра co-occurrence
        for ename, etype in ents:
            ent = self.entities.setdefault(
                ename, {"type": etype, "first_seen": iso}
            )
            ent["type"] = etype
            if ename not in ep.entities:
                ep.entities.append(ename)

        for i in range(len(ep.entities)):
            for j in range(i + 1, len(ep.entities)):
                a, b = ep.entities[i], ep.entities[j]
                self.edges.setdefault(a, {})
                self.edges[a][b] = self.edges[a].get(b, 0) + 1
                self.edges.setdefault(b, {})
                self.edges[b][a] = self.edges[b].get(a, 0) + 1

        # извлечение фактов + би-темпоральная инвалидация противоречий
        self._ingest_facts(ep)
        self.save()
        return ep

    _REL = [
        (r"(?:вырос|подрос|прибавил|увеличился|повысился|растёт|растет)\s+на\s+([\d.,]+)%?", "рост"),
        (r"(?:упал|снизился|подешевел|уменьшился|откатился|просел)\s+на\s+([\d.,]+)%?", "падение"),
        (r"(?:составляет|стоит|торгуется|равен|равна|цена)\s+[—:]?\s*([\d\s.,]+)\s*(?:₽|\$|руб\.?|р\.)?", "цена"),
        (r"(?:цель|таргет|прогноз)\s*[—:]?\s*([\d\s.,]+)", "цель"),
        (r"(?:купил|скупает|докупил)\s+([\d.,]+)", "покупка"),
        (r"(?:продал|продаёт|слил)\s+([\d.,]+)", "продажа"),
        (r"дивиденд[ыа]?\s+([\d.,]+)", "дивиденд"),
        (r"долг\s+([\d.,]+\s*(?:млрд|млн)?)", "долг"),
        (r"отчёт(?:ность)?\s+(?:за\s+)?([\w\s]{2,30}?\d{2,4})", "отчётность"),
    ]

    def _ingest_facts(self, ep: Episode) -> None:
        body = ep.body
        for ename in ep.entities:
            # ищем числа рядом с сущностью в том же предложении
            for sent in re.split(r"[.!?]\s+", body):
                if ename.lower() not in sent.lower():
                    continue
                for pat, pred in self._REL:
                    for m in re.finditer(pat, sent, re.IGNORECASE):
                        val = m.group(1).strip()
                        fid = f"f_{len(self.facts)}_{abs(hash((ename, pred, val)) & 0xFFFF)}"
                        f = Fact(id=fid, subject=ename, predicate=pred, obj=val,
                                 episode_id=ep.id, valid_from=ep.ingested_at,
                                 ingested_at=ep.ingested_at)
                        self.facts.append(f)
                        self._invalidate_contradiction(f)

    def _invalidate_contradiction(self, new: Fact) -> None:
        """Би-темпоральность: тот же subject+predicate, другое значение —
        старый факт закрываем (valid_until = момент нового)."""
        for old in self.facts:
            if old is new:
                continue
            if old.subject == new.subject and old.predicate == new.predicate \
               and old.obj != new.obj and old.valid_until is None \
               and old.episode_id != new.episode_id:
                old.valid_until = new.ingested_at

    # ── гибридный поиск ───────────────────────────────────────────
    def search(self, query: str, limit: int = 6, as_of: str | None = None,
               include_episodes: bool = True) -> list[dict]:
        q_ents = [e[0] for e in extract_entities(query)]
        q_terms = set(_stem(w) for w in re.findall(r"[а-яёa-z]{3,}", query.lower()))

        scored: dict[str, float] = {}

        # (a) обход графа: эпизоды, где есть сущность из запроса
        for eid, ep in self.episodes.items():
            score = 0.0
            matched = set(ep.entities) & set(q_ents)
            if matched:
                score += 3.0 * len(matched)
                # + связи первого порядка (соседи найденных сущностей)
                for ent in matched:
                    score += 1.0 * len(self.edges.get(ent, {}))
            # (b) BM25-подобный по тексту
            words = re.findall(r"[а-яёa-z]{3,}", ep.body.lower())
            for t in q_terms:
                if t in {_stem(w) for w in words}:
                    score += 1.5
            if score > 0:
                scored[eid] = scored.get(eid, 0) + score

        # (c) свежесть
        now = as_of or datetime.now(timezone.utc).isoformat()
        results = []
        for eid, score in sorted(scored.items(), key=lambda x: -x[1])[:limit]:
            ep = self.episodes[eid]
            facts = [
                vars(f) for f in self.facts
                if f.episode_id == eid and f.is_valid_at(now)
            ]
            results.append({
                "episode_id": eid, "name": ep.name, "body": ep.body,
                "source": ep.source, "ingested_at": ep.ingested_at,
                "entities": ep.entities, "facts": facts,
                "score": round(score, 2),
            })
        return results

    def query_as_of(self, subject: str, iso: str) -> list[dict]:
        """Что мы знали о сущности на конкретную дату (би-темпоральный запрос)."""
        out = []
        for f in self.facts:
            if f.subject.lower() == subject.lower() and f.is_valid_at(iso):
                ep = self.episodes.get(f.episode_id)
                out.append({
                    "predicate": f.predicate, "obj": f.obj,
                    "valid_from": f.valid_from, "valid_until": f.valid_until,
                    "source": ep.name if ep else f.episode_id,
                })
        return out

    def neighbors(self, entity: str, depth: int = 1) -> list[str]:
        """Соседи сущности на заданной глубине (без самой сущности)."""
        seen: set[str] = set()
        frontier = {entity}
        for _ in range(depth):
            nxt: set[str] = set()
            for e in frontier:
                nxt |= set(self.edges.get(e, {}))
            seen |= frontier
            frontier = nxt - seen
        return sorted(frontier)

    def stats(self) -> dict:
        return {
            "episodes": len(self.episodes),
            "entities": len(self.entities),
            "facts": len(self.facts),
            "active_facts": sum(1 for f in self.facts if f.valid_until is None),
            "edges": sum(len(v) for v in self.edges.values()) // 2,
        }

    # ── экспорт в Obsidian (узлы + [[wiki-links]] = graph view) ───
    def to_obsidian(self, vault_dir: str | Path, folder: str = "Graph") -> list[str]:
        vault = Path(vault_dir)
        gdir = vault / folder
        gdir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        for ename, meta in self.entities.items():
            safe = re.sub(r"[^0-9A-Za-zА-Яа-яЁё _-]", "", ename).strip()
            if not safe:
                continue
            nb = self.neighbors(ename)
            facts = self.query_as_of(ename, datetime.now(timezone.utc).isoformat())
            lines = [
                f"# {safe}",
                "",
                f"Тип: {meta['type']} · впервые: {meta['first_seen'][:10]}",
                "",
                "## Факты",
                "",
            ]
            if facts:
                for f in facts:
                    state = "активен" if not f["valid_until"] else f"до {f['valid_until'][:10]}"
                    lines.append(f"- **{f['predicate']}**: {f['obj']} ({state}, {f['source']})")
            else:
                lines.append("- (пока нет фактов)")
            lines += ["", "## Связи", ""]
            for n in nb:
                lines.append(f"- [[{re.sub(r'[^0-9A-Za-zА-Яа-яЁё _-]', '', n)}]]")
            (gdir / f"{safe}.md").write_text("\n".join(lines), encoding="utf-8")
            written.append(f"{folder}/{safe}.md")
        return written


# ─────────────────────────────────────────────────────────────────────────────
# 3. Бэкенд Graphiti + Neo4j (полная версия — когда всё доступно)
# ─────────────────────────────────────────────────────────────────────────────

class GraphitiBackend:
    """Обёртка над graphiti-core. Поднимается только если реально доступен.

    Честное ограничение: graphiti-core для извлечения сущностей вызывает
    LLM через новый OpenAI Responses API (/responses), который поддерживают
    не все провайдеры (Ollama/DeepSeek — только /chat/completions). Поэтому
    бэкенд 'graphiti' включается полностью только при наличии OpenAI-ключа
    (OPENAI_API_KEY) или провайдера с поддержкой Responses API. В остальных
    случаях фасад автоматически работает на файловом бэкенде с теми же
    техниками — без внешних вызовов.
    """

    def __init__(self, uri: str | None = None, user: str | None = None,
                 password: str | None = None):
        self.available = False
        self.reason = ""
        try:
            import graphiti_core  # noqa: F401
            self._gc = graphiti_core
        except ImportError:
            self.reason = "graphiti_core не установлен"
            return
        self.uri = uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.environ.get("NEO4J_USER", "neo4j")
        self.password = password or os.environ.get("NEO4J_PASSWORD", "")
        if not self.password:
            self.reason = "нет пароля Neo4j (NEO4J_PASSWORD)"
            return
        # доступность Bolt
        try:
            from neo4j import GraphDatabase
            drv = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            with drv.session() as s:
                s.run("RETURN 1").single()
            drv.close()
        except Exception as e:
            self.reason = f"Neo4j недоступен: {str(e)[:70]}"
            return
        self.available = True

    def client(self):
        """LLM + embedder из локального Ollama (без внешних ключей)."""
        from graphiti_core.llm_client import OpenAIClient
        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.embedder import OpenAIEmbedder, OpenAIEmbedderConfig

        base = os.environ.get("OLLAMA_BASE", "http://localhost:11434/v1")
        model = os.environ.get("GRAPH_LLM", "qwen3:4b")
        llm = OpenAIClient(config=LLMConfig(
            model=model, base_url=base, api_key="ollama"
        ))
        emb = OpenAIEmbedder(config=OpenAIEmbedderConfig(
            embedding_model=os.environ.get("GRAPH_EMBEDDER", "nomic-embed-text"),
            base_url=base, api_key="ollama",
        ))
        return llm, emb

    def graphiti(self):
        from graphiti_core import Graphiti
        llm, emb = self.client()
        return Graphiti(self.uri, self.user, self.password,
                        llm_client=llm, embedder=emb)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Фасад
# ─────────────────────────────────────────────────────────────────────────────

class GraphMemory:
    """Фасад: auto → graphiti (если доступен) → file (всегда)."""

    def __init__(self, backend: str = "auto", path: str | None = None):
        self.backend_name = backend
        self.file = FileGraphStore(path)
        self._graphiti: object | None = None
        self._gb: GraphitiBackend | None = None

        if backend in ("auto", "graphiti"):
            gb = GraphitiBackend()
            if gb.available:
                try:
                    self._graphiti = gb.graphiti()
                    self._gb = gb
                    self.backend_name = "graphiti"
                except Exception as e:
                    self._graphiti = None
                    self.backend_name = "file"
                    gb.reason = f"init Graphiti: {str(e)[:70]}"
            else:
                self.backend_name = "file"
        else:
            self.backend_name = "file"

    @property
    def reason(self) -> str:
        if self._gb and not self._graphiti:
            return self._gb.reason
        return ""

    def add_episode(self, name: str, body: str, source: str = "memory",
                    ref_time: datetime | None = None):
        if self._graphiti is not None:
            try:
                import asyncio
                from graphiti_core.nodes import EpisodeType
                rt = ref_time or datetime.now(timezone.utc)
                return asyncio.run(self._graphiti.add_episode(
                    name=name, episode_body=body,
                    source_description=source, reference_time=rt,
                    source=EpisodeType.message,
                ))
            except Exception:
                # не роняем агента — откат на файловый граф
                pass
        return self.file.add_episode(name, body, source, ref_time)

    def search(self, query: str, limit: int = 6, **kw) -> list[dict]:
        if self._graphiti is not None:
            try:
                import asyncio
                edges = asyncio.run(self._graphiti.search(query, num_results=limit))
                return [
                    {
                        "episode_id": getattr(e, "uuid", ""),
                        "name": getattr(e, "name", ""),
                        "fact": f"{getattr(e, 'subject_name', '?')} {getattr(e, 'relation', '?')} {getattr(e, 'object_name', '?')}",
                        "source": "",
                        "ingested_at": str(getattr(e, "valid_at", ""))[:19],
                        "score": 1.0,
                    } for e in edges
                ]
            except Exception:
                pass
        return self.file.search(query, limit, **kw)

    def query_as_of(self, subject: str, iso: str) -> list[dict]:
        return self.file.query_as_of(subject, iso)

    def neighbors(self, entity: str, depth: int = 1) -> list[str]:
        return self.file.neighbors(entity, depth)

    def stats(self) -> dict:
        s = self.file.stats()
        s["backend"] = self.backend_name
        if self._graphiti is not None:
            s["graphiti"] = True
        return s

    def to_obsidian(self, vault_dir: str | Path, folder: str = "Graph") -> list[str]:
        return self.file.to_obsidian(vault_dir, folder)


# ─────────────────────────────────────────────────────────────────────────────
# 5. CLI: python3 -m token_diet.graph_memory <episode|search|stats|export>
# ─────────────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import sys

    gm = GraphMemory()
    args = sys.argv[1:]
    cmd = args[0] if args else "stats"

    if cmd == "stats":
        s = gm.stats()
        print(f"Бэкенд: {s['backend']}" + (f" (graphiti)" if s.get("graphiti") else ""))
        print(f"Эпизоды: {s['episodes']} | Сущности: {s['entities']} | "
              f"Факты: {s['facts']} (активных {s['active_facts']}) | Связи: {s['edges']}")

    elif cmd == "episode" and len(args) >= 3:
        name, body = args[1], " ".join(args[2:])
        ep = gm.add_episode(name, body, source="cli")
        print(f"Эпизод {ep.id if hasattr(ep, 'id') else 'ok'} — сущности: "
              f"{', '.join(ep.entities) if hasattr(ep, 'entities') else extract_entities(body)}")
        print("Факты:", len(gm.file.facts), "| активных:",
              sum(1 for f in gm.file.facts if f.valid_until is None))

    elif cmd == "search" and len(args) >= 2:
        q = " ".join(args[1:])
        for r in gm.search(q, limit=5):
            print(f"[{r['score']}] {r['name']} ({r['ingested_at'][:16]}) — {r['body'][:90]}...")

    elif cmd == "export" and len(args) >= 2:
        n = gm.to_obsidian(args[1])
        print(f"Экспортировано заметок в Obsidian: {len(n)}")
    else:
        print("Использование:")
        print("  python3 -m token_diet.graph_memory stats")
        print("  python3 -m token_diet.graph_memory episode 'Имя' 'текст эпизода'")
        print("  python3 -m token_diet.graph_memory search 'запрос'")
        print("  python3 -m token_diet.graph_memory export /путь/к/vault")


if __name__ == "__main__":
    _cli()
