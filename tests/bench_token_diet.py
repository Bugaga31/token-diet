"""Measure real token savings of app.token_diet on a synthetic workload.

Run: ./venv/bin/python tests/token_diet/bench_token_diet.py
"""

import json
import random

from app.token_diet import (
    BlobStore,
    ContextLedger,
    count_tokens,
    deduplicate_chunks,
    guarded_records,
    project_fields,
)

TICKERS = ["SBER", "GAZP", "YNDX", "LKOH", "GMKN", "MGNT", "ROSN", "TCSG"]
SECTORS = ["financials", "energy", "technology", "materials", "consumer"]


def market_rows(rng: random.Random, n: int) -> list[dict]:
    return [
        {
            "ticker": rng.choice(TICKERS),
            "price": round(rng.uniform(10, 15000), 2),
            "change_pct": round(rng.uniform(-5, 5), 2),
            "volume": rng.randrange(1000, 9_000_000),
            "sector": rng.choice(SECTORS),
            "currency": "RUB",
            "exchange": "MOEX",
            "updated_at": "2026-08-07T09:00:00Z",
        }
        for _ in range(n)
    ]


def trade_rows(rng: random.Random, n: int) -> list[dict]:
    return [
        {
            "id": i,
            "ticker": rng.choice(TICKERS),
            "side": rng.choice(["BUY", "SELL"]),
            "qty": rng.randrange(1, 500),
            "price": round(rng.uniform(10, 15000), 2),
            "status": rng.choice(["filled", "partial", "rejected"]),
            "account": "sandbox-tinvest-primary",
            "strategy": "deepseek-momentum-v3",
        }
        for i in range(n)
    ]


def documents(rng: random.Random) -> list[str]:
    base = (
        "Рынок закрылся смешанно. Индекс МосБиржи изменился незначительно, "
        "лидеры роста в технологическом секторе, слабость в энергетике. "
    )
    unique = base * 12
    near_duplicate = unique + "Дополнительная деталь про объёмы торгов."
    other = (
        "Регулятор сохранил ключевую ставку. Аналитики ожидают давления "
        "на облигации и умеренного укрепления рубля. "
    ) * 12
    return [unique, near_duplicate, other, unique]


def main() -> None:
    rng = random.Random(1)

    market = market_rows(rng, 120)
    trades = trade_rows(rng, 80)
    docs = documents(rng)

    # Naive prompt: everything raw, every field, every duplicate document.
    naive = (
        json.dumps(market, ensure_ascii=False)
        + json.dumps(trades, ensure_ascii=False)
        + "".join(docs)
    )
    naive_tokens = count_tokens(naive)

    # Optimized path.
    projected_market = project_fields(
        market, {"ticker": [], "price": [], "change_pct": [], "sector": []}
    )
    projected_trades = project_fields(
        trades, {"id": [], "ticker": [], "side": [], "qty": [], "status": []}
    )

    market_text, market_mode, market_before, market_after = guarded_records(projected_market)
    trades_text, trades_mode, trades_before, trades_after = guarded_records(projected_trades)

    unique_docs, removed = deduplicate_chunks(docs)
    blobs = BlobStore(preview_chars=400)
    doc_text = "".join(blobs.reference(d, "document") for d in unique_docs)

    optimized = market_text + trades_text + doc_text
    optimized_tokens = count_tokens(optimized)

    ledger = ContextLedger(keep_recent_turns=4, maximum_tokens=800)
    for i in range(24):
        ledger.add("user", f"Вопрос {i} про портфель и рынок с достаточной длиной текста.")
        ledger.add("assistant", f"Ответ {i} с анализом, метриками и рекомендацией по риску.")
    before_turns = len(ledger.turns)
    ledger.checkpoint(lambda old: [f"обсуждено {len(list(old))} реплик ранее"])
    after_turns = len(ledger.turns)

    saved = naive_tokens - optimized_tokens
    print(f"naive tokens:      {naive_tokens}")
    print(f"optimized tokens:  {optimized_tokens}")
    print(f"saved:             {saved} ({100 * saved / naive_tokens:.1f}%)")
    print()
    print(f"market: mode={market_mode} {market_before}->{market_after}")
    print(f"trades: mode={trades_mode} {trades_before}->{trades_after}")
    print(f"documents: {len(docs)} -> {len(unique_docs)} (removed {removed})")
    print(f"history turns: {before_turns} -> {after_turns}")


if __name__ == "__main__":
    main()
