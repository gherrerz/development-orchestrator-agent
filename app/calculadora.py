def calcular_credito(monto, tasa_interes, plazo):
    """
    Calcula la cuota mensual de un crédito.
    :param monto: Monto del crédito
    :param tasa_interes: Tasa de interés anual en porcentaje
    :param plazo: Plazo en años
    :return: Cuota mensual
    """
    tasa_mensual = tasa_interes / 100 / 12
    numero_cuotas = plazo * 12
    cuota = (monto * tasa_mensual) / (1 - (1 + tasa_mensual) ** -numero_cuotas)
    return cuota


def calcular_total_a_pagar(cuota, plazo):
    """
    Calcula el total a pagar por el crédito.
    :param cuota: Cuota mensual
    :param plazo: Plazo en años
    :return: Total a pagar
    """
    total = cuota * plazo * 12
    return total
