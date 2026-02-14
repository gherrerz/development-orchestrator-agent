def calculate_credit(principal, annual_rate, years):
    """
    Calcula el monto total a pagar en un crédito.

    :param principal: Monto del crédito.
    :param annual_rate: Tasa de interés anual en porcentaje.
    :param years: Número de años para el crédito.
    :return: Monto total a pagar.
    """
    monthly_rate = annual_rate / 100 / 12
    number_of_payments = years * 12
    total_payment = principal * (monthly_rate * (1 + monthly_rate) ** number_of_payments) / ((1 + monthly_rate) ** number_of_payments - 1)
    return total_payment

def round_to_two_decimals(value):
    """
    Redondea un valor a dos decimales.
    """
    return round(value, 2)
