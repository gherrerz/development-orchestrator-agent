def calcular_credito(monto, tasa_interes, plazo):
    """
    Calcula el pago mensual de un crédito basado en el monto, la tasa de interés y el plazo.
    """
    tasa_mensual = tasa_interes / 100 / 12
    num_pagos = plazo * 12
    pago_mensual = (monto * tasa_mensual) / (1 - (1 + tasa_mensual) ** -num_pagos)
    return round(pago_mensual, 2)


# Ejemplo de uso
if __name__ == '__main__':
    monto = 10000
    tasa_interes = 5
    plazo = 2
    print(f'Pago mensual: {calcular_credito(monto, tasa_interes, plazo)}')
