## token-diet v3.0.0 — Lesson Learned

### The Hard Lesson
An AI assistant spent **20+ iterations** debugging `adb input text` to copy a file to a phone — when `adb push` does it in **one command**. This is the pattern v3.0.0 fixes: **stop overthinking, start doing.**

### 4 New Modules (100% algorithmic, no API calls)

| Module | What it does | Tokens saved |
|--------|-------------|-------------|
| **rapid_context.py** | Instant intent classifier: CODE/QUESTION/ACTION/RESEARCH/INVESTMENT/DEBUG/QUICK/CHAT in <1ms | Eliminates context-analysis prompts |
| **decisive_agent.py** | Action-first pipeline: INSTANT/FAST/DEEP modes. Strips deliberation filler ("Let me think...") | Up to 30% on reasoning tokens |
| **focus_keeper.py** | Tangent detector: catches DEBUG_SPIRAL, TOOL_FIXATION, PERMISSION_SPIRAL. Returns to goal | Prevents infinite debug loops |
| **mistake_learner.py** | Anti-pattern memory: 12 built-in patterns (NEVER adb input text for files, PREFER push over input, etc.) | Prevents repeating known mistakes |

### How it works
```python
from token_diet import classify, decide, strip_fluff, check_action

# 1. Classify intent INSTANTLY
ctx = classify("write fibonacci function")
# → ContextClass(intent="CODE", urgency=0.3, complexity=0.2)

# 2. Decide action WITHOUT deliberation
d = decide("write fibonacci function", intent="CODE")
# → Decision(mode="FAST", skip_deliberation=True)

# 3. Strip deliberation filler from output
clean = strip_fluff("Let me think... OK here: def fib(n):...")
# → "def fib(n):..."

# 4. Check before executing
alert = check_action("adb shell input text cp file")
# → MistakeAlert: "NEVER use input text for files. Use adb push."
```

### For the planet. For the people.
Every token not wasted on deliberation, tangent-chasing, or repeating mistakes = **less energy, lower costs, faster results**. That's the mission.

### Install
```bash
pip install git+https://github.com/Bugaga31/token-diet.git
# or with server:
pip install "git+https://github.com/Bugaga31/token-diet.git[server]"
```
