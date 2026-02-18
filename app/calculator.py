def calcular_credito(monto, tasa_interes, plazo):
    """
    Calcula el monto total a pagar en un crédito.
    :param monto: Monto del crédito
    :param tasa_interes: Tasa de interés anual
    :param plazo: Plazo en años
    :return: Monto total a pagar
    """
    tasa_mensual = tasa_interes / 100 / 12
    numero_pagos = plazo * 12
    pago_mensual = (monto * tasa_mensual) / (1 - (1 + tasa_mensual) ** -numero_pagos)
    total_a_pagar = pago_mensual * numero_pagos
    return total_a_pagar
