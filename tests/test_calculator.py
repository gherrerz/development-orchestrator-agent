import pytest
from app.calculator import calcular_credito

def test_calcular_credito():
    # Caso de prueba: monto=10000, tasa_interes=5, plazo=2
    resultado = calcular_credito(10000, 5, 2)
    esperado = 549.86  # Cálculo: (10000 * (5/100/12)) / (1 - (1 + (5/100/12)) ** -(2*12))
    assert resultado == pytest.approx(esperado, rel=1e-2)

