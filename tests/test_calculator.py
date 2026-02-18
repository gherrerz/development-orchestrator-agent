import pytest
from app.calculator import CreditCalculator

class TestCreditCalculator:
    def test_calculate_total_payment(self):
        calculator = CreditCalculator(1000, 0.05, 2)
        assert calculator.calculate_total_payment() == 1100.0  # 1000 * (1 + 0.05 * 2)

    def test_calculate_monthly_payment(self):
        calculator = CreditCalculator(1000, 0.05, 2)
        assert calculator.calculate_monthly_payment() == pytest.approx(45.8333, rel=1e-5)  # 1100 / 24

    def test_calculate_interest(self):
        calculator = CreditCalculator(1000, 0.05, 2)
        assert calculator.calculate_interest() == 100.0  # 1100 - 1000
