import pytest
from agent.tools.calculadora_creditos import calcular_credito

def test_calcular_credito():
    assert calcular_credito(1000, 0.05, 2) == 1102.5
    assert calcular_credito(2000, 0.03, 3) == 2185.09
