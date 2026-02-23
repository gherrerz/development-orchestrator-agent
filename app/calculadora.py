def calcular_credito(monto, tasa_interes, plazo):
    # Convertir tasa de interés anual a mensual
    tasa_mensual = tasa_interes / 100 / 12
    # Número total de pagos
    total_pagos = plazo * 12
    # Cálculo de la cuota mensual
    cuota = monto * (tasa_mensual * (1 + tasa_mensual) ** total_pagos) / ((1 + tasa_mensual) ** total_pagos - 1)
    return cuota
