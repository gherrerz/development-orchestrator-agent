def calcular_credito(monto, tasa_interes, plazo):
    # Convertir tasa de interés anual a mensual
    tasa_mensual = tasa_interes / 100 / 12
    # Calcular número total de pagos
    total_pagos = plazo * 12
    # Calcular cuota mensual usando la fórmula de amortización
    cuota = monto * tasa_mensual / (1 - (1 + tasa_mensual) ** -total_pagos)
    return cuota
