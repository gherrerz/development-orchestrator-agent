def calcular_cuota(monto, tasa_interes_anual, plazo_meses):
    """Calcula la cuota mensual de un crédito usando fórmula de amortización.
    Args:
        monto (float): monto del crédito
        tasa_interes_anual (float): tasa de interés anual en porcentaje (ej. 12.5)
        plazo_meses (int): plazo en meses
    Returns:
        float: cuota mensual
    """
    if plazo_meses <= 0:
        raise ValueError("El plazo debe ser mayor que cero")
    if monto <= 0:
        raise ValueError("El monto debe ser mayor que cero")
    if tasa_interes_anual < 0:
        raise ValueError("La tasa de interés no puede ser negativa")

    tasa_mensual = tasa_interes_anual / 100 / 12
    if tasa_mensual == 0:
        return monto / plazo_meses

    cuota = monto * (tasa_mensual * (1 + tasa_mensual) ** plazo_meses) / ((1 + tasa_mensual) ** plazo_meses - 1)
    return cuota
