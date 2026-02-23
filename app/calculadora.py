def calcular_cuota(principal, tasa_interes, plazo):
    """
    Calcula la cuota mensual de un préstamo.
    :param principal: Monto del préstamo
    :param tasa_interes: Tasa de interés anual en porcentaje
    :param plazo: Plazo en años
    :return: Cuota mensual
    """
    tasa_mensual = tasa_interes / 100 / 12
    meses = plazo * 12
    cuota = principal * tasa_mensual / (1 - (1 + tasa_mensual) ** -meses)
    return cuota


def calcular_total_a_pagar(principal, tasa_interes, plazo):
    """
    Calcula el total a pagar de un préstamo.
    :param principal: Monto del préstamo
    :param tasa_interes: Tasa de interés anual en porcentaje
    :param plazo: Plazo en años
    :return: Total a pagar
    """
    cuota = calcular_cuota(principal, tasa_interes, plazo)
    total = cuota * plazo * 12
    return total
