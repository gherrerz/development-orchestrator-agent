import pytest
from app.credit_calculator import calculate_monthly_payment, simulate_credit

def test_calculate_monthly_payment_zero_interest():
    principal = 1200
    annual_rate = 0
    years = 1
    expected = 100
    assert calculate_monthly_payment(principal, annual_rate, years) == expected

def test_calculate_monthly_payment_positive_interest():
    principal = 1000
    annual_rate = 12
    years = 1
    payment = calculate_monthly_payment(principal, annual_rate, years)
    assert payment > 0

def test_simulate_credit():
    principal = 1000
    annual_rate = 12
    years = 1
    result = simulate_credit(principal, annual_rate, years)
    assert "monthly_payment" in result
    assert "total_payment" in result
    assert "total_interest" in result
    assert result["total_payment"] > principal
    assert result["total_interest"] > 0
