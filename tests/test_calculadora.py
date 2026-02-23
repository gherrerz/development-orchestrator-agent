import pytest
from app.calculadora import calcular_credito


def test_calcular_credito():
    # Test con valores conocidos
    monto = 10000  # Monto del crédito
    tasa_interes = 5  # Tasa de interés anual
    plazo = 2  # Plazo en años
    cuota = calcular_credito(monto, tasa_interes, plazo)
    expected_cuota = 549.86  # Cálculo manual: cuota = 10000 * (0.0041667 * (1 + 0.0041667)^(24)) / ((1 + 0.0041667)^(24) - 1)
    assert cuota == pytest.approx(expected_cuota, rel=1e-2)
