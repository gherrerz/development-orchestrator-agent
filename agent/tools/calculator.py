def calculate_credits(principal, rate, time):
    """
    Calcula el total de créditos basados en el principal, la tasa de interés y el tiempo.

    Args:
        principal (float): Monto principal.
        rate (float): Tasa de interés anual.
        time (int): Tiempo en años.

    Returns:
        float: Total de créditos después del tiempo.
    """
    return principal * (1 + rate) ** time
