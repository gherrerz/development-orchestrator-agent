from django.test import TestCase
from .forms import CreditCalculatorForm


class CreditCalculatorFormTest(TestCase):
    def test_valid_form(self):
        form = CreditCalculatorForm(data={'amount': 1000, 'interest_rate': 5, 'years': 2})
        self.assertTrue(form.is_valid())

    def test_invalid_form(self):
        form = CreditCalculatorForm(data={'amount': -1000, 'interest_rate': 5, 'years': 2})
        self.assertFalse(form.is_valid())

    def test_calculation(self):
        amount = 1000
        interest_rate = 5
        years = 2
        total_payment = amount * (1 + interest_rate / 100) ** years  # 1102.5
        self.assertAlmostEqual(total_payment, 1102.5, places=2)
