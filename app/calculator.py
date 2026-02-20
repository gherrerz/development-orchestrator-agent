def calcular_credito(monto, tasa_interes, plazo):
    # Cálculo del crédito
    tasa_mensual = tasa_interes / 100 / 12
    plazo_meses = plazo * 12
    cuota = (monto * tasa_mensual) / (1 - (1 + tasa_mensual) ** -plazo_meses)
    return round(cuota, 2)

