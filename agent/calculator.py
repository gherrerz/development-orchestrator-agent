def calcular_credito(monto, tasa_interes, anos):
    """Calcula el total a pagar en un crédito."""
    return monto * (1 + (tasa_interes / 100) * anos)
