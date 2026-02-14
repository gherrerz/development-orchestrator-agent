from agent.calculator import calcular_credito

def test_calcular_credito():
    assert abs(calcular_credito(1000, 5, 2) - 1100.0) < 0.01
