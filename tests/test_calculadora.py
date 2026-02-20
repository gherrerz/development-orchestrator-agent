import pytest
from app.calculadora import calcular_credito, calcular_total_a_pagar


class TestCalculadora:
    def test_calcular_credito(self):
        # Caso de prueba: Monto 10000, Tasa 5%, Plazo 1 año
        cuota = calcular_credito(10000, 5, 1)
        assert cuota == pytest.approx(188.71, rel=1e-2)  # Cálculo derivado

    def test_calcular_total_a_pagar(self):
        # Caso de prueba: Cuota 188.71, Plazo 1 año
        total = calcular_total_a_pagar(188.71, 1)
        assert total == 2264.52  # Cálculo derivado
