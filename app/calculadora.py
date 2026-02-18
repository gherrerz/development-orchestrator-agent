def calcular_credito(monto, tasa_interes, plazo):
    # Convertir tasa de interés anual a mensual y plazo a meses
    tasa_mensual = tasa_interes / 100 / 12
    plazo_meses = plazo * 12
    # Cálculo de la cuota mensual usando la fórmula de amortización
    cuota = (monto * tasa_mensual) / (1 - (1 + tasa_mensual) ** -plazo_meses)
    return cuota
