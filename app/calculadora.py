def calcular_credito(monto, tasa_interes, plazo):
    """
    Calcula la cuota mensual de un crédito.
    :param monto: Monto total del crédito.
    :param tasa_interes: Tasa de interés anual en porcentaje.
    :param plazo: Plazo del crédito en años.
    :return: Cuota mensual a pagar.
    """
    tasa_mensual = (tasa_interes / 100) / 12
    numero_cuotas = plazo * 12
    cuota = monto * (tasa_mensual * (1 + tasa_mensual) ** numero_cuotas) / ((1 + tasa_mensual) ** numero_cuotas - 1)
    return cuota
