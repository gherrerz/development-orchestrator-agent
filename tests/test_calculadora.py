import pytest
from app.calculadora import calcular_credito

def test_calcular_credito():
    # Test con valores conocidos
    monto = 10000  # Monto del crédito
    tasa_interes = 5  # Tasa de interés anual
    plazo = 2  # Plazo en años
    cuota_esperada = 549.86  # Cálculo manual o derivado
    cuota = calcular_credito(monto, tasa_interes, plazo)
    assert cuota == pytest.approx(cuota_esperada, rel=1e-2)  # Usar comparación aproximada
