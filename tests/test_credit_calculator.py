import pytest
from app.credit_calculator import simulate_credit

def test_simulate_credit_typical():
    result = simulate_credit(10000, 12, 12)
    assert "monthly_payment" in result
    assert "total_payment" in result
    assert result["monthly_payment"] > 0
    assert result["total_payment"] > 0

def test_simulate_credit_zero_interest():
    result = simulate_credit(12000, 12, 0)
    assert result["monthly_payment"] == 1000
    assert result["total_payment"] == 12000

def test_simulate_credit_invalid_params():
    with pytest.raises(ValueError):
        simulate_credit(-1000, 12, 10)
    with pytest.raises(ValueError):
        simulate_credit(1000, 0, 10)
    with pytest.raises(ValueError):
        simulate_credit(1000, 12, -5)

def test_simulate_credit_rounding():
    result = simulate_credit(1000, 3, 10)
    assert round(result["monthly_payment"], 2) == result["monthly_payment"]
    assert round(result["total_payment"], 2) == result["total_payment"]
