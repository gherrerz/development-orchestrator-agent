import pytest
from app.calculadora import calcular_credito


def test_calcular_credito():
    # Test de la función calcular_credito
    monto = 100000
    tasa_interes = 5
    plazo = 15
    cuota_esperada = calcular_credito(monto, tasa_interes, plazo)
    # Cálculo esperado derivado de la fórmula
    cuota_calculada = 790.79  # Cálculo manual para verificación
    assert cuota_esperada == pytest.approx(cuota_calculada, rel=1e-2)


if __name__ == '__main__':
    pytest.main()
