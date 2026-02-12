import pytest
from agent.tools.credit_calculator import calculate_credit

def test_calculate_credit():
    assert calculate_credit(1000, 0.05, 2) == 1102.5
    assert calculate_credit(2000, 0.03, 5) == 2318.55
