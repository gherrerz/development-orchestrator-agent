import pytest
from agent.tools.calculator import calculate_credits

def test_calculate_credits():
    assert calculate_credits(1000, 0.05, 2) == 1102.5

if __name__ == '__main__':
    pytest.main()
