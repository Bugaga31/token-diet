"""Token Diet: context memory, lossless packing, caching and language routing.
Python 3.10+. Standard library only; tiktoken and a translator are optional.
"""
from __future__ import annotations
import hashlib, json, math, re, time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

# ----------------------------- tokens and prices --------------------------
def count_tokens(text: str, model: str | None = None) -> int:
    try:
        import tiktoken
        return len(tiktoken.encoding_for_model(model or "gpt-4o-mini").encode(text))
    except Exception:
        parts = re.findall(r" ?[A-Za-z]+| ?[0-9]+| ?[^\sA-Za-z0-9]+|\s+", text)
        return sum(max(1, len(p.strip()) // 4) if p.strip().isalnum() else max(1, len(p.strip())) for p in parts)

@dataclass
class Prices:
    input: float = 3.0
    cache_write: float = 3.75
    cache_read: float = 0.30
    output: float = 15.0
    def cost(self, fresh: int, write: int = 0, read: int = 0, output: int = 0) -> float:
        return (fresh*self.input + write*self.cache_write + read*self.cache_read + output*self.output) / 1_000_000

@dataclass
class Usage:
    fresh: int = 0; cache_write: int = 0; cache_read: int = 0; output: int = 0
    def __add__(self, x: "Usage") -> "Usage":
        return Usage(self.fresh+x.fresh, self.cache_write+x.cache_write, self.cache_read+x.cache_read, self.output+x.output)

class TokenMeter:
    def __init__(self, prices: Prices | None = None):
        self.prices = prices or Prices(); self.tasks: dict[str, Usage] = {}; self.sections: dict[str, int] = {}
    def record(self, task_id: str, response: Any) -> Usage:
        u = getattr(response, "usage", {}) or {}
        get = lambda k: int((u.get(k, 0) if isinstance(u, dict) else getattr(u, k, 0)) or 0)
        now = Usage(get("input_tokens"), get("cache_creation_input_tokens"), get("cache_read_input_tokens"), get("output_tokens"))
        self.tasks[task_id] = self.tasks.get(task_id, Usage()) + now
        return now
    def total(self) -> Usage:
        out = Usage()
        for u in self.tasks.values(): out += u
        return out
    def report(self) -> str:
        u = self.total(); inp = u.fresh + u.cache_write + u.cache_read
        return f"tasks={len(self.tasks)} cost=${self.prices.cost(u.fresh,u.cache_write,u.cache_read,u.output):.4f} input/output={inp}/{u.output} cache_hit={100*u.cache_read/max(1,inp):.1f}%"

# ----------------------------- stable prompt -------------------------------
def canonical_json(x: Any) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

class PromptBuilder:
    def __init__(self): self.static=[]; self.semi_static=[]; self.volatile=[]
    def add_static(self, text: str):
        for pattern, label in [(r"\d{4}-\d{2}-\d{2}","date"),(r"\b[0-9a-f]{8}-[0-9a-f]{4}","uuid"),(r"\b\d{10,13}\b","epoch")]:
            if re.search(pattern, text): raise ValueError(f"{label} in static prompt; move it to volatile")
        self.static.append(text); return self
    def add_semi_static(self, text: str): self.semi_static.append(text); return self
    def add_volatile(self, text: str): self.volatile.append(text); return self
    def system(self) -> list[dict[str, Any]]:
        out=[]
        if self.static: out.append({"type":"text","text":"\n\n".join(self.static),"cache_control":{"type":"ephemeral"}})
        if self.semi_static: out.append({"type":"text","text":"\n\n".join(self.semi_static)})
        return out
    def user(self) -> str: return "\n\n".join(self.volatile)

# ----------------------------- lossless StructPack -------------------------
ESC="~"; SEP="|"; MISSING=object()
def _esc(s): return s.replace("~","~~").replace("|","~p").replace("\n","~n")
def _unesc(s):
    out=[]; i=0; mp={"~":"~","p":"|","n":"\n"}
    while i<len(s):
        if s[i]=="~" and i+1<len(s): out.append(mp.get(s[i+1],s[i+1])); i+=2
        else: out.append(s[i]); i+=1
    return "".join(out)
def _split(row):
    out=[]; cur=[]; i=0
    while i<len(row):
        if row[i]=="~" and i+1<len(row): cur.append(row[i:i+2]); i+=2
        elif row[i]=="|": out.append("".join(cur)); cur=[]; i+=1
        else: cur.append(row[i]); i+=1
    out.append("".join(cur)); return out
def _typ(vals):
    k={type(v) for v in vals if v is not None and v is not MISSING}
    if not k:return "s"
    if k=={bool}:return "b"
    if k=={int}:return "i"
    if k<={int,float}:return "f"
    if k=={str}:return "s"
    return "j"
def pack_records(records: list[dict], min_len=8, min_uses=3) -> str|None:
    if len(records)<3 or not all(isinstance(r,dict) for r in records): return None
    cols=[]
    for r in records:
        for k in r:
            if k not in cols: cols.append(k)
    if not cols or len(cols)>60 or any(not isinstance(k,str) or re.search(r"[|,:~\s]",k) for k in cols): return None
    if sum(len(r) for r in records)/(len(records)*len(cols))<.45:return None
    grid=[[r.get(c,MISSING) for c in cols] for r in records]; types=[_typ([r[j] for r in grid]) for j in range(len(cols))]
    freq={}
    for row in grid:
        for v in row:
            if isinstance(v,str) and len(v)>=min_len: freq[v]=freq.get(v,0)+1
    legend={v:f"@{i}" for i,(v,n) in enumerate(sorted([(v,n) for v,n in freq.items() if n>=min_uses],key=lambda x:-x[1]*len(x[0])),1)}
    def cell(v,t):
        if v is MISSING:return "~M"
        if v is None:return "~0"
        if t=="j":return _esc(json.dumps(v,ensure_ascii=False))
        if t=="b":return "1" if v else "0"
        if isinstance(v,str): return legend.get(v,_esc("@"+v if v.startswith("@") else v))
        return _esc(json.dumps(v,ensure_ascii=False))
    lines=["#p1 n=%d c="%len(records)+",".join(f"{c}:{t}" for c,t in zip(cols,types))]
    lines += [f"#d {ref}={_esc(v)}" for v,ref in legend.items()]
    for row in grid:
        line=SEP.join(cell(v,t) for v,t in zip(row,types)); lines.append("~h"+line if line.startswith("#") else line)
    return "\n".join(lines)
def unpack_records(text: str) -> list[dict]:
    lines=text.split("\n"); spec=lines[0].split(" c=",1)[1]; cols=[]; types=[]
    for p in spec.split(","): n,t=p.rsplit(":",1); cols.append(n); types.append(t)
    legend={}; body=[]
    for line in lines[1:]:
        if line.startswith("~h"): body.append(line[2:])
        elif line.startswith("#d "): r,v=line[3:].split("=",1); legend[r]=_unesc(v)
        else: body.append(line)
    out=[]
    for line in body:
        cells=_split(line)
        if len(cells)!=len(cols): raise ValueError("bad StructPack row width")
        rec={}
        for n,t,raw in zip(cols,types,cells):
            if raw=="~M":continue
            if raw=="~0":rec[n]=None
            elif raw in legend:rec[n]=legend[raw]
            elif t=="j":rec[n]=json.loads(_unesc(raw))
            elif t=="b":rec[n]=raw=="1"
            elif t in ("i","f"):rec[n]=json.loads(_unesc(raw))
            else:
                s=_unesc(raw); rec[n]=s[1:] if s.startswith("@@") else s
        out.append(rec)
    return out

def guarded_records(records: list[dict]) -> tuple[str,str,int,int]:
    raw=json.dumps(records,ensure_ascii=False); before=count_tokens(raw); packed=pack_records(records)
    if packed is None:return raw,"json",before,before
    try:
        if unpack_records(packed)!=records:return raw,"json-unsafe",before,before
    except Exception:return raw,"json-unsafe",before,before
    after=count_tokens(packed)
    return (packed,"structpack",before,after) if after<before*.95 else (raw,"json-no-gain",before,before)

# ----------------------------- retrieval and blobs -------------------------
def _shingles(text,k=5):
    w=re.findall(r"\w+",text.lower()); return frozenset(" ".join(w[i:i+k]) for i in range(max(1,len(w)-k+1)))
def deduplicate_chunks(chunks, threshold=.85):
    ranked=sorted(enumerate(chunks),key=lambda x:len(x[1]),reverse=True); kept=[]
    for i,c in ranked:
        s=_shingles(c)
        if not any(len(s&old)/max(1,len(s|old))>=threshold for _,old,_ in kept): kept.append((i,s,c))
    kept.sort(); return [c for _,_,c in kept],len(chunks)-len(kept)
class BlobStore:
    def __init__(self,preview_chars=500):self.preview_chars=preview_chars;self.items={}
    def put(self,body,kind="blob"):
        h=hashlib.sha256(body.encode()).hexdigest()[:16]; key=f"{kind}:{h}"; self.items[key]=body; return key
    def get(self,handle):return self.items.get(handle,"")
    def reference(self,body,kind="blob"):
        if len(body)<=self.preview_chars:return body
        h=self.put(body,kind); return f"<{h} bytes={len(body)}>\n{body[:self.preview_chars]}\n[fetch {h} for full body]\n</{h}>"

# ----------------------------- event memory ---------------------------------
@dataclass
class MemoryEvent:
    kind:str; text:str; importance:float=.5; created_at:float=0; expires_at:float|None=None; source_turn:int=0; event_id:str=""
    def live(self):return self.expires_at is None or time.time()<self.expires_at
    def rendered(self):return f"[{self.kind}] {self.text}"
class EventStore:
    kinds={"fact","decision","preference","constraint","open_question","temporary"}
    def __init__(self,path=None):
        self.path=Path(path) if path else None; self.events={}; self.turn=0
        if self.path and self.path.exists(): self.load()
    def add(self,kind,text,importance=.5,ttl_seconds=None):
        if kind not in self.kinds:raise ValueError(f"unknown kind: {kind}")
        self.turn+=1; text=text.strip(); key=hashlib.sha256(f"{kind}:{text.lower()}".encode()).hexdigest()[:20]; now=time.time()
        self.events[key]=MemoryEvent(kind,text,max(0,min(1,importance)),now,now+ttl_seconds if ttl_seconds else None,self.turn,key); self.save(); return key
    def all(self):self.cleanup();return list(self.events.values())
    def cleanup(self):self.events={k:v for k,v in self.events.items() if v.live()};self.save()
    def remove(self,key):self.events.pop(key,None);self.save()
    def save(self):
        if self.path:
            self.path.parent.mkdir(parents=True,exist_ok=True); tmp=self.path.with_suffix(".tmp"); tmp.write_text(json.dumps({"turn":self.turn,"events":[asdict(x) for x in self.events.values()]},ensure_ascii=False,indent=2)); tmp.replace(self.path)
    def load(self):
        p=json.loads(self.path.read_text());self.turn=p.get("turn",0);self.events={x["event_id"]:MemoryEvent(**x) for x in p.get("events",[])};self.cleanup()
class AdaptiveContext:
    bonus={"constraint":.3,"decision":.2,"preference":.15,"open_question":.1,"temporary":.05,"fact":0}
    def __init__(self,store,counter=count_tokens):self.store=store;self.counter=counter
    def select(self,question,budget=1200):
        terms=set(re.findall(r"\w{3,}",question.lower())); events=self.store.all()
        def score(e):return e.importance*.45+len(terms&set(re.findall(r"\w{3,}",e.text.lower())))/max(1,len(terms))*.4+self.bonus.get(e.kind,0)-min(.3,(self.store.turn-e.source_turn)*.003)
        ordered=sorted(events,key=score,reverse=True); pinned=[e for e in events if e.kind=="constraint"]; ordered=pinned+[e for e in ordered if e not in pinned]; out=[]; used=0
        for e in ordered:
            n=self.counter(e.rendered())
            if used+n<=budget:out.append(e);used+=n
        return out
    def render(self,question,budget=1200):return "\n".join(e.rendered() for e in self.select(question,budget))

def extract_events(user_text,assistant_text,store):
    for pat,kind,imp,ttl in [(r"\bI prefer\s+([^.!?\n]+)","preference",.8,None),(r"\bI need\s+([^.!?\n]+)","constraint",.9,None),(r"\bwe decided\s+(?:to\s+)?([^.!?\n]+)","decision",.9,None)]:
        for m in re.finditer(pat,user_text,re.I):store.add(kind,m.group(1),imp,ttl)
    for m in re.finditer(r"\b(?:decision|next step|constraint):\s*([^.!?\n]+)",assistant_text,re.I):store.add("decision",m.group(1),.8)

# ----------------------------- language router -----------------------------
@dataclass
class TranslationChoice:
    text:str; source:str; target:str; original_tokens:int; translated_tokens:int; translation_cost:int; saved_tokens:int; used:bool; reason:str
class TranslationCache:
    def __init__(self):self.items={}
    def _key(self,text,source,target):return hashlib.sha256(f"{source}:{target}:{text}".encode()).hexdigest()
    def get(self,text,source,target):return self.items.get(self._key(text,source,target))
    def put(self,text,source,target,value):self.items[self._key(text,source,target)]=value

def translation_safe(text):
    if len(text)<80 or text.count("```")>=2:return False
    if re.search(r"https?://|\b[A-Fa-f0-9]{16,}\b|\b(api[_ -]?key|password|secret|legal|medical)\b",text,re.I):return False
    return True

def choose_language(text,source,candidates,translate,counter=count_tokens,cache=None,min_saving=40):
    cache=cache or TranslationCache(); original=counter(text)
    if not translation_safe(text):return TranslationChoice(text,source,source,original,original,0,0,False,"unsafe_or_short")
    best=(text,source,original,0)
    for target in candidates:
        if target==source:continue
        translated=cache.get(text,source,target); fee=0 if translated is not None else max(16,original//5)
        if translated is None:translated=translate(text,source,target);cache.put(text,source,target,translated)
        tokens=counter(translated); net=original-tokens-fee
        if net>original-best[2]-best[3]:best=(translated,target,tokens,fee)
    translated,target,tokens,fee=best; net=original-tokens-fee
    if target==source or net<min_saving:return TranslationChoice(text,source,source,original,original,0,0,False,"not_profitable")
    return TranslationChoice(translated,source,target,original,tokens,fee,net,True,"net_saving")

# ----------------------------- compact context manager ---------------------
class ContextManager:
    def __init__(self,path="memory/events.json",budget=1200):self.store=EventStore(path);self.memory=AdaptiveContext(self.store);self.budget=budget
    def before(self,question):return self.memory.render(question,self.budget)
    def after(self,user,assistant):extract_events(user,assistant,self.store)
    def remember(self,kind,text,importance=.5,ttl_seconds=None):return self.store.add(kind,text,importance,ttl_seconds)

if __name__=="__main__":
    rows=[{"id":i,"status":"blocked","owner":"artem","url":f"https://x/{i}"} for i in range(6)]
    packed,mode,before,after=guarded_records(rows)
    assert unpack_records(packed)==rows if mode=="structpack" else json.loads(packed)==rows
    print(f"StructPack: {mode}, {before}->{after} tokens")
    m=ContextManager(path=None,budget=100);m.remember("constraint","Не использовать платные API",1);m.remember("preference","Пользователь предпочитает Python",.8);print(m.before("Напиши Python код без платных API"))
    print("self-test: OK")
