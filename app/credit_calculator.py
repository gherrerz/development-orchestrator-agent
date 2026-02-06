import math

def calculate_monthly_payment(principal: float, annual_rate: float, years: int) -> float:
    """Calcula la cuota mensual de un crédito.

    Args:
        principal (float): Monto del crédito.
        annual_rate (float): Tasa de interés anual en porcentaje.
        years (int): Plazo en años.

    Returns:
        float: Cuota mensual.
    """
    monthly_rate = annual_rate / 100 / 12
    n_payments = years * 12
    if monthly_rate == 0:
        return principal / n_payments
    return principal * (monthly_rate * (1 + monthly_rate) ** n_payments) / ((1 + monthly_rate) ** n_payments - 1)

def simulate_credit(principal: float, annual_rate: float, years: int) -> dict:
    """Simula un crédito y devuelve detalles de la simulación.

    Args:
        principal (float): Monto del crédito.
        annual_rate (float): Tasa de interés anual en porcentaje.
        years (int): Plazo en años.

    Returns:
        dict: Diccionario con cuota mensual, total pagado y total intereses.
    """
    monthly_payment = calculate_monthly_payment(principal, annual_rate, years)
    total_payment = monthly_payment * years * 12
    total_interest = total_payment - principal
    return {
        "monthly_payment": round(monthly_payment, 2),
        "total_payment": round(total_payment, 2),
        "total_interest": round(total_interest, 2),
    }
