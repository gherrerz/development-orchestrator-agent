def calcular_credito(monto, tasa, plazo):
    """
    Calcula la cuota mensual de un crédito.
    :param monto: Monto del crédito
    :param tasa: Tasa de interés anual en porcentaje
    :param plazo: Plazo en años
    :return: Cuota mensual
    """
    tasa_mensual = tasa / 100 / 12
    numero_pagos = plazo * 12
    cuota = monto * tasa_mensual / (1 - (1 + tasa_mensual) ** -numero_pagos)
    return cuota
