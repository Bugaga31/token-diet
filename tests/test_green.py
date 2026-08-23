"""Tests for GreenCalculator — environmental impact."""

import sys, os, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT = os.path.dirname(_HERE)
if _PROJECT not in sys.path:
    sys.path.insert(0, _PROJECT)

from token_diet.green_calculator import GreenCalculator, GreenMetrics


class TestGreenMetrics(unittest.TestCase):
    """Core calculations are correct."""

    def setUp(self):
        self.calc = GreenCalculator()

    def test_zero_tokens(self):
        m = self.calc.measure(0)
        self.assertEqual(m.tokens_saved, 0)
        self.assertEqual(m.kwh_saved, 0.0)

    def test_one_thousand_tokens(self):
        m = self.calc.measure(1000)
        self.assertAlmostEqual(m.kwh_saved, 0.003, places=5)
        self.assertAlmostEqual(m.kg_co2_saved, 0.0012, places=5)
        self.assertAlmostEqual(m.liters_water_saved, 2.0, places=1)

    def test_one_million_tokens(self):
        m = self.calc.measure(1_000_000)
        self.assertAlmostEqual(m.kwh_saved, 3.0, places=1)
        self.assertAlmostEqual(m.kg_co2_saved, 1.2, places=1)
        self.assertAlmostEqual(m.liters_water_saved, 2000.0, places=0)

    def test_trees_equivalent(self):
        m = self.calc.measure(1_000_000)
        trees = m.trees_equivalent
        # 1.2 kg CO2 / 21 kg per tree ≈ 0.057 trees
        self.assertGreater(trees, 0)
        self.assertLess(trees, 1.0)

    def test_smartphones_charged(self):
        m = self.calc.measure(1_000_000)
        charges = m.smartphones_charged
        # 3 kWh / 0.015 kWh ≈ 200 charges
        self.assertAlmostEqual(charges, 200, delta=10)

    def test_projection(self):
        m = self.calc.projection(
            tokens_per_call=1000,
            calls_per_day=1000,
            days=365,
            savings_pct=46.6,
        )
        # 365M tokens × 0.466 = ~170M saved
        self.assertGreater(m.tokens_saved, 100_000_000)
        self.assertGreater(m.kwh_saved, 300)

    def test_addition(self):
        a = self.calc.measure(1000)
        b = self.calc.measure(1000)
        c = a + b
        self.assertEqual(c.tokens_saved, 2000)
        self.assertAlmostEqual(c.kwh_saved, 0.006, places=5)

    def test_multiplication(self):
        a = self.calc.measure(1000)
        b = a * 10
        self.assertEqual(b.tokens_saved, 10000)

    def test_global_impact(self):
        report = GreenCalculator.global_impact()
        self.assertIn("tokens", report)
        self.assertIn("CO2", report)
        self.assertIn("homes", report)

    def test_render(self):
        m = self.calc.measure(1000)
        r = m.render()
        self.assertIn("tokens saved", r)
        self.assertIn("kWh", r)

    def test_report(self):
        r = self.calc.report(1000000)
        self.assertIn("Environmental Savings", r)
        self.assertIn("trees", r)


if __name__ == "__main__":
    unittest.main()
