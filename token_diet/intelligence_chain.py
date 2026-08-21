"""Intelligence Chain — методология + память для ЛЮБОЙ нейронки.

Вдохновлено:
- Superpowers (Jesse Vincent): brainstorm → plan → TDD → verify
- Beads (Steve Yegge): git-backed task tracker
- Habr статья 3000+ часов в Claude Code (Maslennikovig)
- Наш ObsidianMemoryStore + ObsidianVault

Суть: любая LLM получает ДИСЦИПЛИНУ через промпт-инъекцию и ПАМЯТЬ через
Obsidian-хранилище. Не важно Claude это, GPT, Gemini, DeepSeek или локальная
модель — methodology chain работает одинаково.

Архитектура:
  1. InjectMethodology → добавляет в промпт строгий рабочий процесс
  2. MemoryHooks → авто-сохранение решений, авто-извлечение контекста
  3. CrossModelAdapter → форматирует промпт под конкретную модель
  4. VerificationGate → проверка результата перед ответом
  5. GitTaskTracker → задачи в git, переживают перезапуск контекста

Для людей. Честность дешевле сожаления.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from .obsidian_memory import ObsidianMemoryStore, ObsidianNote, RetrievalResult
    from .obsidian_vault import ObsidianVault
    HAS_OBSIDIAN = True
except ImportError:
    HAS_OBSIDIAN = False

try:
    from .date_anchor import inject_date_anchor, full_date_context
    HAS_DATE_ANCHOR = True
except ImportError:
    HAS_DATE_ANCHOR = False


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Methodology — как Superpowers, но для любой модели
# ═══════════════════════════════════════════════════════════════════════════════

# Универсальная методология (не привязана к Claude):
# Brainstorm → Plan → TDD → Execute → Verify → Remember
# Работает на GPT, Gemini, DeepSeek, локальных моделях.

_METHODOLOGY_SYSTEM = """You are a disciplined engineer. Follow this workflow:

1. BRAINSTORM (1-3 sentences)
   - Clarify the goal. What exactly needs to be done?
   - Consider 2 approaches. Pick the simplest one.

2. PLAN (numbered list, 3-5 steps)
   - Break into atomic tasks (2-5 min each).
   - Each task = one small change.

3. WRITE TEST FIRST
   - Write a test that VERIFIES the desired behavior.
   - Run it. It MUST fail (RED).

4. IMPLEMENT MINIMALLY
   - Write the smallest code to pass the test.
   - Run tests. ALL must pass (GREEN).

5. VERIFY
   - Run the verification command.
   - Read the FULL output. Do not assume.
   - Only claim \"done\" when output PROVES success.

6. REMEMBER
   - Save key decisions and facts to memory for future use.

RULES:
- No production code without a failing test first.
- No claiming \"done\" without reading verification output.
- Prefer SIMPLE solutions. Complexity is debt.
- If stuck for >3 attempts, try a DIFFERENT approach."""

_METHODOLOGY_SHORT = """Workflow: 1) Clarify goal 2) Plan 3-5 steps 3) Write failing test 4) Implement minimally 5) Verify & read output 6) Save to memory. RULE: no code without test. No \"done\" without proof."""


def inject_methodology(system_prompt: str = "", mode: str = "full") -> str:
    """Inject disciplined workflow into any model's system prompt.

    Args:
        system_prompt: existing system prompt (can be empty)
        mode: 'full' (detailed) or 'short' (token-efficient)

    Returns enhanced system prompt.
    """
    methodology = _METHODOLOGY_SYSTEM if mode == "full" else _METHODOLOGY_SHORT

    if not system_prompt or not system_prompt.strip():
        return methodology

    # Don't double-inject
    if "BRAINSTORM" in system_prompt or "Workflow:" in system_prompt:
        return system_prompt

    # Place methodology BEFORE existing prompt (higher priority at beginning)
    return methodology + "\n\n---\n\n" + system_prompt


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Cross-Model Adapter — prompts for any LLM API
# ═══════════════════════════════════════════════════════════════════════════════

MODEL_FORMATS = {
    "claude": {"system_tag": "", "user_tag": "\n\nHuman: ", "assistant_tag": "\n\nAssistant: "},
    "gpt": {"system_tag": "", "user_tag": "", "assistant_tag": ""},
    "gemini": {"system_tag": "", "user_tag": "", "assistant_tag": ""},
    "deepseek": {"system_tag": "", "user_tag": "User: ", "assistant_tag": "Assistant: "},
    "hermes": {"system_tag": "<|im_start|>system\n", "user_tag": "<|im_start|>user\n", "end_tag": "<|im_end|>"},
    "llama": {"system_tag": "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n", "user_tag": "<|start_header_id|>user<|end_header_id|>\n", "assistant_tag": "<|start_header_id|>assistant<|end_header_id|>\n", "end_tag": "<|eot_id|>"},
    "openchat": {"system_tag": "GPT4 Correct System: ", "user_tag": "User: ", "assistant_tag": "Assistant: "},
    "ollama": {"system_tag": "", "user_tag": "", "assistant_tag": ""},
    "buffy": {"system_tag": "", "user_tag": "", "assistant_tag": ""},  # Freebuff's own format
}


@dataclass
class ModelPrompt:
    """A prompt formatted for a specific model."""
    system: str
    user: str
    full_text: str
    model: str


def format_for_model(
    system: str,
    user: str,
    model: str = "auto",
    history: list[dict[str, str]] | None = None,
) -> ModelPrompt:
    """Format a prompt for any LLM model.

    Detects model type and applies the correct formatting template.
    Works with: Claude, GPT, Gemini, DeepSeek, Hermes, Llama, OpenChat, Ollama.

    Args:
        system: system prompt
        user: user message
        model: model name (auto-detects from common patterns)
        history: previous conversation turns [{role, content}, ...]

    Returns ModelPrompt with formatted full_text ready for API.
    """
    model_lower = model.lower()

    # Auto-detect
    if model == "auto":
        for key in MODEL_FORMATS:
            if key in model_lower:
                model = key
                break
        else:
            model = "gpt"  # default

    fmt = MODEL_FORMATS.get(model, MODEL_FORMATS["gpt"])

    parts: list[str] = []

    # System message
    if system:
        if fmt.get("system_tag"):
            parts.append(fmt["system_tag"] + system)
            if fmt.get("end_tag"):
                parts[-1] += fmt["end_tag"]
        else:
            parts.append(system)

    # History
    if history:
        for msg in history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                tag = fmt.get("user_tag", "")
                parts.append(tag + content)
                if fmt.get("end_tag") and tag:
                    parts[-1] += fmt["end_tag"]
            elif role == "assistant":
                tag = fmt.get("assistant_tag", "")
                parts.append(tag + content)
                if fmt.get("end_tag") and tag:
                    parts[-1] += fmt["end_tag"]

    # Current user message
    user_tag = fmt.get("user_tag", "")
    parts.append(user_tag + user)
    if fmt.get("end_tag") and user_tag:
        parts[-1] += fmt["end_tag"]

    # Assistant prefix (for chat models)
    if fmt.get("assistant_tag"):
        parts.append(fmt["assistant_tag"])

    full_text = "\n".join(parts).strip()
    return ModelPrompt(system=system, user=user, full_text=full_text, model=model)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Memory Hooks — автоматическое сохранение в Obsidian
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MemoryContext:
    """Context retrieved from memory for a prompt."""
    relevant_notes: list[str]
    past_decisions: list[str]
    constraints: list[str]
    full_block: str  # ready to insert into prompt


class MemoryHooks:
    """Auto-save and auto-retrieve from Obsidian memory.

    Hooks into the workflow:
    - Before prompt: retrieve relevant context
    - After prompt: save key decisions
    """

    def __init__(
        self,
        vault_path: str = "~/Documents/Obsidian",
        memory_path: str | None = None,
    ):
        self.vault = ObsidianVault(vault_path) if HAS_OBSIDIAN else None
        self.memory = (
            ObsidianMemoryStore(memory_path)
            if HAS_OBSIDIAN and memory_path
            else None
        )
        self._session_decisions: list[str] = []

    def context_for_task(self, task: str, max_chars: int = 800) -> MemoryContext:
        """Retrieve relevant memory before a task."""
        relevant = []
        decisions = []
        constraints = []

        if self.vault:
            results = self.vault.search(task, limit=5)
            relevant = [self.vault.read(t) or "" for t, _ in results if self.vault.read(t)]
            # Also get past decisions
            past = self.vault.search("decision solution resolved", limit=3)
            decisions = [f"{t}: {self.vault.read(t)[:100] if self.vault.read(t) else ''}" for t, _ in past[:2]]

        if self.memory:
            mem_results = self.memory.retrieve(task, max_results=3)
            for r in mem_results:
                if r.note.kind == "constraint":
                    constraints.append(r.note.content[:200])
                elif r.note.kind == "decision":
                    decisions.append(f"{r.note.title}: {r.note.snippet(100)}")

        # Build compact block
        parts = []
        if constraints:
            parts.append("Constraints: " + "; ".join(constraints))
        if decisions:
            parts.append("Past decisions: " + "; ".join(decisions[:2]))
        if relevant:
            # Take first 2 most relevant, truncate
            parts.extend(relevant[:2])

        full = "\n".join(parts) if parts else ""
        return MemoryContext(
            relevant_notes=relevant,
            past_decisions=decisions,
            constraints=constraints,
            full_block=full[:max_chars],
        )

    def remember_decision(self, title: str, decision: str, tags: list[str] | None = None) -> None:
        """Save a decision to Obsidian vault."""
        self._session_decisions.append(f"{title}: {decision[:100]}")
        if self.vault:
            self.vault.write(
                title=title,
                body=decision,
                tags=tags or ["decision", "auto-saved"],
                kind="decision",
            )
        if self.memory:
            self.memory.remember(
                title=title,
                content=decision,
                kind="decision",
                tags=tags or ["auto-saved"],
                importance=0.8,
            )

    def get_session_learnings(self) -> str:
        """Get all decisions made this session."""
        if not self._session_decisions:
            return "(no decisions recorded)"
        return "\n".join(f"- {d}" for d in self._session_decisions)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Verification Gate — проверка перед «готово»
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class VerificationResult:
    passed: bool
    output: str
    issues: list[str]
    proof: str = ""


def verify_command(command: str, expected_pattern: str | None = None) -> VerificationResult:
    """Run a verification command and check it proves success.

    Like Superpowers' verification-before-completion skill.
    """
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        issues = []

        if result.returncode != 0:
            issues.append(f"Command failed with exit code {result.returncode}")

        if expected_pattern and not re.search(expected_pattern, output):
            issues.append(f"Expected pattern '{expected_pattern}' not found in output")

        passed = len(issues) == 0
        return VerificationResult(
            passed=passed,
            output=output[:1000],
            issues=issues,
            proof=output[:500] if passed else "",
        )
    except Exception as e:
        return VerificationResult(
            passed=False,
            output=str(e),
            issues=[f"Verification error: {e}"],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Git Task Tracker — задачи в git (как Beads)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Task:
    """A git-backed task."""
    id: str
    title: str
    status: str  # todo | in_progress | done | blocked
    depends_on: list[str] = field(default_factory=list)
    created_at: str = ""
    done_at: str = ""


class GitTaskTracker:
    """Lightweight task tracker stored in git.

    Inspired by Beads (Steve Yegge): tasks are markdown files in .tasks/,
    committed to git → survive context resets, searchable, auditable.
    """

    def __init__(self, repo_path: str = "."):
        self.repo = Path(repo_path)
        self.repo.mkdir(parents=True, exist_ok=True)
        self.tasks_dir = self.repo / ".tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    def create(self, title: str, depends_on: list[str] | None = None) -> Task:
        """Create a new task."""
        task_id = re.sub(r"[^a-z0-9-]", "", title.lower().replace(" ", "-"))[:40]
        task = Task(
            id=task_id,
            title=title,
            status="todo",
            depends_on=depends_on or [],
            created_at=datetime.now().isoformat(),
        )
        self._write(task)
        return task

    def list(self, status: str | None = None) -> list[Task]:
        """List tasks, optionally filtered by status."""
        tasks = []
        for f in sorted(self.tasks_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                task = Task(
                    id=data["id"],
                    title=data["title"],
                    status=data["status"],
                    depends_on=data.get("depends_on", []),
                    created_at=data.get("created_at", ""),
                    done_at=data.get("done_at", ""),
                )
                if status is None or task.status == status:
                    tasks.append(task)
            except (json.JSONDecodeError, KeyError):
                pass
        return tasks

    def ready(self) -> list[Task]:
        """Tasks with all dependencies satisfied (ready to work on)."""
        all_tasks = {t.id: t for t in self.list()}
        ready = []
        for task in self.list("todo"):
            if all(dep in all_tasks and all_tasks[dep].status == "done" for dep in task.depends_on):
                ready.append(task)
        return ready

    def start(self, task_id: str) -> bool:
        """Mark task as in progress."""
        task = self._read(task_id)
        if task:
            task.status = "in_progress"
            self._write(task)
            return True
        return False

    def done(self, task_id: str) -> bool:
        """Mark task as done."""
        task = self._read(task_id)
        if task:
            task.status = "done"
            task.done_at = datetime.now().isoformat()
            self._write(task)
            # Auto-commit
            self._git_commit(f"Done: {task.title}")
            return True
        return False

    def status_report(self) -> str:
        """Compact status report."""
        todo = len(self.list("todo"))
        progress = len(self.list("in_progress"))
        done = len(self.list("done"))
        ready = len(self.ready())
        return (
            f"Tasks: {done} done, {progress} in progress, {todo} todo "
            f"({ready} ready to start)"
        )

    def _write(self, task: Task) -> None:
        f = self.tasks_dir / f"{task.id}.json"
        f.write_text(json.dumps({
            "id": task.id,
            "title": task.title,
            "status": task.status,
            "depends_on": task.depends_on,
            "created_at": task.created_at,
            "done_at": task.done_at,
        }, indent=2, ensure_ascii=False))

    def _read(self, task_id: str) -> Task | None:
        f = self.tasks_dir / f"{task_id}.json"
        if f.exists():
            data = json.loads(f.read_text())
            return Task(
                id=data["id"], title=data["title"], status=data["status"],
                depends_on=data.get("depends_on", []),
                created_at=data.get("created_at", ""),
                done_at=data.get("done_at", ""),
            )
        return None

    def _git_commit(self, message: str) -> None:
        try:
            subprocess.run(
                ["git", "-C", str(self.repo), "add", ".tasks/"],
                capture_output=True, timeout=10,
            )
            subprocess.run(
                ["git", "-C", str(self.repo), "commit", "-m", message],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass  # Non-critical


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Full Intelligence Chain — всё вместе
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ChainResult:
    """Result from running the intelligence chain."""
    enhanced_prompt: str
    memory_context: MemoryContext | None
    model_prompt: ModelPrompt | None
    methodology_applied: bool


class IntelligenceChain:
    """Full intelligence pipeline for ANY LLM.

    Usage:
        chain = IntelligenceChain(model="deepseek")
        result = chain.prepare(
            task="Implement JWT authentication",
            system="You are a backend developer.",
        )
        # result.enhanced_prompt → ready to send to DeepSeek API
        # After getting response:
        chain.remember("JWT auth: HS256 with 15min expiry")
    """

    def __init__(
        self,
        model: str = "auto",
        vault_path: str = "~/Documents/Obsidian",
        methodology_mode: str = "full",
        anchor_date: bool = True,
    ):
        self.model = model
        self.methodology_mode = methodology_mode
        self.anchor_date = anchor_date
        self.hooks = MemoryHooks(vault_path=vault_path)
        self.tracker = GitTaskTracker()

    def prepare(
        self,
        task: str,
        system: str = "",
        history: list[dict[str, str]] | None = None,
    ) -> ChainResult:
        """Prepare a prompt with methodology + memory + model formatting.

        Returns everything needed to call any LLM API.
        """
        # Step 1: Inject methodology
        enhanced_system = inject_methodology(system, self.methodology_mode)

        # Step 1.5: Inject date anchor (never let the model guess the date)
        if self.anchor_date and HAS_DATE_ANCHOR:
            enhanced_system = inject_date_anchor(enhanced_system)

        # Step 2: Retrieve memory context
        mem_ctx = self.hooks.context_for_task(task)

        # Step 3: Combine system + memory
        if mem_ctx and mem_ctx.full_block:
            enhanced_system = enhanced_system + "\n\n[MEMORY CONTEXT]\n" + mem_ctx.full_block

        # Step 4: Format for target model
        model_prompt = format_for_model(
            system=enhanced_system,
            user=task,
            model=self.model,
            history=history,
        )

        return ChainResult(
            enhanced_prompt=model_prompt.full_text,
            memory_context=mem_ctx,
            model_prompt=model_prompt,
            methodology_applied=True,
        )

    def remember(self, decision: str, tags: list[str] | None = None) -> None:
        """Save a decision to Obsidian memory."""
        title = f"Decision: {decision[:60]}"
        self.hooks.remember_decision(title, decision, tags)

    def create_task(self, title: str) -> Task:
        """Create a tracked task."""
        return self.tracker.create(title)

    def task_status(self) -> str:
        """Get task status report."""
        return self.tracker.status_report()


# ═══════════════════════════════════════════════════════════════════════════════
# Quick utilities
# ═══════════════════════════════════════════════════════════════════════════════

def smart_prompt(
    task: str,
    model: str = "auto",
    system: str = "",
) -> str:
    """One-liner: get a methodology-enhanced, memory-aware prompt for any model."""
    chain = IntelligenceChain(model=model)
    result = chain.prepare(task, system)
    return result.enhanced_prompt


def remember(decision: str) -> None:
    """One-liner: save a decision to Obsidian vault."""
    chain = IntelligenceChain()
    chain.remember(decision)
