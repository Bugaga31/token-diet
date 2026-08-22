## token-diet v3.3.1 — Date Anchor: Never Guess Dates Again

### The lesson
I said "today is August 11" — it was August 10. A model that guesses dates breaks every time-based analysis: tomorrow, this week, dividend cut-offs, deadlines.

### The fix
New module **date_anchor.py**:
- Reads the date from **system clock** (never guesses)
- Formats in Russian: "10 августа 2026, понедельник"
- Calculates **trading days** (skips weekends + RF holidays)
- Injects a DATE ANCHOR block into ANY model prompt automatically

### Date anchor in prompts
```
CURRENT DATE ANCHOR (don't guess the date):
  Today (ISO): 2026-08-10
  Today (RU):  10 августа 2026
  Weekday:     понедельник
  Is trading day: yes
  Next trading day: 2026-08-11
  In 3 trading days: 2026-08-13
```

### Also
- `n_trading_days_ahead(3)` — date in 3 trading days
- `describe_horizon(days)` — "завтра", "послезавтра", "на этой неделе"
- Integrated into IntelligenceChain — any model gets the anchor automatically

28 tests. For the people. For the planet.
