import math
import pytest

# Contract note (enterprise):
# - "years" significa años (int)
# - la función de cálculo debe retornar CUOTA MENSUAL (monthly payment), no total a pagar.
#
# Policy: PROHIBIDO hardcodear expected de amortización "manual/derivado".
# Expected se deriva por fórmula estándar dentro del test.

def expected_monthly_payment(principal: float, annual_rate_pct: float, years: int) -> float:
    r = annual_rate_pct / 100.0 / 12.0
    n = years * 12
    if principal <= 0 or years <= 0:
        raise ValueError("principal/years must be positive")
    if annual_rate_pct < 0:
        raise ValueError("annual_rate_pct must be >= 0")
    if r == 0:
        return principal / n
    return principal * r / (1 - (1 + r) ** (-n))

def _call_sut(principal: float, annual_rate_pct: float, years: int) -> float:
    """
    Intento de import robusto:
    - el implementador puede crear app/calculadora.py o app/calculator.py
    - o colocar la función en un módulo distinto
    Este test solo exige que exista calcular_credito(...) o calculate_monthly_payment(...)
    """
    # 1) app/calculadora.py
    try:
        from app.calculadora import calcular_credito  # type: ignore
        return float(calcular_credito(principal, annual_rate_pct, years))
    except Exception:
        pass

    # 2) app/calculator.py
    try:
        from app.calculator import calculate_monthly_payment  # type: ignore
        return float(calculate_monthly_payment(principal, annual_rate_pct, years))
    except Exception:
        pass

    # 3) fallback: error claro
    raise AssertionError(
        "SUT not found. Debes implementar calcular_credito(...) (ES) o calculate_monthly_payment(...) (EN) "
        "en app/calculadora.py o app/calculator.py. Contrato: retorna cuota mensual; years en años."
    )

def test_finance_monthly_payment_matches_formula():
    principal, rate, years = 10000.0, 5.0, 2
    got = _call_sut(principal, rate, years)
    exp = expected_monthly_payment(principal, rate, years)
    assert got == pytest.approx(exp, abs=0.01)

def test_finance_invariants():
    # Invariantes robustos (multi-implementación)
    p, r = 10000.0, 5.0
    pay_1y = _call_sut(p, r, 1)
    pay_2y = _call_sut(p, r, 2)
    assert pay_1y > 0
    assert pay_2y > 0
    # a mayor plazo, menor cuota (manteniendo tasa y principal)
    assert pay_2y < pay_1y
