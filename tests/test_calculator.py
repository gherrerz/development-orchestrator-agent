import pytest
from app.calculator import calcular_credito


def test_calcular_credito():
    # Caso de prueba: monto=10000, tasa_interes=5, plazo=2
    resultado = calcular_credito(10000, 5, 2)
    esperado = 549.86  # Cálculo: (10000 * (5/100/12)) / (1 - (1 + (5/100/12)) ** -(2*12))
    assert resultado == pytest.approx(esperado, rel=1e-2)

    # Caso de prueba: monto=20000, tasa_interes=3, plazo=1
    resultado = calcular_credito(20000, 3, 1)
    esperado = 1710.69  # Cálculo: (20000 * (3/100/12)) / (1 - (1 + (3/100/12)) ** -(1*12))
    assert resultado == pytest.approx(esperado, rel=1e-2)
