import pytest
from app.calculadora import calcular_credito

class TestCalculadora:
    def test_calcular_credito(self):
        # Test con valores conocidos
        principal = 10000.0
        annual_rate_pct = 5.0
        years = 2
        expected_cuota = calcular_credito(principal, annual_rate_pct, years)
        assert expected_cuota > 0, "La cuota debe ser mayor que 0"

    def test_invariants(self):
        # Invariantes
        p, r = 10000.0, 5.0
        cuota_1y = calcular_credito(p, r, 1)
        cuota_2y = calcular_credito(p, r, 2)
        assert cuota_1y > cuota_2y, "La cuota a 1 año debe ser mayor que a 2 años"
