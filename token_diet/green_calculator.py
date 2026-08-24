"""Green Calculator — environmental impact of token savings.

Every token saved means less electricity, less CO2, less water.
This module translates token-diet's token savings into real-world
environmental metrics based on published research.

Data sources (2024-2026 estimates, conservative values):

  Energy per 1K tokens (large model inference):
    GPT-4 class:     ~0.003 kWh
    Claude class:    ~0.003 kWh
    Smaller models:  ~0.001 kWh

  Carbon intensity:
    Global average:  ~0.4 kg CO2 / kWh
    Green grids:     ~0.05 kg CO2 / kWh (e.g. France, hydro/nuclear)

  Water usage (cooling):
    ~2 liters per 1,000 tokens (evaporative cooling)
    ~0.5 liters per 1,000 tokens (closed-loop cooling)

References:
  - Luccioni et al. "Power Hungry Processing: Watts Driving the Cost of AI Deployment?" (2024)
  - Dodge et al. "Measuring the Carbon Intensity of AI in Cloud Instances" (2022)
  - Li et al. "Making AI Less Thirsty" (2023) — water footprint
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Conservative constants ────────────────────────────────────────────────────

KWH_PER_1K_TOKENS = 0.003        # kWh per 1,000 tokens
KG_CO2_PER_KWH = 0.4             # kg CO2 per kWh (global avg)
KG_CO2_PER_KWH_GREEN = 0.05      # kg CO2 per kWh (green grid)
WATER_LITERS_PER_1K_TOKENS = 2.0  # liters per 1,000 tokens (cooling)


@dataclass
class GreenMetrics:
    """Environmental impact of token savings."""

    tokens_saved: int = 0
    kwh_saved: float = 0.0
    kg_co2_saved: float = 0.0
    kg_co2_saved_green: float = 0.0
    liters_water_saved: float = 0.0

    def __add__(self, other: GreenMetrics) -> GreenMetrics:
        return GreenMetrics(
            tokens_saved=self.tokens_saved + other.tokens_saved,
            kwh_saved=self.kwh_saved + other.kwh_saved,
            kg_co2_saved=self.kg_co2_saved + other.kg_co2_saved,
            kg_co2_saved_green=self.kg_co2_saved_green + other.kg_co2_saved_green,
            liters_water_saved=self.liters_water_saved + other.liters_water_saved,
        )

    def __mul__(self, multiplier: float) -> GreenMetrics:
        return GreenMetrics(
            tokens_saved=int(self.tokens_saved * multiplier),
            kwh_saved=self.kwh_saved * multiplier,
            kg_co2_saved=self.kg_co2_saved * multiplier,
            kg_co2_saved_green=self.kg_co2_saved_green * multiplier,
            liters_water_saved=self.liters_water_saved * multiplier,
        )

    def render(self) -> str:
        """Human-readable summary."""
        lines = [
            f"  {self.tokens_saved:>12,} tokens saved",
            f"  {self.kwh_saved:>12.2f} kWh electricity",
            f"  {self.kg_co2_saved:>12.2f} kg CO2 (global avg grid)",
            f"  {self.kg_co2_saved_green:>12.2f} kg CO2 (green grid)",
            f"  {self.liters_water_saved:>12.1f} liters water (cooling)",
        ]
        return "\n".join(lines)

    @property
    def trees_equivalent(self) -> float:
        """How many trees would absorb this CO2 in one year?

        One mature tree absorbs ~21 kg CO2/year.
        """
        return self.kg_co2_saved / 21.0

    @property
    def smartphones_charged(self) -> float:
        """Equivalent number of smartphone charges.

        One smartphone charge is ~0.015 kWh.
        """
        return self.kwh_saved / 0.015

    @property
    def km_driven_saved(self) -> float:
        """Equivalent kilometers NOT driven (average car).

        Average car emits ~0.12 kg CO2/km.
        """
        if self.kg_co2_saved <= 0:
            return 0.0
        return self.kg_co2_saved / 0.12


class GreenCalculator:
    """Calculate environmental impact of token savings.

    Usage:
        calc = GreenCalculator()
        impact = calc.measure(tokens_saved=1_000_000)
        print(impact.render())
        print(f"Trees: {impact.trees_equivalent:.1f}")
    """

    def __init__(
        self,
        kwh_per_1k: float = KWH_PER_1K_TOKENS,
        kg_co2_per_kwh: float = KG_CO2_PER_KWH,
        kg_co2_green: float = KG_CO2_PER_KWH_GREEN,
        water_liters_per_1k: float = WATER_LITERS_PER_1K_TOKENS,
    ):
        self.kwh_per_1k = kwh_per_1k
        self.kg_co2_per_kwh = kg_co2_per_kwh
        self.kg_co2_green = kg_co2_green
        self.water_per_1k = water_liters_per_1k

    def measure(self, tokens_saved: int) -> GreenMetrics:
        """Convert token count to environmental savings."""
        blocks_of_1k = tokens_saved / 1000.0
        return GreenMetrics(
            tokens_saved=tokens_saved,
            kwh_saved=blocks_of_1k * self.kwh_per_1k,
            kg_co2_saved=blocks_of_1k * self.kwh_per_1k * self.kg_co2_per_kwh,
            kg_co2_saved_green=blocks_of_1k * self.kwh_per_1k * self.kg_co2_green,
            liters_water_saved=blocks_of_1k * self.water_per_1k,
        )

    def projection(
        self,
        tokens_per_call: int,
        calls_per_day: int = 1000,
        days: int = 365,
        savings_pct: float = 46.6,
    ) -> GreenMetrics:
        """Project annual environmental savings.

        Args:
            tokens_per_call: average tokens per API call
            calls_per_day: daily call volume
            days: number of days
            savings_pct: token-diet savings percentage
        """
        total_tokens = tokens_per_call * calls_per_day * days
        tokens_saved = int(total_tokens * savings_pct / 100)
        return self.measure(tokens_saved)

    def report(self, tokens_saved: int) -> str:
        """Full environmental report."""
        m = self.measure(tokens_saved)
        return f"""Environmental Savings
──────────────────────────────────
{m.render()}
──────────────────────────────────
  Equivalents:
    {m.trees_equivalent:.1f} trees absorbing CO2 for 1 year
    {m.smartphones_charged:,.0f} smartphone charges
    {m.km_driven_saved:,.0f} km NOT driven (average car)
"""

    @staticmethod
    def human_impact(annual_savings_usd: float) -> str:
        """What real things can people afford with the money saved?

        Because token-diet is about people, not just tokens.
        """
        ice_cream = annual_savings_usd / 3.0
        park_trips = annual_savings_usd / 10.0
        pizza_nights = annual_savings_usd / 25.0
        books = annual_savings_usd / 15.0
        coffee_with_friends = annual_savings_usd / 5.0
        return f"""Human Impact (${annual_savings_usd:,.0f}/year saved with token-diet)
    {ice_cream:,.0f} ice creams
    {park_trips:,.0f} trips to the park with family
    {pizza_nights:,.0f} pizza nights with friends
    {books:,.0f} books to read
    {coffee_with_friends:,.0f} coffees with someone you love

    It is not about tokens. It is about time, freedom, and the people that matter."""

    @staticmethod
    def global_impact() -> str:
        """Show global impact if everyone used token-diet.

        Assumes 1 billion API calls/day globally × 46.6% savings.
        Conservative estimate: 1,000 tokens/call average.
        """
        calc = GreenCalculator()
        daily_tokens = 1_000_000_000 * 1000  # 1B calls × 1K tokens
        annual_tokens_saved = int(daily_tokens * 365 * 0.466)
        m = calc.measure(annual_tokens_saved)
        return f"""Global Impact (if all LLM calls used token-diet)
──────────────────────────────────────────────────────
  Annual tokens saved:    {m.tokens_saved:>15,}
  Electricity saved:      {m.kwh_saved:>15,.0f} kWh
  CO2 saved:              {m.kg_co2_saved:>15,.0f} kg  ({m.kg_co2_saved/1000:,.0f} tonnes)
  Water saved:            {m.liters_water_saved:>15,.0f} liters  ({m.liters_water_saved/1_000_000:.1f} million L)

  Equivalent to:
    {m.trees_equivalent:,.0f} trees for 1 year
    {m.km_driven_saved:,.0f} km NOT driven
    {m.kwh_saved / 1000:,.0f} MWh — enough to power {m.kwh_saved / 1000 / 10:,.0f} homes for a year
──────────────────────────────────────────────────────"""
