def calcular_credito(monto, tasa_interes, años):
    return round(monto * (1 + tasa_interes) ** años, 2)
