import pytest
from app.calculadora import calcular_credito

class TestCalculadora:
    def test_calcular_credito(self):
        # Caso de prueba: Monto 10000, Tasa 5%, Plazo 1 año
        cuota = calcular_credito(10000, 5, 1)
        assert cuota == pytest.approx(856.07, rel=1e-2)  # Cálculo derivado
