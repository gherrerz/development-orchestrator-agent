def simulate_credit(amount: float, term: int, interest_rate: float) -> dict:
    """Calcula el monto total a pagar y la cuota mensual."""
    if amount <= 0 or term <= 0 or interest_rate < 0:
        return {}
    total_interest = amount * (interest_rate / 100) * term
    total_payment = amount + total_interest
    monthly_payment = total_payment / term
    return {
        "total_payment": round(total_payment, 2),
        "monthly_payment": round(monthly_payment, 2)
    }
