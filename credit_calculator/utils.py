def calculate_credit_simulation(amount, annual_interest_rate, months):
    """
    Calcula la cuota mensual, el pago total y el interés total de un crédito.

    :param amount: Monto del crédito
    :param annual_interest_rate: Tasa de interés anual en porcentaje
    :param months: Plazo en meses
    :return: dict con monthly_payment, total_payment, total_interest
    """
    if months == 0:
        return {'monthly_payment': 0, 'total_payment': 0, 'total_interest': 0}

    monthly_interest_rate = annual_interest_rate / 100 / 12
    if monthly_interest_rate == 0:
        monthly_payment = amount / months
    else:
        monthly_payment = amount * (monthly_interest_rate * (1 + monthly_interest_rate) ** months) / ((1 + monthly_interest_rate) ** months - 1)

    total_payment = monthly_payment * months
    total_interest = total_payment - amount
    return {'monthly_payment': monthly_payment, 'total_payment': total_payment, 'total_interest': total_interest}
