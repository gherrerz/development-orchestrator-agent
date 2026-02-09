from typing import Dict

def simulate_credit(amount: float, term: int, interest_rate: float) -> Dict[str, float]:
    """
    Simula un crédito calculando la cuota mensual y el total a pagar.

    :param amount: Monto del crédito
    :param term: Plazo en meses
    :param interest_rate: Tasa de interés anual en porcentaje
    :return: Diccionario con cuota mensual y total a pagar
    """
    if amount <= 0 or term <= 0 or interest_rate < 0:
        raise ValueError("Los parámetros deben ser positivos y la tasa no negativa.")

    monthly_rate = interest_rate / 100 / 12
    if monthly_rate == 0:
        monthly_payment = amount / term
    else:
        monthly_payment = amount * (monthly_rate * (1 + monthly_rate) ** term) / ((1 + monthly_rate) ** term - 1)

    total_payment = monthly_payment * term
    return {"monthly_payment": round(monthly_payment, 2), "total_payment": round(total_payment, 2)}
