def calcular_credito(principal: float, annual_rate_pct: float, years: int) -> float:
    """
    Calcula la cuota mensual de un préstamo basado en el monto principal, la tasa de interés anual y el plazo en años.
    :param principal: Monto del préstamo
    :param annual_rate_pct: Tasa de interés anual en porcentaje
    :param years: Plazo del préstamo en años
    :return: Cuota mensual a pagar
    """
    monthly_rate = (annual_rate_pct / 100) / 12
    months = years * 12
    cuota = principal * (monthly_rate * (1 + monthly_rate) ** months) / ((1 + monthly_rate) ** months - 1)
    return cuota

