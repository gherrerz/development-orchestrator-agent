import pytest
from agent.tools.credit_calculator import calculate_credit

def test_calculate_credit():
    assert calculate_credit(1000, 0.05, 2) == 1100.0
    assert calculate_credit(2000, 0.03, 1) == 2060.0
    assert calculate_credit(1500, 0.04, 3) == 1860.0
