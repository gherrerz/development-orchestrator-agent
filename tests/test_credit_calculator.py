import pytest
from agent.tools.credit_calculator import calculate_credit, round_to_two_decimals

def test_calculate_credit():
    assert round_to_two_decimals(calculate_credit(1000, 5, 10)) == 1060.66

def test_round_to_two_decimals():
    assert round_to_two_decimals(10.12345) == 10.12
    assert round_to_two_decimals(10.12678) == 10.13
    assert round_to_two_decimals(10.1) == 10.1
    assert round_to_two_decimals(10.0) == 10.0
    assert round_to_two_decimals(10.999) == 11.0

if __name__ == '__main__':
    pytest.main()
